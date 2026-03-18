"""
Banco de controle local (SQLite).
Guarda:
  - Pedidos já processados (evita duplicata — regra obrigatória Mercos)
  - Último timestamp de sincronização por entidade
  - Mapeamento ID Mercos → ID vhsys
  - Status customizados do Mercos
"""

import sqlite3
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "sync.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Cria as tabelas se não existirem."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pedidos_processados (
                mercos_id       INTEGER PRIMARY KEY,
                vhsys_id        TEXT,
                processado_em   TEXT NOT NULL,
                status          TEXT DEFAULT 'ok'  -- ok | erro | duplicata
            );

            CREATE TABLE IF NOT EXISTS sync_timestamps (
                entidade        TEXT PRIMARY KEY,
                ultima_alteracao TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS status_customizados (
                id      INTEGER PRIMARY KEY,
                nome    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mapa_clientes (
                cnpj_cpf        TEXT PRIMARY KEY,
                vhsys_id        INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mapa_produtos (
                mercos_codigo   TEXT PRIMARY KEY,
                vhsys_id        INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS erros_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                entidade        TEXT,
                referencia_id   TEXT,
                erro            TEXT,
                ocorrido_em     TEXT NOT NULL
            );
        """)
    logger.info("[DB] Banco inicializado.")


# ──────────────────────────────────────────────────────────────
# Pedidos
# ──────────────────────────────────────────────────────────────

def pedido_ja_processado(mercos_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM pedidos_processados WHERE mercos_id = ?", (mercos_id,)
        ).fetchone()
    return row is not None


def salvar_pedido_processado(mercos_id: int, vhsys_id: str, status: str = "ok"):
    """
    Regra Mercos: obrigatório gravar ID e timestamp de retorno após POST.
    """
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO pedidos_processados (mercos_id, vhsys_id, processado_em, status)
            VALUES (?, ?, ?, ?)
        """, (mercos_id, str(vhsys_id), datetime.now(timezone.utc).isoformat(), status))
    logger.debug(f"[DB] Pedido Mercos {mercos_id} → vhsys {vhsys_id} salvo.")


def registrar_erro(entidade: str, referencia_id: str, erro: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO erros_log (entidade, referencia_id, erro, ocorrido_em)
            VALUES (?, ?, ?, ?)
        """, (entidade, str(referencia_id), str(erro), datetime.now(timezone.utc).isoformat()))


# ──────────────────────────────────────────────────────────────
# Timestamps de sincronização
# ──────────────────────────────────────────────────────────────

def get_ultimo_timestamp(entidade: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ultima_alteracao FROM sync_timestamps WHERE entidade = ?", (entidade,)
        ).fetchone()
    return row["ultima_alteracao"] if row else None


def salvar_timestamp(entidade: str, timestamp: str):
    """
    Regra Mercos: armazenar ultima_alteracao do último registro
    recebido para usar como alterado_apos na próxima chamada.
    """
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO sync_timestamps (entidade, ultima_alteracao)
            VALUES (?, ?)
        """, (entidade, timestamp))


# ──────────────────────────────────────────────────────────────
# Status customizados
# ──────────────────────────────────────────────────────────────

def salvar_status_customizados(lista: list):
    with get_conn() as conn:
        for s in lista:
            conn.execute(
                "INSERT OR REPLACE INTO status_customizados (id, nome) VALUES (?, ?)",
                (s["id"], s["nome"])
            )


def get_status_id_por_nome(nome: str) -> int | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM status_customizados WHERE nome LIKE ?", (f"%{nome}%",)
        ).fetchone()
    return row["id"] if row else None


# ──────────────────────────────────────────────────────────────
# Mapas de IDs
# ──────────────────────────────────────────────────────────────

def salvar_cliente(cnpj_cpf: str, vhsys_id: int):
    doc = cnpj_cpf.replace(".", "").replace("-", "").replace("/", "")
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO mapa_clientes (cnpj_cpf, vhsys_id) VALUES (?, ?)",
            (doc, vhsys_id)
        )


def get_vhsys_cliente_id(cnpj_cpf: str) -> int | None:
    doc = cnpj_cpf.replace(".", "").replace("-", "").replace("/", "")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT vhsys_id FROM mapa_clientes WHERE cnpj_cpf = ?", (doc,)
        ).fetchone()
    return row["vhsys_id"] if row else None


def salvar_produto(mercos_codigo: str, vhsys_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO mapa_produtos (mercos_codigo, vhsys_id) VALUES (?, ?)",
            (str(mercos_codigo), vhsys_id)
        )


def get_vhsys_produto_id(mercos_codigo: str) -> int | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT vhsys_id FROM mapa_produtos WHERE mercos_codigo = ?",
            (str(mercos_codigo),)
        ).fetchone()
    return row["vhsys_id"] if row else None
