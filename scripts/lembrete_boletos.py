"""
scripts/lembrete_boletos.py
============================
Verifica contas a receber vencendo em 2 dias e envia lembrete WhatsApp.

Cron — todo dia às 9h:
  0 9 * * * cd /home/ubuntu/mercos-vhsysv2 && python scripts/lembrete_boletos.py >> logs/boletos.log 2>&1
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import requests
from datetime import datetime, timedelta
from src.whatsapp import get_whatsapp

VHSYS_BASE     = os.getenv("VHSYS_BASE_URL", "https://api.vhsys.com/v2")
ACCESS_TOKEN   = os.getenv("VHSYS_ACCESS_TOKEN", "")
SECRET_TOKEN   = os.getenv("VHSYS_SECRET_TOKEN", "")

HEADERS = {
    "access-token":        ACCESS_TOKEN,
    "secret-access-token": SECRET_TOKEN,
    "cache-control":       "no-cache",
}

def buscar_contas_vencendo(dias: int = 2) -> list:
    """Busca contas a receber vencendo nos próximos N dias."""
    data_alvo = (datetime.now() + timedelta(days=dias)).strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            f"{VHSYS_BASE}/contas-receber",
            headers=HEADERS,
            params={
                "vencimento_rec": data_alvo,
                "liquidado_rec":  "Nao",
                "limit":          100,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("data", [])
        else:
            print(f"[ERRO] VHSys retornou HTTP {resp.status_code}: {resp.text[:200]}")
            return []
    except Exception as e:
        print(f"[ERRO] Falha ao buscar contas: {e}")
        return []

def buscar_telefone_cliente(id_cliente: str) -> str:
    """Busca telefone do cliente no VHSys."""
    try:
        resp = requests.get(
            f"{VHSYS_BASE}/clientes/{id_cliente}",
            headers=HEADERS,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [{}])
            cliente = data[0] if data else {}
            # Tenta fone_cliente, celular_cliente
            return cliente.get("celular_cliente") or cliente.get("fone_cliente", "")
        return ""
    except Exception:
        return ""

def main():
    print(f"[{datetime.now()}] Verificando boletos vencendo em 2 dias...")
    wa = get_whatsapp()
    contas = buscar_contas_vencendo(dias=2)

    if not contas:
        print("Nenhum boleto vencendo em 2 dias.")
        return

    print(f"{len(contas)} conta(s) encontrada(s).")

    for conta in contas:
        id_cliente    = str(conta.get("id_cliente", ""))
        nome_cliente  = conta.get("nome_cliente", "Cliente")
        valor         = float(conta.get("valor_rec", 0))
        vencimento    = conta.get("vencimento_rec", "")
        n_documento   = conta.get("n_documento_rec", "")
        forma         = conta.get("forma_pagamento", "")

        # Só lembra boletos (não PIX/dinheiro)
        if forma.upper() not in ("BOLETO", "DUPLICATA"):
            print(f"  Pulando {nome_cliente} — forma: {forma}")
            continue

        telefone = buscar_telefone_cliente(id_cliente)

        ok = wa.lembrete_boleto(
            telefone=telefone,
            nome_cliente=nome_cliente,
            numero_pedido=n_documento,
            valor=valor,
            vencimento=vencimento,
        )

        status = "✅ enviado" if ok else "❌ sem telefone/falha"
        print(f"  {nome_cliente} | R$ {valor:.2f} | venc. {vencimento} | {status}")

if __name__ == "__main__":
    main()
