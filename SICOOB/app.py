"""
SICOOB Boletos — FastAPI porta 8001

Executar:
    cd SICOOB
    python app.py
    # ou com reload:
    uvicorn app:app --reload --port 8001
"""
import csv
import io
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from pathlib import Path

import config
from services import boleto_manager, boleto_service, database, sync_service, vhsys_adapter
from services.exceptions import BoletoError, BoletoNaoEncontrado, SicoobConfigError
from webhooks.sicoob_webhook import router as webhook_router

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
# Templates
# ---------------------------------------------------------------------------
_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
scheduler = AsyncIOScheduler(timezone="America/Sao_Paulo")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Banco de dados
    database.init_db()

    # Configuração Sicoob
    try:
        config.validar()
        logger.info(
            "SICOOB app iniciado (sandbox=%s, numero_cliente=%s)",
            config.SANDBOX,
            config.NUMERO_CLIENTE,
        )
    except RuntimeError as e:
        logger.error("Configuração incompleta: %s", e)

    # Sync periódico a cada 15 min
    scheduler.add_job(
        sync_service.sincronizar,
        "interval",
        minutes=15,
        id="sync_boletos",
        misfire_grace_time=60,
    )
    scheduler.start()
    logger.info("Scheduler iniciado — sync a cada 15min.")

    yield

    scheduler.shutdown(wait=False)


app = FastAPI(
    title="SICOOB Boletos",
    description="Gestão de boletos SICOOB com banco local, sincronização e UI web.",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(webhook_router)


# ---------------------------------------------------------------------------
# Exception handlers
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
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "sandbox": config.SANDBOX,
        "numero_cliente": config.NUMERO_CLIENTE,
        "vhsys_configurado": bool(config.VHSYS_ACCESS_TOKEN),
    }


# ---------------------------------------------------------------------------
# Endpoints legados (baixo nível — mantidos para compatibilidade)
# ---------------------------------------------------------------------------
@app.post("/boletos", status_code=201)
def emitir_boleto_legado(payload: dict[str, Any]):
    """Emite boleto direto (sem persistência local). Usar /admin/api/boletos."""
    payload.setdefault("numeroCliente", config.NUMERO_CLIENTE)
    if config.NUMERO_CONTA_CORRENTE:
        payload.setdefault("numeroContaCorrente", config.NUMERO_CONTA_CORRENTE)
    return boleto_service.emitir(payload)


@app.get("/boletos/{nosso_numero}")
def consultar_boleto_legado(nosso_numero: str):
    return boleto_service.consultar(nosso_numero)


@app.patch("/boletos/{nosso_numero}")
def alterar_boleto_legado(nosso_numero: str, dados: dict[str, Any]):
    return boleto_service.alterar(nosso_numero, dados)


@app.delete("/boletos/{nosso_numero}")
def baixar_boleto_legado(nosso_numero: str, motivo: str = "BAIXA_MANUAL"):
    return boleto_service.baixar(nosso_numero, motivo)


@app.get("/boletos/{nosso_numero}/segunda-via")
def segunda_via_legado(nosso_numero: str):
    return boleto_service.segunda_via(nosso_numero)


@app.get("/boletos/{nosso_numero}/pdf")
def boleto_pdf_legado(nosso_numero: str):
    pdf_bytes = boleto_service.segunda_via_pdf(nosso_numero)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=boleto_{nosso_numero}.pdf"},
    )


# ---------------------------------------------------------------------------
# Admin — UI
# ---------------------------------------------------------------------------
@app.get("/admin", include_in_schema=False)
def admin_ui(request: Request):
    return templates.TemplateResponse("boletos.html", {"request": request})


# ---------------------------------------------------------------------------
# Admin — API: sincronização
# ---------------------------------------------------------------------------
@app.post("/admin/api/sync")
def sync_manual(dias: int = Query(default=60, ge=1, le=365)):
    """Dispara sincronização manual com o Sicoob."""
    resultado = sync_service.sincronizar(dias=dias)
    return resultado


# ---------------------------------------------------------------------------
# Admin — API: boletos
# ---------------------------------------------------------------------------
@app.get("/admin/api/boletos")
def listar_boletos(
    status: list[str] | None = Query(default=None),
    data_inicio: str | None = Query(default=None),
    data_fim: str | None = Query(default=None),
    tipo_data: str = Query(default="vencimento"),
    cliente: str | None = Query(default=None),
    valor_min: float | None = Query(default=None),
    valor_max: float | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    boletos = database.listar_boletos(
        status=status,
        data_inicio=data_inicio,
        data_fim=data_fim,
        tipo_data=tipo_data,
        cliente=cliente,
        valor_min=valor_min,
        valor_max=valor_max,
        limit=limit,
        offset=offset,
    )
    return {"boletos": boletos, "total": len(boletos), "stats": database.stats()}


@app.get("/admin/api/boletos/exportar")
def exportar_csv(
    status: list[str] | None = Query(default=None),
    data_inicio: str | None = Query(default=None),
    data_fim: str | None = Query(default=None),
    tipo_data: str = Query(default="vencimento"),
    cliente: str | None = Query(default=None),
):
    boletos = database.listar_boletos(
        status=status,
        data_inicio=data_inicio,
        data_fim=data_fim,
        tipo_data=tipo_data,
        cliente=cliente,
        limit=5000,
    )
    output = io.StringIO()
    campos = ["nosso_numero", "seu_numero", "cliente_nome", "cliente_doc",
              "valor", "vencimento", "status_atual", "linha_digitavel", "criado_em"]
    writer = csv.DictWriter(output, fieldnames=campos, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(boletos)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=boletos.csv"},
    )


@app.get("/admin/api/boletos/{nosso_numero}")
def detalhe_boleto(nosso_numero: str):
    boleto = database.get_boleto(nosso_numero)
    if boleto is None:
        raise HTTPException(status_code=404, detail=f"Boleto {nosso_numero} não encontrado localmente.")
    return boleto


@app.post("/admin/api/boletos", status_code=201)
def emitir_boleto_admin(payload: dict[str, Any]):
    """
    Emite boleto com persistência local + rastreabilidade de eventos.
    Bloqueia duplicata por vhsys_pedido_id.
    """
    payload.setdefault("numeroCliente", config.NUMERO_CLIENTE)
    if config.NUMERO_CONTA_CORRENTE:
        payload.setdefault("numeroContaCorrente", config.NUMERO_CONTA_CORRENTE)
    return boleto_manager.emitir(payload)


@app.post("/admin/api/boletos/{nosso_numero}/pago-externo")
def pago_externo(nosso_numero: str):
    """Marca boleto como pago externamente (Pix/dinheiro) e baixa no Sicoob."""
    return boleto_manager.marcar_pago_externo(nosso_numero)


@app.post("/admin/api/boletos/{nosso_numero}/baixar")
def baixar_boleto_admin(nosso_numero: str, motivo: str = "BAIXA_MANUAL"):
    """Baixa manual de boleto."""
    return boleto_manager.baixar_manual(nosso_numero, motivo=motivo)


@app.get("/admin/api/boletos/{nosso_numero}/pdf")
def boleto_pdf_admin(nosso_numero: str):
    """Retorna PDF oficial do boleto via Sicoob."""
    pdf_bytes = boleto_service.segunda_via_pdf(nosso_numero)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=boleto_{nosso_numero}.pdf"},
    )


# ---------------------------------------------------------------------------
# Admin — API: VHSys (pré-preenchimento)
# ---------------------------------------------------------------------------
@app.get("/admin/api/vhsys/pedidos")
def listar_pedidos_vhsys(
    situacao: str | None = Query(default=None),
    data_inicio: str | None = Query(default=None),
    limite: int = Query(default=50, ge=1, le=200),
):
    if not config.VHSYS_ACCESS_TOKEN:
        return {"pedidos": [], "aviso": "VHSys não configurado"}
    pedidos = vhsys_adapter.buscar_pedidos(
        situacao=situacao, data_inicio=data_inicio, limite=limite
    )
    return {"pedidos": pedidos}


@app.get("/admin/api/vhsys/pedidos/{pedido_id}")
def detalhe_pedido_vhsys(pedido_id: int):
    if not config.VHSYS_ACCESS_TOKEN:
        raise HTTPException(status_code=503, detail="VHSys não configurado")
    pedido = vhsys_adapter.buscar_pedido(pedido_id)
    if pedido is None:
        raise HTTPException(status_code=404, detail=f"Pedido VHSys #{pedido_id} não encontrado")
    return pedido


@app.get("/admin/api/vhsys/clientes")
def buscar_cliente_vhsys(doc: str = Query(..., description="CPF ou CNPJ")):
    if not config.VHSYS_ACCESS_TOKEN:
        return {"cliente": None, "aviso": "VHSys não configurado"}
    cliente = vhsys_adapter.buscar_cliente(doc)
    return {"cliente": cliente}


@app.get("/admin/api/stats")
def dashboard_stats():
    return database.stats()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8001, reload=False)
