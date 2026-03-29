"""
consulta_vhsys — servidor FastAPI.
"""

import os
import sys
import logging
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from consulta_vhsys.database.database import init_db
from consulta_vhsys.services.product_lookup import (
    buscar_por_ean,
    buscar_por_nome,
    vincular_ean,
    editar_produto,
)
from consulta_vhsys.services.sync_service import sincronizar_sujos, atualizar_base
from consulta_vhsys.services.duplicidade_service import (
    verificar_duplicidades,
    resolver_duplicidade_ean,
    resolver_duplicidade_nome,
)

logger = logging.getLogger(__name__)

# ── Template path ──────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    _TPL_DIR = os.path.join(sys._MEIPASS, "templates")
else:
    _TPL_DIR = os.path.join(os.path.dirname(__file__), "templates")

_HTML = os.path.join(_TPL_DIR, "consulta.html")

app = FastAPI(title="Consulta VHSys")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    init_db()
    logger.info("[CONSULTA] Banco inicializado.")


# ── UI ─────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def ui():
    with open(_HTML, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


# ── Busca ──────────────────────────────────────────────────────────────────────

@app.get("/api/produto/ean/{ean}")
async def api_busca_ean(ean: str):
    produto = buscar_por_ean(ean)
    if not produto:
        return {"encontrado": False, "produto": None}
    return {"encontrado": True, "produto": produto}


@app.get("/api/produto/nome")
async def api_busca_nome(q: str = ""):
    if not q.strip():
        return {"produtos": []}
    return {"produtos": buscar_por_nome(q.strip())}


# ── Edição ─────────────────────────────────────────────────────────────────────

class VincularEanPayload(BaseModel):
    ean: str


@app.post("/api/produto/{vhsys_id}/vincular-ean")
async def api_vincular_ean(vhsys_id: int, payload: VincularEanPayload):
    resultado = vincular_ean(vhsys_id, payload.ean)
    if not resultado["ok"]:
        raise HTTPException(status_code=409, detail=resultado["erro"])
    return resultado


class EditarPayload(BaseModel):
    preco:   float
    estoque: float


@app.post("/api/produto/{vhsys_id}/editar")
async def api_editar(vhsys_id: int, payload: EditarPayload):
    resultado = editar_produto(vhsys_id, payload.preco, payload.estoque)
    if not resultado["ok"]:
        raise HTTPException(status_code=404, detail=resultado["erro"])
    return resultado


# ── Sync ───────────────────────────────────────────────────────────────────────

@app.post("/api/sync/sujos")
async def api_sync_sujos():
    return sincronizar_sujos()


@app.post("/api/sync/atualizar-base")
async def api_atualizar_base():
    return atualizar_base()


# ── Duplicidades ───────────────────────────────────────────────────────────────

@app.get("/api/duplicidades")
async def api_duplicidades():
    return {"conflitos": verificar_duplicidades()}


class ResolverEanPayload(BaseModel):
    vhsys_id_manter:        int
    vhsys_ids_remover_ean:  list[int]


@app.post("/api/duplicidades/resolver-ean")
async def api_resolver_ean(payload: ResolverEanPayload):
    return resolver_duplicidade_ean(payload.vhsys_id_manter, payload.vhsys_ids_remover_ean)


class ResolverNomePayload(BaseModel):
    vhsys_id_inativar: int


@app.post("/api/duplicidades/resolver-nome")
async def api_resolver_nome(payload: ResolverNomePayload):
    return resolver_duplicidade_nome(payload.vhsys_id_inativar)
