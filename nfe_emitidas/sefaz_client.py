"""
Cliente SEFAZ para consulta de NF-e emitidas via nfeConsultaProtocolo.
Cada UF tem seu próprio endpoint. Para MG o serviço correto é NFeConsultaProtocolo4.
"""

import logging
import os
import time

import requests
from lxml import etree

logger = logging.getLogger(__name__)

AMBIENTE = os.getenv("NFE_AMBIENTE", "1")

_CERT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "certs")

# Formato: (url, soap_action, xmlns_wsdl, soap12)
_ENDPOINTS = {
    # MG — confirmado: usa NFeConsultaProtocolo4 com SOAP 1.2
    "31": (
        "https://nfe.fazenda.mg.gov.br/nfe2/services/NFeConsultaProtocolo4",
        "http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsultaProtocolo4/nfeConsultaNF",
        "http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsultaProtocolo4",
        True,   # SOAP 1.2
    ),
    # SP
    "35": (
        "https://nfe.fazenda.sp.gov.br/ws/nfeconsulta4.asmx",
        "http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsulta4/nfeConsultaNF",
        "http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsulta4",
        False,
    ),
    # RS
    "43": (
        "https://nfe.sefaz.rs.gov.br/ws/NfeConsulta/NfeConsulta4.asmx",
        "http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsulta4/nfeConsultaNF",
        "http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsulta4",
        False,
    ),
    # PR
    "41": (
        "https://nfe.fazenda.pr.gov.br/nfe/services/NFeConsulta4",
        "http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsulta4/nfeConsultaNF",
        "http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsulta4",
        False,
    ),
    # BA
    "29": (
        "https://nfe.sefaz.ba.gov.br/webservices/NfeConsulta4/NfeConsulta4.asmx",
        "http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsulta4/nfeConsultaNF",
        "http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsulta4",
        False,
    ),
}

# Estados sem servidor próprio usam SVRS (SOAP 1.1)
_SVRS_ENDPOINT = (
    "https://nfe.svrs.rs.gov.br/ws/NfeConsulta/NfeConsulta4.asmx",
    "http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsulta4/nfeConsultaNF",
    "http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsulta4",
    False,
)


def _resolver_endpoint(chave: str) -> tuple[str, str, str, bool]:
    uf = chave[:2]
    return _ENDPOINTS.get(uf, _SVRS_ENDPOINT)


def _exportar_cert() -> tuple[str, str]:
    """Exporta PFX → PEM (reutiliza cache se já exportado pelo módulo compras)."""
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates

    path  = os.getenv("NFE_CERT_PATH")
    senha = os.getenv("NFE_CERT_SENHA")
    if not path:
        raise ValueError("NFE_CERT_PATH não configurado no .env")
    if not senha:
        raise ValueError("NFE_CERT_SENHA não configurado no .env")

    os.makedirs(_CERT_DIR, exist_ok=True)
    cert_pem = os.path.join(_CERT_DIR, "cert.pem")
    key_pem  = os.path.join(_CERT_DIR, "key.pem")

    if os.path.exists(cert_pem) and os.path.exists(key_pem):
        return cert_pem, key_pem

    with open(path, "rb") as f:
        pfx = f.read()

    key, cert, _ = load_key_and_certificates(pfx, senha.encode())
    open(cert_pem, "wb").write(cert.public_bytes(Encoding.PEM))
    open(key_pem,  "wb").write(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    return cert_pem, key_pem


def _soap_consulta(chave: str, xmlns_wsdl: str, soap12: bool = False) -> bytes:
    # Corpo da mensagem NF-e — sem whitespace entre tags (cStat=588)
    nfe_msg = (
        f'<consSitNFe xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">'
        f'<tpAmb>{AMBIENTE}</tpAmb>'
        f'<xServ>CONSULTAR</xServ>'
        f'<chNFe>{chave}</chNFe>'
        f'</consSitNFe>'
    )
    if soap12:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope" xmlns:nfe="{xmlns_wsdl}">'
            '<soap12:Header/>'
            '<soap12:Body>'
            f'<nfe:nfeDadosMsg>{nfe_msg}</nfe:nfeDadosMsg>'
            '</soap12:Body>'
            '</soap12:Envelope>'
        ).encode("utf-8")
    else:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:nfe="{xmlns_wsdl}">'
            '<soapenv:Header/>'
            '<soapenv:Body>'
            f'<nfe:nfeDadosMsg>{nfe_msg}</nfe:nfeDadosMsg>'
            '</soapenv:Body>'
            '</soapenv:Envelope>'
        ).encode("utf-8")


def _call_sefaz(url: str, soap_action: str, soap_xml: bytes, cert: tuple,
                soap12: bool = False) -> bytes:
    if soap12:
        # SOAP 1.2: action vai embutido no Content-Type
        headers = {
            "Content-Type": f'application/soap+xml; charset=utf-8; action="{soap_action}"',
        }
    else:
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction":   f'"{soap_action}"',
        }
    resp = requests.post(url, data=soap_xml, cert=cert, headers=headers,
                         timeout=30, verify=False)
    resp.raise_for_status()
    return resp.content


def _extrair_xml_nfe(resp_bytes: bytes, chave: str) -> str:
    """
    Extrai o XML da NF-e autorizada do envelope SOAP de resposta.
    Retorna procNFe (NFe + protNFe) como string UTF-8.
    """
    NS = "http://www.portalfiscal.inf.br/nfe"
    root = etree.fromstring(resp_bytes)

    ret = root.find(f".//{{{NS}}}retConsSitNFe")
    if ret is None:
        raise RuntimeError("Resposta SEFAZ inválida: retConsSitNFe não encontrado")

    cstat   = ret.findtext(f"{{{NS}}}cStat", "")
    xmotivo = ret.findtext(f"{{{NS}}}xMotivo", "")

    if cstat != "100":
        raise RuntimeError(f"SEFAZ cStat={cstat}: {xmotivo}")

    # Monta procNFe (NFe + protNFe) — formato oficial
    nfe_el  = ret.find(f".//{{{NS}}}NFe")
    prot_el = ret.find(f".//{{{NS}}}protNFe")

    if nfe_el is not None and prot_el is not None:
        proc = etree.Element(f"{{{NS}}}procNFe", versao="4.00")
        proc.append(nfe_el)
        proc.append(prot_el)
        return etree.tostring(proc, xml_declaration=True, encoding="UTF-8").decode("utf-8")

    if nfe_el is not None:
        return etree.tostring(nfe_el, xml_declaration=True, encoding="UTF-8").decode("utf-8")

    raise RuntimeError(f"XML da NF-e {chave} não encontrado na resposta")


def _extrair_metadados(xml_str: str) -> dict:
    NS = "http://www.portalfiscal.inf.br/nfe"
    try:
        root = etree.fromstring(xml_str.encode("utf-8"))
        ide  = root.find(f".//{{{NS}}}ide")
        dest = root.find(f".//{{{NS}}}dest")
        tot  = root.find(f".//{{{NS}}}ICMSTot")

        numero     = ide.findtext(f"{{{NS}}}nNF", "")   if ide is not None else ""
        serie      = ide.findtext(f"{{{NS}}}serie", "") if ide is not None else ""
        dh_emi     = (ide.findtext(f"{{{NS}}}dhEmi") or
                      ide.findtext(f"{{{NS}}}dEmi") or "") if ide is not None else ""
        emitida_em = dh_emi[:10]

        destinatario = ""
        if dest is not None:
            destinatario = (dest.findtext(f"{{{NS}}}xNome") or
                            dest.findtext(f"{{{NS}}}CNPJ")  or
                            dest.findtext(f"{{{NS}}}CPF")   or "")

        valor_total = 0.0
        if tot is not None:
            try:
                valor_total = float(tot.findtext(f"{{{NS}}}vNF") or 0)
            except ValueError:
                pass

        return {
            "numero":       numero,
            "serie":        serie,
            "emitida_em":   emitida_em,
            "destinatario": destinatario,
            "valor_total":  valor_total,
        }
    except Exception as e:
        logger.warning(f"Erro ao extrair metadados: {e}")
        return {}


def _parsear_chave(chave: str) -> dict:
    """
    Extrai campos da chave de acesso (44 dígitos) sem chamar nenhuma API.
    Estrutura: cUF(2) AAMM(4) CNPJ(14) mod(2) serie(3) nNF(9) tpEmis(1) cNF(8) cDV(1)
    """
    cnpj_raw = chave[6:20]
    cnpj_fmt = (
        f"{cnpj_raw[:2]}.{cnpj_raw[2:5]}.{cnpj_raw[5:8]}"
        f"/{cnpj_raw[8:12]}-{cnpj_raw[12:]}"
    )
    aamm = chave[2:6]
    ano = "20" + aamm[:2]
    mes = aamm[2:4]
    return {
        "uf_cod":    chave[:2],
        "emitida_em": f"{ano}-{mes}-01",
        "emit_cnpj": cnpj_fmt,
        "serie":     str(int(chave[22:25])),
        "numero":    str(int(chave[25:34])),
    }


def consultar_protocolo(chave: str) -> dict:
    """
    Consulta o SEFAZ e retorna os dados de protocolo de autorização.

    Retorna dict com: cStat, xMotivo, nProt, dhRecbto, prot_xml (XML do protNFe)
    Levanta RuntimeError se a NF-e não estiver autorizada.
    """
    if len(chave) != 44 or not chave.isdigit():
        raise ValueError(f"Chave inválida: {chave!r} (deve ter 44 dígitos numéricos)")

    url, soap_action, xmlns_wsdl, soap12 = _resolver_endpoint(chave)
    cert = _exportar_cert()
    soap = _soap_consulta(chave, xmlns_wsdl, soap12=soap12)

    NS = "http://www.portalfiscal.inf.br/nfe"

    for tentativa in range(3):
        try:
            resp_bytes = _call_sefaz(url, soap_action, soap, cert, soap12=soap12)
            root = etree.fromstring(resp_bytes)

            ret = root.find(f".//{{{NS}}}retConsSitNFe")
            if ret is None:
                raise RuntimeError("Resposta SEFAZ inválida: retConsSitNFe não encontrado")

            cstat   = ret.findtext(f"{{{NS}}}cStat", "")
            xmotivo = ret.findtext(f"{{{NS}}}xMotivo", "")

            if cstat != "100":
                raise RuntimeError(f"SEFAZ cStat={cstat}: {xmotivo}")

            inf_prot = ret.find(f".//{{{NS}}}infProt")
            nprot    = inf_prot.findtext(f"{{{NS}}}nProt",   "") if inf_prot is not None else ""
            dhrecbto = inf_prot.findtext(f"{{{NS}}}dhRecbto", "") if inf_prot is not None else ""

            prot_el  = ret.find(f".//{{{NS}}}protNFe")
            prot_xml = (
                etree.tostring(prot_el, xml_declaration=True, encoding="UTF-8").decode("utf-8")
                if prot_el is not None else ""
            )

            return {
                "cStat":    cstat,
                "xMotivo":  xmotivo,
                "nProt":    nprot,
                "dhRecbto": dhrecbto,
                "prot_xml": prot_xml,
            }
        except RuntimeError:
            raise
        except Exception as e:
            if tentativa == 2:
                raise RuntimeError(f"Falha após 3 tentativas: {e}") from e
            logger.warning(f"Tentativa {tentativa+1} falhou: {e} — aguardando 3s")
            time.sleep(3)


def consultar_nfe(chave: str) -> tuple[str, dict]:
    """
    Consulta NF-e emitida via nfeConsultaProtocolo no SEFAZ estadual.

    Retorna:
        (xml_str, metadados)
    Levanta:
        RuntimeError em caso de rejeição ou NF-e não autorizada
    """
    if len(chave) != 44 or not chave.isdigit():
        raise ValueError(f"Chave inválida: {chave!r} (deve ter 44 dígitos numéricos)")

    url, soap_action, xmlns_wsdl, soap12 = _resolver_endpoint(chave)
    cert = _exportar_cert()
    soap = _soap_consulta(chave, xmlns_wsdl, soap12=soap12)

    logger.info(f"Consultando {url} (SOAP {'1.2' if soap12 else '1.1'}) — chave {chave}")

    for tentativa in range(3):
        try:
            resp    = _call_sefaz(url, soap_action, soap, cert, soap12=soap12)
            xml_str = _extrair_xml_nfe(resp, chave)
            meta    = _extrair_metadados(xml_str)
            return xml_str, meta
        except RuntimeError:
            raise
        except Exception as e:
            if tentativa == 2:
                raise RuntimeError(f"Falha após 3 tentativas: {e}") from e
            logger.warning(f"Tentativa {tentativa+1} falhou: {e} — aguardando 3s")
            time.sleep(3)
