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
import json
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

import requests as _requests_lib
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from pathlib import Path

# ---------------------------------------------------------------------------
# Config de boleto (defaults salvos em data/boleto_config.json)
# ---------------------------------------------------------------------------
_CONFIG_PATH = Path(__file__).parent / "data" / "boleto_config.json"
_CONFIG_DEFAULT: dict = {
    "valorMulta": 2.0, "tipoMulta": 2,
    "valorJurosMora": 0.2, "tipoJurosMora": 2,
    "codigoProtesto": 3, "diasProtesto": 0,
    "codigoNegativacao": 3, "diasNegativacao": 0,
    "vencimentoPadraoADias": 7,
    "mensagens": [
        "Apos vencimento, Juros 0,2%/dia.",
        "Apos vencimento, Multa de 2%.",
        "Nao conceder desconto.",
    ],
}


def _ler_config() -> dict:
    if _CONFIG_PATH.exists():
        try:
            return {**_CONFIG_DEFAULT, **json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return _CONFIG_DEFAULT.copy()


def _salvar_config(cfg: dict) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

import config
from services import boleto_manager, boleto_service, database, movimentacao_service, sync_service, erp_adapter
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


@app.get("/admin/imprimir", include_in_schema=False)
def imprimir_boletos(
    request: Request,
    status: list[str] | None = Query(default=None),
    data_inicio: str | None = Query(default=None),
    data_fim: str | None = Query(default=None),
    tipo_data: str = Query(default="vencimento"),
    cliente: str | None = Query(default=None),
    valor_min: float | None = Query(default=None),
    valor_max: float | None = Query(default=None),
):
    from datetime import datetime
    boletos = database.listar_boletos(
        status=status,
        data_inicio=data_inicio,
        data_fim=data_fim,
        tipo_data=tipo_data,
        cliente=cliente,
        valor_min=valor_min,
        valor_max=valor_max,
        limit=5000,
    )
    partes = []
    if status:       partes.append("Status: " + ", ".join(status))
    if data_inicio:  partes.append(f"De {data_inicio}")
    if data_fim:     partes.append(f"Até {data_fim}")
    if cliente:      partes.append(f"Cliente: {cliente}")
    return templates.TemplateResponse("imprimir.html", {
        "request": request,
        "boletos": boletos,
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "filtros": " · ".join(partes) if partes else None,
    })


# ---------------------------------------------------------------------------
# Admin — API: sincronização
# ---------------------------------------------------------------------------
@app.post("/admin/api/sync")
def sync_manual(dias: int = Query(default=60, ge=1, le=365)):
    """Verifica status dos boletos locais em aberto no Sicoob."""
    resultado = sync_service.sincronizar(dias=dias)
    return resultado


@app.post("/admin/api/sync/pagador")
def sync_por_pagador(cpf_cnpj: str = Query(...), dias: int = Query(default=365, ge=1, le=730)):
    """Importa histórico de boletos de um pagador (CPF/CNPJ) do Sicoob."""
    resultado = sync_service.sincronizar_por_pagador(cpf_cnpj, dias=dias)
    return resultado


@app.post("/admin/api/sync/todos")
def sync_todos(dias: int = Query(default=60, ge=1, le=730)):
    """Importa boletos de todos os clientes VHSys dos últimos N dias."""
    resultado = sync_service.sincronizar_todos(dias=dias)
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


class ConsultaListaRequest(BaseModel):
    nossos_numeros: list[str]


@app.post("/admin/api/boletos/consultar-lista")
def consultar_lista_boletos(req: ConsultaListaRequest):
    """Consulta múltiplos boletos por lista de nosso_numero (DB local + Sicoob)."""
    _TERMINAIS = {"LIQUIDADO", "BAIXADO", "PAGO_EXTERNO"}
    _STATUS_MAP = {
        "LIQUIDADO": "LIQUIDADO", "BAIXADO": "BAIXADO",
        "EM_ABERTO": "EMITIDO",   "VENCIDO":  "VENCIDO",
    }
    resultados = []
    for n in req.nossos_numeros:
        n = n.strip()
        if not n:
            continue
        local = database.get_boleto(n)
        if local and local.get("status_atual") in _TERMINAIS:
            resultados.append({
                "nosso_numero":   n,
                "cliente_nome":   local.get("cliente_nome"),
                "valor":          local.get("valor"),
                "vencimento":     local.get("vencimento"),
                "status":         local.get("status_atual"),
                "linha_digitavel": local.get("linha_digitavel"),
                "origem":         "LOCAL",
                "erro":           None,
            })
            continue
        try:
            dados = boleto_service.consultar(n)
            res = dados.get("resultado", dados)
            status_raw = res.get("situacaoBoleto") or ""
            if isinstance(status_raw, dict):
                status_raw = status_raw.get("codigo", "")
            status = _STATUS_MAP.get((status_raw or "").upper(), status_raw or "EMITIDO")
            linha = res.get("linhaDigitavel") or res.get("codigoLinhaDigitavel") or ""
            pagador = res.get("pagador") or {}
            nome = pagador.get("nome") or (local.get("cliente_nome") if local else None)
            resultados.append({
                "nosso_numero":   n,
                "cliente_nome":   nome,
                "valor":          res.get("valor") or res.get("valorNominal"),
                "vencimento":     res.get("dataVencimento"),
                "status":         status,
                "linha_digitavel": linha,
                "origem":         "SICOOB",
                "erro":           None,
            })
        except BoletoNaoEncontrado:
            resultados.append({
                "nosso_numero": n, "cliente_nome": None, "valor": None,
                "vencimento": None, "status": "NAO_ENCONTRADO",
                "linha_digitavel": None, "origem": None,
                "erro": "Boleto não encontrado no Sicoob",
            })
        except Exception as exc:
            resultados.append({
                "nosso_numero": n, "cliente_nome": None, "valor": None,
                "vencimento": None, "status": "ERRO",
                "linha_digitavel": None, "origem": None,
                "erro": str(exc),
            })
    return {"resultados": resultados, "total": len(resultados)}


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
    pedidos = erp_adapter.buscar_pedidos(
        situacao=situacao, data_inicio=data_inicio, limite=limite
    )
    return {"pedidos": pedidos}


@app.get("/admin/api/vhsys/pedidos/{pedido_id}")
def detalhe_pedido_vhsys(pedido_id: int):
    pedido = erp_adapter.buscar_pedido(pedido_id)
    if pedido is None:
        raise HTTPException(status_code=404, detail=f"Pedido ERP #{pedido_id} não encontrado")
    return pedido


@app.get("/admin/api/vhsys/clientes")
def buscar_cliente_vhsys(doc: str = Query(..., description="CPF ou CNPJ")):
    cliente = erp_adapter.buscar_cliente(doc)
    return {"cliente": cliente}


@app.get("/admin/api/stats")
def dashboard_stats():
    return database.stats()


# ---------------------------------------------------------------------------
# Admin — API: configuração de boleto
# ---------------------------------------------------------------------------
@app.get("/admin/api/config")
def get_config():
    """Retorna configurações globais de emissão de boleto."""
    return _ler_config()


@app.put("/admin/api/config")
def save_config(cfg: dict):
    """Salva configurações globais de emissão de boleto."""
    _salvar_config(cfg)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin — API: busca de clientes VHSys
# ---------------------------------------------------------------------------
@app.get("/admin/api/clientes")
def buscar_clientes(q: str = Query(..., min_length=2)):
    """Busca clientes no VHSys por nome ou CNPJ para pré-preenchimento."""
    if not config.VHSYS_ACCESS_TOKEN:
        return {"clientes": []}
    headers = {
        "access-token":        config.VHSYS_ACCESS_TOKEN,
        "secret-access-token": config.VHSYS_SECRET_TOKEN,
        "Content-Type":        "application/json",
    }
    try:
        r = _requests_lib.get(
            f"{config.VHSYS_BASE_URL}/clientes",
            headers=headers,
            params={"razao_cliente": q, "limit": 15},
            timeout=10,
        )
        r.raise_for_status()
        clientes = r.json().get("data", [])
        return {"clientes": [
            {
                "nome":     c.get("razao_cliente") or c.get("fantasia_cliente") or "",
                "doc":      c.get("cnpj_cliente") or c.get("cpf_cliente") or "",
                "cidade":   c.get("cidade_cliente") or "",
                "uf":       c.get("uf_cliente") or "MG",
                "endereco": (c.get("endereco_cliente") or "") + (
                    ", " + c.get("numero_cliente") if c.get("numero_cliente") else ""
                ),
                "bairro":   c.get("bairro_cliente") or "",
                "cep":      (c.get("cep_cliente") or "").replace("-", "").replace(".", ""),
            }
            for c in clientes
            if c.get("razao_cliente") or c.get("fantasia_cliente")
        ]}
    except Exception as e:
        logger.error("Busca de clientes VHSys falhou: %s", e)
        return {"clientes": []}


# ---------------------------------------------------------------------------
# Relatórios
# ---------------------------------------------------------------------------
_DESKTOP = Path.home() / "Desktop"

_TIPO_NOME = {1: "Entrada", 2: "Prorrogação", 3: "A Vencer", 4: "Vencido", 5: "Liquidação", 6: "Baixa"}


def _salvar_desktop(nome: str, conteudo: str | bytes, encoding: str = "utf-8") -> Path:
    _DESKTOP.mkdir(parents=True, exist_ok=True)
    caminho = _DESKTOP / nome
    if isinstance(conteudo, bytes):
        caminho.write_bytes(conteudo)
    else:
        caminho.write_text(conteudo, encoding=encoding)
    return caminho


def _csv_extrato(registros: list[dict]) -> str:
    output = io.StringIO()
    campos = ["data_evento", "descricao", "nosso_numero", "cliente_nome", "cliente_doc", "valor"]
    writer = csv.DictWriter(output, fieldnames=campos, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(registros)
    return output.getvalue()


def _enriquecer_boletos(boletos: list[dict]) -> list[dict]:
    """Adiciona campo 'mes' (YYYY-MM) para agrupamento no template."""
    for b in boletos:
        b["mes"] = (b.get("criado_em") or "")[:7] or "—"
    return boletos


def _csv_boletos(boletos: list[dict]) -> str:
    output = io.StringIO()
    campos = ["nosso_numero", "seu_numero", "cliente_nome", "cliente_doc",
              "valor", "vencimento", "status_atual", "linha_digitavel", "criado_em"]
    writer = csv.DictWriter(output, fieldnames=campos, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(boletos)
    return output.getvalue()


# --- Extrato ---

@app.get("/admin/relatorios/extrato", include_in_schema=False)
def extrato_ui(request: Request):
    return templates.TemplateResponse("extrato_relatorio.html", {"request": request})


@app.post("/admin/api/relatorios/extrato/gerar")
def extrato_gerar(
    data_inicio: str = Query(..., description="YYYY-MM-DD"),
    data_fim:    str = Query(..., description="YYYY-MM-DD"),
    tipos: list[int] = Query(default=[1, 5, 6]),
):
    """Busca movimentações no SICOOB, salva no DB e exporta CSV+HTML para o Desktop."""
    from datetime import datetime
    stats = movimentacao_service.solicitar_e_salvar(data_inicio, data_fim, tipos)
    registros = database.listar_movimentacoes(data_inicio, data_fim, tipos)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv_str = _csv_extrato(registros)
    csv_path = _salvar_desktop(f"extrato_{ts}.csv", csv_str)

    html_str = templates.env.get_template("extrato_relatorio.html").render(
        registros=registros,
        data_inicio=data_inicio,
        data_fim=data_fim,
        tipos_selecionados=tipos,
        tipo_nome=_TIPO_NOME,
        total_entrada=sum(r["valor"] or 0 for r in registros if r["tipo_movimento"] == 5),
        total_saida=sum(r["valor"] or 0 for r in registros if r["tipo_movimento"] == 6),
        modo_impressao=True,
    )
    html_path = _salvar_desktop(f"extrato_{ts}.html", html_str)

    return {
        "ok": True,
        "stats_sicoob": stats,
        "registros": len(registros),
        "arquivo_csv": str(csv_path),
        "arquivo_html": str(html_path),
    }


@app.get("/admin/api/relatorios/extrato/csv")
def extrato_csv(
    data_inicio: str = Query(...),
    data_fim:    str = Query(...),
    tipos: list[int] = Query(default=[1, 5, 6]),
):
    """Retorna CSV dos registros já no DB (sem nova busca ao SICOOB)."""
    registros = database.listar_movimentacoes(data_inicio, data_fim, tipos)
    return StreamingResponse(
        iter([_csv_extrato(registros)]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=extrato_{data_inicio}_{data_fim}.csv"},
    )


@app.get("/admin/api/relatorios/extrato/pdf", include_in_schema=False)
def extrato_pdf(
    request: Request,
    data_inicio: str = Query(...),
    data_fim:    str = Query(...),
    tipos: list[int] = Query(default=[1, 5, 6]),
):
    """Retorna HTML imprimível do extrato."""
    registros = database.listar_movimentacoes(data_inicio, data_fim, tipos)
    return templates.TemplateResponse("extrato_relatorio.html", {
        "request": request,
        "registros": registros,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "tipos_selecionados": tipos,
        "tipo_nome": _TIPO_NOME,
        "total_entrada": sum(r["valor"] or 0 for r in registros if r["tipo_movimento"] == 5),
        "total_saida": sum(r["valor"] or 0 for r in registros if r["tipo_movimento"] == 6),
        "modo_impressao": True,
    })


# --- Boletos 120 dias ---

@app.get("/admin/relatorios/boletos", include_in_schema=False)
def boletos_relatorio_ui(request: Request):
    return templates.TemplateResponse("boletos_relatorio.html", {"request": request})


@app.post("/admin/api/relatorios/boletos-120-dias/gerar")
def boletos_120_gerar():
    """Sincroniza SICOOB (120 dias) → gera CSV + HTML → salva no Desktop."""
    from datetime import datetime, date, timedelta
    sync_resultado = sync_service.sincronizar_todos(dias=120)

    hoje = date.today()
    data_inicio = (hoje - timedelta(days=120)).isoformat()
    boletos = _enriquecer_boletos(database.listar_boletos(
        data_inicio=data_inicio,
        tipo_data="criado_em",
        limit=5000,
    ))
    stats = database.stats_periodo(data_inicio, hoje.isoformat(), tipo_data="criado_em")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv_str = _csv_boletos(boletos)
    csv_path = _salvar_desktop(f"boletos_120dias_{ts}.csv", csv_str)

    html_str = templates.env.get_template("boletos_relatorio.html").render(
        boletos=boletos,
        stats=stats,
        data_inicio=data_inicio,
        data_fim=hoje.isoformat(),
        modo_impressao=True,
    )
    html_path = _salvar_desktop(f"boletos_120dias_{ts}.html", html_str)

    return {
        "ok": True,
        "sync": sync_resultado,
        "total_boletos": len(boletos),
        "stats": stats,
        "arquivo_csv": str(csv_path),
        "arquivo_html": str(html_path),
    }


@app.get("/admin/api/relatorios/boletos-120-dias/csv")
def boletos_120_csv():
    """Retorna CSV dos boletos 120 dias do DB local (sem novo sync)."""
    from datetime import date, timedelta
    data_inicio = (date.today() - timedelta(days=120)).isoformat()
    boletos = database.listar_boletos(data_inicio=data_inicio, tipo_data="criado_em", limit=5000)
    return StreamingResponse(
        iter([_csv_boletos(boletos)]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=boletos_120dias.csv"},
    )


@app.get("/admin/api/relatorios/boletos-120-dias/pdf", include_in_schema=False)
def boletos_120_pdf(request: Request):
    """Retorna HTML imprimível com todos os boletos dos últimos 120 dias."""
    from datetime import date, timedelta
    hoje = date.today()
    data_inicio = (hoje - timedelta(days=120)).isoformat()
    boletos = _enriquecer_boletos(database.listar_boletos(data_inicio=data_inicio, tipo_data="criado_em", limit=5000))
    stats = database.stats_periodo(data_inicio, hoje.isoformat(), tipo_data="criado_em")
    return templates.TemplateResponse("boletos_relatorio.html", {
        "request": request,
        "boletos": boletos,
        "stats": stats,
        "data_inicio": data_inicio,
        "data_fim": hoje.isoformat(),
        "modo_impressao": True,
    })


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8001, reload=False)
