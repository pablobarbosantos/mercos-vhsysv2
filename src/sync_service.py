import logging
from src.mercos_client import MercosClient
from src.vhsys_client import VhsysClient
from src import database as db
from src.mapper import pedido_mercos_para_vhsys

logger = logging.getLogger(__name__)


class SyncService:

    def __init__(self):
        self.mercos = MercosClient()
        self.vhsys = VhsysClient()

    # ──────────────────────────────────────────────────────────
    # Setup inicial
    # ──────────────────────────────────────────────────────────

    def setup(self):
        """Inicializa banco."""
        db.init_db()
        logger.info("[Setup] Serviço inicializado.")

    # ──────────────────────────────────────────────────────────
    # Ciclo principal
    # ──────────────────────────────────────────────────────────

    def sincronizar_pedidos(self):
        logger.info("[Sync] Iniciando ciclo de sincronização de pedidos...")

        alterado_apos = db.get_ultimo_timestamp("pedidos")
        if alterado_apos:
            logger.info(f"[Sync] Buscando pedidos alterados após: {alterado_apos}")
        else:
            logger.info("[Sync] Primeira execução — buscando todos os pedidos confirmados.")

        try:
            pedidos = self.mercos.get_pedidos(alterado_apos=alterado_apos)
        except Exception as e:
            logger.error(f"[Sync] Erro ao buscar pedidos do Mercos: {e}")
            return

        if not pedidos:
            logger.info("[Sync] Nenhum pedido novo. Aguardando próximo ciclo.")
            return

        logger.info(f"[Sync] {len(pedidos)} pedidos para processar.")

        novo_timestamp = None
        processados = 0
        erros = 0
        duplicatas = 0

        for pedido in pedidos:
            mercos_id = pedido.get("id")

            ts = pedido.get("ultima_alteracao") or pedido.get("alterado_em")
            if ts and (novo_timestamp is None or ts > novo_timestamp):
                novo_timestamp = ts

            # Deduplicação
            if db.pedido_ja_processado(mercos_id):
                logger.debug(f"[Sync] Pedido {mercos_id} já processado. Ignorando.")
                duplicatas += 1
                continue

            # Conversão
            payload = pedido_mercos_para_vhsys(pedido)

            if payload is None:
                logger.warning(f"[Sync] Pedido {mercos_id} não pôde ser convertido. Pulando.")
                db.registrar_erro("pedidos", mercos_id, "Payload nulo — cliente ou itens não mapeados")
                erros += 1
                continue

            # DEBUG (IMPORTANTE PRA PRÓXIMO PASSO)
            logger.info(f"[DEBUG] Payload VHSYS: {payload}")

            # Envio
            try:
                resultado = self.vhsys.criar_pedido(payload)

                vhsys_id = (
                    resultado.get("id")
                    or resultado.get("codigo")
                    or resultado.get("numero_pedido")
                    or "?"
                )

                db.salvar_pedido_processado(mercos_id, str(vhsys_id), status="ok")

                logger.info(f"[Sync] ✓ Pedido Mercos {mercos_id} → vhsys {vhsys_id}")

                processados += 1

            except Exception as e:
                logger.error(f"[Sync] ✗ Erro ao criar pedido {mercos_id} no vhsys: {e}")
                db.registrar_erro("pedidos", mercos_id, str(e))
                db.salvar_pedido_processado(mercos_id, "erro", status="erro")
                erros += 1

        # Timestamp
        if novo_timestamp:
            db.salvar_timestamp("pedidos", novo_timestamp)
            logger.info(f"[Sync] Timestamp salvo: {novo_timestamp}")

        logger.info(
            f"[Sync] Ciclo concluído. "
            f"✓ {processados} processados | "
            f"↩ {duplicatas} duplicatas ignoradas | "
            f"✗ {erros} erros"
        )