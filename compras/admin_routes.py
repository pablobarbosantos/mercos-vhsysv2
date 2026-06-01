"""
Rotas admin do módulo Compras.
Prefixo: /compras
"""

import difflib
import logging
import os
import threading

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from lxml import etree

from compras import database as db
from compras.service import reprocessar_nota, processar_nota_agora
from compras.nfe_parser import parse_nfe
from compras.nfe_collector import cert_info, coletar_nfes
from compras.erp_adapter import buscar_produtos_erp, auto_match_produto, criar_produto_erp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compras", tags=["compras"])

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

XML_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "nfe_xmls")


# ──────────────────────────────────────────────────────────────────────────────
# Painel
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def painel_compras(request: Request):
    return templates.TemplateResponse(request=request, name="compras.html")


# ──────────────────────────────────────────────────────────────────────────────
# NF-e
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/notas")
async def api_notas(limit: int = 100):
    notas = db.nota_listar(limit=limit)
    stats = db.nota_stats()
    return {"notas": notas, "stats": stats}


@router.post("/upload-xml")
async def upload_xml(file: UploadFile = File(...)):
    """Aceita upload manual de XML NF-e. Valida estrutura antes de salvar."""
    conteudo = await file.read()

    # Validação básica de XML NF-e
    try:
        root = etree.fromstring(conteudo)
        inf_nfe = root.find(".//{http://www.portalfiscal.inf.br/nfe}infNFe")
        if inf_nfe is None:
            raise ValueError("XML não contém elemento infNFe — não é uma NF-e válida")
        id_attr = inf_nfe.get("Id", "")
        chave_nfe = id_attr[3:] if id_attr.startswith("NFe") else id_attr
        if len(chave_nfe) != 44:
            raise ValueError(f"Chave NF-e inválida (esperado 44 dígitos): {chave_nfe!r}")
    except etree.XMLSyntaxError as exc:
        raise HTTPException(status_code=400, detail=f"XML inválido: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Salva arquivo
    os.makedirs(XML_DIR, exist_ok=True)
    xml_path = os.path.join(XML_DIR, f"{chave_nfe}.xml")
    with open(xml_path, "wb") as f:
        f.write(conteudo)

    # Registra no banco se ainda não existe
    if db.nota_ja_existe(chave_nfe):
        return {"ok": True, "chave_nfe": chave_nfe, "msg": "NF-e já existe — reprocessando"}

    parsed = parse_nfe(xml_path)
    if parsed:
        db.nota_criar(
            chave_nfe       = chave_nfe,
            numero          = parsed["numero"],
            serie           = parsed["serie"],
            emitida_em      = parsed["emitida_em"],
            fornecedor_cnpj = parsed["fornecedor"]["cnpj"],
            fornecedor_nome = parsed["fornecedor"]["nome"],
            valor_total     = parsed["valor_total"],
            xml_path        = xml_path,
        )
    else:
        db.nota_criar(
            chave_nfe="", numero="", serie="", emitida_em="",
            fornecedor_cnpj="", fornecedor_nome="",
            valor_total=0.0, xml_path=xml_path,
        )
        raise HTTPException(status_code=422, detail="XML salvo mas falhou no parse — verifique o arquivo")

    db.fila_enfileirar(chave_nfe)
    db.log_registrar(chave_nfe, "upload_manual", f"Upload via painel admin | arquivo={file.filename}")
    return {"ok": True, "chave_nfe": chave_nfe}


@router.post("/api/notas/processar-todas")
async def api_processar_todas():
    """Enfileira todas as notas com status pendente ou erro para reprocessamento."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT chave_nfe FROM notas_fiscais WHERE status IN ('pendente','erro')"
        ).fetchall()
    enfileiradas = 0
    for row in rows:
        if reprocessar_nota(row["chave_nfe"]):
            enfileiradas += 1
    return {"ok": True, "enfileiradas": enfileiradas}


@router.post("/api/notas/processar-selecionadas")
async def api_processar_selecionadas(request: Request):
    body = await request.json()
    chaves = body.get("chaves", [])
    resultados = {"concluido": 0, "aguardando_mapeamento": 0, "erro": 0}
    for chave in chaves:
        status = processar_nota_agora(chave)
        if status in resultados:
            resultados[status] += 1
        else:
            resultados["erro"] += 1
    return {"ok": True, "enfileiradas": len(chaves), **resultados}


@router.post("/api/notas/{chave}/processar")
async def api_reprocessar_nota(chave: str):
    status = processar_nota_agora(chave)
    if status == "nao_encontrada":
        raise HTTPException(status_code=404, detail="Nota não encontrada")
    return {"ok": True, "status": status}


@router.post("/api/notas/ignorar")
async def api_ignorar_notas(request: Request):
    body = await request.json()
    chaves = body.get("chaves", [])
    for chave in chaves:
        db.nota_ignorar(chave)
    return {"ok": True, "ignoradas": len(chaves)}


@router.post("/api/notas/{chave}/itens/{item_id}/ignorar")
async def api_ignorar_item(chave: str, item_id: int):
    db.item_ignorar(item_id)
    return {"ok": True}


@router.get("/api/notas/{chave}/itens")
async def api_itens_nota(chave: str):
    itens = db.item_listar_por_nota(chave)
    return {"itens": itens}


@router.get("/api/produtos/{vhsys_id}/historico-custo")
async def api_historico_custo(vhsys_id: int):
    historico = db.historico_custo_listar(vhsys_id)
    return {"vhsys_id": vhsys_id, "historico": historico}


@router.post("/api/notas/{chave}/lancar-estoque")
async def api_lancar_estoque_nota(chave: str):
    """Lança entrada de estoque no ERP para todos os itens mapeados da nota."""
    from compras.erp_adapter import lancar_entrada_compra
    itens = db.item_listar_por_nota(chave)
    lancados = []
    erros = []
    for it in itens:
        if not it.get("mapeado") or it.get("ignorado"):
            continue
        vhsys_id = it.get("vhsys_id")
        if not vhsys_id:
            continue
        mapa = db.mapeamento_get_por_vhsys_id(vhsys_id)
        fator = (mapa.get("fator_conversao") or 1.0) if mapa else 1.0
        qtd = it["quantidade"] * fator
        ok = lancar_entrada_compra(vhsys_id, qtd, chave, it["descricao"])
        if ok:
            lancados.append({"descricao": it["descricao"], "quantidade": qtd, "vhsys_id": vhsys_id})
        else:
            erros.append(it["descricao"])
    return {"ok": not erros, "lancados": lancados, "erros": erros}


# ──────────────────────────────────────────────────────────────────────────────
# Fila
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/fila")
async def api_fila_compras():
    return {"stats": db.fila_stats()}


# ──────────────────────────────────────────────────────────────────────────────
# Busca de produtos ERP
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/produtos/buscar")
async def api_buscar_produto(q: str = ""):
    if len(q.strip()) < 2:
        return {"produtos": []}
    return {"produtos": buscar_produtos_erp(q.strip())}


@router.get("/api/categorias")
async def api_categorias():
    # ERP não tem categorias — retorna lista vazia para compatibilidade
    return {"categorias": []}


@router.post("/api/produtos/novo")
async def api_criar_produto(request: Request):
    body = await request.json()
    nome = (body.get("desc_produto") or body.get("nome", "")).strip()
    if not nome:
        raise HTTPException(status_code=400, detail="desc_produto obrigatório")

    payload = {
        "nome":       nome,
        "codigo":     body.get("cod_produto") or body.get("codigo") or nome[:20].replace(" ", "_").upper(),
        "unidade":    body.get("unidade_produto", "UN"),
        "preco_venda": float(body.get("valor_produto") or 0),
        "preco_custo": float(body.get("valor_custo_produto") or 0),
        "ean":        body.get("codigo_barra_produto") or None,
        "situacao":   "Ativo",
    }
    criado = criar_produto_erp(payload)
    if not criado:
        raise HTTPException(status_code=502, detail="Falha ao criar produto no ERP")

    return {
        "id_produto":   criado.get("id"),
        "desc_produto": criado.get("nome", nome),
        "cod_produto":  criado.get("codigo"),
        "valor_produto": float(criado.get("preco_venda") or 0),
        "estoque":         0,
        "estoque_vhsys":   0,
        "ativo":           1,
    })

    return {
        "ok": True,
        "vhsys_id": criado["id_produto"],
        "nome":     criado.get("desc_produto", body["desc_produto"]),
        "preco":    float(body.get("valor_produto") or 0),
        "ean":      body.get("codigo_barra_produto") or "",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Notas pendentes de mapeamento (agrupadas por nota)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/mapeamentos/pendentes")
async def api_pendentes_mapeamento():
    """Retorna todas as notas não concluídas com itens + mapeamento + sugestão automática."""
    with db.get_conn() as conn:
        notas = conn.execute(
            """SELECT chave_nfe, numero, serie, fornecedor_cnpj, fornecedor_nome,
                      valor_total, emitida_em, status
               FROM notas_fiscais
               WHERE status IN ('pendente','aguardando_mapeamento','erro')
               ORDER BY emitida_em DESC"""
        ).fetchall()

    resultado = []
    for nota in notas:
        chave = nota["chave_nfe"]
        itens = db.item_listar_por_nota(chave)
        itens_out = []
        for it in itens:
            mapeamento = db.mapeamento_get(nota["fornecedor_cnpj"], it["descricao"])
            sugestao = None
            if not mapeamento:
                sugestao = _auto_match(it["codigo_fornecedor"], it["descricao"])
            itens_out.append({**it, "mapeamento": mapeamento, "sugestao": sugestao})
        resultado.append({**dict(nota), "itens": itens_out})

    return {"notas": resultado}


# ──────────────────────────────────────────────────────────────────────────────
# Mapeamentos
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/mapeamentos")
async def api_mapeamentos():
    return {"mapeamentos": db.mapeamento_listar()}


@router.post("/api/mapeamentos")
async def api_criar_mapeamento(request: Request):
    body = await request.json()
    campos_obrigatorios = ["fornecedor_cnpj", "descricao_nota", "vhsys_id"]
    for campo in campos_obrigatorios:
        if not body.get(campo):
            raise HTTPException(status_code=400, detail=f"Campo obrigatório: {campo}")

    vhsys_id = int(body["vhsys_id"])
    db.mapeamento_upsert(
        fornecedor_cnpj = str(body["fornecedor_cnpj"]).strip(),
        descricao_nota  = str(body["descricao_nota"]).strip(),
        vhsys_id        = vhsys_id,
        nome_vhsys      = body.get("nome_vhsys", ""),
        unidade_compra  = body.get("unidade_compra", ""),
        fator_conversao = float(body.get("fator_conversao", 1.0)),
        unidade_estoque = body.get("unidade_estoque", ""),
    )

    # Se houver notas aguardando mapeamento, re-enfileira automaticamente
    _reenfileirar_aguardando(str(body["fornecedor_cnpj"]).strip())

    return {"ok": True}


@router.delete("/api/mapeamentos/{mapeamento_id}")
async def api_deletar_mapeamento(mapeamento_id: int):
    with db.get_conn() as conn:
        conn.execute("DELETE FROM mapeamento_produtos_compra WHERE id=?", (mapeamento_id,))
    return {"ok": True}


def _reenfileirar_aguardando(fornecedor_cnpj: str) -> None:
    """Re-enfileira notas aguardando mapeamento do fornecedor que acabou de ser mapeado."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT chave_nfe FROM notas_fiscais WHERE status='aguardando_mapeamento' AND fornecedor_cnpj=?",
            (fornecedor_cnpj,)
        ).fetchall()
    for row in rows:
        reprocessar_nota(row["chave_nfe"])
        logger.info("[Compras] Nota %s re-enfileirada após novo mapeamento", row["chave_nfe"][:8])


# ──────────────────────────────────────────────────────────────────────────────
# Contas a pagar
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/contas-pagar")
async def api_contas_pagar(status: str | None = None):
    contas = db.conta_listar(status=status)
    return {"contas": contas}


@router.patch("/api/contas-pagar/{conta_id}/pago")
async def api_marcar_pago(conta_id: int):
    with db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE contas_pagar_compra SET status='pago' WHERE id=?", (conta_id,)
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Conta não encontrada")
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────────
# SEFAZ
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/sefaz/status")
async def api_sefaz_status():
    info = cert_info()
    info["ultimo_nsu"] = db.sefaz_get_ultimo_nsu()
    return info


@router.post("/api/sefaz/certificado")
async def api_upload_certificado(
    file: UploadFile = File(...),
    senha: str = Form(...),
    cnpj: str = Form(...),
):
    """
    Faz upload do certificado A1 (.pfx/.p12), salva em data/certs/empresa.pfx
    e persiste senha + CNPJ no banco de dados.
    """
    from compras.nfe_collector import invalidar_cache_cert, CERT_DIR, cert_info

    # Valida extensão
    nome = file.filename or ""
    if not any(nome.lower().endswith(ext) for ext in (".pfx", ".p12")):
        raise HTTPException(status_code=400, detail="Arquivo deve ser .pfx ou .p12")

    conteudo = await file.read()
    if len(conteudo) < 100:
        raise HTTPException(status_code=400, detail="Arquivo muito pequeno — inválido")

    # Valida senha antes de salvar tentando fazer o parse
    try:
        from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
        senha_bytes = senha.encode("utf-8")
        load_key_and_certificates(conteudo, senha_bytes)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Certificado inválido ou senha incorreta: {exc}"
        )

    # Salva o arquivo
    os.makedirs(CERT_DIR, exist_ok=True)
    pfx_path = os.path.join(CERT_DIR, "empresa.pfx")
    with open(pfx_path, "wb") as f:
        f.write(conteudo)
    try:
        os.chmod(pfx_path, 0o600)
    except Exception:
        pass

    # Persiste senha e CNPJ no banco
    cnpj_limpo = cnpj.strip().replace(".", "").replace("/", "").replace("-", "")
    db.config_set("cert_senha", senha)
    db.config_set("cnpj_empresa", cnpj_limpo)

    # Invalida cache PEM para forçar re-exportação
    invalidar_cache_cert()

    # Retorna info do certificado para feedback imediato
    info = cert_info()
    db.log_registrar(None, "upload_certificado",
                     f"Certificado A1 atualizado | CNPJ={cnpj_limpo} | expira={info.get('cert_expira_em')}")
    logger.info("[SEFAZ] Certificado A1 atualizado via painel | CNPJ=%s | expira=%s",
                cnpj_limpo, info.get("cert_expira_em"))

    return {"ok": True, **info}


@router.post("/api/sefaz/coletar-agora")
async def api_sefaz_coletar_agora():
    """Executa coleta SEFAZ de forma síncrona e retorna resultado."""
    import asyncio
    from compras.nfe_collector import coletar_nfes
    loop = asyncio.get_event_loop()
    try:
        resultado = await loop.run_in_executor(None, coletar_nfes)
        logger.info("[Compras/SEFAZ] Coleta manual: %s", resultado)
        return {"ok": True, **resultado}
    except Exception as exc:
        logger.error("[Compras/SEFAZ] Erro na coleta manual: %s", exc, exc_info=True)
        return {"ok": False, "erro": str(exc), "baixadas": 0, "ja_existentes": 0, "erros": 0}
