from dotenv import load_dotenv
load_dotenv()

import uvicorn
from fastapi import FastAPI, Request, HTTPException
import logging
import logging.handlers
import os
import sys
import json
import asyncio
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

from compras import database as compras_db
compras_db.init_db()
logger.info("[Startup] Banco compras OK.")

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
COMPRAS_SEFAZ_HORAS  = float(os.getenv("COMPRAS_SEFAZ_INTERVAL_HORAS", 1.5))
COMPRAS_WORKER_MIN   = int(os.getenv("COMPRAS_WORKER_INTERVAL_MIN", 5))


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


def _job_reconciliacao():
    try:
        from src.auditoria import reconciliar_fim_de_dia
        reconciliar_fim_de_dia()
    except Exception as e:
        logger.error(f"[Scheduler/Reconciliacao] Erro: {e}", exc_info=True)


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


# ── Compras / NF-e ────────────────────────────────────────────────────────────

def _job_sefaz_coletar():
    try:
        from compras.nfe_collector import coletar_nfes
        resultado = coletar_nfes()
        logger.info(f"[Compras/SEFAZ] {resultado}")
    except Exception as e:
        logger.error(f"[Compras/SEFAZ] Erro: {e}", exc_info=True)


def _job_processar_compras():
    try:
        from compras.service import processar_fila_compras
        resultado = processar_fila_compras()
        if not resultado.get("skipped"):
            logger.info(f"[Compras/Worker] {resultado}")
    except Exception as e:
        logger.error(f"[Compras/Worker] Erro: {e}", exc_info=True)


# ── Recuperação automática de histórico ──────────────────────────────────────

def _recuperar_historico_sync():
    """
    Roda uma vez no startup em background thread.
    Popula pedidos_fluxo.valor e itens_pedido para pedidos históricos (antes da fila).
    Estratégias: lista VHSys (valor) → GET individual (valor fallback) → API Mercos (itens).
    """
    import time as _time
    from datetime import datetime, timezone as _tz

    vhsys = mercos_service.vhsys
    corrigidos = 0
    itens_preenchidos = 0

    # Estratégia 1: GET /pedidos (últimos 30 dias) — batch, rápido
    try:
        pedidos_vhsys = vhsys.buscar_pedidos_recentes(dias=30)
        if pedidos_vhsys:
            with db.get_conn() as conn:
                for p in pedidos_vhsys:
                    vid = str(p.get("id_pedido") or p.get("id") or "").strip()
                    if not vid:
                        continue
                    row = conn.execute(
                        "SELECT mercos_id FROM pedidos_processados WHERE vhsys_id=?", (vid,)
                    ).fetchone()
                    if not row:
                        continue
                    mercos_id = row["mercos_id"]
                    valor = float(p.get("valor_total_nota") or p.get("valor_total") or 0)
                    if valor > 0:
                        conn.execute(
                            "UPDATE pedidos_fluxo SET valor=? WHERE mercos_id=? AND (valor=0 OR valor IS NULL)",
                            (valor, mercos_id),
                        )
                        corrigidos += 1
    except Exception as e:
        logger.warning(f"[HistoricoRecuperacao] Estratégia 1 (lista): {e}")

    # Estratégia 2: pedidos ainda com valor=0 → GET /pedidos/{id} individual
    # Filtra mercos_id > 9999999 para evitar IDs globais Mercos (garbage data)
    with db.get_conn() as conn:
        pares = conn.execute("""
            SELECT f.mercos_id, p.vhsys_id, f.recebido_em
            FROM pedidos_fluxo f
            JOIN pedidos_processados p ON p.mercos_id = f.mercos_id
            WHERE (f.valor = 0 OR f.valor IS NULL)
              AND p.status = 'ok'
              AND p.vhsys_id NOT IN ('', 'erro')
              AND p.vhsys_id IS NOT NULL
              AND f.mercos_id <= 9999999
        """).fetchall()

    for par in pares:
        mercos_id, vhsys_id = par["mercos_id"], par["vhsys_id"]
        try:
            resp = vhsys._requisitar_com_retry("GET", f"{vhsys.base_url}/pedidos/{vhsys_id}", timeout=15)
            if resp and resp.status_code == 200:
                raw = resp.json().get("data", {})
                if isinstance(raw, list):
                    raw = raw[0] if raw else {}
                if not isinstance(raw, dict):
                    continue
                valor = float(raw.get("valor_total_nota") or raw.get("valor_total") or 0)
                with db.get_conn() as conn2:
                    conn2.execute(
                        "UPDATE pedidos_fluxo SET valor=? WHERE mercos_id=? AND (valor=0 OR valor IS NULL)",
                        (valor, mercos_id),
                    )
                if valor > 0:
                    corrigidos += 1
        except Exception as e:
            logger.debug(f"[HistoricoRecuperacao] valor mercos={mercos_id}: {e}")
        _time.sleep(0.3)

    # Estratégia 3: pedidos sem itens → tenta endpoints alternativos VHSys
    # VHSys não retorna itens em GET /pedidos/{id} nem em /pedidos/{id}/itens.
    # Tenta: GET /itenspedido?id_ped={id_ped} e GET /pedidos/{id_ped}/produtos
    with db.get_conn() as conn:
        sem_itens = conn.execute("""
            SELECT f.mercos_id, p.vhsys_id, f.recebido_em
            FROM pedidos_fluxo f
            JOIN pedidos_processados p ON p.mercos_id = f.mercos_id
            WHERE p.status = 'ok'
              AND p.vhsys_id NOT IN ('', 'erro')
              AND p.vhsys_id IS NOT NULL
              AND f.mercos_id <= 9999999
              AND NOT EXISTS (SELECT 1 FROM itens_pedido i WHERE i.mercos_id = f.mercos_id)
        """).fetchall()

    for par in sem_itens:
        mercos_id, vhsys_id = par["mercos_id"], par["vhsys_id"]
        ts = par["recebido_em"] or datetime.now(_tz.utc).isoformat()
        try:
            itens_raw = []
            # Tenta GET /itenspedido?id_ped={id_ped}
            resp = vhsys._requisitar_com_retry("GET", f"{vhsys.base_url}/itenspedido",
                                               params={"id_ped": vhsys_id}, timeout=15)
            if resp and resp.status_code == 200:
                body = resp.json()
                if body.get("code") != 404:
                    data = body.get("data", [])
                    itens_raw = data if isinstance(data, list) else []

            if not itens_raw:
                # Tenta GET /pedidos/{id_ped}/produtos
                resp2 = vhsys._requisitar_com_retry("GET", f"{vhsys.base_url}/pedidos/{vhsys_id}/produtos", timeout=15)
                if resp2 and resp2.status_code == 200:
                    body2 = resp2.json()
                    if body2.get("code") != 404:
                        data2 = body2.get("data", [])
                        itens_raw = data2 if isinstance(data2, list) else []

            if itens_raw:
                with db.get_conn() as conn2:
                    salvou = False
                    for it in itens_raw:
                        qtd   = float(it.get("qtde_produto") or it.get("quantidade") or 0)
                        preco = float(it.get("preco_unitario") or it.get("valor_unit") or it.get("preco_liquido") or 0)
                        nome  = (it.get("descricao_produto") or it.get("nome_produto")
                                 or it.get("descricao") or it.get("produto_nome") or "")
                        sku   = str(it.get("codigo_produto") or it.get("sku") or it.get("produto_codigo") or "").strip()
                        if not nome and not sku:
                            continue
                        conn2.execute(
                            "INSERT OR IGNORE INTO itens_pedido "
                            "(mercos_id,sku,nome_produto,quantidade,valor_unit,valor_total,processado_em) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (mercos_id, sku, nome, qtd, preco, qtd * preco, ts),
                        )
                        salvou = True
                    if salvou:
                        itens_preenchidos += 1
            else:
                logger.debug(f"[HistoricoRecuperacao] sem itens VHSys para mercos={mercos_id} vhsys={vhsys_id}")
        except Exception as e:
            logger.debug(f"[HistoricoRecuperacao] itens mercos={mercos_id}: {e}")
        _time.sleep(0.3)

    logger.info(
        f"[HistoricoRecuperacao] Concluído — "
        f"{corrigidos} valores corrigidos | {itens_preenchidos} pedidos com itens populados."
    )


async def _job_recuperar_historico():
    """Wrapper async — aguarda caches carregarem, depois executa recuperação em thread pool."""
    await asyncio.sleep(10)  # aguarda VHSys cache terminar (~8s)
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _recuperar_historico_sync)
    except Exception as e:
        logger.error(f"[HistoricoRecuperacao] Erro inesperado: {e}", exc_info=True)


# ── Expedição VHSys ───────────────────────────────────────────────────────────

from src.expedicao import init_expedicao, job_sync_expedicao as _job_sync_expedicao


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
scheduler.add_job(
    _job_reconciliacao,
    CronTrigger(hour=19, minute=55, timezone="America/Sao_Paulo"),
    id="reconciliacao_fim_dia"
)
# job_sync_expedicao desativado — API VHSys não expõe módulo Expedição
# Marcar separado/enviado manualmente via painel admin ou POST /admin/api/expedicao/verificar-agora
# scheduler.add_job(_job_sync_expedicao, "interval", minutes=EXPEDICAO_POLL_MIN,
#                   id="job_sync_expedicao", max_instances=1)

scheduler.add_job(_job_sefaz_coletar,    "interval", hours=COMPRAS_SEFAZ_HORAS, id="compras_sefaz",   max_instances=1)
scheduler.add_job(_job_processar_compras,"interval", minutes=COMPRAS_WORKER_MIN, id="compras_worker",  max_instances=1)

# ──────────────────────────────────────────────────────────────────────────────
# FastAPI
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI()
app.include_router(admin_router)

from compras.admin_routes import router as compras_router
app.include_router(compras_router)


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

    init_expedicao(mercos_service.vhsys)

    recuperados = db.fila_recuperar_travados()
    if recuperados:
        logger.warning(
            f"[Startup] Fila recuperada — {recuperados} item(s) resetados "
            f"para reprocessamento (crash anterior detectado)."
        )

    recuperados_compras = compras_db.fila_recuperar_travados()
    if recuperados_compras:
        logger.warning(
            f"[Startup] Fila compras: {recuperados_compras} item(s) resetados "
            f"para reprocessamento (crash anterior detectado)."
        )
    scheduler.start()
    logger.info(
        f"[Scheduler] Iniciado — "
        f"Worker fila: a cada {FILA_WORKER_SEG}s | "
        f"Sequência: a cada {AUDIT_SEQ_MIN}min | "
        f"Fluxo: a cada {AUDIT_FLUXO_MIN}min | "
        f"Fechamento: {FECHAMENTO_HORA}h"
    )

    asyncio.create_task(_job_recuperar_historico())
    logger.info("[HistoricoRecuperacao] Agendado para rodar após caches carregarem.")


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
                        cidade=dados.get("cliente_cidade", "") or "",
                        bairro=dados.get("cliente_bairro", "") or "",
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
                        cidade=dados.get("cliente_cidade", "") or "",
                        bairro=dados.get("cliente_bairro", "") or "",
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
