"""
Orquestrador do worker de processamento de NF-e.
Chamado pelo APScheduler a cada 5 minutos.
"""

import logging
import threading

from compras import database as db
from compras.nfe_parser import parse_nfe
from compras.vhsys_adapter import atualizar_custo_produto, lancar_entrada_compra
from consulta_vhsys.database.database import get_produto_by_vhsys_id, set_ean
from consulta_vhsys.services.vhsys_adapter import requisitar as _vhsys_requisitar

logger = logging.getLogger(__name__)

_worker_lock = threading.Lock()


def processar_fila_compras() -> dict:
    """
    Worker principal. Processa até 10 NF-e pendentes por execução.
    Lock garante que não há execuções sobrepostas.
    """
    if not _worker_lock.acquire(blocking=False):
        logger.debug("[Compras/Worker] Já em execução — ignorando")
        return {"processados": 0, "aguardando": 0, "erros": 0, "skipped": True}

    resultado = {"processados": 0, "aguardando": 0, "erros": 0}
    try:
        itens = db.fila_pegar_proximos(limite=10)
        for item in itens:
            fila_id   = item["id"]
            chave_nfe = item["chave_nfe"]
            tentativas = item["tentativas"] + 1
            try:
                status = _processar_nota(fila_id, chave_nfe)
                if status == "concluido":
                    resultado["processados"] += 1
                elif status == "aguardando_mapeamento":
                    resultado["aguardando"] += 1
                else:
                    resultado["erros"] += 1
            except Exception as exc:
                logger.error(
                    "[Compras/Worker] Erro inesperado na nota %s: %s",
                    chave_nfe, exc, exc_info=True
                )
                db.fila_marcar_erro(fila_id, str(exc)[:500], tentativas)
                db.nota_atualizar_status(chave_nfe, "erro", str(exc)[:200])
                resultado["erros"] += 1
    finally:
        _worker_lock.release()

    return resultado


def _processar_nota(fila_id: int, chave_nfe: str) -> str:
    """
    Processa uma única NF-e.
    Retorna: 'concluido' | 'aguardando_mapeamento' | 'erro'
    """
    db.fila_marcar_processando(fila_id)
    tentativas_item = db.fila_pegar_por_id(fila_id)
    tentativas = tentativas_item["tentativas"] if tentativas_item else 1

    nota = db.nota_get(chave_nfe)
    if not nota:
        logger.error("[Compras/Worker] Nota %s não encontrada no banco", chave_nfe)
        db.fila_marcar_erro(fila_id, "Nota não encontrada no banco", tentativas)
        return "erro"

    xml_path = nota.get("xml_path", "")
    if not xml_path:
        db.fila_marcar_erro(fila_id, "xml_path vazio", tentativas)
        db.nota_atualizar_status(chave_nfe, "erro", "xml_path vazio")
        return "erro"

    # Parse do XML
    parsed = parse_nfe(xml_path)
    if parsed is None:
        msg = f"Falha no parse do XML: {xml_path}"
        db.fila_marcar_erro(fila_id, msg, tentativas)
        db.nota_atualizar_status(chave_nfe, "erro", msg)
        return "erro"

    fornecedor_cnpj = parsed["fornecedor"]["cnpj"]
    fornecedor_nome = parsed["fornecedor"]["nome"]

    # Insere itens no banco se ainda não foram inseridos
    itens_db = db.item_listar_por_nota(chave_nfe)
    if not itens_db:
        for it in parsed["itens"]:
            db.item_criar(
                chave_nfe         = chave_nfe,
                codigo_fornecedor = it["codigo_fornecedor"],
                descricao         = it["descricao"],
                quantidade        = it["quantidade"],
                unidade           = it["unidade"],
                valor_unitario    = it["valor_unitario"],
                valor_total       = it["valor_total"],
                ean               = it.get("ean", ""),
            )
        itens_db = db.item_listar_por_nota(chave_nfe)

    # Monta índice EAN por código_fornecedor para uso no loop
    ean_por_codigo = {it["codigo_fornecedor"]: it.get("ean", "") for it in parsed["itens"]}

    # Processa cada item
    aguardando_mapeamento = False
    for item_db in itens_db:
        if item_db.get("ignorado"):
            continue  # ignorado pelo operador
        if item_db["mapeado"]:
            continue  # já processado em execução anterior (idempotente)

        mapeamento = db.mapeamento_get(fornecedor_cnpj, item_db["descricao"])
        if mapeamento is None:
            logger.info(
                "[Compras/Worker] Sem mapeamento para '%s' (CNPJ %s) — aguardando",
                item_db["descricao"], fornecedor_cnpj
            )
            aguardando_mapeamento = True
            continue

        vhsys_id       = mapeamento["vhsys_id"]
        fator          = mapeamento.get("fator_conversao") or 1.0
        qtd_estoque    = item_db["quantidade"] * fator
        valor_unitario = item_db["valor_unitario"]

        # Atualiza custo no VHSys (valor_custo_produto — não toca no preço de venda)
        atualizar_custo_produto(vhsys_id, valor_unitario)
        db.registrar_historico_custo(vhsys_id, valor_unitario, chave_nfe)

        # Atualiza unidade no VHSys se a NF-e trouxer uma
        unidade_nfe = item_db.get("unidade", "")
        if unidade_nfe:
            _vhsys_requisitar("PUT", f"produtos/{vhsys_id}", body={"unidade_produto": unidade_nfe})
            logger.info("[Compras] Unidade atualizada vhsys_id=%d → %s", vhsys_id, unidade_nfe)

        # Atualiza EAN se a NF-e trouxer um e for diferente do cadastrado
        ean_nfe = ean_por_codigo.get(item_db["codigo_fornecedor"], "")
        if ean_nfe:
            _atualizar_ean_se_necessario(vhsys_id, ean_nfe)

        # Lançamento de estoque desativado — balanço sendo feito manualmente
        # ok_estoque = lancar_entrada_compra(...)

        db.item_marcar_mapeado(item_db["id"], vhsys_id)

    # Algum item sem mapeamento → aguarda operador
    if aguardando_mapeamento:
        db.nota_atualizar_status(chave_nfe, "aguardando_mapeamento")
        db.fila_marcar_concluido(fila_id)
        db.log_registrar(chave_nfe, "aguardando_mapeamento",
                         "Um ou mais itens sem mapeamento de produto")
        return "aguardando_mapeamento"

    # Todos os itens processados → cria contas a pagar
    _criar_contas_pagar(chave_nfe, parsed, fornecedor_cnpj, fornecedor_nome)

    db.nota_atualizar_status(chave_nfe, "concluido")
    db.fila_marcar_concluido(fila_id)
    db.log_registrar(
        chave_nfe, "concluido",
        f"NF-e processada: {len(itens_db)} item(s) | "
        f"fornecedor={fornecedor_nome}"
    )
    logger.info("[Compras/Worker] NF-e %s...%s concluída", chave_nfe[:8], chave_nfe[-4:])
    return "concluido"


def _atualizar_ean_se_necessario(vhsys_id: int, ean_nfe: str) -> None:
    """
    Atualiza EAN no consulta_vhsys.db e no VHSys apenas se:
    - a NF-e trouxe um EAN válido
    - e o cadastro está vazio ou com valor diferente
    """
    try:
        produto = get_produto_by_vhsys_id(vhsys_id)
        ean_atual = (produto.get("ean") or "") if produto else ""
        if ean_atual == ean_nfe:
            return  # já correto, não toca

        set_ean(vhsys_id, ean_nfe)
        _vhsys_requisitar("PUT", f"produtos/{vhsys_id}", body={"codigo_barra_produto": ean_nfe})
        logger.info(
            "[Compras] EAN atualizado vhsys_id=%d: '%s' → '%s'",
            vhsys_id, ean_atual or "(vazio)", ean_nfe
        )
    except Exception as exc:
        logger.warning("[Compras] Falha ao atualizar EAN vhsys_id=%d: %s", vhsys_id, exc)


def _criar_contas_pagar(chave_nfe: str, parsed: dict,
                         fornecedor_cnpj: str, fornecedor_nome: str) -> None:
    """Cria registros de contas a pagar. INSERT OR IGNORE garante idempotência."""
    for pag in parsed.get("pagamentos", []):
        if pag.get("valor", 0) <= 0:
            continue
        db.conta_criar(
            chave_nfe        = chave_nfe,
            numero_duplicata = pag.get("numero_duplicata"),
            fornecedor_cnpj  = fornecedor_cnpj,
            fornecedor_nome  = fornecedor_nome,
            valor            = pag["valor"],
            vencimento       = pag.get("vencimento"),
            forma_pagamento  = pag.get("forma_pagamento"),
        )


def reprocessar_nota(chave_nfe: str) -> bool:
    """
    Reseta status da nota para pendente e re-enfileira.
    Chamado pelo endpoint admin POST /compras/api/notas/{chave}/processar.
    """
    nota = db.nota_get(chave_nfe)
    if not nota:
        return False
    db.nota_atualizar_status(chave_nfe, "pendente", None)
    db.fila_enfileirar(chave_nfe)
    db.log_registrar(chave_nfe, "reprocessar_manual", "Reprocessamento manual via painel admin")
    logger.info("[Compras] Nota %s...%s re-enfileirada", chave_nfe[:8], chave_nfe[-4:])
    return True


def processar_nota_agora(chave_nfe: str) -> str:
    """
    Processa a nota imediatamente (síncrono), sem passar pela fila.
    Retorna: 'concluido' | 'aguardando_mapeamento' | 'erro' | 'nao_encontrada'
    """
    nota = db.nota_get(chave_nfe)
    if not nota:
        return "nao_encontrada"
    # Cria entrada temporária na fila para satisfazer _processar_nota
    fila_id = db.fila_enfileirar(chave_nfe)
    db.log_registrar(chave_nfe, "processar_manual", "Processamento manual imediato via painel admin")
    try:
        return _processar_nota(fila_id, chave_nfe)
    except Exception as exc:
        logger.error("[Compras] Erro ao processar nota %s imediatamente: %s", chave_nfe, exc, exc_info=True)
        return "erro"
