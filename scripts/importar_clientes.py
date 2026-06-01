"""
Importa clientes do CSV Omie para a tabela clientes_base no sync.db.

Uso:
  python scripts/importar_clientes.py
  python scripts/importar_clientes.py --csv C:/outro/caminho.csv
"""

import csv
import os
import sys
import argparse

# Ajusta path para importar src.database
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.database import clientes_upsert, clientes_count, init_db

CSV_PADRAO = os.path.join(
    os.path.dirname(__file__), "..", "..", "omie_xml", "clientes_omie.csv"
)


def _limpar(v: str) -> str:
    return (v or "").strip()


def _normalizar_cnpj(v: str) -> str:
    """Remove pontuação, retorna só dígitos."""
    return "".join(c for c in (v or "") if c.isdigit())


def importar(caminho_csv: str, apenas_clientes: bool = True, dry_run: bool = False) -> int:
    caminho_csv = os.path.abspath(caminho_csv)
    if not os.path.exists(caminho_csv):
        print(f"[ERRO] Arquivo não encontrado: {caminho_csv}")
        return 0

    registros: list[dict] = []

    with open(caminho_csv, encoding="latin-1") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            tipo_cad = _limpar(row.get("Tipo Cadastro (Cliente/Fornecedor/Ambos)", ""))
            situacao  = _limpar(row.get("Situacao (Ativo/Inativo)", "Ativo"))

            if apenas_clientes and "Cliente" not in tipo_cad:
                continue

            cnpj_cpf = _normalizar_cnpj(row.get("CNPJ/CPF", ""))
            if not cnpj_cpf:
                continue

            registros.append({
                "cnpj_cpf":    cnpj_cpf,
                "razao_social": _limpar(row.get("Razao Social/Nome", "")),
                "fantasia":    _limpar(row.get("Fantasia", "")),
                "endereco":    _limpar(row.get("Endereco", "")),
                "numero":      _limpar(row.get("Numero", "")),
                "bairro":      _limpar(row.get("Bairro", "")),
                "complemento": _limpar(row.get("Complemento", "")),
                "cep":         _limpar(row.get("CEP", "")),
                "cidade":      _limpar(row.get("Cidade", "")),
                "uf":          _limpar(row.get("UF", "")),
                "telefone":    _limpar(row.get("Telefone", "")),
                "celular":     _limpar(row.get("Celular", "")),
                "email":       _limpar(row.get("E-mail", "")),
                "ie":          _limpar(row.get("Inscricao Estadual/RG", "")),
                "situacao":    situacao if situacao else "Ativo",
                "tipo_pessoa": _limpar(row.get("Tipo Pessoa (PJ/PF)", "PJ")),
                "regime_trib": _limpar(row.get("Regime Tributario", "")),
                "vendedor":    _limpar(row.get("Nome Vendedor", "")),
                "obs":         _limpar(row.get("Observacoes", "")),
            })

    print(f"[Import] {len(registros)} clientes lidos do CSV.")

    if dry_run:
        print("[DRY-RUN] Nenhum dado gravado.")
        return len(registros)

    antes = clientes_count()
    clientes_upsert(registros)
    depois = clientes_count()

    print(f"[Import] Clientes antes: {antes} | depois: {depois} | importados: {len(registros)}")
    return len(registros)


if __name__ == "__main__":
    init_db()

    parser = argparse.ArgumentParser(description="Importa clientes do CSV Omie")
    parser.add_argument("--csv", default=CSV_PADRAO, help="Caminho do CSV de clientes")
    parser.add_argument("--todos", action="store_true", help="Importa fornecedores também (default: só clientes)")
    parser.add_argument("--dry-run", action="store_true", help="Apenas lê o CSV, não grava")
    args = parser.parse_args()

    total = importar(
        caminho_csv=args.csv,
        apenas_clientes=not args.todos,
        dry_run=args.dry_run,
    )
    print(f"[Import] Concluído: {total} registros processados.")
