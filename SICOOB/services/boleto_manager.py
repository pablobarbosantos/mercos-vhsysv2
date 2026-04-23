"""
Camada de orquestração de negócio.
Todos os endpoints chamam daqui, nunca boleto_service diretamente.
"""
import logging
from typing import Any

from services import boleto_service, database
from services.exceptions import BoletoError

logger = logging.getLogger(__name__)


def emitir(payload: dict[str, Any], usuario: str | None = None) -> dict:
    """
    Emite boleto no Sicoob e persiste localmente.
    Bloqueia se já existe boleto ativo para o mesmo vhsys_pedido_id.
    """
    vhsys_id = payload.get("vhsys_pedido_id")
    if vhsys_id and database.checar_duplicidade(int(vhsys_id)):
        raise BoletoError(
            f"Já existe boleto ativo para o pedido VHSys #{vhsys_id}. "
            "Dê baixa no boleto existente antes de emitir outro."
        )

    # Remove campo interno antes de enviar ao Sicoob
    payload_sicoob = {k: v for k, v in payload.items() if k != "vhsys_pedido_id"}

    resultado = boleto_service.emitir(payload_sicoob)

    # Extrair campos do retorno Sicoob (estrutura varia por SDK version)
    inner = resultado.get("resultado", resultado)
    if isinstance(inner, list):
        inner = inner[0] if inner else {}

    nosso_numero = str(
        inner.get("nossoNumero") or payload.get("nossoNumero") or ""
    )
    pagador = payload.get("pagador", {})

    campos = {
        "nosso_numero":     nosso_numero,
        "seu_numero":       payload.get("seuNumero") or payload.get("seu_numero"),
        "vhsys_pedido_id":  vhsys_id,
        "cliente_nome":     pagador.get("nome"),
        "cliente_doc":      pagador.get("numeroCpfCnpj"),
        "valor":            payload.get("valor") or inner.get("valor", 0),
        "vencimento":       payload.get("dataVencimento") or inner.get("dataVencimento", ""),
        "linha_digitavel":  inner.get("linhaDigitavel") or inner.get("codigoLinhaDigitavel"),
        "codigo_barras":    inner.get("codigoBarras"),
    }

    boleto_id = database.upsert_boleto(nosso_numero, campos)
    database.registrar_evento(boleto_id, "EMITIDO", "SISTEMA", dados=inner, usuario=usuario)

    logger.info("Boleto emitido e persistido: nossoNumero=%s", nosso_numero)
    return resultado


def marcar_pago_externo(nosso_numero: str, usuario: str | None = None) -> dict:
    """
    1. Registra PAGO_EXTERNO
    2. Baixa no Sicoob
    3. Registra BAIXADO
    """
    boleto = database.get_boleto(nosso_numero)
    if boleto is None:
        raise BoletoError(f"Boleto {nosso_numero} não encontrado no banco local.")

    if boleto["status_atual"] in ("BAIXADO", "LIQUIDADO"):
        raise BoletoError(
            f"Boleto {nosso_numero} já está com status '{boleto['status_atual']}'. "
            "Não é possível marcar como pago externo."
        )

    database.registrar_evento(
        boleto["id"], "PAGO_EXTERNO", "USUARIO",
        dados={"observacao": "Pagamento registrado manualmente"},
        usuario=usuario,
    )

    try:
        boleto_service.baixar(nosso_numero, motivo="BAIXA_MANUAL")
        database.registrar_evento(
            boleto["id"], "BAIXADO", "SISTEMA",
            dados={"motivo": "BAIXA_MANUAL", "origem": "pago_externo"},
            usuario=usuario,
        )
        logger.info("Boleto %s marcado como pago externo e baixado no Sicoob.", nosso_numero)
    except Exception as e:
        logger.error(
            "Boleto %s marcado como pago externo localmente, mas baixa no Sicoob falhou: %s",
            nosso_numero, e,
        )
        raise BoletoError(
            f"Pagamento registrado localmente, mas falha ao baixar no Sicoob: {e}"
        ) from e

    return database.get_boleto(nosso_numero)


def baixar_manual(nosso_numero: str, motivo: str = "BAIXA_MANUAL", usuario: str | None = None) -> dict:
    """Baixa manual sem marcar como pago externo."""
    boleto = database.get_boleto(nosso_numero)
    if boleto is None:
        raise BoletoError(f"Boleto {nosso_numero} não encontrado no banco local.")

    boleto_service.baixar(nosso_numero, motivo=motivo)
    database.registrar_evento(
        boleto["id"], "BAIXADO", "USUARIO",
        dados={"motivo": motivo},
        usuario=usuario,
    )
    logger.info("Boleto %s baixado manualmente (motivo=%s).", nosso_numero, motivo)
    return database.get_boleto(nosso_numero)
