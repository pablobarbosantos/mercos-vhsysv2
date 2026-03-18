import uvicorn
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
import logging
import logging.handlers
import os
import sys
from mercos_service import MercosService
from src import database as db

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
# Inicialização
# ──────────────────────────────────────────────────────────────────────────────

logger.info("[Startup] Inicializando banco SQLite...")
db.init_db()
logger.info("[Startup] Banco OK.")

mercos_service = MercosService()

app = FastAPI()

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

        # O Mercos pode enviar um dict ou uma lista de eventos
        eventos = payload if isinstance(payload, list) else [payload]

        for item in eventos:
            evento = item.get("evento")
            logger.info(f"[Webhook] Evento recebido: '{evento}'")

            if evento == "pedido.gerado":
                dados  = item.get("dados", {})
                numero = dados.get("numero")
                cnpj   = dados.get("cliente_cnpj", "N/A")

                logger.info(
                    f"[Webhook] Pedido #{numero} | "
                    f"CNPJ: {cnpj} | "
                    f"Itens: {len(dados.get('itens', []))}"
                )
                background_tasks.add_task(mercos_service.processar_para_vhsys, dados)
                logger.debug(f"[Webhook] Pedido #{numero} enfileirado.")

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
