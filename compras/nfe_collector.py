"""
Coletor de NF-e via SEFAZ - VERSÃO PROFISSIONAL

MELHORIAS:
- Paginação completa (NSU)
- Controle por banco (evita execução duplicada)
- Retry automático (instabilidade SEFAZ)
- Logs mais claros
"""

import base64
import gzip
import logging
import os
import time

import requests
from lxml import etree

from compras import database as db
from compras.nfe_parser import parse_nfe

logger = logging.getLogger(__name__)

SEFAZ_URL = os.getenv(
    "SEFAZ_NFE_URL",
    "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"
)
AMBIENTE = os.getenv("NFE_AMBIENTE", "1")

CERT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "certs")
XML_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "nfe_xmls")

INTERVALO_MINIMO = 5400  # 1h30 — margem de segurança sobre o limite da SEFAZ (1h)

# cStats de sucesso do nfeDistDFeInteresse
_CSTAT_OK = {"137", "138"}  # 137=nenhum doc, 138=docs encontrados


def cert_info() -> dict:
    """Retorna informações do certificado atual (validade, razão social, CNPJ)."""
    try:
        path = os.getenv("NFE_CERT_PATH") or db.config_get("cert_path") or ""
        senha_str = os.getenv("NFE_CERT_SENHA") or db.config_get("cert_senha") or ""
        if not path or not os.path.exists(path):
            return {"cert_ok": False, "msg": "Certificado não encontrado"}

        from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
        with open(path, "rb") as f:
            pfx = f.read()
        _, cert, _ = load_key_and_certificates(pfx, senha_str.encode())

        subject = {a.oid.dotted_string: a.value for a in cert.subject}
        return {
            "cert_ok": True,
            "cert_razao_social": subject.get("2.5.4.3", ""),
            "cert_expira_em": cert.not_valid_after_utc.strftime("%Y-%m-%d"),
        }
    except Exception as exc:
        return {"cert_ok": False, "msg": str(exc)}


def invalidar_cache_cert() -> None:
    """Remove os PEMs exportados para forçar re-exportação na próxima coleta."""
    for nome in ("cert.pem", "key.pem"):
        p = os.path.join(CERT_DIR, nome)
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


class SefazRejeicaoError(Exception):
    """SEFAZ rejeitou a requisição. Preserva o ultNSU para salvar progresso."""
    def __init__(self, cstat, xmotivo, ult_nsu):
        super().__init__(f"cStat={cstat}: {xmotivo}")
        self.cstat = cstat
        self.ult_nsu = ult_nsu


def _pode_rodar():
    """Retorna 0 se pode rodar, ou segundos restantes se ainda em bloqueio."""
    ultima = db.config_get("sefaz_ultima_execucao") or 0
    agora = time.time()
    decorrido = agora - float(ultima)

    if decorrido < INTERVALO_MINIMO:
        return int(INTERVALO_MINIMO - decorrido)

    db.config_set("sefaz_ultima_execucao", str(agora))
    return 0


def _retry(func, tentativas=3, delay=2):
    for i in range(tentativas):
        try:
            return func()
        except Exception as e:
            if i == tentativas - 1:
                raise
            logger.warning(f"Retry {i+1} após erro: {e}")
            time.sleep(delay)


def _get_env(nome):
    val = os.getenv(nome)
    if not val:
        raise ValueError(f"Env {nome} não configurada")
    return val


def _exportar_cert():
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates

    path = _get_env("NFE_CERT_PATH")
    senha = _get_env("NFE_CERT_SENHA")

    with open(path, "rb") as f:
        pfx = f.read()

    key, cert, _ = load_key_and_certificates(pfx, senha.encode())

    cert_pem = os.path.join(CERT_DIR, "cert.pem")
    key_pem = os.path.join(CERT_DIR, "key.pem")

    os.makedirs(CERT_DIR, exist_ok=True)

    open(cert_pem, "wb").write(cert.public_bytes(Encoding.PEM))
    open(key_pem, "wb").write(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))

    return cert_pem, key_pem


def _soap(cnpj, nsu):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:nfe="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">
<soapenv:Header/>
<soapenv:Body>
<nfe:nfeDistDFeInteresse>
<nfe:nfeDadosMsg>
<distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">
<tpAmb>{AMBIENTE}</tpAmb>
<cUFAutor>31</cUFAutor>
<CNPJ>{cnpj}</CNPJ>
<distNSU><ultNSU>{nsu.zfill(15)}</ultNSU></distNSU>
</distDFeInt>
</nfe:nfeDadosMsg>
</nfe:nfeDistDFeInteresse>
</soapenv:Body>
</soapenv:Envelope>""".encode("utf-8")


def _call(xml, cert):
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe/nfeDistDFeInteresse",
    }
    return requests.post(SEFAZ_URL, data=xml, cert=cert, headers=headers, timeout=30).content


def _parse(resp):
    root = etree.fromstring(resp)
    ret = root.find(".//{http://www.portalfiscal.inf.br/nfe}retDistDFeInt")

    NS = "{http://www.portalfiscal.inf.br/nfe}"
    cstat   = ret.findtext(f"{NS}cStat", "0")
    xmotivo = ret.findtext(f"{NS}xMotivo", "")
    ult_nsu = ret.findtext(f"{NS}ultNSU", "0")
    max_nsu = ret.findtext(f"{NS}maxNSU", "0")

    if cstat not in _CSTAT_OK:
        raise SefazRejeicaoError(cstat, xmotivo, ult_nsu)

    docs = []
    lote = ret.find(f"{NS}loteDistDFeInt")

    if lote is not None:
        for d in lote:
            xml = gzip.decompress(base64.b64decode(d.text))
            tipo = d.get("schema", "").split("_")[0]
            docs.append((tipo, xml))

    return max_nsu, docs


def _descobrir_max_nsu(cnpj, cert):
    """Faz uma chamada com NSU máximo possível para descobrir o maxNSU atual da SEFAZ."""
    nsu_teto = "999999999999999"
    try:
        resp = _call(_soap(cnpj, nsu_teto), cert)
        root = etree.fromstring(resp)
        ret = root.find(".//{http://www.portalfiscal.inf.br/nfe}retDistDFeInt")
        NS = "{http://www.portalfiscal.inf.br/nfe}"
        ult_nsu = ret.findtext(f"{NS}ultNSU", "0")
        return ult_nsu
    except Exception as e:
        logger.warning(f"Não foi possível descobrir maxNSU: {e}")
        return None


def coletar_nfes():
    res = {"baixadas": 0, "ja_existentes": 0, "erros": 0}

    restante = _pode_rodar()
    if restante:
        logger.info("Ignorado - faltam %ds para próxima coleta", restante)
        return {**res, "skipped": True, "restante_seg": restante}

    cnpj = _get_env("NFE_CNPJ_EMPRESA")
    cert = _exportar_cert()

    nsu = db.sefaz_get_ultimo_nsu()

    # Primeira execução (NSU zerado): descobre o maxNSU atual e começa pelos
    # documentos mais recentes. Backfill dos antigos ocorre nas execuções seguintes
    # avançando para trás a partir do NSU inicial guardado em sefaz_nsu_backfill.
    NSU_ZERO = "000000000000000"
    if nsu == NSU_ZERO:
        logger.info("Primeira execução — descobrindo NSU mais recente da SEFAZ...")
        max_atual = _descobrir_max_nsu(cnpj, cert)
        if max_atual and max_atual != NSU_ZERO:
            logger.info(f"maxNSU atual: {max_atual} — iniciando pelos documentos mais recentes")
            db.sefaz_salvar_ultimo_nsu(max_atual)
            nsu = max_atual
        # guarda ponto de backfill (NSU 0) para buscar histórico nas próximas execuções
        if not db.config_get("sefaz_nsu_backfill_pendente"):
            db.config_set("sefaz_nsu_backfill_pendente", "1")
            db.config_set("sefaz_nsu_backfill_pos", NSU_ZERO)

    while True:
        try:
            resp = _retry(lambda: _call(_soap(cnpj, nsu), cert))
            max_nsu, docs = _parse(resp)
        except SefazRejeicaoError as e:
            logger.error(f"SEFAZ rejeitou: {e} — salvando ultNSU={e.ult_nsu}")
            db.sefaz_salvar_ultimo_nsu(e.ult_nsu)
            res["erros"] += 1
            return res
        except Exception as e:
            logger.error(f"Erro SEFAZ: {e}")
            res["erros"] += 1
            break

        for tipo, xml in docs:
            if tipo not in ("procNFe", "NFe"):
                continue

            try:
                root = etree.fromstring(xml)
                chave = root.find(".//{http://www.portalfiscal.inf.br/nfe}infNFe").get("Id")[3:]

                if db.nota_ja_existe(chave):
                    res["ja_existentes"] += 1
                    continue

                os.makedirs(XML_DIR, exist_ok=True)
                path = os.path.join(XML_DIR, f"{chave}.xml")
                open(path, "wb").write(xml)

                parsed = parse_nfe(path)

                db.nota_criar(
                    chave_nfe=chave,
                    numero=parsed.get("numero", ""),
                    serie=parsed.get("serie", ""),
                    emitida_em=parsed.get("emitida_em", ""),
                    fornecedor_cnpj=parsed.get("fornecedor", {}).get("cnpj", ""),
                    fornecedor_nome=parsed.get("fornecedor", {}).get("nome", ""),
                    valor_total=parsed.get("valor_total", 0.0),
                    xml_path=path,
                )

                res["baixadas"] += 1

            except Exception as e:
                logger.error(f"Erro doc: {e}")
                res["erros"] += 1

        # salva NSU após cada página — não perde progresso em caso de crash
        db.sefaz_salvar_ultimo_nsu(max_nsu)

        if nsu == max_nsu:
            break

        nsu = max_nsu
        time.sleep(1)

    return res
