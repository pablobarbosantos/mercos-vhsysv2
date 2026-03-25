"""
Script auxiliar para os testes que precisam de SQL ou lógica Python.
Uso: python testes_ps.py <teste>
"""
import sys
import sqlite3

cmd = sys.argv[1] if len(sys.argv) > 1 else ""


def get_conn():
    conn = sqlite3.connect("data/sync.db")
    conn.row_factory = sqlite3.Row
    return conn


if cmd == "t2":
    conn = get_conn()
    tabelas = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    print("Tabelas:", tabelas)
    conn.close()

elif cmd == "t3_check":
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, evento, mercos_id, status, tentativas FROM fila_eventos WHERE mercos_id=99999"
    ).fetchall()
    for r in rows:
        print(dict(r))
    if not rows:
        print("(nenhum registro encontrado)")
    conn.close()

elif cmd == "t4_check":
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, status, tentativas, ultimo_erro, proxima_tentativa FROM fila_eventos WHERE mercos_id=99999"
    ).fetchall()
    for r in rows:
        print(dict(r))
    if not rows:
        print("(nenhum registro encontrado)")
    conn.close()

elif cmd == "t5_zerar":
    conn = get_conn()
    conn.execute("UPDATE fila_eventos SET proxima_tentativa=NULL WHERE mercos_id=99999")
    conn.commit()
    conn.close()
    print("proxima_tentativa zerada")

elif cmd == "t5_check":
    conn = get_conn()
    rows = conn.execute(
        "SELECT status, tentativas, proxima_tentativa FROM fila_eventos WHERE mercos_id=99999"
    ).fetchall()
    for r in rows:
        print(dict(r))
    conn.close()

elif cmd == "t6_insert":
    conn = get_conn()
    conn.execute("""
        INSERT INTO fila_eventos (evento, mercos_id, payload_json, status, criado_em, atualizado_em)
        VALUES ('pedido.gerado', 88888, '{}', 'processando', datetime('now'), datetime('now'))
    """)
    conn.commit()
    conn.close()
    print("Item preso em processando inserido.")

elif cmd == "t6_check":
    conn = get_conn()
    r = conn.execute(
        "SELECT status, ultimo_erro FROM fila_eventos WHERE mercos_id=88888"
    ).fetchone()
    if r:
        print(dict(r))
    else:
        print("(nenhum registro encontrado)")
    conn.close()

elif cmd == "t7_setup":
    conn = get_conn()
    conn.execute("""
        UPDATE fila_eventos
        SET tentativas=4, proxima_tentativa=datetime('now','-1 minute')
        WHERE mercos_id=99999
    """)
    conn.commit()
    conn.close()
    print("Configurado para ultima tentativa.")

elif cmd == "t7_check":
    conn = get_conn()
    r = conn.execute(
        "SELECT status, tentativas FROM fila_eventos WHERE mercos_id=99999"
    ).fetchone()
    if r:
        print(dict(r))
    else:
        print("(nenhum registro encontrado)")
    conn.close()

elif cmd == "t10_insert":
    conn = get_conn()
    conn.execute("""
        INSERT OR IGNORE INTO pedidos_fluxo (mercos_id, numero, cliente, valor, recebido_em, status_fluxo)
        VALUES (77777, 'T002', 'Teste', 500, datetime('now'), 'processado')
    """)
    conn.commit()
    conn.close()
    print("Pedido 77777 inserido no fluxo.")

else:
    print(f"Comando desconhecido: {cmd}")
    print("Comandos: t2 t3_check t4_check t5_zerar t5_check t6_insert t6_check t7_setup t7_check t10_insert")
