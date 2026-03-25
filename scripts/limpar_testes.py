import sqlite3

conn = sqlite3.connect('data/sync.db')

# Ver erro permanente
print("=== ERRO PERMANENTE ===")
rows = conn.execute(
    "SELECT id, evento_tipo, tentativas, erro_msg FROM fila_eventos WHERE status='erro_permanente'"
).fetchall()
for r in rows:
    print(r)

# Limpar pedidos de teste (numero < 200)
r1 = conn.execute(
    "DELETE FROM pedidos_fluxo WHERE CAST(numero AS INTEGER) < 200 AND numero NOT LIKE '%/%'"
).rowcount

# Marcar gaps falsos como resolvidos (mercos_id < 200)
r2 = conn.execute(
    "UPDATE auditoria_sequencia SET resolvido=1 WHERE mercos_id < 200"
).rowcount

conn.commit()
print(f"\nPedidos teste removidos: {r1}")
print(f"Gaps falsos resolvidos: {r2}")
conn.close()
