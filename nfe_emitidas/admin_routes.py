"""
Rotas do módulo NF-e Emitidas.
Prefixo: /nfe-emitidas
"""

import io
import logging
import os
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from nfe_emitidas import database as db
from nfe_emitidas.sefaz_client import consultar_protocolo, _parsear_chave
from nfe_emitidas.erp_adapter import (
    buscar_nfe_por_chave,
    buscar_cliente_por_id,
)
from nfe_emitidas.danfe_generator import gerar_danfe_combinado

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nfe-emitidas", tags=["nfe_emitidas"])

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

_XML_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "nfe_emitidas")


def _xml_path(chave: str) -> str:
    return os.path.join(_XML_DIR, f"{chave}.xml")


def _pdf_path(chave: str) -> str:
    return os.path.join(_XML_DIR, f"{chave}.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# Painel HTML
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def painel(request: Request):
    return templates.TemplateResponse(request=request, name="nfe_emitidas.html")


# ──────────────────────────────────────────────────────────────────────────────
# API
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/lista")
async def api_lista():
    registros = db.registro_listar()
    return {"registros": registros, "total": len(registros)}


class ProcessarRequest(BaseModel):
    chaves: list[str]


@router.post("/api/processar")
async def api_processar(body: ProcessarRequest):
    """
    Recebe lista de chaves NF-e (44 dígitos), consulta o SEFAZ,
    salva XML e gera DANFE para cada uma.
    Retorna resultado por chave.
    """
    os.makedirs(_XML_DIR, exist_ok=True)

    # Normaliza: remove espaços e caracteres não-numéricos
    chaves = []
    for c in body.chaves:
        limpa = "".join(ch for ch in c if ch.isdigit())
        if limpa:
            chaves.append(limpa)

    if not chaves:
        raise HTTPException(400, "Nenhuma chave válida fornecida")

    resultados = []

    emit_nome = os.getenv("NFE_EMIT_NOME", "")
    emit_end  = os.getenv("NFE_EMIT_END", "")

    for chave in chaves:
        resultado = {"chave": chave, "status": "erro", "erro": None,
                     "numero": "", "serie": "", "emitida_em": "",
                     "destinatario": "", "valor_total": 0.0}

        if len(chave) != 44:
            resultado["erro"] = f"Chave com {len(chave)} dígitos (esperado: 44)"
            resultados.append(resultado)
            continue

        db.registro_criar(chave)

        try:
            # 1. Consulta SEFAZ — verifica autorização + obtém protocolo
            prot = consultar_protocolo(chave)

            # 2. Extrai campos da chave (numero, serie, CNPJ emitente, data)
            ck = _parsear_chave(chave)

            # 3. Busca dados no VHSys por chave
            nf_vhsys  = buscar_nfe_por_chave(chave) or {}
            id_cliente = nf_vhsys.get("id_cliente")
            cliente    = buscar_cliente_por_id(id_cliente) if id_cliente else {}

            # 4. Resolve data de emissão (VHSys tem mais precisão que chave)
            emitida_em = (
                str(nf_vhsys.get("data_emissao") or
                    nf_vhsys.get("data_pedido")  or
                    ck["emitida_em"])[:10]
            )

            # 5. Resolve endereço do destinatário
            def _end(c: dict) -> str:
                partes = [
                    c.get("endereco", ""), c.get("numero", ""),
                    c.get("bairro", ""), c.get("cidade", ""), c.get("uf", ""),
                ]
                return ", ".join(p for p in partes if p)

            dest_nome = (
                nf_vhsys.get("nome_cliente") or
                nf_vhsys.get("razao_social_cliente") or
                cliente.get("razao_social") or
                cliente.get("nome_fantasia") or ""
            )
            dest_doc = (
                cliente.get("cpf_cnpj") or
                nf_vhsys.get("cpf_cnpj_cliente") or ""
            )
            dest_end = _end(cliente)

            valor_total = float(
                nf_vhsys.get("valor_total_nota") or
                nf_vhsys.get("valor_total") or 0
            )

            # 6. Monta dict consolidado para DANFE
            dados = {
                "chave":       chave,
                "numero":      nf_vhsys.get("nota_numero") or nf_vhsys.get("numero_pedido") or ck["numero"],
                "serie":       nf_vhsys.get("nota_serie") or ck["serie"],
                "emitida_em":  emitida_em,
                "emit_cnpj":   ck["emit_cnpj"],
                "emit_nome":   emit_nome,
                "emit_end":    emit_end,
                "dest_nome":   dest_nome,
                "dest_doc":    dest_doc,
                "dest_end":    dest_end,
                "v_prod":      float(nf_vhsys.get("valor_produtos") or valor_total),
                "v_frete":     float(nf_vhsys.get("frete") or nf_vhsys.get("valor_frete") or 0),
                "v_desc":      float(nf_vhsys.get("desconto") or nf_vhsys.get("valor_desconto") or 0),
                "v_icms":      float(nf_vhsys.get("valor_ICMS") or nf_vhsys.get("valor_icms") or 0),
                "v_st":        float(nf_vhsys.get("valor_baseST") or nf_vhsys.get("valor_st") or 0),
                "v_ipi":       float(nf_vhsys.get("valor_IPI") or nf_vhsys.get("valor_ipi") or 0),
                "v_nf":        valor_total,
                "nProt":       prot["nProt"],
                "dhRecbto":    prot["dhRecbto"],
                "inf_adicional": nf_vhsys.get("informacoes_complementares") or "",
                "itens":       [],
            }

            # 7. Salva protocolo XML (prova de autorização SEFAZ)
            xp = _xml_path(chave)
            with open(xp, "w", encoding="utf-8") as f:
                f.write(prot["prot_xml"])

            # 8. Gera DANFE PDF
            pp = _pdf_path(chave)
            gerar_danfe_combinado(dados, pp)

            # 9. Atualiza banco
            db.registro_atualizar_ok(
                chave=chave,
                xml_path=xp,
                pdf_path=pp,
                numero=str(dados["numero"]),
                serie=str(dados["serie"]),
                emitida_em=emitida_em,
                destinatario=dest_nome,
                valor_total=valor_total,
            )

            resultado.update({
                "status":      "ok",
                "numero":      str(dados["numero"]),
                "serie":       str(dados["serie"]),
                "emitida_em":  emitida_em,
                "destinatario": dest_nome,
                "valor_total": valor_total,
            })
            logger.info(
                f"NF-e {chave} processada OK "
                f"(NF {dados['numero']} prot={prot['nProt']})"
            )

        except Exception as e:
            msg = str(e)
            logger.error(f"Erro ao processar NF-e {chave}: {msg}", exc_info=True)
            db.registro_atualizar_erro(chave, msg)
            resultado["erro"] = msg

        resultados.append(resultado)

    ok    = sum(1 for r in resultados if r["status"] == "ok")
    erros = sum(1 for r in resultados if r["status"] == "erro")
    return {"resultados": resultados, "ok": ok, "erros": erros}


@router.delete("/api/{chave}")
async def api_deletar(chave: str):
    reg = db.registro_buscar(chave)
    if not reg:
        raise HTTPException(404, "Registro não encontrado")
    # Remove arquivos se existirem
    for path in (reg.get("xml_path"), reg.get("pdf_path")):
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    db.registro_deletar(chave)
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────────
# Downloads individuais
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/download/{chave}/xml")
async def download_xml(chave: str):
    reg = db.registro_buscar(chave)
    path = reg.get("xml_path") if reg else _xml_path(chave)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "XML não encontrado — processe a chave primeiro")
    numero = reg.get("numero", chave) if reg else chave
    return FileResponse(
        path,
        media_type="application/xml",
        filename=f"NFe_{numero}.xml",
    )


@router.get("/download/{chave}/pdf")
async def download_pdf(chave: str):
    reg = db.registro_buscar(chave)
    path = reg.get("pdf_path") if reg else _pdf_path(chave)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "PDF não encontrado — processe a chave primeiro")
    numero = reg.get("numero", chave) if reg else chave
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"DANFE_{numero}.pdf",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Download ZIP — todos os arquivos disponíveis
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/download/zip")
async def download_zip():
    registros = [r for r in db.registro_listar() if r["status"] == "ok"]
    if not registros:
        raise HTTPException(404, "Nenhum arquivo disponível para download")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for reg in registros:
            numero = reg.get("numero") or reg["chave"][:8]
            xp = reg.get("xml_path")
            pp = reg.get("pdf_path")
            if xp and os.path.exists(xp):
                zf.write(xp, f"NFe_{numero}.xml")
            if pp and os.path.exists(pp):
                zf.write(pp, f"DANFE_{numero}.pdf")
    buf.seek(0)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=nfe_emitidas_{ts}.zip"},
    )
