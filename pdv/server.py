"""
PDV — servidor FastAPI.
"""

import os
import sys
import time
import threading
import logging
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from pdv.database import (
    init_pdv_tables,
    buscar_produtos, get_produto, salvar_precos, listar_todos_produtos, contar_produtos,
    criar_venda, listar_vendas,
    salvar_pendente, listar_pendentes,
)

logger = logging.getLogger(__name__)

# ── Template path ─────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    _TPL_DIR = os.path.join(sys._MEIPASS, "templates")
else:
    _TPL_DIR = os.path.join(os.path.dirname(__file__), "templates")

_PDV_HTML = os.path.join(_TPL_DIR, "pdv.html")

app = FastAPI(title="PDV")

def _sync_periodico(intervalo_seg: int = 1800):
    """Thread daemon: sincroniza produtos a cada 30min (sync inicial feito pelo main.py)."""
    while True:
        time.sleep(intervalo_seg)
        try:
            from pdv.vhsys import sincronizar_produtos
            resultado = sincronizar_produtos()
            if resultado["erro"]:
                logger.warning(f"[PDV auto-sync] {resultado['erro']}")
            else:
                logger.info(f"[PDV auto-sync] {resultado['importados']} produtos sincronizados")
        except Exception as e:
            logger.error(f"[PDV auto-sync] erro: {e}", exc_info=True)


@app.on_event("startup")
def _startup():
    init_pdv_tables()
    logger.info("[PDV] Tabelas inicializadas.")
    threading.Thread(target=_sync_periodico, daemon=True, name="pdv-sync").start()
    logger.info("[PDV] Auto-sync agendado (startup + 30min).")


# ── UI ────────────────────────────────────────────────────────────────────────

@app.get("/pdv/", response_class=HTMLResponse)
async def pdv_ui():
    with open(_PDV_HTML, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


# ── Produtos ──────────────────────────────────────────────────────────────────

@app.get("/pdv/api/produtos")
async def api_buscar_produtos(q: str = ""):
    if not q.strip():
        return {"produtos": [], "total": contar_produtos()}
    return {"produtos": buscar_produtos(q.strip())}


@app.get("/pdv/api/produtos/todos")
async def api_todos_produtos():
    return {"produtos": listar_todos_produtos()}


@app.post("/pdv/api/produtos/sync")
async def api_sync_produtos():
    from pdv.vhsys import sincronizar_produtos
    resultado = sincronizar_produtos()
    if resultado["erro"]:
        raise HTTPException(status_code=502, detail=resultado["erro"])
    return {"importados": resultado["importados"]}


@app.get("/pdv/api/produtos/{produto_id}/precos")
async def api_get_precos(produto_id: int):
    p = get_produto(produto_id)
    if not p:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    return {
        "id":       p["id"],
        "nome":     p["nome"],
        "base":     p["preco_base"],
        "dinheiro": p["preco_dinheiro"],
        "pix":      p["preco_pix"],
        "credito":  p["preco_credito"],
        "debito":   p["preco_debito"],
    }


class PrecosPayload(BaseModel):
    dinheiro: float
    pix:      float
    credito:  float
    debito:   float


@app.post("/pdv/api/produtos/{produto_id}/precos")
async def api_salvar_precos(produto_id: int, payload: PrecosPayload):
    p = get_produto(produto_id)
    if not p:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    salvar_precos(produto_id, payload.model_dump())
    return {"ok": True}


# ── Vendas ────────────────────────────────────────────────────────────────────

class ItemPayload(BaseModel):
    produto_id:    int | None = None
    nome:          str
    quantidade:    float
    preco_unitario: float


class PagamentoPayload(BaseModel):
    tipo:  str  # dinheiro | pix | credito | debito
    valor: float


class VendaPayload(BaseModel):
    itens:      list[ItemPayload]
    pagamentos: list[PagamentoPayload]
    desconto:   float = 0.0


@app.post("/pdv/api/vendas")
async def api_criar_venda(payload: VendaPayload):
    if not payload.itens:
        raise HTTPException(status_code=400, detail="Venda sem itens")
    if not payload.pagamentos:
        raise HTTPException(status_code=400, detail="Venda sem pagamento")

    total_itens = sum(i.quantidade * i.preco_unitario for i in payload.itens)
    total = round(total_itens - payload.desconto, 2)

    venda_id = criar_venda(
        total=total,
        desconto=payload.desconto,
        itens=[i.model_dump() for i in payload.itens],
        pagamentos=[p.model_dump() for p in payload.pagamentos],
    )

    # Sync VHSys em background (não bloqueia o caixa)
    def _sync():
        try:
            from pdv.vhsys import sincronizar_venda
            sincronizar_venda(venda_id)
        except Exception as e:
            logger.error(f"[PDV sync venda {venda_id}] {e}", exc_info=True)

    threading.Thread(target=_sync, daemon=True).start()

    return {"id": venda_id, "total": total}


@app.get("/pdv/api/vendas")
async def api_listar_vendas():
    return {"vendas": listar_vendas()}


# ── Pendentes ─────────────────────────────────────────────────────────────────

class PendentePayload(BaseModel):
    nome: str


@app.post("/pdv/api/pendentes")
async def api_criar_pendente(payload: PendentePayload):
    salvar_pendente(payload.nome.strip())
    return {"ok": True}


@app.get("/pdv/api/pendentes")
async def api_listar_pendentes():
    return {"pendentes": listar_pendentes()}
