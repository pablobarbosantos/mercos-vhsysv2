from dotenv import load_dotenv
load_dotenv()

import uvicorn
from fastapi import FastAPI, Request, HTTPException
import logging
import logging.handlers
import os
import sys
import json
import threading
from mercos_service import MercosService
from src import database as db
from src.admin_routes import router as admin_router

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────

AMBIENTE  = os.getenv("ENV", "development")
NIVEL_LOG = logging.DEBUG if AMBIENTE == "development" else logging.INFO

LOG_DIR  = os.path.join(os.path.dirname(__file__), "logs")
LOG_FILE = os.path.join(LOG_DIR, "sync.log")
os.makedirs(LOG_DIR, exist_ok=True)

_fmt = logging.Formatter(
    fmt="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_file_handler = logging.handlers.TimedRotatingFileHandler(
    LOG_FILE, when="midnight", backupCount=30, encoding="utf-8"
)
_file_handler.setFormatter(_fmt)
_file_handler.setLevel(logging.DEBUG)

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(_fmt)
_console_handler.setLevel(NIVEL_LOG)

logging.basicConfig(level=logging.DEBUG, handlers=[_file_handler, _console_handler])

# Silencia loggers externos verbosos
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)
logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
logger.info(f"[Startup] Ambiente: {AMBIENTE.upper()} | Nível: {logging.getLevelName(NIVEL_LOG)}")

# ──────────────────────────────────────────────────────────────────────────────
# Inicialização do banco
# ──────────────────────────────────────────────────────────────────────────────

logger.info("[Startup] Inicializando banco SQLite...")
db.init_db()
logger.info("[Startup] Banco OK.")

mercos_service = MercosService()

# ──────────────────────────────────────────────────────────────────────────────
# APScheduler — tarefas periódicas
# ──────────────────────────────────────────────────────────────────────────────

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

AUDIT_SEQ_MIN        = int(os.getenv("AUDIT_SEQ_INTERVAL_MIN", 15))
AUDIT_FLUXO_MIN      = int(os.getenv("AUDIT_FLUXO_INTERVAL_MIN", 30))
FECHAMENTO_HORA      = os.getenv("AUDIT_FECHAMENTO_HORA", "20")
FILA_WORKER_SEG      = int(os.getenv("FILA_WORKER_INTERVAL_SEG", 10))
CACHE_REFRESH_HORAS  = int(os.getenv("VHSYS_CACHE_TTL_HORAS", 4))
EXPEDICAO_POLL_MIN   = int(os.getenv("EXPEDICAO_POLL_INTERVAL_MIN", 5))


def _job_sequencia():
    try:
        from src.auditoria import verificar_sequencia
        verificar_sequencia()
    except Exception as e:
        logger.error(f"[Scheduler/Seq] Erro: {e}", exc_info=True)


def _job_fluxo():
    try:
        from src.auditoria import verificar_fluxo
        verificar_fluxo()
    except Exception as e:
        logger.error(f"[Scheduler/Fluxo] Erro: {e}", exc_info=True)


def _job_fechamento():
    try:
        from src.auditoria import fechamento_do_dia
        fechamento_do_dia()
    except Exception as e:
        logger.error(f"[Scheduler/Fechamento] Erro: {e}", exc_info=True)


def _job_auditoria_fila():
    try:
        from src.auditoria import verificar_fila_eventos
        verificar_fila_eventos()
    except Exception as e:
        logger.error(f"[Scheduler/FilaAuditoria] Erro: {e}", exc_info=True)


def _job_refresh_cache():
    try:
        mercos_service.vhsys.forcar_refresh_cache()
    except Exception as e:
        logger.error(f"[Scheduler/Cache] Erro: {e}", exc_info=True)


def _job_boletos_vencidos():
    try:
        from src.auditoria import verificar_boletos_vencidos
        verificar_boletos_vencidos()
    except Exception as e:
        logger.error(f"[Scheduler/Boletos] Erro: {e}", exc_info=True)


# ── Worker da fila de eventos ──────────────────────────────────────────────

_worker_lock = threading.Lock()


def _job_processar_fila():
    """
    Worker principal. Processa até 5 eventos pendentes por execução.
    Lock garante que apenas uma instância roda por vez.
    """
    if not _worker_lock.acquire(blocking=False):
        return

    try:
        # Limpa locks de pedidos que não estão mais em uso
        mercos_service.limpar_locks_antigos()

        itens = db.fila_pegar_proximos(limite=5)
        if not itens:
            return

        for item in itens:
            fila_id    = item["id"]
            evento     = item["evento"]
            tentativas = item["tentativas"] + 1

            db.fila_marcar_processando(fila_id)
            logger.info(
                f"[Fila] Processando fila_id={fila_id} | "
                f"evento={evento} | tentativa={tentativas}"
            )

            try:
                dados     = json.loads(item["payload_json"])
                mercos_id = dados.get("id")

                # Deduplicação para pedido.faturado (segunda chance)
                if evento == "pedido.faturado" and db.pedido_ja_processado(mercos_id):
                    logger.info(
                        f"[Fila] fila_id={fila_id} pedido {mercos_id} "
                        f"já processado — descartando."
                    )
                    db.fila_marcar_ok(fila_id)
                    continue

                resposta = mercos_service.processar_para_vhsys(dados)

                if resposta and resposta.get("code") == 200:
                    db.fila_marcar_ok(fila_id)
                    logger.info(f"[Fila] fila_id={fila_id} processado com sucesso.")
                else:
                    raise RuntimeError(f"VHSys retornou resposta inesperada: {resposta}")

            except Exception as e:
                logger.error(
                    f"[Fila] fila_id={fila_id} erro (tentativa {tentativas}): {e}",
                    exc_info=True,
                )
                db.fila_marcar_erro(fila_id, str(e), tentativas)

    finally:
        _worker_lock.release()


# ── Expedição VHSys ───────────────────────────────────────────────────────────

_expedicao_lock = threading.Lock()
_expedicao_endpoint_disponivel: bool | None = None  # None=não testado ainda


def _job_sync_expedicao():
    """
    Detecta mudanças de expedição no VHSys e atualiza pedidos_fluxo automaticamente.
    Tenta GET /expedicoes (estratégia primária); se 404, usa GET /pedidos/{id} (fallback).
    """
    global _expedicao_endpoint_disponivel

    if not _expedicao_lock.acquire(blocking=False):
        logger.debug("[Expedicao] Job já em execução — pulando.")
        return

    try:
        pedidos = db.fluxo_listar_para_sync_expedicao(limit=50)
        if not pedidos:
            logger.debug("[Expedicao] Nenhum pedido aguardando sync de expedição.")
            return

        logger.info(f"[Expedicao] Verificando {len(pedidos)} pedido(s).")
        mudancas, endpoint_ok = mercos_service.vhsys.sincronizar_expedicao(
            pedidos, _expedicao_endpoint_disponivel
        )
        _expedicao_endpoint_disponivel = endpoint_ok

        if not mudancas:
            logger.debug("[Expedicao] Nenhuma mudança detectada.")
            return

        from src.whatsapp import get_whatsapp
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


# ─────────────────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
scheduler.add_job(_job_sequencia,       "interval", minutes=AUDIT_SEQ_MIN,   id="auditoria_sequencia")
scheduler.add_job(_job_fluxo,           "interval", minutes=AUDIT_FLUXO_MIN,  id="auditoria_fluxo")
scheduler.add_job(_job_auditoria_fila,  "interval", minutes=15,               id="auditoria_fila_eventos")
scheduler.add_job(_job_processar_fila,  "interval", seconds=FILA_WORKER_SEG,  id="worker_fila_eventos", max_instances=1)
scheduler.add_job(_job_refresh_cache,   "interval", hours=CACHE_REFRESH_HORAS, id="refresh_cache_vhsys")
scheduler.add_job(
    _job_fechamento,
    CronTrigger(hour=int(FECHAMENTO_HORA), minute=0, timezone="America/Sao_Paulo"),
    id="fechamento_dia"
)
scheduler.add_job(
    _job_boletos_vencidos,
    CronTrigger(hour=9, minute=0, timezone="America/Sao_Paulo"),
    id="boletos_vencidos"
)
scheduler.add_job(_job_sync_expedicao, "interval", minutes=EXPEDICAO_POLL_MIN,
                  id="job_sync_expedicao", max_instances=1)

# ──────────────────────────────────────────────────────────────────────────────
# FastAPI
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI()
app.include_router(admin_router)


@app.on_event("startup")
async def startup_event():
    # Limpa falsos positivos de auditoria gerados com mercos_id global (bug antigo)
    # IDs globais Mercos são muito maiores que números de pedido (~9 dígitos vs ~4)
    with db.get_conn() as conn:
        removidos = conn.execute(
            "DELETE FROM auditoria_sequencia WHERE mercos_id > 9999999"
        ).rowcount
    if removidos:
        logger.info(f"[Startup] {removidos} falso(s) positivo(s) de auditoria removidos (IDs globais Mercos).")

    recuperados = db.fila_recuperar_travados()
    if recuperados:
        logger.warning(
            f"[Startup] Fila recuperada — {recuperados} item(s) resetados "
            f"para reprocessamento (crash anterior detectado)."
        )
    scheduler.start()
    logger.info(
        f"[Scheduler] Iniciado — "
        f"Worker fila: a cada {FILA_WORKER_SEG}s | "
        f"Sequência: a cada {AUDIT_SEQ_MIN}min | "
        f"Fluxo: a cada {AUDIT_FLUXO_MIN}min | "
        f"Expedição: a cada {EXPEDICAO_POLL_MIN}min | "
        f"Fechamento: {FECHAMENTO_HORA}h"
    )


@app.on_event("shutdown")
async def shutdown_event():
    scheduler.shutdown(wait=False)
    logger.info("[Scheduler] Encerrado.")


# ──────────────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "online"}


@app.post("/webhook/mercos")
async def receive_mercos_order(request: Request):
    try:
        payload = await request.json()
        logger.debug(f"[Webhook] Payload recebido: {payload}")

        eventos = payload if isinstance(payload, list) else [payload]

        for item in eventos:
            evento = item.get("evento")
            dados  = item.get("dados", {})
            logger.info(f"[Webhook] Evento: '{evento}'")

            # ── Pedido novo — persiste na fila antes de qualquer processamento
            if evento == "pedido.gerado":
                numero    = dados.get("numero")
                cnpj      = dados.get("cliente_cnpj", "N/A")
                mercos_id = dados.get("id")
                logger.info(
                    f"[Webhook] Pedido #{numero} | "
                    f"CNPJ: {cnpj} | "
                    f"Itens: {len(dados.get('itens', []))}"
                )

                fila_id = db.fila_enfileirar(
                    evento=evento,
                    mercos_id=mercos_id,
                    payload_json=json.dumps(dados, ensure_ascii=False),
                )

                if mercos_id:
                    db.fluxo_registrar_recebido(
                        mercos_id=mercos_id,
                        numero=str(numero or mercos_id),
                        cliente=dados.get("cliente_razao_social", ""),
                        valor=float(dados.get("valor_total", 0) or 0),
                    )

                logger.info(f"[Webhook] Pedido #{numero} persistido na fila (id={fila_id}).")

            # ── Pedido faturado — segunda chance de importação
            elif evento == "pedido.faturado":
                mercos_id = dados.get("id")
                numero    = dados.get("numero", mercos_id)

                if not mercos_id:
                    logger.warning("[Webhook] pedido.faturado sem ID — ignorado.")
                    continue

                if db.pedido_ja_processado(mercos_id):
                    logger.info(
                        f"[Webhook] pedido.faturado #{numero} (id={mercos_id}) "
                        f"já consta no VHSys — nenhuma ação necessária."
                    )
                else:
                    logger.warning(
                        f"[Webhook] pedido.faturado #{numero} (id={mercos_id}) "
                        f"NÃO encontrado no VHSys — enfileirando (segunda chance)."
                    )
                    fila_id = db.fila_enfileirar(
                        evento=evento,
                        mercos_id=mercos_id,
                        payload_json=json.dumps(dados, ensure_ascii=False),
                    )
                    db.fluxo_registrar_recebido(
                        mercos_id=mercos_id,
                        numero=str(numero),
                        cliente=dados.get("cliente_razao_social", ""),
                        valor=float(dados.get("valor_total", 0) or 0),
                    )
                    logger.info(f"[Webhook] Pedido #{numero} persistido na fila (id={fila_id}).")

            # ── Pedido atualizado (status mudou no Mercos)
            elif evento == "pedido.atualizado":
                mercos_id   = dados.get("id")
                novo_status = str(dados.get("status_customizado_nome", "")).lower()

                if mercos_id:
                    if "separa" in novo_status:
                        db.fluxo_marcar_separado(mercos_id)
                        logger.info(f"[Webhook] Pedido #{mercos_id} marcado como SEPARADO.")
                    elif "envi" in novo_status or "expedi" in novo_status:
                        db.fluxo_marcar_enviado(mercos_id)
                        logger.info(f"[Webhook] Pedido #{mercos_id} marcado como ENVIADO.")
                    elif "cancel" in novo_status:
                        db.fluxo_marcar_cancelado(mercos_id)
                        logger.info(f"[Webhook] Pedido #{mercos_id} marcado como CANCELADO.")
                        from src.auditoria import marcar_buraco_resolvido
                        marcar_buraco_resolvido(mercos_id, "cancelado")

            # ── Outros eventos
            else:
                logger.debug(f"[Webhook] Evento '{evento}' ignorado.")

        return {"status": "received"}

    except ValueError as e:
        logger.error(f"[Webhook] JSON inválido: {e}")
        raise HTTPException(status_code=400, detail="Payload JSON inválido.")
    except Exception as e:
        logger.error(f"[Webhook] Erro inesperado: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Erro interno.")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
