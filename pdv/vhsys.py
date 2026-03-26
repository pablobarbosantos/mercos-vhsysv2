"""
PDV — integração VHSys.

Funções:
  - sincronizar_produtos()   : importa catálogo via GET /produtos
  - baixar_estoque_venda()   : POST /produtos/{id}/estoque (Saida) por item
  - registrar_receita()      : POST /contas-receber para a venda
"""

import os
import sys
import logging
import requests
from datetime import datetime, date

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

_HEADERS = {
    "access-token":        VHSYS_ACCESS,
    "secret-access-token": VHSYS_SECRET,
    "Cache-Control":       "no-cache",
    "Content-Type":        "application/json",
}

_RETRY_STATUS = {429, 500, 502, 503, 504}


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
    url = f"{VHSYS_BASE_URL}/{endpoint.lstrip('/')}"
    for tentativa in range(3):
        try:
            r = requests.post(url, headers=_HEADERS, json=body, timeout=30)
            if r.status_code in _RETRY_STATUS:
                import time; time.sleep(2 ** tentativa)
                continue
            if r.status_code in (400, 404, 422):
                logger.error(f"[VHSYS POST] {endpoint} erro {r.status_code}: {r.text[:200]}")
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning(f"[VHSYS POST] tentativa {tentativa+1} erro: {e}")
            import time; time.sleep(2 ** tentativa)
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


# ── Baixar estoque ────────────────────────────────────────────────────────────

def baixar_estoque_venda(venda_id: int, itens: list[dict]) -> list[str]:
    """
    Para cada item com vhsys_id, lança saída de estoque.
    Retorna lista de erros (vazia = sucesso total).
    """
    erros = []
    for item in itens:
        vid = item.get("produto_id")
        if not vid:
            continue  # produto rascunho, sem vhsys_id

        # Precisamos do vhsys_id real, não o id local
        from pdv.database import get_produto
        prod = get_produto(vid)
        if not prod or not prod.get("vhsys_id"):
            continue

        body = {
            "tipo_estoque":  "Saida",
            "qtde_estoque":  str(float(item["quantidade"])),
            "valor_estoque": str(float(item["preco_unitario"])),
            "obs_estoque":   f"PDV Venda #{venda_id}",
            "identificacao": f"PDV_{venda_id}",
        }
        resp = _post(f"produtos/{prod['vhsys_id']}/estoque", body)
        if not resp or resp.get("code") != 200:
            erros.append(f"Estoque produto {prod['nome']}: {resp}")

    return erros


# ── Registrar receita ─────────────────────────────────────────────────────────

_FORMA_MAP = {
    "dinheiro": "Dinheiro",
    "pix":      "Pix",
    "credito":  "Cartão de Crédito",
    "debito":   "Cartão de Débito",
}


def registrar_receita(venda_id: int, total: float, pagamentos: list[dict]) -> str | None:
    """
    Lança receita em /contas-receber para a venda.
    Retorna None em sucesso, ou mensagem de erro.
    """
    # Forma predominante = maior valor
    forma_raw = max(pagamentos, key=lambda x: x["valor"])["tipo"] if pagamentos else "dinheiro"
    forma_vhsys = _FORMA_MAP.get(forma_raw, "Dinheiro")
    hoje = date.today().isoformat()

    body: dict = {
        "nome_conta":     f"PDV Venda #{venda_id}",
        "vencimento_rec": hoje,
        "data_emissao":   hoje,
        "valor_rec":      f"{total:.2f}",
        "valor_pago":     f"{total:.2f}",
        "liquidado_rec":  "Sim",
        "data_pagamento": hoje,
        "forma_pagamento": forma_vhsys,
        "tipo_conta":     forma_vhsys,
        "observacoes_rec": f"Venda PDV #{venda_id} — {', '.join(p['tipo'] for p in pagamentos)}",
    }
    if VHSYS_ID_BANCO:
        body["id_banco"] = int(VHSYS_ID_BANCO)

    resp = _post("contas-receber", body)
    if not resp or resp.get("code") != 200:
        return f"Erro ao registrar receita: {resp}"
    return None


# ── Sync completo pós-venda ───────────────────────────────────────────────────

def sincronizar_venda(venda_id: int):
    """
    Chamado em background thread após criar a venda.
    Baixa estoque + registra receita + atualiza status sync.
    """
    from pdv.database import get_itens_venda, get_pagamentos_venda, atualizar_sync_venda

    itens      = get_itens_venda(venda_id)
    pagamentos = get_pagamentos_venda(venda_id)

    erros_estoque = baixar_estoque_venda(venda_id, itens)
    erro_receita  = registrar_receita(venda_id, sum(p["valor"] for p in pagamentos), pagamentos)

    if erros_estoque or erro_receita:
        logger.warning(f"[PDV/Sync venda {venda_id}] erros: estoque={erros_estoque} receita={erro_receita}")
        atualizar_sync_venda(venda_id, "erro")
    else:
        logger.info(f"[PDV/Sync venda {venda_id}] OK")
        atualizar_sync_venda(venda_id, "ok")
