"""
Busca uma venda balcão pelo número do pedido (id_pedido) e inspeciona os detalhes.

Uso:
    python scripts/vhsys_buscar_pedido.py <numero_pedido>
"""

import sys
import os
import json

_ROOT = os.path.join(os.path.dirname(__file__), "..")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"), override=False)

import requests

BASE_URL = os.getenv("VHSYS_BASE_URL", "https://api.vhsys.com.br/v2")
HEADERS  = {
    "access-token":        os.getenv("VHSYS_ACCESS_TOKEN", ""),
    "secret-access-token": os.getenv("VHSYS_SECRET_TOKEN", ""),
    "Cache-Control":       "no-cache",
}


def buscar(numero_pedido: str):
    # Busca lista de vendas-balcao e filtra por id_pedido
    offset = 0
    limit  = 50
    while True:
        r = requests.get(f"{BASE_URL}/vendas-balcao",
                         headers=HEADERS,
                         params={"limit": limit, "offset": offset},
                         timeout=30)
        data = r.json()
        items = data.get("data", [])
        for v in items:
            if str(v.get("id_pedido")) == str(numero_pedido):
                id_frente = v["id_frente"]
                print(f"\nEncontrado: id_frente={id_frente}")
                print(json.dumps(v, indent=2, ensure_ascii=False))

                # Busca produtos
                rp = requests.get(f"{BASE_URL}/vendas-balcao/{id_frente}/produtos",
                                  headers=HEADERS, timeout=30)
                print(f"\n--- Produtos ---")
                print(json.dumps(rp.json(), indent=2, ensure_ascii=False))

                # Salva
                out = os.path.join(os.path.dirname(__file__), f"vhsys_pedido_{numero_pedido}.json")
                with open(out, "w", encoding="utf-8") as f:
                    json.dump({"venda": v, "produtos": rp.json()}, f, indent=2, ensure_ascii=False)
                print(f"\nSalvo em: {out}")
                return

        total = data.get("paging", {}).get("total", 0)
        offset += limit
        if offset >= total or not items:
            print(f"Pedido {numero_pedido} não encontrado (buscados {offset} registros)")
            return


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python scripts/vhsys_buscar_pedido.py <numero_pedido>")
        sys.exit(1)
    buscar(sys.argv[1])
