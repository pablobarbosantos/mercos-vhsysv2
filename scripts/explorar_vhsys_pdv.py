"""
Engenharia reversa — Vendas Balcão VHSys
=========================================
Uso:
  python scripts/explorar_vhsys_pdv.py --varrer
      Descobre quais endpoints de PDV/Vendas existem e aceitam o token.

  python scripts/explorar_vhsys_pdv.py --capturar
      Busca os registros mais recentes nos endpoints que funcionaram
      e salva o JSON completo em scripts/vhsys_pdv_captura.json

Rode --varrer primeiro. Depois crie uma venda manualmente no VHSys
e rode --capturar para ver a estrutura exata.
"""

import os, sys, json, argparse
from datetime import datetime

# ── Localiza e carrega o .env da raiz do projeto ─────────────────────────────
_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _ROOT)
_env = os.path.join(_ROOT, ".env")
if os.path.exists(_env):
    from dotenv import load_dotenv
    load_dotenv(_env)

import requests

BASE_URL = os.getenv("VHSYS_BASE_URL", "https://api.vhsys.com.br/v2").rstrip("/")
HEADERS  = {
    "access-token":        os.getenv("VHSYS_ACCESS_TOKEN", ""),
    "secret-access-token": os.getenv("VHSYS_SECRET_TOKEN", ""),
    "Cache-Control":       "no-cache",
    "Content-Type":        "application/json",
}

# Endpoints candidatos para o módulo Vendas Balcão / PDV
CANDIDATOS = [
    "vendas-balcao",
    "vendas",
    "pdv",
    "balcao",
    "caixa",
    "nfce",
    "orcamentos",
    "pedidos",
    "notas-fiscais",
    "notas",
    "saidas",
]

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "vhsys_pdv_captura.json")

# ─────────────────────────────────────────────────────────────────────────────

def _get(endpoint, params=None):
    url = f"{BASE_URL}/{endpoint}"
    try:
        r = requests.get(url, headers=HEADERS, params=params or {}, timeout=15)
        return r.status_code, r.text
    except Exception as e:
        return None, str(e)


def varrer():
    print(f"\n{'='*60}")
    print(f"  Varrendo endpoints — {BASE_URL}")
    print(f"{'='*60}\n")

    funcionaram = []

    for ep in CANDIDATOS:
        status, body = _get(ep, {"limit": 1})
        if status is None:
            print(f"  --{ep:30s}  ERRO DE REDE: {body[:80]}")
            continue

        try:
            data = json.loads(body)
            code = data.get("code", "?")
            msg  = data.get("msg", "")
            keys = list(data.keys())
        except Exception:
            data, code, msg, keys = {}, "?", body[:80], []

        if status == 200 and code == 200:
            print(f"  OK{ep:30s}  HTTP {status}  code={code}  chaves={keys}")
            funcionaram.append(ep)
        elif status == 403:
            print(f"  --{ep:30s}  HTTP {status}  FORBIDDEN (sem permissão no token)")
        elif status == 404 or code == 404:
            print(f"  --{ep:30s}  HTTP {status}  NOT FOUND")
        else:
            print(f"  ??{ep:30s}  HTTP {status}  code={code}  msg={str(msg)[:60]}")

    print()
    if funcionaram:
        print(f"Endpoints OK: {funcionaram}")
        print("Agora crie uma venda manualmente no VHSys e rode --capturar")
    else:
        print("Nenhum endpoint funcionou. Verifique o token e a URL base no .env")
    print()

    # Salva lista de funcionaram para --capturar usar
    captura = {"varrido_em": datetime.now().isoformat(), "funcionaram": funcionaram, "capturas": {}}
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(captura, f, ensure_ascii=False, indent=2)
    print(f"Resultado salvo em: {OUTPUT_FILE}\n")


def capturar():
    print(f"\n{'='*60}")
    print(f"  Capturando registros recentes — {BASE_URL}")
    print(f"{'='*60}\n")

    # Carrega lista de endpoints que funcionaram (do --varrer)
    funcionaram = CANDIDATOS  # fallback: tenta todos
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            prev = json.load(f)
        if prev.get("funcionaram"):
            funcionaram = prev["funcionaram"]
            print(f"Usando endpoints do --varrer: {funcionaram}\n")

    capturas = {}

    for ep in funcionaram:
        print(f"  → GET {ep} (limit=5) ...")
        status, body = _get(ep, {"limit": 5})
        if status is None:
            print(f"     ERRO: {body}")
            continue

        try:
            data = json.loads(body)
        except Exception:
            print(f"     Resposta não é JSON: {body[:200]}")
            continue

        code = data.get("code")
        if code != 200:
            print(f"     Ignorado (code={code})")
            continue

        registros = data.get("data", [])
        print(f"     {len(registros)} registro(s) encontrado(s)")

        if registros:
            # Mostra campos do primeiro registro
            primeiro = registros[0]
            print(f"     Campos: {list(primeiro.keys())}")
            for k, v in primeiro.items():
                print(f"       {k}: {repr(v)}")

        capturas[ep] = data
        print()

    resultado = {
        "capturado_em":  datetime.now().isoformat(),
        "base_url":      BASE_URL,
        "funcionaram":   funcionaram,
        "capturas":      capturas,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    print(f"\nJSON completo salvo em: {OUTPUT_FILE}")
    print("Compartilhe o conteúdo desse arquivo para análise.\n")


# ─────────────────────────────────────────────────────────────────────────────

def detalhar(id_frente: int):
    """Busca detalhe de uma venda-balcao pelo id_frente, incluindo tentativas de sub-endpoints de itens."""
    print(f"\n{'='*60}")
    print(f"  Detalhando venda-balcao id_frente={id_frente}")
    print(f"{'='*60}\n")

    resultado = {}

    # Tenta endpoint direto
    candidatos = [
        f"vendas-balcao/{id_frente}",
        f"vendas-balcao/{id_frente}/itens",
        f"vendas-balcao/{id_frente}/produtos",
    ]

    for ep in candidatos:
        status, body = _get(ep)
        print(f"  GET {ep} -> HTTP {status}")
        try:
            data = json.loads(body)
        except Exception:
            print(f"     Resposta nao-JSON: {body[:200]}\n")
            continue

        code = data.get("code")
        print(f"     code={code}  chaves={list(data.keys())}")

        if code == 200:
            registros = data.get("data", data)
            if isinstance(registros, list) and registros:
                print(f"     {len(registros)} item(ns):")
                for item in registros:
                    for k, v in item.items():
                        print(f"       {k}: {repr(v)}")
                    print()
            elif isinstance(registros, dict):
                for k, v in registros.items():
                    print(f"       {k}: {repr(v)}")
            resultado[ep] = data
        else:
            print(f"     msg={data.get('msg','')}")
        print()

    # Salva
    saida = os.path.join(os.path.dirname(__file__), f"vhsys_detalhe_{id_frente}.json")
    with open(saida, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"Salvo em: {saida}\n")


def _post_raw(endpoint: str, body: dict):
    url = f"{BASE_URL}/{endpoint}"
    import json as _json
    print(f"  POST {url}")
    print(f"  BODY: {_json.dumps(body, ensure_ascii=False, indent=2)}")
    try:
        r = requests.post(url, headers=HEADERS, json=body, timeout=15)
        print(f"  HTTP {r.status_code}")
        print(f"  RESP: {r.text}")
        return r.status_code, r.text
    except Exception as e:
        print(f"  ERRO: {e}")
        return None, str(e)


def testar_post(id_produto_vhsys: int):
    """
    Testa criacao de uma venda-balcao minimal com 1 produto.
    ATENCAO: cria dados reais no VHSys!
    Uso: --testar-post {vhsys_id_produto}
    """
    print(f"\n{'='*60}")
    print(f"  TESTE POST vendas-balcao (produto vhsys_id={id_produto_vhsys})")
    print(f"  ATENCAO: vai criar uma venda real no VHSys!")
    print(f"{'='*60}\n")

    # Tenta 1: POST com produtos embedded
    body_v1 = {
        "id_cliente": 0,
        "valor_total_produtos": "1.00",
        "desconto_pedido": "0.00",
        "acrescimo_pedido": "0.00",
        "valor_total_nota": "1.00",
        "tipo_pagamento": 1,
        "forma_pagamento": "Dinheiro",
        "valor_recebido": "1.00",
        "troco_pedido": "0.00",
        "condicao_pagamento": 1,
        "obs_pedido": "TESTE API PDV - pode apagar",
        "produtos": [
            {
                "id_produto": id_produto_vhsys,
                "qtde_produto": "1",
                "valor_unit_produto": "1.00",
                "valor_total_produto": "1.00",
            }
        ],
    }

    print("[Tentativa 1] POST com produtos embedded:")
    status, body = _post_raw("vendas-balcao", body_v1)
    print()

    if status == 200:
        import json as _json
        try:
            data = _json.loads(body)
            if data.get("code") == 200:
                id_frente = data.get("data", {})
                print(f"SUCESSO! Venda criada. Resposta completa acima.")
                return
        except Exception:
            pass

    # Tenta 2: POST sem produtos
    body_v2 = {k: v for k, v in body_v1.items() if k != "produtos"}
    print("[Tentativa 2] POST sem produtos (só header):")
    _post_raw("vendas-balcao", body_v2)
    print()
    print("Se uma das tentativas retornou code=200, temos o formato correto.")
    print("Se retornou erro, analise a mensagem para ajustar os campos.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Explorar endpoints VHSys PDV")
    parser.add_argument("--varrer",   action="store_true", help="Descobre quais endpoints existem")
    parser.add_argument("--capturar", action="store_true", help="Captura registros recentes para análise")
    parser.add_argument("--detalhar", type=int, metavar="ID_FRENTE", help="Detalha uma venda-balcao pelo id_frente")
    parser.add_argument("--testar-post", type=int, metavar="VHSYS_ID_PRODUTO", dest="testar_post",
                        help="Testa POST de venda-balcao com 1 produto (cria dado REAL no VHSys)")
    args = parser.parse_args()

    if not args.varrer and not args.capturar and not args.detalhar and not args.testar_post:
        parser.print_help()
        sys.exit(0)

    if args.varrer:
        varrer()
    if args.capturar:
        capturar()
    if args.detalhar:
        detalhar(args.detalhar)
    if args.testar_post:
        testar_post(args.testar_post)
