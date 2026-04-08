"""
Rotas FastAPI do módulo de boletos.
Prefix /boletos — autenticação herdada via Depends(verificar_admin) em main.py.
Webhook sem autenticação (router separado).
"""
import json
import logging
import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from boletos import database as db
from boletos import boleto_service, vhsys_adapter, webhook_handler
from boletos.pdf_generator import gerar_pdf_boleto

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

# Router principal (com auth aplicada em main.py via Depends)
router = APIRouter(prefix="/boletos")

# Router do webhook (sem auth — precisa ser público para o SICOOB chamar)
webhook_router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def pagina_boletos(request: Request):
    return templates.TemplateResponse("boletos.html", {"request": request})


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/config")
def get_config():
    return db.get_config()


@router.post("/api/config")
async def post_config(request: Request):
    data = await request.json()
    # Tipos corretos
    for campo in ("juros_percentual", "multa_percentual"):
        if campo in data:
            data[campo] = float(data[campo])
    for campo in ("dias_protesto", "dias_baixa", "codigo_modalidade", "carteira"):
        if campo in data:
            data[campo] = int(data[campo])
    db.save_config(data)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# VHSys — clientes (autocomplete para boleto avulso)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/clientes")
def get_clientes(q: str = ""):
    """Busca clientes VHSys por nome (autocomplete para boleto avulso)."""
    try:
        return vhsys_adapter.buscar_clientes(q)
    except Exception as e:
        logger.error("[Boletos/clientes] %s", e)
        raise HTTPException(status_code=502, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# VHSys — contas pendentes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/pendentes")
def get_pendentes():
    try:
        contas = vhsys_adapter.buscar_contas_abertas()
        # Filtrar apenas as sem boleto emitido e que têm forma de pagamento boleto
        pendentes = [
            c for c in contas
            if not c.get("boleto_ja_emitido")
        ]
        return pendentes
    except Exception as e:
        logger.error("[Boletos/pendentes] %s", e)
        raise HTTPException(status_code=502, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Emissão
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/api/emitir", status_code=201)
async def post_emitir(request: Request):
    data = await request.json()

    vhsys_conta_id = str(data.get("vhsys_conta_id", ""))
    data_vencimento = data.get("data_vencimento", "")
    if not vhsys_conta_id or not data_vencimento:
        raise HTTPException(status_code=400, detail="vhsys_conta_id e data_vencimento são obrigatórios")

    try:
        boleto = boleto_service.validar_e_emitir(
            vhsys_conta_id=vhsys_conta_id,
            data_vencimento=data_vencimento,
            valor_override=data.get("valor"),
        )
        return boleto
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error("[Boletos/emitir] %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Emissão avulsa (qualquer cliente VHSys, sem conta-a-receber)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/api/emitir-avulso", status_code=201)
async def post_emitir_avulso(request: Request):
    data = await request.json()

    vhsys_cliente_id = str(data.get("vhsys_cliente_id", ""))
    valor = data.get("valor")
    data_vencimento = data.get("data_vencimento", "")
    descricao = data.get("descricao", "").strip()

    if not vhsys_cliente_id or not valor or not data_vencimento:
        raise HTTPException(status_code=400, detail="vhsys_cliente_id, valor e data_vencimento são obrigatórios")

    try:
        boleto = boleto_service.emitir_avulso(
            vhsys_cliente_id=vhsys_cliente_id,
            valor=float(valor),
            data_vencimento=data_vencimento,
            descricao=descricao or f"Avulso",
        )
        return boleto
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error("[Boletos/emitir-avulso] %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Emitidos
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/emitidos")
def get_emitidos(status: str | None = None):
    return db.listar_boletos(status=status)


@router.get("/api/{nosso_numero}")
def get_boleto(nosso_numero: int):
    """Consulta em tempo real no SICOOB."""
    try:
        return boleto_service.consultar_sicoob(nosso_numero)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.patch("/api/{nosso_numero}")
async def patch_boleto(nosso_numero: int, request: Request):
    """Altera vencimento."""
    data = await request.json()
    nova_data = data.get("data_vencimento")
    if not nova_data:
        raise HTTPException(status_code=400, detail="data_vencimento obrigatório")
    try:
        result = boleto_service.alterar_vencimento(nosso_numero, nova_data)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.delete("/api/{nosso_numero}")
def delete_boleto(nosso_numero: int, motivo: str = "BAIXA_MANUAL"):
    """Baixa (cancela) um boleto."""
    try:
        return boleto_service.baixar_boleto(nosso_numero, motivo)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# PDF
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/{nosso_numero}/pdf")
def get_pdf(nosso_numero: int):
    boleto = db.get_boleto_by_nosso_numero(nosso_numero)
    if not boleto:
        raise HTTPException(status_code=404, detail=f"Boleto {nosso_numero} não encontrado")
    try:
        import requests as _req
        resp = _req.get(f"{boleto_service._SICOOB_URL}/boletos/{nosso_numero}/pdf", timeout=30)
        if resp.status_code == 200 and resp.headers.get("Content-Type", "").startswith("application/pdf"):
            return Response(
                content=resp.content,
                media_type="application/pdf",
                headers={"Content-Disposition": f"inline; filename=boleto_{nosso_numero}.pdf"},
            )
        # fallback: repassa o erro do SICOOB
        raise HTTPException(status_code=502, detail=resp.text[:300])
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Boletos/PDF] nossoNumero=%s erro: %s", nosso_numero, e, exc_info=True)
        raise HTTPException(status_code=502, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Webhook (sem auth)
# ─────────────────────────────────────────────────────────────────────────────

@webhook_router.post("/boletos/webhook/sicoob")
async def sicoob_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = await webhook_handler.processar(payload)
    return result
