"""
Standalone SICOOB app — emissão e gestão de boletos via sicoob-sdk.

Executar:
    cd SICOOB
    python app.py
    # ou
    uvicorn app:app --reload --port 8001
"""
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

import config
from services import boleto_service
from services.exceptions import BoletoError, BoletoNaoEncontrado, SicoobConfigError

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        config.validar()
        logger.info(
            "SICOOB app iniciado (sandbox=%s, numero_cliente=%s)",
            config.SANDBOX,
            config.NUMERO_CLIENTE,
        )
    except RuntimeError as e:
        logger.error("Configuração incompleta: %s", e)
    yield


app = FastAPI(
    title="SICOOB Boletos",
    description="Standalone app para emissão e gestão de boletos SICOOB.",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Handlers de erro
# ---------------------------------------------------------------------------
@app.exception_handler(BoletoNaoEncontrado)
async def handle_nao_encontrado(request, exc):
    return JSONResponse(status_code=404, content={"erro": str(exc)})


@app.exception_handler(BoletoError)
async def handle_boleto_error(request, exc):
    return JSONResponse(status_code=502, content={"erro": str(exc)})


@app.exception_handler(SicoobConfigError)
async def handle_config_error(request, exc):
    return JSONResponse(status_code=503, content={"erro": str(exc)})


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "sandbox": config.SANDBOX,
        "numero_cliente": config.NUMERO_CLIENTE,
    }


@app.post("/boletos", status_code=201)
def emitir_boleto(payload: dict[str, Any]):
    """
    Emite um boleto no SICOOB.

    Payload segue o formato da API SICOOB Cobrança Bancária V3.
    `numeroCliente` é preenchido automaticamente se ausente.
    """
    payload.setdefault("numeroCliente", config.NUMERO_CLIENTE)
    return boleto_service.emitir(payload)


@app.get("/boletos/{nosso_numero}")
def consultar_boleto(nosso_numero: str):
    """Consulta status de um boleto pelo nossoNumero."""
    return boleto_service.consultar(nosso_numero)


@app.patch("/boletos/{nosso_numero}")
def alterar_boleto(nosso_numero: str, dados: dict[str, Any]):
    """Altera dados de um boleto (ex: dataVencimento, valorNominal)."""
    return boleto_service.alterar(nosso_numero, dados)


@app.delete("/boletos/{nosso_numero}")
def baixar_boleto(nosso_numero: str, motivo: str = "BAIXA_MANUAL"):
    """Dá baixa (cancela) um boleto."""
    return boleto_service.baixar(nosso_numero, motivo)


@app.get("/boletos/{nosso_numero}/segunda-via")
def segunda_via(nosso_numero: str):
    """Retorna dados da segunda via do boleto (inclui link/PDF)."""
    return boleto_service.segunda_via(nosso_numero)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8001, reload=True)
