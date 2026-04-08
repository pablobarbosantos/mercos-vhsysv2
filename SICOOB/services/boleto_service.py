import logging
from typing import Any
import config
from services.sicoob_client import get_client
from services.exceptions import BoletoError, BoletoNaoEncontrado

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.sicoob.com.br"
_BOLETOS_PATH = "/cobranca-bancaria/v3/boletos"


def _boleto_api():
    """Retorna BoletoAPI do SDK (para emissão)."""
    return get_client().cobranca.boleto


def _session_e_token(scope: str):
    """Retorna (session_mTLS, access_token) para chamadas HTTP diretas."""
    client = get_client()
    token = client.oauth_client.get_access_token(scope=scope)
    session = client.session
    return session, token


def emitir(payload: dict[str, Any]) -> dict:
    """Emite e registra um boleto no SICOOB."""
    nosso_numero = payload.get("nossoNumero", "?")
    logger.info("Emitindo boleto nossoNumero=%s", nosso_numero)
    try:
        resultado = _boleto_api().emitir_boleto(payload)
        logger.info("Boleto emitido — nossoNumero=%s", nosso_numero)
        return resultado
    except Exception as e:
        logger.error("Erro ao emitir boleto nossoNumero=%s: %s", nosso_numero, e)
        raise BoletoError(f"Falha na emissão: {e}") from e


def consultar(nosso_numero: int | str, codigo_modalidade: int = 1) -> dict:
    """Consulta um boleto pelo nossoNumero (inteiro, sem traço)."""
    scope = "boletos_consulta"
    session, token = _session_e_token(scope)
    url = f"{_BASE_URL}{_BOLETOS_PATH}"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "numeroCliente": config.NUMERO_CLIENTE,
        "nossoNumero": int(nosso_numero),
        "codigoModalidade": codigo_modalidade,
    }
    try:
        resp = session.get(url, params=params, headers=headers, timeout=config.TIMEOUT)
        if resp.status_code == 404:
            raise BoletoNaoEncontrado(f"Boleto {nosso_numero} não encontrado")
        data = resp.json()
        # API retorna 400 com codigo 5003 quando não encontrado
        mensagens = data.get("mensagens", [])
        if mensagens and any(m.get("codigo") in ("5003", "5002") for m in mensagens):
            raise BoletoNaoEncontrado(f"Boleto {nosso_numero} não encontrado")
        resp.raise_for_status()
        return data.get("resultado", data)
    except BoletoNaoEncontrado:
        raise
    except Exception as e:
        msg = str(e).lower()
        if "404" in msg or "não encontrado" in msg or "not found" in msg:
            raise BoletoNaoEncontrado(f"Boleto {nosso_numero} não encontrado") from e
        raise BoletoError(f"Erro na consulta: {e}") from e


def alterar(nosso_numero: str, dados: dict[str, Any]) -> dict:
    """Altera dados de um boleto (ex: data vencimento, valor)."""
    scope = "boletos_alteracao"
    session, token = _session_e_token(scope)
    url = f"{_BASE_URL}{_BOLETOS_PATH}/{config.NUMERO_CLIENTE}/{nosso_numero}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = session.patch(url, json=dados, headers=headers, timeout=config.TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        raise BoletoError(f"Erro na alteração: {e}") from e


def baixar(nosso_numero: str, motivo: str = "BAIXA_MANUAL") -> dict:
    """Dá baixa (cancela) um boleto."""
    logger.info("Baixando boleto nossoNumero=%s motivo=%s", nosso_numero, motivo)
    scope = "boletos_alteracao"
    session, token = _session_e_token(scope)
    url = f"{_BASE_URL}{_BOLETOS_PATH}/{config.NUMERO_CLIENTE}/{nosso_numero}/baixar"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = session.post(url, json={"motivo": motivo}, headers=headers, timeout=config.TIMEOUT)
        resp.raise_for_status()
        logger.info("Boleto %s baixado com sucesso.", nosso_numero)
        return resp.json() if resp.content else {}
    except Exception as e:
        raise BoletoError(f"Erro ao baixar boleto: {e}") from e


def segunda_via(nosso_numero: str) -> dict:
    """Retorna dados da segunda via (JSON, sem PDF)."""
    try:
        resultado = _boleto_api().emitir_segunda_via(
            numero_cliente=config.NUMERO_CLIENTE,
            codigo_modalidade=1,
            nosso_numero=int(nosso_numero),
            gerar_pdf=False,
        )
        return resultado
    except Exception as e:
        raise BoletoError(f"Erro na segunda via: {e}") from e


def segunda_via_pdf(nosso_numero: str) -> bytes:
    """Retorna o PDF oficial do boleto via segunda via SICOOB.
    modeloImpressao=2 = A4 sem envelopamento 3 vias.
    """
    import base64
    scope = "boletos_consulta"
    session, token = _session_e_token(scope)
    url = f"{_BASE_URL}{_BOLETOS_PATH}/segunda-via"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "numeroCliente": config.NUMERO_CLIENTE,
        "codigoModalidade": 1,
        "nossoNumero": int(nosso_numero),
        "gerarPdf": "true",
        "modeloImpressao": 2,  # 1=A4 1via, 2=A4 3vias, 3=A4 com envelopamento
    }
    try:
        resp = session.get(url, params=params, headers=headers, timeout=config.TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        inner = data.get("resultado", data)
        pdf_b64 = (
            inner.get("pdfBoleto")
            or inner.get("pdf")
            or inner.get("pdfBase64")
        )
        if not pdf_b64:
            raise BoletoError(f"Campo PDF não encontrado. Campos disponíveis: {list(inner.keys())}")
        return base64.b64decode(pdf_b64)
    except BoletoError:
        raise
    except Exception as e:
        raise BoletoError(f"Erro ao obter PDF da segunda via: {e}") from e
