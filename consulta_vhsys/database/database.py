import os
import sys
import sqlite3
import logging
from datetime import datetime, timezone

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

DB_PATH = os.path.join(_BASE_DIR, "data", "consulta_vhsys.db")

logger = logging.getLogger("consulta_vhsys.database")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS produtos (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                vhsys_id         INTEGER UNIQUE NOT NULL,
                nome             TEXT,
                ean              TEXT,
                preco            REAL,
                preco_vhsys      REAL,
                estoque          REAL,
                estoque_vhsys    REAL,
                atualizado_em    DATETIME,
                updated_at_vhsys DATETIME,
                dirty            INTEGER DEFAULT 0,
                ativo            INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS log_operacoes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                operacao   TEXT NOT NULL,
                produto_id INTEGER,
                detalhes   TEXT,
                criado_em  DATETIME DEFAULT (datetime('now'))
            );
        """)
    logger.info("[DB] Banco inicializado: %s", DB_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def upsert_produto(p: dict) -> None:
    """INSERT OR REPLACE preservando dirty/ean/preco/estoque quando dirty=1."""
    vhsys_id = p["vhsys_id"]
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT dirty, ean, preco, estoque FROM produtos WHERE vhsys_id = ?",
            (vhsys_id,)
        ).fetchone()

        if existing and existing["dirty"] == 1:
            # Preserva edições locais — só atualiza metadados do VHSys
            conn.execute("""
                UPDATE produtos
                SET nome          = ?,
                    preco_vhsys   = ?,
                    estoque_vhsys = ?,
                    ativo         = ?
                WHERE vhsys_id = ?
            """, (
                p.get("nome"),
                p.get("preco_vhsys"),
                p.get("estoque_vhsys"),
                p.get("ativo", 1),
                vhsys_id,
            ))
        else:
            conn.execute("""
                INSERT INTO produtos
                    (vhsys_id, nome, ean, preco, preco_vhsys, estoque, estoque_vhsys,
                     atualizado_em, updated_at_vhsys, dirty, ativo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(vhsys_id) DO UPDATE SET
                    nome             = excluded.nome,
                    ean              = COALESCE(excluded.ean, ean),
                    preco            = excluded.preco,
                    preco_vhsys      = excluded.preco_vhsys,
                    estoque          = excluded.estoque,
                    estoque_vhsys    = excluded.estoque_vhsys,
                    atualizado_em    = excluded.atualizado_em,
                    updated_at_vhsys = excluded.updated_at_vhsys,
                    dirty            = 0,
                    ativo            = excluded.ativo
            """, (
                vhsys_id,
                p.get("nome"),
                p.get("ean"),
                p.get("preco"),
                p.get("preco_vhsys"),
                p.get("estoque"),
                p.get("estoque_vhsys"),
                p.get("atualizado_em", _now()),
                p.get("updated_at_vhsys", _now()),
                p.get("ativo", 1),
            ))


def get_produto_by_ean(ean: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM produtos WHERE ean = ? AND ativo = 1", (ean,)
        ).fetchone()
    return dict(row) if row else None


def get_produto_by_vhsys_id(vhsys_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM produtos WHERE vhsys_id = ?", (vhsys_id,)
        ).fetchone()
    return dict(row) if row else None


def buscar_por_nome(termo: str) -> list[dict]:
    like = f"%{termo.lower()}%"
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM produtos WHERE lower(nome) LIKE ? AND ativo = 1 ORDER BY nome",
            (like,)
        ).fetchall()
    return [dict(r) for r in rows]


def listar_sujos() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM produtos WHERE dirty = 1"
        ).fetchall()
    return [dict(r) for r in rows]


def marcar_dirty(vhsys_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE produtos SET dirty = 1, atualizado_em = ? WHERE vhsys_id = ?",
            (_now(), vhsys_id)
        )


def marcar_limpo(vhsys_id: int, preco_vhsys: float, estoque_vhsys: float) -> None:
    with get_conn() as conn:
        conn.execute("""
            UPDATE produtos
            SET dirty = 0, preco_vhsys = ?, estoque_vhsys = ?, updated_at_vhsys = ?
            WHERE vhsys_id = ?
        """, (preco_vhsys, estoque_vhsys, _now(), vhsys_id))


def set_ean(vhsys_id: int, ean: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE produtos SET ean = ?, atualizado_em = ? WHERE vhsys_id = ?",
            (ean, _now(), vhsys_id)
        )


def set_preco_estoque(vhsys_id: int, preco: float | None, estoque: float | None) -> None:
    with get_conn() as conn:
        if preco is not None and estoque is not None:
            conn.execute(
                "UPDATE produtos SET preco = ?, estoque = ?, atualizado_em = ? WHERE vhsys_id = ?",
                (preco, estoque, _now(), vhsys_id)
            )
        elif preco is not None:
            conn.execute(
                "UPDATE produtos SET preco = ?, atualizado_em = ? WHERE vhsys_id = ?",
                (preco, _now(), vhsys_id)
            )
        elif estoque is not None:
            conn.execute(
                "UPDATE produtos SET estoque = ?, atualizado_em = ? WHERE vhsys_id = ?",
                (estoque, _now(), vhsys_id)
            )


def set_ativo(vhsys_id: int, ativo: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE produtos SET ativo = ?, atualizado_em = ? WHERE vhsys_id = ?",
            (ativo, _now(), vhsys_id)
        )


def log(operacao: str, produto_id: int | None, detalhes: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO log_operacoes (operacao, produto_id, detalhes) VALUES (?, ?, ?)",
            (operacao, produto_id, detalhes)
        )
