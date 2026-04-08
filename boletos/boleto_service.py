"""
Lógica de negócio para emissão de boletos SICOOB.
Chama o SICOOB app.py (porta 8001) via HTTP — sem importar o SDK diretamente.
"""
import logging
import os
import re
import requests
from datetime import datetime, timedelta
from boletos import database as db
from boletos.vhsys_adapter import buscar_conta_por_id, buscar_cliente_por_id

logger = logging.getLogger(__name__)

_SICOOB_URL = os.getenv("SICOOB_APP_URL", "http://localhost:8001")
_NUMERO_CLIENTE = int(os.getenv("SICOOB_NUMERO_CLIENTE", "1385690"))
_TIMEOUT = 35


def _so_digitos(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))


def construir_payload(nosso_numero: int, config: dict, conta: dict, data_vencimento: str) -> dict:
    """
    Monta o payload para POST /boletos no SICOOB app (porta 8001).
    Segue o schema exato do sicoob-sdk (sicoob/validation.py:get_boleto_schema).
    """
    cpf_cnpj = _so_digitos(
        conta.get("cpf_cnpj_cliente")
        or conta.get("cnpj_cpf_cliente")
        or conta.get("cpf_cnpj")
        or ""
    )
    nome = (conta.get("nome_cliente") or conta.get("nome_contato") or "").strip()

    # Endereço do pagador (melhor esforço)
    endereco_rua  = (conta.get("endereco_cliente") or conta.get("endereco") or "NAO INFORMADO").strip()
    numero_end    = (conta.get("numero_cliente")   or conta.get("numero")   or "").strip()
    endereco_full = f"{endereco_rua}, {numero_end}".strip(", ") if numero_end else endereco_rua
    cidade        = (conta.get("cidade_cliente")   or conta.get("cidade")   or "NAO INFORMADO").strip()
    uf            = (conta.get("uf_cliente")       or conta.get("uf")       or "MG").strip()
    cep           = _so_digitos(conta.get("cep_cliente") or conta.get("cep") or "00000000")
    bairro        = (conta.get("bairro_cliente")   or conta.get("bairro")   or "NAO INFORMADO").strip()

    valor = float(conta.get("valor_rec") or conta.get("valor") or 0)

    # tipoJurosMora: 1=valor fixo, 2=taxa mensal, 3=isento
    tipo_juros = 2 if config.get("juros_percentual", 0) > 0 else 3
    # tipoMulta: 0=sem multa, 1=valor fixo, 2=percentual
    tipo_multa = 2 if config.get("multa_percentual", 0) > 0 else 0

    payload: dict = {
        # Obrigatórios
        "numeroCliente": _NUMERO_CLIENTE,
        "codigoModalidade": 1,            # SDK só aceita 1
        "nossoNumero": nosso_numero,
        "seuNumero": str(nosso_numero)[:18],
        "dataEmissao": datetime.now().strftime("%Y-%m-%d"),
        "dataVencimento": data_vencimento,
        "valor": round(valor, 2),
        "codigoEspecieDocumento": str(config.get("especie_titulo", "DM")).upper()[:3],
        "identificacaoEmissaoBoleto": 1,          # 1 = banco registra
        "identificacaoDistribuicaoBoleto": 2,     # 2 = cliente distribui (não imprime via banco)
        "numeroParcela": 1,
        # Juros mora (campos raiz, não objeto aninhado)
        "tipoJurosMora": tipo_juros,
        "valorJurosMora": round(float(config.get("juros_percentual", 1.0)), 4),
        "dataJurosMora": (datetime.strptime(data_vencimento, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d"),
        # Multa (campos raiz)
        "tipoMulta": tipo_multa,
        "valorMulta": round(float(config.get("multa_percentual", 2.0)), 4),
        "dataMulta": (datetime.strptime(data_vencimento, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d"),
        # Desconto (sem desconto = 0)
        "tipoDesconto": 0,
        # Pagador — nomes de campo conforme get_pagador_schema()
        "pagador": {
            "numeroCpfCnpj": cpf_cnpj,
            "nome": nome[:50],
            "endereco": endereco_full[:40],
            "bairro": bairro[:30],
            "cidade": cidade[:40],
            "uf": uf[:2].upper(),
            "cep": cep[:8].zfill(8),
        },
    }

    # Protesto (opcional)
    dias_protesto = config.get("dias_protesto", 0)
    if dias_protesto and int(dias_protesto) > 0:
        payload["codigoProtesto"] = 1          # 1 = protestar dias corridos
        payload["numeroDiasProtesto"] = int(dias_protesto)

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


def emitir_avulso(
    vhsys_cliente_id: str,
    valor: float,
    data_vencimento: str,
    descricao: str,
) -> dict:
    """
    Emite boleto para qualquer cliente VHSys sem conta-a-receber vinculada.
    vhsys_conta_id salvo como 'AVULSO-{nosso_numero}' para não colidir com idempotência.
    """
    cliente = buscar_cliente_por_id(vhsys_cliente_id)
    if not cliente:
        raise ValueError(f"Cliente {vhsys_cliente_id} não encontrado no VHSys")

    config = db.get_config()
    nosso_numero = db.next_nosso_numero()

    conta = {
        "valor_rec": valor,
        "nome_cliente": (cliente.get("razao_cliente") or cliente.get("nome_cliente") or cliente.get("razao_social") or "").strip(),
        "cpf_cnpj_cliente": cliente.get("cnpj_cliente") or cliente.get("cpf_cliente") or "",
        "endereco_cliente": cliente.get("endereco_cliente", ""),
        "bairro_cliente": cliente.get("bairro_cliente", ""),
        "cidade_cliente": cliente.get("cidade_cliente", ""),
        "uf_cliente": cliente.get("uf_cliente", "MG"),
        "cep_cliente": cliente.get("cep_cliente", ""),
        "n_documento_rec": descricao,
    }

    payload = construir_payload(nosso_numero, config, conta, data_vencimento)
    logger.info("[BoletoService] Emitindo avulso nossoNumero=%s cliente=%s", nosso_numero, vhsys_cliente_id)

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
    synthetic_id = f"AVULSO-{nosso_numero}"

    cpf_cnpj = _so_digitos(conta["cpf_cnpj_cliente"])
    db.salvar_boleto({
        "vhsys_conta_id": synthetic_id,
        "vhsys_nro": descricao,
        "nosso_numero": nosso_numero,
        "cliente_nome": conta["nome_cliente"],
        "cliente_cpf_cnpj": cpf_cnpj,
        "valor_nominal": round(valor, 2),
        "data_vencimento": data_vencimento,
        "data_emissao": datetime.now().strftime("%Y-%m-%d"),
        "linha_digitavel": resultado.get("linhaDigitavel"),
        "codigo_barras": resultado.get("codigoBarras"),
        "qr_code": resultado.get("qrCode"),
        "sicoob_json": resultado,
    })

    boleto = db.get_boleto_by_conta_id(synthetic_id)
    logger.info("[BoletoService] ✅ Boleto avulso emitido: nossoNumero=%s", nosso_numero)
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
