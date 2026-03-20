"""
scripts/resumo_diario.py
========================
Envia resumo diário de pedidos via WhatsApp.
Executar via cron todo dia às 8h:

  0 8 * * * cd /home/ubuntu/mercos-vhsysv2 && python scripts/resumo_diario.py >> logs/resumo.log 2>&1

Ou no Windows Task Scheduler apontando para este script.
"""

import sys
import os

# Adiciona raiz do projeto ao path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import sqlite3
from datetime import datetime
from src.whatsapp import get_whatsapp
from src import database as db

def main():
    print(f"[{datetime.now()}] Iniciando resumo diário...")

    # Stats do dia
    with db.get_conn() as conn:
        hoje_total = conn.execute(
            "SELECT COUNT(*) FROM pedidos_processados WHERE DATE(processado_em) = DATE('now')"
        ).fetchone()[0]

        hoje_ok = conn.execute(
            "SELECT COUNT(*) FROM pedidos_processados WHERE DATE(processado_em) = DATE('now') AND status = 'ok'"
        ).fetchone()[0]

        hoje_erro = conn.execute(
            "SELECT COUNT(*) FROM pedidos_processados WHERE DATE(processado_em) = DATE('now') AND status = 'erro'"
        ).fetchone()[0]

        total_geral = conn.execute(
            "SELECT COUNT(*) FROM pedidos_processados"
        ).fetchone()[0]

        pedidos_hoje = conn.execute(
            """SELECT mercos_id, vhsys_id, status
               FROM pedidos_processados
               WHERE DATE(processado_em) = DATE('now')
               ORDER BY processado_em DESC"""
        ).fetchall()

    stats = {
        "hoje":      hoje_total,
        "ok_hoje":   hoje_ok,
        "erro_hoje": hoje_erro,
        "total":     total_geral,
    }

    pedidos = [{"mercos_id": r[0], "vhsys_id": r[1], "status": r[2]} for r in pedidos_hoje]

    wa = get_whatsapp()
    ok = wa.enviar_resumo_diario(stats, pedidos)

    print(f"[{datetime.now()}] Resumo {'enviado ✅' if ok else 'FALHOU ❌'}")
    print(f"  Hoje: {hoje_total} pedidos | OK: {hoje_ok} | Erro: {hoje_erro}")

if __name__ == "__main__":
    main()
