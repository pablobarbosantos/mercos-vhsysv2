"""
Banco de dados do módulo Compras (data/compras.db).
Tabelas: notas_fiscais, notas_fiscais_itens, fila_notas,
         mapeamento_produtos_compra, contas_pagar_compra,
         log_compras, compras_config.
"""

import sqlite3
import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "compras.db")


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
    """Cria todas as tabelas se não existirem."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS notas_fiscais (
                chave_nfe        TEXT PRIMARY KEY,
                numero           TEXT,
                serie            TEXT,
                emitida_em       TEXT,
                fornecedor_cnpj  TEXT,
                fornecedor_nome  TEXT,
                valor_total      REAL,
                status           TEXT DEFAULT 'pendente',
                xml_path         TEXT,
                erro_msg         TEXT,
                criado_em        TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notas_fiscais_itens (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                chave_nfe          TEXT NOT NULL REFERENCES notas_fiscais(chave_nfe),
                codigo_fornecedor  TEXT,
                descricao          TEXT,
                quantidade         REAL,
                unidade            TEXT,
                valor_unitario     REAL,
                valor_total        REAL,
                ean                TEXT,
                vhsys_id           INTEGER,
                mapeado            INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_itens_chave
                ON notas_fiscais_itens(chave_nfe);

            CREATE TABLE IF NOT EXISTS fila_notas (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                chave_nfe          TEXT NOT NULL,
                status             TEXT DEFAULT 'pendente',
                tentativas         INTEGER DEFAULT 0,
                proxima_tentativa  TEXT,
                erro_msg           TEXT,
                criado_em          TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_fila_status
                ON fila_notas(status, proxima_tentativa);

            CREATE TABLE IF NOT EXISTS mapeamento_produtos_compra (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                fornecedor_cnpj  TEXT NOT NULL,
                descricao_nota   TEXT NOT NULL,
                vhsys_id         INTEGER NOT NULL,
                nome_vhsys       TEXT,
                unidade_compra   TEXT,
                fator_conversao  REAL DEFAULT 1.0,
                unidade_estoque  TEXT,
                criado_em        TEXT NOT NULL,
                UNIQUE(fornecedor_cnpj, descricao_nota)
            );

            CREATE TABLE IF NOT EXISTS contas_pagar_compra (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                chave_nfe        TEXT NOT NULL REFERENCES notas_fiscais(chave_nfe),
                numero_duplicata TEXT,
                fornecedor_cnpj  TEXT,
                fornecedor_nome  TEXT,
                valor            REAL,
                vencimento       TEXT,
                forma_pagamento  TEXT,
                status           TEXT DEFAULT 'pendente',
                criado_em        TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_contas_chave
                ON contas_pagar_compra(chave_nfe);

            CREATE TABLE IF NOT EXISTS log_compras (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                chave_nfe TEXT,
                operacao  TEXT NOT NULL,
                detalhes  TEXT,
                criado_em TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS compras_config (
                chave TEXT PRIMARY KEY,
                valor TEXT
            );
        """)
    # Migrações para colunas adicionadas após criação inicial
    with get_conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(notas_fiscais_itens)")}
        if "ean" not in cols:
            conn.execute("ALTER TABLE notas_fiscais_itens ADD COLUMN ean TEXT DEFAULT ''")
        if "ignorado" not in cols:
            conn.execute("ALTER TABLE notas_fiscais_itens ADD COLUMN ignorado INTEGER DEFAULT 0")
    logger.info("[ComprasDB] Banco inicializado em %s", DB_PATH)


# ──────────────────────────────────────────────────────────────────────────────
# Notas fiscais
# ──────────────────────────────────────────────────────────────────────────────

def nota_criar(chave_nfe: str, numero: str, serie: str, emitida_em: str,
               fornecedor_cnpj: str, fornecedor_nome: str,
               valor_total: float, xml_path: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO notas_fiscais
               (chave_nfe, numero, serie, emitida_em, fornecedor_cnpj,
                fornecedor_nome, valor_total, xml_path, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (chave_nfe, numero, serie, emitida_em, fornecedor_cnpj,
             fornecedor_nome, valor_total, xml_path, _now())
        )


def nota_atualizar_status(chave_nfe: str, status: str, erro_msg: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE notas_fiscais SET status=?, erro_msg=? WHERE chave_nfe=?",
            (status, erro_msg, chave_nfe)
        )


def nota_listar(limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notas_fiscais ORDER BY criado_em DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def nota_get(chave_nfe: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM notas_fiscais WHERE chave_nfe=?", (chave_nfe,)
        ).fetchone()
    return dict(row) if row else None


def nota_ja_existe(chave_nfe: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM notas_fiscais WHERE chave_nfe=?", (chave_nfe,)
        ).fetchone()
    return row is not None


def nota_stats() -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as total FROM notas_fiscais GROUP BY status"
        ).fetchall()
    stats = {r["status"]: r["total"] for r in rows}
    stats["total"] = sum(stats.values())
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# Itens de NF-e
# ──────────────────────────────────────────────────────────────────────────────

def item_criar(chave_nfe: str, codigo_fornecedor: str, descricao: str,
               quantidade: float, unidade: str, valor_unitario: float,
               valor_total: float, ean: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO notas_fiscais_itens
               (chave_nfe, codigo_fornecedor, descricao, quantidade, unidade,
                valor_unitario, valor_total, ean)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (chave_nfe, codigo_fornecedor, descricao, quantidade, unidade,
             valor_unitario, valor_total, ean or "")
        )
        return cur.lastrowid


def item_listar_por_nota(chave_nfe: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notas_fiscais_itens WHERE chave_nfe=?", (chave_nfe,)
        ).fetchall()
    return [dict(r) for r in rows]


def item_marcar_mapeado(item_id: int, vhsys_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE notas_fiscais_itens SET mapeado=1, vhsys_id=? WHERE id=?",
            (vhsys_id, item_id)
        )


def item_ignorar(item_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE notas_fiscais_itens SET ignorado=1, mapeado=1 WHERE id=?",
            (item_id,)
        )


def nota_ignorar(chave_nfe: str) -> None:
    nota_atualizar_status(chave_nfe, "ignorado")
    with get_conn() as conn:
        conn.execute("DELETE FROM fila_notas WHERE chave_nfe=? AND status='pendente'", (chave_nfe,))


# ──────────────────────────────────────────────────────────────────────────────
# Fila de processamento
# ──────────────────────────────────────────────────────────────────────────────

def fila_enfileirar(chave_nfe: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO fila_notas (chave_nfe, status, proxima_tentativa, criado_em)
               VALUES (?, 'pendente', ?, ?)""",
            (chave_nfe, _now(), _now())
        )
        return cur.lastrowid


def fila_pegar_proximos(limite: int = 10) -> list[dict]:
    agora = _now()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM fila_notas
               WHERE status='pendente' AND (proxima_tentativa IS NULL OR proxima_tentativa <= ?)
               ORDER BY criado_em ASC LIMIT ?""",
            (agora, limite)
        ).fetchall()
    return [dict(r) for r in rows]


def fila_marcar_processando(fila_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE fila_notas SET status='processando' WHERE id=?", (fila_id,)
        )


def fila_marcar_concluido(fila_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE fila_notas SET status='concluido' WHERE id=?", (fila_id,)
        )


def fila_marcar_erro(fila_id: int, erro: str, tentativas: int) -> None:
    # Backoff: 60 * (4 ** (tentativas-1)) segundos → 1min, 4min, 16min, 64min
    max_tentativas = int(os.getenv("COMPRAS_MAX_TENTATIVAS", 4))
    if tentativas >= max_tentativas:
        novo_status = "erro_permanente"
        proxima = None
    else:
        novo_status = "pendente"
        delay_seg = 60 * (4 ** (tentativas - 1))
        proxima = (datetime.now(timezone.utc) + timedelta(seconds=delay_seg)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    with get_conn() as conn:
        conn.execute(
            """UPDATE fila_notas
               SET status=?, tentativas=?, proxima_tentativa=?, erro_msg=?
               WHERE id=?""",
            (novo_status, tentativas, proxima, erro[:500], fila_id)
        )


def fila_pegar_por_id(fila_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM fila_notas WHERE id=?", (fila_id,)
        ).fetchone()
    return dict(row) if row else None


def fila_recuperar_travados() -> int:
    """Reseta itens 'processando' para 'pendente' (crash recovery)."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE fila_notas SET status='pendente' WHERE status='processando'"
        )
        return cur.rowcount


def fila_stats() -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as total FROM fila_notas GROUP BY status"
        ).fetchall()
    return {r["status"]: r["total"] for r in rows}


# ──────────────────────────────────────────────────────────────────────────────
# Mapeamentos de produtos
# ──────────────────────────────────────────────────────────────────────────────

def mapeamento_get(fornecedor_cnpj: str, descricao_nota: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM mapeamento_produtos_compra
               WHERE fornecedor_cnpj=? AND descricao_nota=?""",
            (fornecedor_cnpj, descricao_nota.strip())
        ).fetchone()
    return dict(row) if row else None


def mapeamento_upsert(fornecedor_cnpj: str, descricao_nota: str, vhsys_id: int,
                      nome_vhsys: str, unidade_compra: str,
                      fator_conversao: float, unidade_estoque: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO mapeamento_produtos_compra
               (fornecedor_cnpj, descricao_nota, vhsys_id, nome_vhsys,
                unidade_compra, fator_conversao, unidade_estoque, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(fornecedor_cnpj, descricao_nota) DO UPDATE SET
                 vhsys_id=excluded.vhsys_id,
                 nome_vhsys=excluded.nome_vhsys,
                 unidade_compra=excluded.unidade_compra,
                 fator_conversao=excluded.fator_conversao,
                 unidade_estoque=excluded.unidade_estoque""",
            (fornecedor_cnpj, descricao_nota.strip(), vhsys_id, nome_vhsys,
             unidade_compra, fator_conversao, unidade_estoque, _now())
        )


def mapeamento_get_por_vhsys_id(vhsys_id: int) -> dict | None:
    """Retorna o primeiro mapeamento encontrado para este vhsys_id (para obter fator_conversao)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM mapeamento_produtos_compra WHERE vhsys_id=? LIMIT 1",
            (vhsys_id,)
        ).fetchone()
    return dict(row) if row else None


def mapeamento_listar() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM mapeamento_produtos_compra ORDER BY fornecedor_cnpj, descricao_nota"
        ).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────────────────────────
# Contas a pagar
# ──────────────────────────────────────────────────────────────────────────────

def conta_criar(chave_nfe: str, numero_duplicata: str | None,
                fornecedor_cnpj: str, fornecedor_nome: str,
                valor: float, vencimento: str | None,
                forma_pagamento: str | None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO contas_pagar_compra
               (chave_nfe, numero_duplicata, fornecedor_cnpj, fornecedor_nome,
                valor, vencimento, forma_pagamento, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (chave_nfe, numero_duplicata, fornecedor_cnpj, fornecedor_nome,
             valor, vencimento, forma_pagamento, _now())
        )
        return cur.lastrowid


def conta_listar(status: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM contas_pagar_compra WHERE status=? ORDER BY vencimento",
                (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM contas_pagar_compra ORDER BY vencimento"
            ).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────────────────────────
# Log de operações
# ──────────────────────────────────────────────────────────────────────────────

def log_registrar(chave_nfe: str | None, operacao: str, detalhes: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO log_compras (chave_nfe, operacao, detalhes, criado_em) VALUES (?, ?, ?, ?)",
            (chave_nfe, operacao, detalhes, _now())
        )


# ──────────────────────────────────────────────────────────────────────────────
# Config (NSU SEFAZ)
# ──────────────────────────────────────────────────────────────────────────────

def config_get(chave: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT valor FROM compras_config WHERE chave=?", (chave,)
        ).fetchone()
    return row["valor"] if row else None


def config_set(chave: str, valor: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO compras_config (chave, valor) VALUES (?, ?)",
            (chave, valor)
        )


def sefaz_get_ultimo_nsu() -> str:
    return config_get("ultimo_nsu") or "000000000000000"


def sefaz_salvar_ultimo_nsu(nsu: str) -> None:
    config_set("ultimo_nsu", nsu)
