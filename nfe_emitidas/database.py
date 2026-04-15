"""
Banco de dados do módulo NF-e Emitidas.
Usa data/sync.db (mesmo banco principal).
Tabela: nfe_emitidas — tracking de chaves processadas.
"""

import sqlite3
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "sync.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    """Cria tabela se não existir."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS nfe_emitidas (
                chave         TEXT PRIMARY KEY,
                status        TEXT NOT NULL DEFAULT 'pendente',
                erro_msg      TEXT,
                xml_path      TEXT,
                pdf_path      TEXT,
                numero        TEXT,
                serie         TEXT,
                emitida_em    TEXT,
                destinatario  TEXT,
                valor_total   REAL,
                processado_em TEXT
            );
        """)


def registro_criar(chave: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO nfe_emitidas (chave, status, processado_em) VALUES (?,?,?)",
            (chave, "pendente", _now())
        )


def registro_atualizar_ok(chave: str, xml_path: str, pdf_path: str,
                           numero: str = "", serie: str = "",
                           emitida_em: str = "", destinatario: str = "",
                           valor_total: float = 0.0):
    with get_conn() as conn:
        conn.execute(
            """UPDATE nfe_emitidas
               SET status='ok', xml_path=?, pdf_path=?, numero=?, serie=?,
                   emitida_em=?, destinatario=?, valor_total=?, erro_msg=NULL,
                   processado_em=?
               WHERE chave=?""",
            (xml_path, pdf_path, numero, serie, emitida_em, destinatario,
             valor_total, _now(), chave)
        )


def registro_atualizar_erro(chave: str, msg: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE nfe_emitidas SET status='erro', erro_msg=?, processado_em=? WHERE chave=?",
            (msg, _now(), chave)
        )


def registro_listar(limit: int = 500) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM nfe_emitidas ORDER BY processado_em DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def registro_buscar(chave: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM nfe_emitidas WHERE chave=?", (chave,)
        ).fetchone()
    return dict(row) if row else None


def registro_deletar(chave: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM nfe_emitidas WHERE chave=?", (chave,))
