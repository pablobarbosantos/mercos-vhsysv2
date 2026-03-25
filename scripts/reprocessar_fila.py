import sqlite3

conn = sqlite3.connect('data/sync.db')

rows = conn.execute(
    "SELECT id, evento, mercos_id, tentativas, ultimo_erro FROM fila_eventos WHERE status='erro_permanente'"
).fetchall()

print("=== Itens em erro_permanente ===")
for r in rows:
    print(f"  id={r[0]} | evento={r[1]} | mercos_id={r[2]} | tentativas={r[3]}")
    print(f"  erro: {r[4]}")

conn.execute(
    "UPDATE fila_eventos SET status='pendente', tentativas=0, ultimo_erro=NULL WHERE status='erro_permanente'"
)
conn.commit()
print(f"\n{len(rows)} item(s) recolocado(s) na fila — worker processa em ate 10s.")
conn.close()
