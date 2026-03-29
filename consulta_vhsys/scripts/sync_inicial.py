"""
Importação inicial: busca todos os produtos do VHSys e salva no SQLite local.

Uso:
    python consulta_vhsys/scripts/sync_inicial.py
    python -m consulta_vhsys.scripts.sync_inicial

Executar UMA VEZ antes de usar o módulo consulta_vhsys.
Pode ser executado novamente sem perda de dados (upsert seguro).
"""
import sys
import os
import logging

# Permite executar diretamente sem instalar o pacote
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "..", "..", "logs", "consulta_vhsys.log"),
            encoding="utf-8",
        ),
    ],
)

from consulta_vhsys.database.database import init_db, upsert_produto
from consulta_vhsys.services.vhsys_adapter import listar_produtos_paginado
from consulta_vhsys.services.duplicidade_service import verificar_duplicidades

logger = logging.getLogger("consulta_vhsys.sync_inicial")


def main():
    print("=" * 60)
    print("consulta_vhsys — Importação Inicial")
    print("=" * 60)

    print("\n[1/3] Inicializando banco de dados...")
    init_db()
    print("      OK")

    print("\n[2/3] Buscando produtos do VHSys (pode demorar)...")
    produtos = listar_produtos_paginado()
    print(f"      {len(produtos)} produtos obtidos")

    if not produtos:
        print("\nNenhum produto retornado. Verifique as credenciais em .env")
        sys.exit(1)

    erros = 0
    for p in produtos:
        vhsys_id = p.get("id_produto")
        if not vhsys_id:
            erros += 1
            continue

        status_produto = str(p.get("status_produto", "Ativo"))
        ativo   = 1 if status_produto.lower() == "ativo" else 0
        preco   = float(p.get("valor_produto") or 0)
        estoque = float(p.get("qtde_produto") or 0)
        nome    = str(p.get("desc_produto", "")).strip()
        ean     = str(p.get("codigo_barra_produto", "") or "").strip() or None

        upsert_produto({
            "vhsys_id":      vhsys_id,
            "nome":          nome,
            "ean":           ean,
            "preco":         preco,
            "preco_vhsys":   preco,
            "estoque":       estoque,
            "estoque_vhsys": estoque,
            "ativo":         ativo,
        })

    importados = len(produtos) - erros
    print(f"      {importados} produtos importados ({erros} ignorados por id inválido)")
    logger.info("Importação inicial: %d produtos importados, %d ignorados", importados, erros)

    print("\n[3/3] Verificando duplicidades...")
    conflitos = verificar_duplicidades()

    if not conflitos:
        print("      Nenhuma duplicidade encontrada")
    else:
        print(f"      ATENÇÃO: {len(conflitos)} grupo(s) de duplicidade encontrado(s):")
        for c in conflitos:
            nomes_produtos = [p.get("nome", "?") for p in c["produtos"]]
            print(f"        [{c['tipo'].upper()}] '{c['valor']}' → {', '.join(nomes_produtos)}")
        print("\n      Use duplicidade_service.resolver_duplicidade_ean() ou")
        print("      duplicidade_service.resolver_duplicidade_nome() para resolver.")

    print("\n" + "=" * 60)
    print(f"Importação concluída: {importados} produtos")
    if conflitos:
        print(f"ATENÇÃO: {len(conflitos)} duplicidade(s) pendente(s) de resolução")
    print("=" * 60)


if __name__ == "__main__":
    main()
