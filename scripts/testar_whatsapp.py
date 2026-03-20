"""
scripts/testar_whatsapp.py
===========================
Testa todas as notificações WhatsApp sem precisar de pedido real.

Uso:
  python scripts/testar_whatsapp.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.whatsapp import get_whatsapp

def main():
    wa = get_whatsapp()

    print("=" * 50)
    print("TESTE WHATSAPP — PABLO AGRO")
    print("=" * 50)

    if not wa.enabled:
        print("\n❌ WhatsApp DESATIVADO.")
        print("Configure no .env:")
        print("  EVOLUTION_API_URL=http://SEU_IP:8080")
        print("  EVOLUTION_API_KEY=sua_chave")
        print("  EVOLUTION_INSTANCE=pablo-agro")
        print("  WHATSAPP_NOTIFY_NUMBER=5534XXXXXXXXX")
        return

    print(f"\n✅ WhatsApp ativado!")
    print(f"   URL:      {wa.base_url}")
    print(f"   Instância:{wa.instance}")
    print(f"   Notificar:{wa.notify_to}")
    print()

    # Teste 1 — Pedido OK
    print("📤 Teste 1: Pedido processado com sucesso...")
    ok = wa.notificar_pedido_ok(
        numero_pedido="PED-9999",
        mercos_id=9999,
        vhsys_id="47820999",
        cliente="AGROPECUARIA TESTE LTDA",
        valor=1580.50,
        condicao="14 / 21",
    )
    print(f"   {'✅ Enviado!' if ok else '❌ Falhou'}\n")

    # Teste 2 — Erro
    print("📤 Teste 2: Alerta de erro...")
    ok = wa.notificar_pedido_erro(
        numero_pedido="PED-8888",
        mercos_id=8888,
        cliente="FAZENDA ERRO LTDA",
        motivo="CNPJ inválido: 00.000.000/0000-00",
    )
    print(f"   {'✅ Enviado!' if ok else '❌ Falhou'}\n")

    # Teste 3 — Resumo diário
    print("📤 Teste 3: Resumo diário...")
    stats = {"hoje": 8, "ok_hoje": 7, "erro_hoje": 1, "total": 47}
    pedidos = [
        {"mercos_id": 9001, "vhsys_id": "47820636", "status": "ok"},
        {"mercos_id": 9002, "vhsys_id": "47820696", "status": "ok"},
        {"mercos_id": 9003, "vhsys_id": "erro",     "status": "erro"},
    ]
    ok = wa.enviar_resumo_diario(stats, pedidos)
    print(f"   {'✅ Enviado!' if ok else '❌ Falhou'}\n")

    # Teste 4 — Confirmação pro cliente (use seu próprio número pra testar)
    print("📤 Teste 4: Confirmação pro cliente...")
    ok = wa.confirmar_pedido_cliente(
        telefone=wa.notify_to,   # usa seu número como destino de teste
        nome_cliente="Pablo",
        numero_pedido="PED-9999",
        valor=1580.50,
        condicao="14 / 21",
    )
    print(f"   {'✅ Enviado!' if ok else '❌ Falhou'}\n")

    print("=" * 50)
    print("Teste concluído! Verifique o WhatsApp.")
    print("=" * 50)

if __name__ == "__main__":
    main()
