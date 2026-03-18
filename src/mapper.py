"""
Mapper: converte um pedido do Mercos para o payload do vhsys.

⚠️  IMPORTANTE: os campos do vhsys são estimados com base no padrão
da API v2. Na primeira execução, leia os erros 400 com atenção —
o vhsys devolve os campos inválidos no corpo do erro.
Ajuste os nomes dos campos conforme necessário.
"""

import logging
from src import database as db

logger = logging.getLogger(__name__)


def pedido_mercos_para_vhsys(pedido: dict) -> dict | None:
    """
    Converte um pedido Mercos para o payload de POST /pedidos do vhsys.
    Retorna None se não for possível montar o pedido (ex: cliente não mapeado).
    """

    # ── Cliente ──────────────────────────────────────────────
    # Regra Mercos: NÃO usar dados do cliente no pedido para atualizar cadastro.
    # Usar o mapa local cnpj → vhsys_id.
    cliente_mercos = pedido.get("cliente", {})
    cnpj_cpf = cliente_mercos.get("cnpj") or cliente_mercos.get("cpf", "")
    vhsys_cliente_id = db.get_vhsys_cliente_id(cnpj_cpf)

    if not vhsys_cliente_id:
        logger.warning(
            f"[Mapper] Cliente não encontrado no mapa local. "
            f"CNPJ/CPF: {cnpj_cpf} | Pedido Mercos: {pedido.get('id')}"
        )
        return None

    # ── Itens ─────────────────────────────────────────────────
    itens_vhsys = []
    for item in pedido.get("itens", []):
        # Regra Mercos: ignorar itens marcados como excluído
        if item.get("excluido"):
            continue

        codigo_produto = str(item.get("produto", {}).get("codigo") or item.get("codigo_produto", ""))
        vhsys_produto_id = db.get_vhsys_produto_id(codigo_produto)

        if not vhsys_produto_id:
            logger.warning(
                f"[Mapper] Produto não mapeado: {codigo_produto} | Pedido {pedido.get('id')}"
            )
            # Produto não mapeado: pular item (não abortar pedido inteiro)
            continue

        itens_vhsys.append({
            "codigo_produto": vhsys_produto_id,    # ⚠️ confirmar nome do campo
            "quantidade":     float(item.get("quantidade", 1)),
            "valor_unitario": float(item.get("valor_unitario", 0)),
            "desconto":       float(item.get("desconto_percentual", 0)),
            "observacao":     item.get("observacao", ""),
        })

    if not itens_vhsys:
        logger.error(f"[Mapper] Pedido {pedido.get('id')} sem itens válidos. Abortando.")
        return None

    # ── Condição de pagamento ─────────────────────────────────
    # O ID da condição de pagamento no vhsys precisa estar mapeado.
    # Por ora usamos o ID vindo do Mercos como fallback — ajuste se necessário.
    condicao_pagamento_id = (
        pedido.get("condicao_pagamento", {}).get("id")
        or pedido.get("condicao_pagamento_id")
    )

    # ── Observações ───────────────────────────────────────────
    obs_partes = []
    if pedido.get("observacoes"):
        obs_partes.append(pedido["observacoes"])
    obs_partes.append(f"Pedido Mercos #{pedido.get('id')}")
    observacao = " | ".join(obs_partes)

    # ── Payload final ─────────────────────────────────────────
    # ⚠️  Nomes de campos estimados. Valide contra a doc vhsys.
    payload = {
        "codigo_cliente":      vhsys_cliente_id,
        "data_pedido":         _formatar_data(pedido.get("data_criacao") or pedido.get("criado_em")),
        "condicao_pagamento":  condicao_pagamento_id,
        "observacao":          observacao,
        "itens":               itens_vhsys,
    }

    # Endereço de entrega (se houver)
    endereco = pedido.get("endereco_entrega")
    if endereco:
        payload["endereco_entrega"] = {
            "logradouro": endereco.get("rua", ""),
            "numero":     endereco.get("numero", ""),
            "bairro":     endereco.get("bairro", ""),
            "cidade":     endereco.get("cidade", ""),
            "estado":     endereco.get("estado", ""),
            "cep":        endereco.get("cep", ""),
        }

    return payload


def _formatar_data(data_str: str | None) -> str:
    """Converte ISO 8601 para YYYY-MM-DD esperado pelo vhsys."""
    if not data_str:
        from datetime import date
        return date.today().isoformat()
    # "2024-03-15T18:30:00-03:00" → "2024-03-15"
    return data_str[:10]
