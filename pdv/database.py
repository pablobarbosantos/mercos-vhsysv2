"""
PDV — banco de dados SQLite.
Usa o mesmo data/sync.db do sistema principal.
"""

import sqlite3
import os
import sys
from datetime import datetime, timezone

# Resolve caminho do banco: frozen (.exe) vs script
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
                vhsys_id       INTEGER UNIQUE,
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
                vhsys_sync   TEXT DEFAULT 'pendente',
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


# ── Produtos ─────────────────────────────────────────────────────────────────

def buscar_produtos(q: str, limit: int = 20) -> list[dict]:
    """Busca por nome (LIKE), codigo exato ou codigo_barras exato."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, vhsys_id, codigo, codigo_barras, nome, unidade,
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
    """Insere ou atualiza produto (chave: vhsys_id)."""
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO pdv_produtos
                (vhsys_id, codigo, codigo_barras, nome, unidade,
                 preco_base, preco_dinheiro, preco_pix, preco_credito, preco_debito,
                 ativo, atualizado_em)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(vhsys_id) DO UPDATE SET
                codigo        = excluded.codigo,
                codigo_barras = excluded.codigo_barras,
                nome          = excluded.nome,
                unidade       = excluded.unidade,
                preco_base    = excluded.preco_base,
                ativo         = excluded.ativo,
                atualizado_em = excluded.atualizado_em
            """,
            (
                p["vhsys_id"], p.get("codigo"), p.get("codigo_barras"),
                p["nome"], p.get("unidade", "UN"),
                p["preco_base"],
                p.get("preco_dinheiro", p["preco_base"]),
                p.get("preco_pix", p["preco_base"]),
                p.get("preco_credito", p["preco_base"]),
                p.get("preco_debito", p["preco_base"]),
                1 if p.get("ativo", True) else 0,
                agora,
            ),
        )


def salvar_precos(produto_id: int, precos: dict):
    """Atualiza os preços manuais por forma de pagamento."""
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE pdv_produtos
            SET preco_dinheiro = ?,
                preco_pix      = ?,
                preco_credito  = ?,
                preco_debito   = ?
            WHERE id = ?
            """,
            (
                precos.get("dinheiro"),
                precos.get("pix"),
                precos.get("credito"),
                precos.get("debito"),
                produto_id,
            ),
        )


def contar_produtos() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM pdv_produtos WHERE ativo=1").fetchone()[0]


def listar_todos_produtos(limit: int = 500) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, vhsys_id, codigo, codigo_barras, nome, unidade,
                      preco_base, preco_dinheiro, preco_pix, preco_credito, preco_debito
               FROM pdv_produtos WHERE ativo=1 ORDER BY nome LIMIT ?""",
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
            """SELECT v.id, v.data, v.total, v.desconto, v.status, v.vhsys_sync, v.criado_em,
                      GROUP_CONCAT(p.tipo || ':' || p.valor, '|') AS pagamentos
               FROM pdv_vendas v
               LEFT JOIN pdv_pagamentos p ON p.venda_id = v.id
               GROUP BY v.id
               ORDER BY v.id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def atualizar_sync_venda(venda_id: int, status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE pdv_vendas SET vhsys_sync = ? WHERE id = ?",
            (status, venda_id),
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
