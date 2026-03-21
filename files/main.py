from dotenv import load_dotenv
load_dotenv()

import uvicorn
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
import logging
import logging.handlers
import os
import sys
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
# APScheduler — tarefas periódicas de auditoria
# ──────────────────────────────────────────────────────────────────────────────

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

AUDIT_SEQ_MIN    = int(os.getenv("AUDIT_SEQ_INTERVAL_MIN", 15))     # verificação de sequência
AUDIT_FLUXO_MIN  = int(os.getenv("AUDIT_FLUXO_INTERVAL_MIN", 30))   # verificação de fluxo
FECHAMENTO_HORA  = os.getenv("AUDIT_FECHAMENTO_HORA", "20")          # hora do fechamento do dia


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


scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
scheduler.add_job(_job_sequencia, "interval", minutes=AUDIT_SEQ_MIN,   id="auditoria_sequencia")
scheduler.add_job(_job_fluxo,     "interval", minutes=AUDIT_FLUXO_MIN,  id="auditoria_fluxo")
scheduler.add_job(
    _job_fechamento,
    CronTrigger(hour=int(FECHAMENTO_HORA), minute=0, timezone="America/Sao_Paulo"),
    id="fechamento_dia"
)

# ──────────────────────────────────────────────────────────────────────────────
# FastAPI
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI()
app.include_router(admin_router)


@app.on_event("startup")
async def startup_event():
    scheduler.start()
    logger.info(
        f"[Scheduler] Iniciado — "
        f"Sequência: a cada {AUDIT_SEQ_MIN}min | "
        f"Fluxo: a cada {AUDIT_FLUXO_MIN}min | "
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
async def receive_mercos_order(request: Request, background_tasks: BackgroundTasks):
    try:
        payload = await request.json()
        logger.debug(f"[Webhook] Payload recebido: {payload}")

        eventos = payload if isinstance(payload, list) else [payload]

        for item in eventos:
            evento = item.get("evento")
            dados  = item.get("dados", {})
            logger.info(f"[Webhook] Evento: '{evento}'")

            # ── Pedido novo
            if evento == "pedido.gerado":
                numero = dados.get("numero")
                cnpj   = dados.get("cliente_cnpj", "N/A")
                logger.info(
                    f"[Webhook] Pedido #{numero} | "
                    f"CNPJ: {cnpj} | "
                    f"Itens: {len(dados.get('itens', []))}"
                )

                # Registra no fluxo imediatamente ao receber
                mercos_id = dados.get("id")
                if mercos_id:
                    db.fluxo_registrar_recebido(
                        mercos_id=mercos_id,
                        numero=str(numero or mercos_id),
                        cliente=dados.get("cliente_razao_social", ""),
                        valor=float(dados.get("valor_total", 0) or 0),
                    )

                background_tasks.add_task(mercos_service.processar_para_vhsys, dados)
                logger.debug(f"[Webhook] Pedido #{numero} enfileirado.")

            # ── Pedido atualizado (ex: status mudou no Mercos)
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
