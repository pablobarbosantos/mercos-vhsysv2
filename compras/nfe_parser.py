"""
Parser de XML NF-e.
Extrai: fornecedor, itens, pagamentos/duplicatas.
Suporta wrappers nfeProc/NFe e NFe diretamente.
"""

import logging
from lxml import etree

logger = logging.getLogger(__name__)

NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}

_FORMA_PAGAMENTO = {
    "01": "Dinheiro",
    "02": "Cheque",
    "03": "Cartão de Crédito",
    "04": "Cartão de Débito",
    "05": "Crédito Loja",
    "10": "Vale Alimentação",
    "11": "Vale Refeição",
    "12": "Vale Presente",
    "13": "Vale Combustível",
    "15": "Boleto Bancário",
    "16": "Depósito Bancário",
    "17": "PIX",
    "18": "Transferência Bancária",
    "90": "Sem Pagamento",
    "99": "Outros",
}


def _find(node, xpath: str) -> "etree._Element | None":
    return node.find(xpath, namespaces=NS)


def _text(node, xpath: str, default: str = "") -> str:
    el = node.find(xpath, namespaces=NS)
    return (el.text or "").strip() if el is not None else default


def _float(node, xpath: str) -> float:
    txt = _text(node, xpath)
    try:
        return float(txt) if txt else 0.0
    except ValueError:
        return 0.0


def parse_nfe(xml_path: str) -> dict | None:
    """
    Faz o parse de um XML NF-e e retorna um dict estruturado.
    Retorna None em caso de falha para que o caller marque a nota como erro.
    """
    try:
        tree = etree.parse(xml_path)
        root = tree.getroot()

        # Navega até infNFe (pode estar dentro de nfeProc > NFe ou diretamente em NFe)
        inf_nfe = root.find(".//nfe:infNFe", namespaces=NS)
        if inf_nfe is None:
            logger.error("[NFeParser] infNFe não encontrado em %s", xml_path)
            return None

        # Chave NF-e (44 dígitos, Id começa com "NFe")
        id_attr = inf_nfe.get("Id", "")
        chave_nfe = id_attr[3:] if id_attr.startswith("NFe") else id_attr

        ide = _find(inf_nfe, "nfe:ide")
        if ide is None:
            logger.error("[NFeParser] elemento <ide> não encontrado em %s", xml_path)
            return None

        numero   = _text(ide, "nfe:nNF")
        serie    = _text(ide, "nfe:serie")
        dhemi    = _text(ide, "nfe:dhEmi")
        emitida_em = dhemi[:10] if dhemi else ""  # extrai só a data YYYY-MM-DD

        # Fornecedor (emitente)
        emit = _find(inf_nfe, "nfe:emit")
        if emit is None:
            logger.error("[NFeParser] elemento <emit> não encontrado em %s", xml_path)
            return None

        fornecedor_cnpj = _text(emit, "nfe:CNPJ") or _text(emit, "nfe:CPF")
        fornecedor_nome = _text(emit, "nfe:xNome")

        # Valor total
        total = _find(inf_nfe, "nfe:total/nfe:ICMSTot")
        valor_total = _float(total, "nfe:vNF") if total is not None else 0.0

        # Itens
        itens = []
        for det in inf_nfe.findall("nfe:det", namespaces=NS):
            prod = _find(det, "nfe:prod")
            if prod is None:
                continue
            ean_raw = _text(prod, "nfe:cEAN") or ""
            ean = ean_raw if ean_raw not in ("", "SEM GTIN", "0", "00000000000000") else ""
            itens.append({
                "codigo_fornecedor": _text(prod, "nfe:cProd"),
                "descricao":         _text(prod, "nfe:xProd"),
                "quantidade":        _float(prod, "nfe:qCom"),
                "unidade":           _text(prod, "nfe:uCom"),
                "valor_unitario":    _float(prod, "nfe:vUnCom"),
                "valor_total":       _float(prod, "nfe:vProd"),
                "ean":               ean,
            })

        # Pagamentos / duplicatas
        pagamentos = _extrair_pagamentos(inf_nfe)

        return {
            "chave_nfe":        chave_nfe,
            "numero":           numero,
            "serie":            serie,
            "emitida_em":       emitida_em,
            "fornecedor": {
                "cnpj": fornecedor_cnpj,
                "nome": fornecedor_nome,
            },
            "valor_total":      valor_total,
            "itens":            itens,
            "pagamentos":       pagamentos,
        }

    except Exception as exc:
        logger.error("[NFeParser] Falha ao parsear %s: %s", xml_path, exc, exc_info=True)
        return None


def _extrair_pagamentos(inf_nfe) -> list[dict]:
    """
    Extrai duplicatas de cobr/dup (preferencial) ou falls back para pag/detPag.
    """
    pagamentos = []

    # Preferencial: boletos declarados em <cobr><dup>
    cobr = _find(inf_nfe, "nfe:cobr")
    if cobr is not None:
        for dup in cobr.findall("nfe:dup", namespaces=NS):
            pagamentos.append({
                "numero_duplicata": _text(dup, "nfe:nDup"),
                "vencimento":       _text(dup, "nfe:dVenc"),
                "valor":            _float(dup, "nfe:vDup"),
                "forma_pagamento":  "Boleto Bancário",
            })

    # Fallback: formas de pagamento em <pag><detPag>
    if not pagamentos:
        pag = _find(inf_nfe, "nfe:pag")
        if pag is not None:
            for det in pag.findall("nfe:detPag", namespaces=NS):
                tpag = _text(det, "nfe:tPag")
                pagamentos.append({
                    "numero_duplicata": None,
                    "vencimento":       None,
                    "valor":            _float(det, "nfe:vPag"),
                    "forma_pagamento":  _FORMA_PAGAMENTO.get(tpag, tpag),
                })

    return pagamentos
