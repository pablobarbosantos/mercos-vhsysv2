import logging
from consulta_vhsys.database.database import (
    listar_sujos,
    marcar_limpo,
    upsert_produto,
    get_produto_by_vhsys_id,
    log,
)
from consulta_vhsys.services.vhsys_adapter import (
    listar_produtos_paginado,
    get_produto,
    atualizar_produto,
    lancar_movimento_estoque,
)

logger = logging.getLogger("consulta_vhsys.sync")


def sincronizar_sujos() -> dict:
    """
    Sincroniza produtos com dirty=1 de volta ao VHSys.

    Para cada produto dirty:
      1. Busca estado atual no VHSys
      2. Compara valor_produto com preco_vhsys (baseline da última importação)
         - Se diferentes → CONFLITO: VHSys foi alterado externamente, não sobrescreve
         - Se iguais → envia preco/estoque local para VHSys, limpa dirty

    Retorna:
    {
      "sincronizados": [{"vhsys_id": int, "nome": str}],
      "conflitos":     [{"vhsys_id": int, "nome": str, "preco_local": float,
                         "preco_vhsys_baseline": float, "preco_vhsys_atual": float}],
      "erros":         [{"vhsys_id": int, "nome": str, "erro": str}]
    }
    """
    sujos = listar_sujos()
    sincronizados = []
    conflitos     = []
    erros         = []

    logger.info("[SYNC] %d produto(s) dirty para sincronizar", len(sujos))

    for produto in sujos:
        vhsys_id = produto["vhsys_id"]
        nome     = produto.get("nome", "")

        # Busca estado atual no VHSys
        dados_vhsys = get_produto(vhsys_id)
        if dados_vhsys is None:
            msg = f"Não foi possível obter produto {vhsys_id} do VHSys"
            logger.error("[SYNC] %s", msg)
            erros.append({"vhsys_id": vhsys_id, "nome": nome, "erro": msg})
            log("sync_erro", vhsys_id, msg)
            continue

        preco_vhsys_atual    = float(dados_vhsys.get("valor_produto") or 0)
        preco_vhsys_baseline = float(produto.get("preco_vhsys") or 0)

        estoque_vhsys_atual    = float(dados_vhsys.get("estoque_produto") or dados_vhsys.get("qtde_produto") or 0)
        estoque_vhsys_baseline = float(produto.get("estoque_vhsys") or 0)
        estoque_local          = float(produto.get("estoque") or 0)

        conflito_preco   = abs(preco_vhsys_atual - preco_vhsys_baseline) > 0.01
        conflito_estoque = abs(estoque_vhsys_atual - estoque_vhsys_baseline) > 0.01

        # Tolerância de 0.01 para evitar falsos conflitos por arredondamento
        if conflito_preco or conflito_estoque:
            conflito = {
                "vhsys_id":               vhsys_id,
                "nome":                   nome,
                "preco_local":            produto.get("preco"),
                "preco_vhsys_baseline":   preco_vhsys_baseline,
                "preco_vhsys_atual":      preco_vhsys_atual,
                "estoque_local":          estoque_local,
                "estoque_vhsys_baseline": estoque_vhsys_baseline,
                "estoque_vhsys_atual":    estoque_vhsys_atual,
            }
            conflitos.append(conflito)
            logger.warning(
                "[SYNC] CONFLITO vhsys_id=%d '%s': "
                "preco baseline=%.2f vhsys_atual=%.2f | "
                "estoque baseline=%.2f vhsys_atual=%.2f",
                vhsys_id, nome,
                preco_vhsys_baseline, preco_vhsys_atual,
                estoque_vhsys_baseline, estoque_vhsys_atual,
            )
            log("sync_conflito", vhsys_id,
                f"preco: baseline={preco_vhsys_baseline}, vhsys_atual={preco_vhsys_atual}, local={produto.get('preco')} | "
                f"estoque: baseline={estoque_vhsys_baseline}, vhsys_atual={estoque_vhsys_atual}, local={estoque_local}")
            continue

        # Sem conflito — envia alterações locais
        preco_local = float(produto.get("preco") or preco_vhsys_atual)
        ean_local   = produto.get("ean") or None

        ok = atualizar_produto(vhsys_id, preco_local, ean_local)
        if not ok:
            msg = f"Falha ao atualizar vhsys_id={vhsys_id} no VHSys"
            erros.append({"vhsys_id": vhsys_id, "nome": nome, "erro": msg})
            log("sync_erro", vhsys_id, msg)
            continue

        # Lança movimento de estoque se necessário
        delta = round(estoque_local - estoque_vhsys_atual, 4)
        if abs(delta) > 0.01:
            ok_est = lancar_movimento_estoque(
                vhsys_id, delta,
                obs=f"Sync Consulta VHSys — {'Entrada' if delta > 0 else 'Saida'}",
            )
            if not ok_est:
                msg = f"Falha ao lançar movimento de estoque vhsys_id={vhsys_id}"
                erros.append({"vhsys_id": vhsys_id, "nome": nome, "erro": msg})
                log("sync_erro", vhsys_id, msg)
                continue

        # Baseline atualizado para o estado que acabamos de enviar
        marcar_limpo(vhsys_id, preco_local, estoque_local)
        sincronizados.append({"vhsys_id": vhsys_id, "nome": nome})
        log("sync_ok", vhsys_id, f"preco={preco_local:.2f}, estoque={estoque_local}")
        logger.info("[SYNC] vhsys_id=%d '%s' sincronizado com sucesso", vhsys_id, nome)

    logger.info(
        "[SYNC] Resultado: %d sincronizados, %d conflitos, %d erros",
        len(sincronizados), len(conflitos), len(erros),
    )
    return {"sincronizados": sincronizados, "conflitos": conflitos, "erros": erros}


def atualizar_base() -> dict:
    """
    Atualiza a base local com dados atuais do VHSys.

    - dirty=0: atualiza todos os campos
    - dirty=1: preserva preco/estoque/ean locais, atualiza apenas nome/ativo/baselines
    - Novo produto: insere

    Retorna {"atualizados": int, "inseridos": int, "preservados_dirty": int}
    """
    produtos_vhsys = listar_produtos_paginado()
    atualizados     = 0
    inseridos       = 0
    preservados     = 0

    logger.info("[ATUALIZAR_BASE] Processando %d produtos do VHSys", len(produtos_vhsys))

    for p in produtos_vhsys:
        vhsys_id = p.get("id_produto")
        if not vhsys_id:
            continue

        status_produto = str(p.get("status_produto", "Ativo"))
        ativo  = 1 if status_produto.lower() == "ativo" else 0
        preco  = float(p.get("valor_produto") or 0)
        estoque = float(p.get("estoque_produto") or 0)
        nome    = str(p.get("desc_produto", "")).strip()
        ean     = str(p.get("codigo_barra_produto", "") or "").strip() or None

        existente = get_produto_by_vhsys_id(vhsys_id)

        if existente:
            if existente["dirty"] == 1:
                preservados += 1
            else:
                atualizados += 1

            upsert_produto({
                "vhsys_id":        vhsys_id,
                "nome":            nome,
                "ean":             ean,
                "preco":           preco,
                "preco_vhsys":     preco,
                "estoque":         estoque,
                "estoque_vhsys":   estoque,
                "ativo":           ativo,
            })
        else:
            inseridos += 1
            upsert_produto({
                "vhsys_id":        vhsys_id,
                "nome":            nome,
                "ean":             ean,
                "preco":           preco,
                "preco_vhsys":     preco,
                "estoque":         estoque,
                "estoque_vhsys":   estoque,
                "ativo":           ativo,
            })

    resultado = {
        "atualizados":      atualizados,
        "inseridos":        inseridos,
        "preservados_dirty": preservados,
    }
    log("atualizar_base", None,
        f"atualizados={atualizados}, inseridos={inseridos}, preservados_dirty={preservados}")
    logger.info("[ATUALIZAR_BASE] %s", resultado)
    return resultado
