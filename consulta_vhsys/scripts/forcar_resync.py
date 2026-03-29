"""
Força re-sync de todos os produtos alterados hoje.
Marca dirty=1 em todos que foram sincronizados nas últimas 24h
e roda o sync imediatamente com verificação.

Uso: python -m consulta_vhsys.scripts.forcar_resync
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")

from consulta_vhsys.database.database import get_conn
from consulta_vhsys.services.sync_service import sincronizar_sujos

def forcar_resync():
    # Mostra todos os produtos com preco/estoque editados (dirty=0 mas com preco_vhsys != preco ou estoque_vhsys != estoque)
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT vhsys_id, nome, preco, preco_vhsys, estoque, estoque_vhsys, dirty
            FROM produtos
            WHERE preco IS NOT NULL OR estoque IS NOT NULL
            ORDER BY vhsys_id
        """).fetchall()

    total = len(rows)
    print(f"\n{total} produtos no banco local.\n")
    print(f"{'vhsys_id':<12} {'dirty':<6} {'preco_local':>12} {'preco_vhsys':>12} {'est_local':>10} {'est_vhsys':>10}  nome")
    print("-" * 100)

    para_resync = []
    for r in rows:
        preco_diff   = abs((r["preco"] or 0) - (r["preco_vhsys"] or 0)) > 0.01
        estoque_diff = abs((r["estoque"] or 0) - (r["estoque_vhsys"] or 0)) > 0.01
        if preco_diff or estoque_diff or r["dirty"] == 1:
            para_resync.append(r["vhsys_id"])
        flag = "* DIFF" if (preco_diff or estoque_diff) else ""
        print(f"{r['vhsys_id']:<12} {r['dirty']:<6} {(r['preco'] or 0):>12.2f} {(r['preco_vhsys'] or 0):>12.2f} {(r['estoque'] or 0):>10.1f} {(r['estoque_vhsys'] or 0):>10.1f}  {r['nome']}  {flag}")

    print(f"\n{len(para_resync)} produto(s) com divergência local vs VHSys (ou dirty=1).")

    if not para_resync:
        print("Nada para re-sincronizar.")
        return

    resp = input("\nMarcar todos como dirty=1 e rodar sync agora? (s/n): ").strip().lower()
    if resp != "s":
        print("Cancelado.")
        return

    with get_conn() as conn:
        conn.execute(
            f"UPDATE produtos SET dirty=1 WHERE vhsys_id IN ({','.join('?' * len(para_resync))})",
            para_resync,
        )
    print(f"{len(para_resync)} produto(s) marcado(s) como dirty=1.\n")

    resultado = sincronizar_sujos()
    print(f"\nResultado: {len(resultado['sincronizados'])} sincronizados, "
          f"{len(resultado['conflitos'])} conflitos, {len(resultado['erros'])} erros")

    if resultado["erros"]:
        print("\nErros:")
        for e in resultado["erros"]:
            print(f"  {e['vhsys_id']} {e['nome']}: {e['erro']}")

if __name__ == "__main__":
    forcar_resync()
