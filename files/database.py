"""
Banco de controle local (SQLite).
Guarda:
  - Pedidos já processados (evita duplicata — regra obrigatória Mercos)
  - Último timestamp de sincronização por entidade
  - Mapeamento ID Mercos → ID vhsys
  - Status customizados do Mercos
  - [NOVO] Fluxo operacional de cada pedido
  - [NOVO] Auditoria de sequência (buracos detectados)
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

            -- ────────────────────────────────────────────────────────
            -- NOVO: Fluxo operacional de cada pedido
            -- Etapas: recebido → processado → separado → enviado
            -- ────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS pedidos_fluxo (
                mercos_id       INTEGER PRIMARY KEY,
                numero          TEXT,
                cliente         TEXT,
                valor           REAL DEFAULT 0,
                recebido_em     TEXT NOT NULL,
                processado_em   TEXT,
                separado_em     TEXT,
                enviado_em      TEXT,
                status_fluxo    TEXT DEFAULT 'recebido'
                -- recebido | processado | separado | enviado | cancelado | erro
            );

            -- ────────────────────────────────────────────────────────
            -- NOVO: Registro de buracos na sequência de IDs Mercos
            -- ────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS auditoria_sequencia (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                mercos_id       INTEGER NOT NULL,
                classificacao   TEXT NOT NULL,    -- nao_recebido | cancelado | erro_api
                detectado_em    TEXT NOT NULL,
                resolvido       INTEGER DEFAULT 0,
                resolucao       TEXT,
                resolvido_em    TEXT
            );
        """)
    logger.info("[DB] Banco inicializado.")


# ──────────────────────────────────────────────────────────────
# Pedidos (existente)
# ──────────────────────────────────────────────────────────────

def pedido_ja_processado(mercos_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM pedidos_processados WHERE mercos_id = ?", (mercos_id,)
        ).fetchone()
    return row is not None


def salvar_pedido_processado(mercos_id: int, vhsys_id: str, status: str = "ok"):
    """Regra Mercos: obrigatório gravar ID e timestamp de retorno após POST."""
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
# NOVO: Fluxo operacional
# ──────────────────────────────────────────────────────────────

def fluxo_registrar_recebido(mercos_id: int, numero: str, cliente: str, valor: float = 0):
    """Chamado quando o webhook chega — primeira etapa do fluxo."""
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO pedidos_fluxo
                (mercos_id, numero, cliente, valor, recebido_em, status_fluxo)
            VALUES (?, ?, ?, ?, ?, 'recebido')
        """, (mercos_id, str(numero), cliente, valor, datetime.now(timezone.utc).isoformat()))


def fluxo_marcar_processado(mercos_id: int):
    """Chamado quando pedido é enviado ao VHSys com sucesso."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE pedidos_fluxo
            SET processado_em = ?, status_fluxo = 'processado'
            WHERE mercos_id = ?
        """, (datetime.now(timezone.utc).isoformat(), mercos_id))


def fluxo_marcar_erro(mercos_id: int):
    """Chamado quando falha ao enviar ao VHSys."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE pedidos_fluxo
            SET status_fluxo = 'erro'
            WHERE mercos_id = ?
        """, (mercos_id,))


def fluxo_marcar_separado(mercos_id: int):
    """Chamado via admin ou webhook de status do Mercos."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE pedidos_fluxo
            SET separado_em = ?, status_fluxo = 'separado'
            WHERE mercos_id = ?
        """, (datetime.now(timezone.utc).isoformat(), mercos_id))


def fluxo_marcar_enviado(mercos_id: int):
    """Chamado via admin ou webhook de status do Mercos."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE pedidos_fluxo
            SET enviado_em = ?, status_fluxo = 'enviado'
            WHERE mercos_id = ?
        """, (datetime.now(timezone.utc).isoformat(), mercos_id))


def fluxo_marcar_cancelado(mercos_id: int):
    with get_conn() as conn:
        conn.execute("""
            UPDATE pedidos_fluxo
            SET status_fluxo = 'cancelado'
            WHERE mercos_id = ?
        """, (mercos_id,))


def fluxo_get_pedido(mercos_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pedidos_fluxo WHERE mercos_id = ?", (mercos_id,)
        ).fetchone()
    return dict(row) if row else None


def fluxo_listar(limit: int = 200) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM pedidos_fluxo
            ORDER BY recebido_em DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────────
# NOVO: Auditoria de sequência
# ──────────────────────────────────────────────────────────────

def auditoria_listar_buracos(apenas_abertos: bool = True) -> list[dict]:
    with get_conn() as conn:
        query = "SELECT * FROM auditoria_sequencia"
        if apenas_abertos:
            query += " WHERE resolvido = 0"
        query += " ORDER BY mercos_id DESC"
        rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────────
# Timestamps de sincronização (existente)
# ──────────────────────────────────────────────────────────────

def get_ultimo_timestamp(entidade: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ultima_alteracao FROM sync_timestamps WHERE entidade = ?", (entidade,)
        ).fetchone()
    return row["ultima_alteracao"] if row else None


def salvar_timestamp(entidade: str, timestamp: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO sync_timestamps (entidade, ultima_alteracao)
            VALUES (?, ?)
        """, (entidade, timestamp))


# ──────────────────────────────────────────────────────────────
# Status customizados (existente)
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
# Mapas de IDs (existente)
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
