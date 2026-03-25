"""
Job de sincronização de Expedição VHSys.
Mantido em módulo separado para evitar o problema de double-import
(main vs __main__) quando admin_routes importa o job.

Uso:
  # em main.py, após criar mercos_service:
  from src.expedicao import init_expedicao, job_sync_expedicao
  init_expedicao(mercos_service.vhsys)

  # em admin_routes.py ou qualquer lugar:
  from src.expedicao import job_sync_expedicao
  threading.Thread(target=job_sync_expedicao, daemon=True).start()
"""

import logging
import threading

from src import database as db
from src.whatsapp import get_whatsapp

logger = logging.getLogger(__name__)

_expedicao_lock = threading.Lock()
_expedicao_endpoint_disponivel: bool | None = None  # None=não testado ainda
_vhsys_service = None


def init_expedicao(vhsys_service):
    """Inicializa o módulo com a instância de VhsysService. Chamar uma vez no startup."""
    global _vhsys_service
    _vhsys_service = vhsys_service


def job_sync_expedicao():
    """
    Detecta mudanças de expedição no VHSys e atualiza pedidos_fluxo automaticamente.
    Tenta GET /expedicoes (estratégia primária); se 404, usa GET /pedidos/{id} (fallback).
    """
    global _expedicao_endpoint_disponivel

    if _vhsys_service is None:
        logger.error("[Expedicao] VhsysService não inicializado — chame init_expedicao() no startup.")
        return

    if not _expedicao_lock.acquire(blocking=False):
        logger.debug("[Expedicao] Job já em execução — pulando.")
        return

    try:
        pedidos = db.fluxo_listar_para_sync_expedicao(limit=50)
        if not pedidos:
            logger.debug("[Expedicao] Nenhum pedido aguardando sync de expedição.")
            return

        logger.info(f"[Expedicao] Verificando {len(pedidos)} pedido(s).")
        mudancas, endpoint_ok = _vhsys_service.sincronizar_expedicao(
            pedidos, _expedicao_endpoint_disponivel
        )
        _expedicao_endpoint_disponivel = endpoint_ok

        if not mudancas:
            logger.debug("[Expedicao] Nenhuma mudança detectada.")
            return

        wa = get_whatsapp()

        for m in mudancas:
            try:
                if m["novo_status"] == "separado":
                    db.fluxo_marcar_separado(m["mercos_id"])
                    logger.info(f"[Expedicao] Pedido #{m['numero']} (mercos={m['mercos_id']}) → SEPARADO")
                    try:
                        wa.notificar_separado_automatico(
                            m["numero"], m["mercos_id"], m["cliente"], m["vhsys_id"]
                        )
                    except Exception as e:
                        logger.warning(f"[Expedicao] WhatsApp separado falhou: {e}")

                elif m["novo_status"] == "enviado":
                    db.fluxo_marcar_enviado(m["mercos_id"])
                    logger.info(f"[Expedicao] Pedido #{m['numero']} (mercos={m['mercos_id']}) → ENVIADO")
                    try:
                        wa.notificar_enviado_automatico(
                            m["numero"], m["mercos_id"], m["cliente"], m["vhsys_id"]
                        )
                    except Exception as e:
                        logger.warning(f"[Expedicao] WhatsApp enviado falhou: {e}")

            except Exception as e:
                logger.error(
                    f"[Expedicao] Erro ao processar mercos_id={m['mercos_id']}: {e}",
                    exc_info=True,
                )

    except Exception as e:
        logger.error(f"[Expedicao] Erro inesperado no job: {e}", exc_info=True)
    finally:
        _expedicao_lock.release()
