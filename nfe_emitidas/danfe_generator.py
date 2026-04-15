"""
Geração de DANFE PDF a partir do XML de NF-e.

Tenta usar erpbrasil.edoc.pdf (biblioteca oficial).
Se não estiver instalada, gera um PDF simplificado com fpdf2
contendo os dados principais da NF-e.
"""

import logging
import os

from lxml import etree

logger = logging.getLogger(__name__)

NS = "http://www.portalfiscal.inf.br/nfe"


def gerar_danfe_combinado(dados: dict, pdf_path: str) -> None:
    """
    Gera DANFE PDF a partir de um dict de dados consolidados
    (SEFAZ protocol + VHSys fields + chave parsing).
    Não precisa do XML original da NF-e.
    """
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def secao(titulo: str):
        pdf.set_fill_color(220, 220, 220)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, titulo, fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)

    def linha(label: str, valor):
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(55, 5, label + ":", new_x="END", new_y="TOP")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 5, str(valor or ""), new_x="LMARGIN", new_y="NEXT")

    chave = dados.get("chave", "")
    numero = dados.get("numero", "")
    serie  = dados.get("serie", "")
    emitida_em = dados.get("emitida_em", "")
    nprot  = dados.get("nProt", "")
    dhrecbto = (dados.get("dhRecbto") or "")[:10]

    # Cabeçalho
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "NOTA FISCAL ELETRÔNICA", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6,
             f"NF-e N° {numero}  |  Série {serie}  |  Emissão: {emitida_em}",
             align="C", new_x="LMARGIN", new_y="NEXT")
    if nprot:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5,
                 f"PROTOCOLO DE AUTORIZAÇÃO: {nprot}  |  {dhrecbto}",
                 align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Chave de acesso
    secao("CHAVE DE ACESSO")
    pdf.set_font("Courier", "", 8)
    chave_fmt = " ".join(chave[i:i+4] for i in range(0, len(chave), 4))
    pdf.cell(0, 5, chave_fmt, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Emitente
    secao("EMITENTE")
    linha("Razão Social", dados.get("emit_nome", ""))
    linha("CNPJ", dados.get("emit_cnpj", ""))
    if dados.get("emit_end"):
        linha("Endereço", dados["emit_end"])
    pdf.ln(2)

    # Destinatário
    secao("DESTINATÁRIO")
    linha("Nome / Razão Social", dados.get("dest_nome", ""))
    linha("CNPJ / CPF", dados.get("dest_doc", ""))
    if dados.get("dest_end"):
        linha("Endereço", dados["dest_end"])
    pdf.ln(2)

    # Totais
    secao("TOTAIS")
    v_prod   = float(dados.get("v_prod") or dados.get("v_nf") or 0)
    v_frete  = float(dados.get("v_frete") or 0)
    v_desc   = float(dados.get("v_desc") or 0)
    v_icms   = float(dados.get("v_icms") or 0)
    v_st     = float(dados.get("v_st") or 0)
    v_ipi    = float(dados.get("v_ipi") or 0)
    v_nf     = float(dados.get("v_nf") or 0)

    if v_prod and v_prod != v_nf:
        linha("Valor dos Produtos", f"R$ {v_prod:.2f}")
    if v_frete:
        linha("Valor do Frete",    f"R$ {v_frete:.2f}")
    if v_desc:
        linha("Desconto",          f"R$ {v_desc:.2f}")
    if v_icms:
        linha("ICMS",              f"R$ {v_icms:.2f}")
    if v_st:
        linha("ICMS-ST",           f"R$ {v_st:.2f}")
    if v_ipi:
        linha("IPI",               f"R$ {v_ipi:.2f}")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(55, 6, "VALOR TOTAL DA NOTA:", new_x="END", new_y="TOP")
    pdf.cell(0,  6, f"R$ {v_nf:.2f}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Itens (se disponíveis)
    itens = dados.get("itens") or []
    if itens:
        secao("ITENS")
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(10, 5, "#",         border=1)
        pdf.cell(80, 5, "Descrição", border=1)
        pdf.cell(20, 5, "Qtd",       border=1, align="R")
        pdf.cell(15, 5, "Un",        border=1)
        pdf.cell(30, 5, "Vl Unit",   border=1, align="R")
        pdf.cell(30, 5, "Vl Total",  border=1, align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 7)
        for item in itens:
            pdf.cell(10, 5, str(item.get("num", "")),   border=1)
            pdf.cell(80, 5, str(item.get("desc", ""))[:45], border=1)
            pdf.cell(20, 5, str(item.get("qtd", "")),   border=1, align="R")
            pdf.cell(15, 5, str(item.get("un", "")),    border=1)
            pdf.cell(30, 5, f"{float(item.get('vunit', 0)):.2f}",  border=1, align="R")
            pdf.cell(30, 5, f"{float(item.get('vtotal', 0)):.2f}", border=1, align="R",
                     new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
    else:
        secao("ITENS")
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 5, "Detalhamento de itens não disponível via API.", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    # Informações complementares
    inf = dados.get("inf_adicional", "")
    if inf:
        secao("INFORMAÇÕES COMPLEMENTARES")
        pdf.set_font("Helvetica", "", 8)
        pdf.multi_cell(0, 4, inf)

    # Rodapé
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 4,
             "Documento gerado a partir dos dados do SEFAZ e VHSys. "
             "Consulte o XML original no emissor da NF-e.",
             align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.output(pdf_path)


def gerar_danfe(xml_path: str, pdf_path: str) -> None:
    """
    Gera DANFE PDF a partir do XML salvo em xml_path.
    Salva o resultado em pdf_path.
    Levanta Exception se falhar.
    """
    try:
        _gerar_com_erpbrasil(xml_path, pdf_path)
        logger.info(f"DANFE gerado (erpbrasil): {pdf_path}")
    except ImportError:
        logger.warning("erpbrasil.edoc.pdf não instalado — usando gerador simplificado")
        _gerar_simplificado(xml_path, pdf_path)
        logger.info(f"DANFE simplificado gerado: {pdf_path}")


def _gerar_com_erpbrasil(xml_path: str, pdf_path: str) -> None:
    from erpbrasil.edoc.pdf import base as danfe_base
    pdf_bytes = danfe_base.ImprimirXml.imprimir(xml_path)
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)


def _gerar_simplificado(xml_path: str, pdf_path: str) -> None:
    """Fallback: gera PDF simples com fpdf2 mostrando os dados da NF-e."""
    from fpdf import FPDF

    dados = _parsear_nfe(xml_path)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Cabeçalho
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "NOTA FISCAL ELETRÔNICA", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"NF-e N° {dados['numero']}  |  Série {dados['serie']}  |  {dados['emitida_em']}",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    def secao(titulo):
        pdf.set_fill_color(220, 220, 220)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, titulo, fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)

    def linha(label, valor):
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(50, 5, label + ":", new_x="END", new_y="TOP")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 5, str(valor), new_x="LMARGIN", new_y="NEXT")

    # Chave de acesso
    secao("CHAVE DE ACESSO")
    pdf.set_font("Courier", "", 8)
    chave = dados.get("chave", "")
    chave_fmt = " ".join(chave[i:i+4] for i in range(0, len(chave), 4))
    pdf.cell(0, 5, chave_fmt, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Emitente
    secao("EMITENTE")
    linha("Razão Social", dados.get("emit_nome", ""))
    linha("CNPJ", dados.get("emit_cnpj", ""))
    linha("Endereço", dados.get("emit_end", ""))
    pdf.ln(2)

    # Destinatário
    secao("DESTINATÁRIO")
    linha("Nome / Razão Social", dados.get("dest_nome", ""))
    linha("CNPJ / CPF", dados.get("dest_doc", ""))
    linha("Endereço", dados.get("dest_end", ""))
    pdf.ln(2)

    # Totais
    secao("TOTAIS")
    linha("Valor dos Produtos", f"R$ {dados.get('v_prod', 0.0):.2f}")
    linha("Valor do Frete",     f"R$ {dados.get('v_frete', 0.0):.2f}")
    linha("Valor do Desconto",  f"R$ {dados.get('v_desc', 0.0):.2f}")
    linha("ICMS",               f"R$ {dados.get('v_icms', 0.0):.2f}")
    linha("PIS",                f"R$ {dados.get('v_pis', 0.0):.2f}")
    linha("COFINS",             f"R$ {dados.get('v_cofins', 0.0):.2f}")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(50, 6, "VALOR TOTAL DA NOTA:", new_x="END", new_y="TOP")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, f"R$ {dados.get('v_nf', 0.0):.2f}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Itens
    secao("ITENS")
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(10,  5, "#",          border=1)
    pdf.cell(80,  5, "Descrição",  border=1)
    pdf.cell(20,  5, "Qtd",        border=1, align="R")
    pdf.cell(15,  5, "Un",         border=1)
    pdf.cell(30,  5, "Vl Unit",    border=1, align="R")
    pdf.cell(30,  5, "Vl Total",   border=1, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 7)
    for item in dados.get("itens", []):
        pdf.cell(10,  5, str(item.get("num", "")),  border=1)
        pdf.cell(80,  5, item.get("desc", "")[:45], border=1)
        pdf.cell(20,  5, str(item.get("qtd", "")),  border=1, align="R")
        pdf.cell(15,  5, item.get("un", ""),        border=1)
        pdf.cell(30,  5, f"{item.get('vunit', 0.0):.2f}", border=1, align="R")
        pdf.cell(30,  5, f"{item.get('vtotal', 0.0):.2f}", border=1, align="R",
                 new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Informações Complementares
    inf = dados.get("inf_adicional", "")
    if inf:
        secao("INFORMAÇÕES COMPLEMENTARES")
        pdf.set_font("Helvetica", "", 8)
        pdf.multi_cell(0, 4, inf)

    pdf.output(pdf_path)


def _parsear_nfe(xml_path: str) -> dict:
    """Extrai os dados relevantes do XML para montar o PDF simplificado."""
    try:
        root = etree.parse(xml_path).getroot()
    except Exception:
        with open(xml_path, "rb") as f:
            root = etree.fromstring(f.read())

    def txt(el, tag):
        found = el.find(f".//{{{NS}}}{tag}") if el is not None else None
        return found.text or "" if found is not None else ""

    # Raiz NFe
    inf_nfe = root.find(f".//{{{NS}}}infNFe")
    chave   = inf_nfe.get("Id", "")[3:] if inf_nfe is not None else ""

    ide  = root.find(f".//{{{NS}}}ide")
    emit = root.find(f".//{{{NS}}}emit")
    dest = root.find(f".//{{{NS}}}dest")
    tot  = root.find(f".//{{{NS}}}ICMSTot")
    inf_adic = root.find(f".//{{{NS}}}infAdic")

    # Endereço emitente
    end_emit = root.find(f".//{{{NS}}}enderEmit")
    emit_end = ""
    if end_emit is not None:
        partes = [txt(end_emit, "xLgr"), txt(end_emit, "nro"), txt(end_emit, "xBairro"),
                  txt(end_emit, "xMun"), txt(end_emit, "UF")]
        emit_end = ", ".join(p for p in partes if p)

    # Endereço destinatário
    end_dest = root.find(f".//{{{NS}}}enderDest")
    dest_end = ""
    if end_dest is not None:
        partes = [txt(end_dest, "xLgr"), txt(end_dest, "nro"), txt(end_dest, "xBairro"),
                  txt(end_dest, "xMun"), txt(end_dest, "UF")]
        dest_end = ", ".join(p for p in partes if p)

    # Destinatário documento
    dest_doc = txt(dest, "CNPJ") or txt(dest, "CPF") if dest is not None else ""

    # Data emissão
    dh_emi = txt(ide, "dhEmi") or txt(ide, "dEmi")
    emitida_em = dh_emi[:10] if dh_emi else ""

    # Totais
    def vf(el, tag):
        v = txt(el, tag) if el is not None else "0"
        try:
            return float(v or 0)
        except ValueError:
            return 0.0

    # Itens
    itens = []
    for det in root.findall(f".//{{{NS}}}det"):
        prod = det.find(f"{{{NS}}}prod")
        if prod is None:
            continue
        itens.append({
            "num":    det.get("nItem", ""),
            "desc":   txt(prod, "xProd"),
            "qtd":    txt(prod, "qCom"),
            "un":     txt(prod, "uCom"),
            "vunit":  vf(prod, "vUnCom"),
            "vtotal": vf(prod, "vProd"),
        })

    return {
        "chave":        chave,
        "numero":       txt(ide,  "nNF"),
        "serie":        txt(ide,  "serie"),
        "emitida_em":   emitida_em,
        "emit_nome":    txt(emit, "xNome"),
        "emit_cnpj":    txt(emit, "CNPJ"),
        "emit_end":     emit_end,
        "dest_nome":    txt(dest, "xNome") if dest is not None else "",
        "dest_doc":     dest_doc,
        "dest_end":     dest_end,
        "v_prod":       vf(tot, "vProd"),
        "v_frete":      vf(tot, "vFrete"),
        "v_desc":       vf(tot, "vDesc"),
        "v_icms":       vf(tot, "vICMS"),
        "v_pis":        vf(tot, "vPIS"),
        "v_cofins":     vf(tot, "vCOFINS"),
        "v_nf":         vf(tot, "vNF"),
        "itens":        itens,
        "inf_adicional": txt(inf_adic, "infCpl") if inf_adic is not None else "",
    }
