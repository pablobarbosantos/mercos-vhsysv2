"""
Lógica de negócio para emissão de boletos SICOOB.
Chama o SICOOB app.py (porta 8001) via HTTP — sem importar o SDK diretamente.
"""
import logging
import os
import re
import requests
from datetime import datetime
from boletos import database as db
from boletos.vhsys_adapter import buscar_conta_por_id

logger = logging.getLogger(__name__)

_SICOOB_URL = os.getenv("SICOOB_APP_URL", "http://localhost:8001")
_NUMERO_CLIENTE = int(os.getenv("SICOOB_NUMERO_CLIENTE", "1385690"))
_TIMEOUT = 35


def _so_digitos(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))


def construir_payload(nosso_numero: int, config: dict, conta: dict, data_vencimento: str) -> dict:
    """
    Monta o payload para POST /boletos no SICOOB app (porta 8001).
    """
    cpf_cnpj = _so_digitos(
        conta.get("cpf_cnpj_cliente")
        or conta.get("cnpj_cpf_cliente")
        or conta.get("cpf_cnpj")
        or ""
    )
    nome = (conta.get("nome_cliente") or conta.get("nome_contato") or "").strip()

    # Endereço do pagador (melhor esforço)
    endereco = (conta.get("endereco_cliente") or conta.get("endereco") or "").strip()
    cidade   = (conta.get("cidade_cliente")   or conta.get("cidade")   or "").strip()
    uf       = (conta.get("uf_cliente")       or conta.get("uf")       or "SP").strip()
    cep      = _so_digitos(conta.get("cep_cliente") or conta.get("cep") or "00000000")
    bairro   = (conta.get("bairro_cliente")   or conta.get("bairro")   or "").strip()

    valor = float(conta.get("valor_rec") or conta.get("valor") or 0)

    payload: dict = {
        "numeroCliente": _NUMERO_CLIENTE,
        "codigoModalidade": config.get("codigo_modalidade", 3),
        "nossoNumero": nosso_numero,
        "dataVencimento": data_vencimento,
        "valorNominal": round(valor, 2),
        "especieDocumento": config.get("especie_titulo", "DM"),
        "pagador": {
            "nome": nome[:40],
            "cpfCnpj": cpf_cnpj,
            "endereco": endereco[:40] if endereco else "NAO INFORMADO",
            "bairro": bairro[:15] if bairro else "NAO INFORMADO",
            "cidade": cidade[:15] if cidade else "NAO INFORMADO",
            "uf": uf[:2] if uf else "MG",
            "cep": cep[:8] if cep else "00000000",
        },
        "mensagem": {
            "linha1": config.get("local_pagamento", "Pagável em qualquer banco até o vencimento")[:80],
        },
        "juros": {
            "tipo": "PERCENTUAL_MES",
            "valor": config.get("juros_percentual", 1.0),
        },
        "multa": {
            "tipo": "PERCENTUAL",
            "valor": config.get("multa_percentual", 2.0),
        },
        "instrucoes": [
            {
                "tipo": "PROTESTAR_DIAS_CORRIDOS",
                "quantidade": config.get("dias_protesto", 3),
            },
            {
                "tipo": "BAIXAR_BOLETO",
                "quantidade": config.get("dias_baixa", 60),
            },
        ],
    }
    return payload


def validar_e_emitir(
    vhsys_conta_id: str,
    data_vencimento: str,
    valor_override: float | None = None,
    cliente_nome_override: str | None = None,
    cliente_cpf_cnpj_override: str | None = None,
) -> dict:
    """
    Valida, reserva nossoNumero e emite boleto via SICOOB app (porta 8001).
    Retorna o dict do boleto salvo no banco.
    """
    # 1. Idempotência — já emitido?
    existente = db.get_boleto_by_conta_id(vhsys_conta_id)
    if existente:
        raise ValueError(f"Boleto já emitido para conta {vhsys_conta_id} (nossoNumero={existente['nosso_numero']})")

    # 2. Buscar dados da conta no VHSys
    conta = buscar_conta_por_id(vhsys_conta_id)
    if not conta:
        raise ValueError(f"Conta {vhsys_conta_id} não encontrada no VHSys")

    # Overrides do operador
    if valor_override:
        conta["valor_rec"] = valor_override
    if cliente_nome_override:
        conta["nome_cliente"] = cliente_nome_override
    if cliente_cpf_cnpj_override:
        conta["cpf_cnpj_cliente"] = cliente_cpf_cnpj_override

    # 3. Config global
    config = db.get_config()

    # 4. Reservar nossoNumero
    nosso_numero = db.next_nosso_numero()

    # 5. Montar payload e emitir
    payload = construir_payload(nosso_numero, config, conta, data_vencimento)

    logger.info("[BoletoService] Emitindo nossoNumero=%s para conta=%s", nosso_numero, vhsys_conta_id)

    try:
        resp = requests.post(f"{_SICOOB_URL}/boletos", json=payload, timeout=_TIMEOUT)
    except requests.exceptions.ConnectionError:
        raise RuntimeError("SICOOB app (porta 8001) não está rodando. Inicie com: python SICOOB/app.py")

    if resp.status_code not in (200, 201):
        body = resp.text[:500]
        logger.error("[BoletoService] Erro SICOOB %s: %s", resp.status_code, body)
        raise ValueError(f"SICOOB rejeitou a emissão (HTTP {resp.status_code}): {body}")

    sicoob_data = resp.json()
    resultado = sicoob_data.get("resultado", sicoob_data)

    # 6. Salvar no banco local
    cpf_cnpj = _so_digitos(
        conta.get("cpf_cnpj_cliente") or conta.get("cnpj_cpf_cliente") or ""
    )
    db.salvar_boleto({
        "vhsys_conta_id": vhsys_conta_id,
        "vhsys_nro": conta.get("n_documento_rec") or conta.get("identificacao", ""),
        "nosso_numero": nosso_numero,
        "cliente_nome": conta.get("nome_cliente", ""),
        "cliente_cpf_cnpj": cpf_cnpj,
        "valor_nominal": float(conta.get("valor_rec", 0)),
        "data_vencimento": data_vencimento,
        "data_emissao": datetime.now().strftime("%Y-%m-%d"),
        "linha_digitavel": resultado.get("linhaDigitavel"),
        "codigo_barras": resultado.get("codigoBarras"),
        "qr_code": resultado.get("qrCode"),
        "sicoob_json": resultado,
    })

    boleto = db.get_boleto_by_conta_id(vhsys_conta_id)
    logger.info("[BoletoService] ✅ Boleto emitido e salvo: nossoNumero=%s", nosso_numero)
    return boleto


def consultar_sicoob(nosso_numero: int) -> dict:
    """Consulta status em tempo real via SICOOB app."""
    resp = requests.get(f"{_SICOOB_URL}/boletos/{nosso_numero}", timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def alterar_vencimento(nosso_numero: int, nova_data: str) -> dict:
    """Altera data de vencimento via SICOOB app."""
    resp = requests.patch(
        f"{_SICOOB_URL}/boletos/{nosso_numero}",
        json={"dataVencimento": nova_data},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def baixar_boleto(nosso_numero: int, motivo: str = "BAIXA_MANUAL") -> dict:
    """Dá baixa (cancela) via SICOOB app."""
    resp = requests.delete(
        f"{_SICOOB_URL}/boletos/{nosso_numero}",
        params={"motivo": motivo},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    db.atualizar_status(nosso_numero, "baixado")
    return resp.json() if resp.content else {"ok": True}
