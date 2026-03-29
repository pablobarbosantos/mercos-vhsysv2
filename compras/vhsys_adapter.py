"""
Thin wrapper sobre consulta_vhsys/services/vhsys_adapter.py.
Provê funções específicas para o módulo Compras com log prefix [Compras].
"""

import logging

from consulta_vhsys.services.vhsys_adapter import (
    atualizar_produto,
    lancar_movimento_estoque,
    get_produto,
    requisitar,
)

logger = logging.getLogger(__name__)


def atualizar_custo_produto(vhsys_id: int, valor_unitario: float) -> bool:
    """
    Atualiza o custo (valor_produto) de um produto no VHSys.
    Usa atualizar_produto() do adapter compartilhado.
    """
    ok = atualizar_produto(vhsys_id, valor_unitario)
    if ok:
        logger.info("[Compras] Custo atualizado vhsys_id=%d → R$ %.4f", vhsys_id, valor_unitario)
    else:
        logger.error("[Compras] Falha ao atualizar custo vhsys_id=%d", vhsys_id)
    return ok


def lancar_entrada_compra(vhsys_id: int, quantidade: float,
                          chave_nfe: str, descricao: str) -> bool:
    """
    Lança entrada de estoque referenciando a chave NF-e na observação.
    Usa lancar_movimento_estoque() do adapter compartilhado.
    """
    obs = f"Entrada NF-e {chave_nfe[:8]}...{chave_nfe[-4:]} — {descricao[:50]}"
    ok = lancar_movimento_estoque(vhsys_id, quantidade, obs)
    if ok:
        logger.info(
            "[Compras] Entrada lançada vhsys_id=%d qtd=%.4f | %s",
            vhsys_id, quantidade, obs
        )
    else:
        logger.error(
            "[Compras] Falha ao lançar entrada vhsys_id=%d qtd=%.4f",
            vhsys_id, quantidade
        )
    return ok
