"""
PDV — integração VHSys.

Funções:
  - sincronizar_produtos()   : importa catálogo via GET /produtos
  - criar_venda_balcao()     : POST /vendas-balcao (estoque + contas geridos pelo VHSys)
  - sincronizar_venda()      : orquestra criar_venda_balcao e atualiza status local
"""

import os
import sys
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

# Resolve .env: ao lado do executável (.exe) ou na raiz do projeto (script)
def _carregar_env():
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.join(os.path.dirname(__file__), "..")
    env_path = os.path.join(base, ".env")
    if os.path.exists(env_path):
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)

_carregar_env()

VHSYS_BASE_URL    = os.getenv("VHSYS_BASE_URL", "https://api.vhsys.com.br/v2")
VHSYS_ACCESS      = os.getenv("VHSYS_ACCESS_TOKEN", "")
VHSYS_SECRET      = os.getenv("VHSYS_SECRET_TOKEN", "")
VHSYS_ID_BANCO    = os.getenv("VHSYS_ID_BANCO", "")
# PDV_SYNC_RECEITA=false desativa o lançamento em contas-receber (faça manualmente no VHSys)
PDV_SYNC_RECEITA  = os.getenv("PDV_SYNC_RECEITA", "true").lower() != "false"

_HEADERS = {
    "access-token":        VHSYS_ACCESS,
    "secret-access-token": VHSYS_SECRET,
    "Cache-Control":       "no-cache",
    "Content-Type":        "application/json",
}

_RETRY_STATUS   = {429, 500, 502, 503, 504}
_PERMANENT_FAIL = {400, 401, 403, 404, 422}  # não retenta — erro definitivo


def _get(endpoint: str, params: dict | None = None) -> dict | None:
    url = f"{VHSYS_BASE_URL}/{endpoint.lstrip('/')}"
    for tentativa in range(3):
        try:
            r = requests.get(url, headers=_HEADERS, params=params, timeout=30)
            if r.status_code in _RETRY_STATUS:
                import time; time.sleep(2 ** tentativa)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning(f"[VHSYS GET] tentativa {tentativa+1} erro: {e}")
            import time; time.sleep(2 ** tentativa)
    return None


def _post(endpoint: str, body: dict) -> dict | None:
    import json as _json
    url = f"{VHSYS_BASE_URL}/{endpoint.lstrip('/')}"
    body_str = _json.dumps(body, ensure_ascii=False)
    for tentativa in range(3):
        try:
            r = requests.post(url, headers=_HEADERS, json=body, timeout=30)
            if r.status_code in _RETRY_STATUS:
                import time; time.sleep(2 ** tentativa)
                continue
            if r.status_code in _PERMANENT_FAIL:
                logger.error(
                    f"[VHSYS POST] {endpoint} erro {r.status_code}\n"
                    f"  BODY: {body_str}\n"
                    f"  RESP: {r.text}"
                )
                return None
            r.raise_for_status()
            resp = r.json()
            if resp.get("code") != 200:
                logger.warning(
                    f"[VHSYS POST] {endpoint} code={resp.get('code')}\n"
                    f"  BODY: {body_str}\n"
                    f"  RESP: {r.text}"
                )
            return resp
        except Exception as e:
            logger.warning(f"[VHSYS POST] tentativa {tentativa+1} erro: {e}")
            import time; time.sleep(2 ** tentativa)
    logger.error(f"[VHSYS POST] {endpoint} falhou após 3 tentativas\n  BODY: {body_str}")
    return None


# ── Sincronização de produtos ─────────────────────────────────────────────────

def sincronizar_produtos() -> dict:
    """
    Importa todos os produtos ativos do VHSys e salva em pdv_produtos.
    Preserva preços manuais já configurados pelo usuário.
    Retorna {"importados": N, "erro": msg | None}
    """
    from pdv.database import upsert_produto, get_conn

    offset = 0
    limit  = 250
    total_importados = 0

    while True:
        data = _get("produtos", params={"limit": limit, "offset": offset, "lixeira": "Nao"})
        if not data or data.get("code") != 200:
            msg = f"Erro ao buscar produtos: {data}"
            logger.error(msg)
            return {"importados": total_importados, "erro": msg}

        items = data.get("data", [])
        if not items:
            break

        for p in items:
            status = str(p.get("status_produto", "Ativo"))
            ativo  = status.lower() == "ativo"
            preco  = float(p.get("valor_produto") or 0)

            upsert_produto({
                "vhsys_id":     p.get("id_produto"),
                "codigo":       str(p.get("cod_produto", "") or "").strip(),
                "codigo_barras": str(p.get("codigo_barra_produto", "") or "").strip(),
                "nome":         str(p.get("desc_produto", "")).strip(),
                "unidade":      str(p.get("unidade_produto", "UN") or "UN"),
                "preco_base":   preco,
                "ativo":        ativo,
            })
            total_importados += 1

        if len(items) < limit:
            break
        offset += limit

    logger.info(f"[PDV/Sync] {total_importados} produtos importados do VHSys")
    return {"importados": total_importados, "erro": None}


# ── Criar Venda Balcão ────────────────────────────────────────────────────────

_FORMA_PDV_MAP = {
    "dinheiro": "Dinheiro",
    "pix":      "Pix",
    "credito":  "Cartao de Credito",
    "debito":   "Cartao de Debito",
}


def criar_venda_balcao(venda_id: int, itens: list[dict], pagamentos: list[dict],
                       total: float, desconto: float) -> tuple[int | None, str | None]:
    """
    Cria uma Venda Balcão no VHSys via POST /vendas-balcao.
    O VHSys cuida automaticamente de estoque e contas a receber.
    Retorna (id_frente, None) em sucesso ou (None, mensagem_erro) em falha.
    """
    from pdv.database import get_produto

    # Forma predominante = maior valor pago
    forma_raw   = max(pagamentos, key=lambda x: x["valor"])["tipo"] if pagamentos else "dinheiro"
    forma_vhsys = _FORMA_PDV_MAP.get(forma_raw, "Dinheiro")
    pago_total  = sum(p["valor"] for p in pagamentos)
    troco       = max(0.0, round(pago_total - total, 2))

    # Monta lista de produtos com vhsys_id resolvido
    produtos_body = []
    itens_sem_vhsys = []
    for item in itens:
        vid = item.get("produto_id")
        if not vid:
            itens_sem_vhsys.append(item.get("nome", "?"))
            continue
        prod = get_produto(vid)
        if not prod or not prod.get("vhsys_id"):
            itens_sem_vhsys.append(item.get("nome", f"id={vid}"))
            continue
        qty   = float(item["quantidade"])
        price = float(item["preco_unitario"])
        produtos_body.append({
            "id_produto":         prod["vhsys_id"],
            "qtde_produto":       str(qty),
            "valor_unit_produto": f"{price:.2f}",
            "valor_total_produto": f"{qty * price:.2f}",
        })

    if itens_sem_vhsys:
        logger.warning(f"[PDV/VB venda {venda_id}] itens sem vhsys_id ignorados: {itens_sem_vhsys}")

    if not produtos_body:
        return None, "Nenhum item com vhsys_id — venda não enviada ao VHSys"

    body = {
        "id_cliente":           0,
        "valor_total_produtos": f"{total:.2f}",
        "desconto_pedido":      f"{desconto:.2f}",
        "acrescimo_pedido":     "0.00",
        "valor_total_nota":     f"{total:.2f}",
        "tipo_pagamento":       1,
        "forma_pagamento":      forma_vhsys,
        "valor_recebido":       f"{pago_total:.2f}",
        "troco_pedido":         f"{troco:.2f}",
        "condicao_pagamento":   1,
        "obs_pedido":           f"PDV Venda #{venda_id}",
        "produtos":             produtos_body,
    }

    resp = _post("vendas-balcao", body)
    if not resp or resp.get("code") != 200:
        return None, f"Erro ao criar venda balcao: {resp}"

    id_frente = (resp.get("data") or {}).get("id_frente")
    return id_frente, None


# ── Sync completo pós-venda ───────────────────────────────────────────────────

def sincronizar_venda(venda_id: int):
    """
    Chamado em background thread após criar a venda.
    Cria Venda Balcão no VHSys — estoque e contas a receber são geridos automaticamente pelo VHSys.
    """
    from pdv.database import get_itens_venda, get_pagamentos_venda, atualizar_sync_venda, get_conn

    itens      = get_itens_venda(venda_id)
    pagamentos = get_pagamentos_venda(venda_id)

    # Recupera desconto da venda
    with get_conn() as conn:
        row = conn.execute("SELECT desconto, total FROM pdv_vendas WHERE id = ?", (venda_id,)).fetchone()
    desconto = float(row["desconto"]) if row else 0.0
    total    = float(row["total"])    if row else sum(p["valor"] for p in pagamentos)

    id_frente, erro = criar_venda_balcao(venda_id, itens, pagamentos, total, desconto)

    if erro:
        logger.warning(f"[PDV/Sync venda {venda_id}] {erro}")
        atualizar_sync_venda(venda_id, "erro", erro)
    else:
        logger.info(f"[PDV/Sync venda {venda_id}] OK — id_frente={id_frente}")
        atualizar_sync_venda(venda_id, "ok")
