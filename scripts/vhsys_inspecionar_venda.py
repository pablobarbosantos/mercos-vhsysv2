"""
Engenharia reversa de uma venda VHSys.

Uso:
    python scripts/vhsys_inspecionar_venda.py <id_frente>

Busca GET /vendas-balcao/{id_frente} e GET /vendas-balcao/{id_frente}/produtos
e imprime o JSON completo para análise.
"""

import sys
import os
import json

# Garante que a raiz do projeto está no path
_ROOT = os.path.join(os.path.dirname(__file__), "..")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"), override=False)

import requests

BASE_URL   = os.getenv("VHSYS_BASE_URL", "https://api.vhsys.com.br/v2")
ACCESS     = os.getenv("VHSYS_ACCESS_TOKEN", "")
SECRET     = os.getenv("VHSYS_SECRET_TOKEN", "")

HEADERS = {
    "access-token":        ACCESS,
    "secret-access-token": SECRET,
    "Cache-Control":       "no-cache",
}


def inspecionar(id_frente: str):
    endpoints = [
        f"vendas-balcao/{id_frente}",
        f"vendas-balcao/{id_frente}/produtos",
    ]
    resultado = {}
    for ep in endpoints:
        url = f"{BASE_URL}/{ep}"
        r = requests.get(url, headers=HEADERS, timeout=30)
        try:
            resultado[ep] = r.json()
        except Exception:
            resultado[ep] = {"status_code": r.status_code, "text": r.text}
        print(f"\n{'='*60}")
        print(f"GET /{ep}  →  HTTP {r.status_code}")
        print(json.dumps(resultado[ep], indent=2, ensure_ascii=False))

    # Salva resultado
    out = os.path.join(os.path.dirname(__file__), f"vhsys_inspecao_{id_frente}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(resultado, f, indent=2, ensure_ascii=False)
    print(f"\nSalvo em: {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python scripts/vhsys_inspecionar_venda.py <id_frente>")
        sys.exit(1)
    inspecionar(sys.argv[1])
