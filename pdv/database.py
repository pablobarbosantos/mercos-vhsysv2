"""
PDV — banco de dados SQLite.
Usa o mesmo data/sync.db do sistema principal.
"""

import sqlite3
import os
import sys
from datetime import datetime, timezone

if getattr(sys, "frozen", False):
    _BASE = os.path.dirname(sys.executable)
else:
    _BASE = os.path.join(os.path.dirname(__file__), "..")

DB_PATH = os.path.join(_BASE, "data", "sync.db")


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_pdv_tables():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pdv_produtos (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                erp_id         INTEGER UNIQUE,
                codigo         TEXT,
                codigo_barras  TEXT,
                nome           TEXT NOT NULL,
                unidade        TEXT DEFAULT 'UN',
                preco_base     REAL DEFAULT 0,
                preco_dinheiro REAL DEFAULT 0,
                preco_pix      REAL DEFAULT 0,
                preco_credito  REAL DEFAULT 0,
                preco_debito   REAL DEFAULT 0,
                ativo          INTEGER DEFAULT 1,
                atualizado_em  TEXT
            );

            CREATE TABLE IF NOT EXISTS pdv_vendas (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                data         TEXT NOT NULL,
                total        REAL NOT NULL,
                desconto     REAL DEFAULT 0,
                status       TEXT DEFAULT 'concluida',
                erp_sync     TEXT DEFAULT 'pendente',
                criado_em    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pdv_itens (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                venda_id       INTEGER NOT NULL,
                produto_id     INTEGER,
                nome           TEXT NOT NULL,
                quantidade     REAL NOT NULL,
                preco_unitario REAL NOT NULL,
                total          REAL NOT NULL,
                FOREIGN KEY (venda_id) REFERENCES pdv_vendas(id)
            );

            CREATE TABLE IF NOT EXISTS pdv_pagamentos (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                venda_id INTEGER NOT NULL,
                tipo     TEXT NOT NULL,
                valor    REAL NOT NULL,
                FOREIGN KEY (venda_id) REFERENCES pdv_vendas(id)
            );

            CREATE TABLE IF NOT EXISTS pdv_pendentes (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                nome   TEXT NOT NULL,
                data   TEXT NOT NULL,
                status TEXT DEFAULT 'pendente_cadastro'
            );
        """)

        # Migrações incrementais — idempotentes
        _migrar(conn, "ALTER TABLE pdv_produtos ADD COLUMN total_vendido_erp REAL DEFAULT 0")
        _migrar(conn, "ALTER TABLE pdv_produtos ADD COLUMN freq_historico REAL DEFAULT 0")
        _migrar(conn, "ALTER TABLE pdv_produtos ADD COLUMN codigo_balanca TEXT")
        _migrar(conn, "ALTER TABLE pdv_vendas ADD COLUMN sync_erro TEXT")
        _migrar(conn, "ALTER TABLE pdv_vendas ADD COLUMN erp_sync TEXT DEFAULT 'pendente'")
        _migrar(conn, "ALTER TABLE pdv_produtos RENAME COLUMN vhsys_id TO erp_id")
        _migrar(conn, "ALTER TABLE pdv_vendas RENAME COLUMN vhsys_sync TO erp_sync")

        # Remove duplicatas por codigo (ERP remapeou IDs — mantém a linha mais antiga,
        # que preserva preços manuais configurados pelo operador)
        conn.execute("""
            DELETE FROM pdv_produtos
            WHERE codigo IS NOT NULL AND codigo != ''
              AND id NOT IN (
                SELECT MIN(id) FROM pdv_produtos
                WHERE codigo IS NOT NULL AND codigo != ''
                GROUP BY codigo
              )
        """)
        # Para produtos sem código, deduplica por nome
        conn.execute("""
            DELETE FROM pdv_produtos
            WHERE (codigo IS NULL OR codigo = '')
              AND id NOT IN (
                SELECT MIN(id) FROM pdv_produtos
                WHERE codigo IS NULL OR codigo = ''
                GROUP BY nome
              )
        """)
        # Remove linhas sem código quando já existe outra linha com mesmo nome e código preenchido
        conn.execute("""
            DELETE FROM pdv_produtos
            WHERE (codigo IS NULL OR codigo = '')
              AND nome IN (
                SELECT nome FROM pdv_produtos
                WHERE codigo IS NOT NULL AND codigo != ''
              )
        """)


def _migrar(conn, sql: str):
    try:
        conn.execute(sql)
    except Exception:
        pass  # coluna já existe ou renomeada


# ── Produtos ─────────────────────────────────────────────────────────────────

def buscar_produtos(q: str, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, erp_id, codigo, codigo_barras, nome, unidade,
                   preco_base, preco_dinheiro, preco_pix, preco_credito, preco_debito
            FROM pdv_produtos
            WHERE ativo = 1
              AND (
                  LOWER(nome) LIKE LOWER(?)
                  OR codigo = ?
                  OR codigo_barras = ?
              )
            ORDER BY nome
            LIMIT ?
            """,
            (f"%{q}%", q, q, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_produto(produto_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pdv_produtos WHERE id = ?", (produto_id,)
        ).fetchone()
    return dict(row) if row else None


def upsert_produto(p: dict):
    """Insere ou atualiza produto.

    Chave de lookup: `codigo` (estável mesmo quando o ERP remapeia IDs).
    Fallback: `erp_id` para produtos sem código.
    Preços manuais (dinheiro/pix/credito/debito) nunca são sobrescritos pelo sync.
    """
    agora = datetime.now(timezone.utc).isoformat()
    codigo = str(p.get("codigo") or "").strip()
    ativo  = 1 if p.get("ativo", True) else 0

    with get_conn() as conn:
        tv_erp = float(p.get("total_vendido_erp") or 0)

        # Tenta achar pelo codigo interno (chave estável)
        if codigo:
            row = conn.execute(
                "SELECT id FROM pdv_produtos WHERE codigo = ?", (codigo,)
            ).fetchone()
            if row:
                cod_bal = p.get("codigo_balanca")
                if cod_bal:
                    conn.execute(
                        """UPDATE pdv_produtos SET
                               erp_id = ?, codigo_barras = ?, nome = ?,
                               unidade = ?, preco_base = ?, total_vendido_erp = ?,
                               codigo_balanca = ?, ativo = ?, atualizado_em = ?
                           WHERE id = ?""",
                        (
                            p["erp_id"], p.get("codigo_barras"), p["nome"],
                            p.get("unidade", "UN"), p["preco_base"], tv_erp,
                            cod_bal, ativo, agora, row["id"],
                        ),
                    )
                else:
                    conn.execute(
                        """UPDATE pdv_produtos SET
                               erp_id = ?, codigo_barras = ?, nome = ?,
                               unidade = ?, preco_base = ?, total_vendido_erp = ?,
                               ativo = ?, atualizado_em = ?
                           WHERE id = ?""",
                        (
                            p["erp_id"], p.get("codigo_barras"), p["nome"],
                            p.get("unidade", "UN"), p["preco_base"], tv_erp,
                            ativo, agora, row["id"],
                        ),
                    )
                return

        # Fallback: INSERT com ON CONFLICT(erp_id)
        cod_bal = p.get("codigo_balanca")
        conn.execute(
            """
            INSERT INTO pdv_produtos
                (erp_id, codigo, codigo_barras, nome, unidade,
                 preco_base, preco_dinheiro, preco_pix, preco_credito, preco_debito,
                 total_vendido_erp, codigo_balanca, ativo, atualizado_em)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(erp_id) DO UPDATE SET
                codigo            = excluded.codigo,
                codigo_barras     = excluded.codigo_barras,
                nome              = excluded.nome,
                unidade           = excluded.unidade,
                preco_base        = excluded.preco_base,
                total_vendido_erp = excluded.total_vendido_erp,
                codigo_balanca    = CASE WHEN excluded.codigo_balanca IS NOT NULL
                                         THEN excluded.codigo_balanca
                                         ELSE pdv_produtos.codigo_balanca END,
                ativo             = excluded.ativo,
                atualizado_em     = excluded.atualizado_em
            """,
            (
                p["erp_id"], codigo, p.get("codigo_barras"),
                p["nome"], p.get("unidade", "UN"),
                p["preco_base"],
                p.get("preco_dinheiro", p["preco_base"]),
                p.get("preco_pix", p["preco_base"]),
                p.get("preco_credito", p["preco_base"]),
                p.get("preco_debito", p["preco_base"]),
                tv_erp, cod_bal, ativo, agora,
            ),
        )


def salvar_precos(produto_id: int, precos: dict):
    cod_bal = (precos.get("codigo_balanca") or "").strip().lower() or None
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE pdv_produtos
            SET preco_dinheiro = ?,
                preco_pix      = ?,
                preco_credito  = ?,
                preco_debito   = ?,
                codigo_balanca = ?
            WHERE id = ?
            """,
            (
                precos.get("dinheiro"),
                precos.get("pix"),
                precos.get("credito"),
                precos.get("debito"),
                cod_bal,
                produto_id,
            ),
        )


def contar_produtos() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM pdv_produtos WHERE ativo=1").fetchone()[0]


def buscar_produtos_debug(q: str, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, erp_id, codigo, codigo_barras, nome, unidade,
                   preco_base, preco_dinheiro, preco_pix, preco_credito, preco_debito, ativo, atualizado_em
            FROM pdv_produtos
            WHERE LOWER(nome) LIKE LOWER(?) OR codigo = ? OR codigo_barras = ?
            ORDER BY ativo DESC, nome
            LIMIT ?
            """,
            (f"%{q}%", q, q, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def reativar_produto(produto_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE pdv_produtos SET ativo = 1 WHERE id = ?", (produto_id,))


def listar_todos_produtos(limit: int = 9999) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.erp_id, p.codigo, p.codigo_barras, p.nome, p.unidade,
                   p.preco_base, p.preco_dinheiro, p.preco_pix, p.preco_credito, p.preco_debito,
                   p.codigo_balanca,
                   COALESCE(COUNT(i.id), 0)          AS freq_pdv,
                   COALESCE(p.freq_historico, 0)     AS freq_hist,
                   COALESCE(COUNT(i.id), 0) * 10000
                       + COALESCE(p.freq_historico, 0) AS freq
            FROM pdv_produtos p
            LEFT JOIN pdv_itens i ON i.produto_id = p.id
            WHERE p.ativo = 1
            GROUP BY p.id
            ORDER BY freq DESC, p.nome
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Vendas ────────────────────────────────────────────────────────────────────

def criar_venda(total: float, desconto: float, itens: list[dict], pagamentos: list[dict]) -> int:
    agora = datetime.now(timezone.utc).isoformat()
    data_hoje = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pdv_vendas (data, total, desconto, criado_em) VALUES (?,?,?,?)",
            (data_hoje, total, desconto, agora),
        )
        venda_id = cur.lastrowid
        for item in itens:
            conn.execute(
                """INSERT INTO pdv_itens (venda_id, produto_id, nome, quantidade, preco_unitario, total)
                   VALUES (?,?,?,?,?,?)""",
                (
                    venda_id, item.get("produto_id"), item["nome"],
                    item["quantidade"], item["preco_unitario"],
                    item["quantidade"] * item["preco_unitario"],
                ),
            )
        for pag in pagamentos:
            conn.execute(
                "INSERT INTO pdv_pagamentos (venda_id, tipo, valor) VALUES (?,?,?)",
                (venda_id, pag["tipo"], pag["valor"]),
            )
    return venda_id


def listar_vendas(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT v.id, v.data, v.total, v.desconto, v.status, v.erp_sync, v.sync_erro, v.criado_em,
                      GROUP_CONCAT(p.tipo || ':' || p.valor, '|') AS pagamentos
               FROM pdv_vendas v
               LEFT JOIN pdv_pagamentos p ON p.venda_id = v.id
               GROUP BY v.id
               ORDER BY v.id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def atualizar_sync_venda(venda_id: int, status: str, erro: str | None = None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE pdv_vendas SET erp_sync = ?, sync_erro = ? WHERE id = ?",
            (status, erro, venda_id),
        )


def get_itens_venda(venda_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pdv_itens WHERE venda_id = ?", (venda_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_pagamentos_venda(venda_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pdv_pagamentos WHERE venda_id = ?", (venda_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Pendentes ─────────────────────────────────────────────────────────────────

def salvar_pendente(nome: str):
    data_hoje = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO pdv_pendentes (nome, data) VALUES (?,?)",
            (nome, data_hoje),
        )


def listar_pendentes() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pdv_pendentes WHERE status='pendente_cadastro' ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]
