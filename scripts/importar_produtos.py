"""
Importa produtos do CSV Omie para a tabela produtos_base no sync.db.

Uso:
  python scripts/importar_produtos.py
  python scripts/importar_produtos.py --csv C:/outro/caminho.csv
"""

import csv
import os
import sys
import argparse
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.database import produtos_upsert, produtos_count, init_db, get_conn

CSV_PADRAO = os.path.join(
    os.path.dirname(__file__), "..", "..", "omie_xml", "produtos_1037490.csv"
)

# Conversões padrão que o CSV não indica explicitamente
CONVERSOES_PADRAO = [
    ("FD",  "UN",  10),
    ("CX",  "UN",  12),
    ("CX6", "UN",   6),
    ("CX24","UN",  24),
    ("PCT", "UN",  10),
    ("DZ",  "UN",  12),
    ("KG",  "UN",   1),
    ("LT",  "UN",   1),
    ("ML",  "LT", 0.001),
]


def _limpar(v: str) -> str:
    return (v or "").strip()


def _float_br(v: str) -> float:
    """Converte '1.234,56' ou '1234.56' para float."""
    v = _limpar(v).replace(".", "").replace(",", ".")
    try:
        return float(v) if v else 0.0
    except ValueError:
        return 0.0


def _col(row: dict, *alternativas: str) -> str:
    """Retorna o primeiro campo não-vazio dentre as alternativas."""
    for k in alternativas:
        for real_key in row:
            if re.sub(r"\s*\(\d+\)\s*$", "", real_key).strip().lower() == k.lower():
                val = _limpar(row[real_key])
                if val:
                    return val
    # Fallback: busca parcial
    for k in alternativas:
        for real_key in row:
            if k.lower() in real_key.lower():
                val = _limpar(row[real_key])
                if val:
                    return val
    return ""


def _popular_conversoes():
    """Popula tabela conversoes_unidade com defaults se ainda vazia."""
    with get_conn() as conn:
        existente = conn.execute("SELECT COUNT(*) FROM conversoes_unidade").fetchone()[0]
        if existente:
            return
        for orig, dest, fator in CONVERSOES_PADRAO:
            conn.execute(
                "INSERT OR IGNORE INTO conversoes_unidade (unidade_origem, unidade_destino, fator) VALUES (?,?,?)",
                (orig, dest, fator)
            )
    print(f"[Import] {len(CONVERSOES_PADRAO)} conversões de unidade inseridas.")


def importar(caminho_csv: str, apenas_ativos: bool = True, dry_run: bool = False) -> int:
    caminho_csv = os.path.abspath(caminho_csv)
    if not os.path.exists(caminho_csv):
        print(f"[ERRO] Arquivo não encontrado: {caminho_csv}")
        return 0

    registros: list[dict] = []

    with open(caminho_csv, encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            situacao = _col(row, "Situação (Ativo/Inativo)", "Situacao")
            if apenas_ativos and "Inativo" in situacao:
                continue

            codigo = _col(row, "Código do Produto", "Codigo do Produto")
            if not codigo:
                continue

            nome = _col(row, "Nome do Produto", "Nome do Produto (120)")
            if not nome:
                continue

            registros.append({
                "codigo":          codigo,
                "nome":            nome,
                "tipo":            _col(row, "Tipo (Produto/Servico)") or "Produto",
                "fornecedor":      _col(row, "Fornecedor"),
                "marca":           _col(row, "Marca"),
                "unidade":         _col(row, "Unidade") or "un",
                "estoque_minimo":  _float_br(_col(row, "Estoque Mínimo", "Estoque Minimo")),
                "estoque_maximo":  _float_br(_col(row, "Estoque Máximo", "Estoque Maximo")),
                "estoque_atual":   _float_br(_col(row, "Estoque Atual")),
                "preco_venda":     _float_br(_col(row, "Valor Venda (Tabela Padrão)", "Valor Venda")),
                "preco_custo":     _float_br(_col(row, "Valor Custo")),
                "peso":            _float_br(_col(row, "Peso")),
                "peso_liq":        _float_br(_col(row, "Peso Liq")),
                "ncm":             _col(row, "NCM"),
                "ean":             _col(row, "Código de Barras"),
                "situacao":        "Ativo" if "Ativo" in (situacao or "Ativo") else "Inativo",
                "familia":         _col(row, "ID Categoria", "Categoria"),
            })

    print(f"[Import] {len(registros)} produtos lidos do CSV.")

    if dry_run:
        print("[DRY-RUN] Nenhum dado gravado.")
        return len(registros)

    antes = produtos_count()
    produtos_upsert(registros)
    depois = produtos_count()

    _popular_conversoes()

    print(f"[Import] Produtos antes: {antes} | depois: {depois} | importados: {len(registros)}")
    return len(registros)


if __name__ == "__main__":
    init_db()

    parser = argparse.ArgumentParser(description="Importa produtos do CSV Omie")
    parser.add_argument("--csv", default=CSV_PADRAO, help="Caminho do CSV de produtos")
    parser.add_argument("--todos", action="store_true", help="Importa inativos também")
    parser.add_argument("--dry-run", action="store_true", help="Apenas lê o CSV, não grava")
    args = parser.parse_args()

    total = importar(
        caminho_csv=args.csv,
        apenas_ativos=not args.todos,
        dry_run=args.dry_run,
    )
    print(f"[Import] Concluído: {total} registros processados.")
