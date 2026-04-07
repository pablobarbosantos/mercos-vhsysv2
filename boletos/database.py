"""
Banco de dados SQLite para o módulo de boletos SICOOB.
Arquivo: data/boletos.db
"""
import json
import logging
import os
import sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "boletos.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS boletos_config (
                id                INTEGER PRIMARY KEY DEFAULT 1,
                especie_titulo    TEXT    DEFAULT 'DM',
                juros_percentual  REAL    DEFAULT 1.0,
                multa_percentual  REAL    DEFAULT 2.0,
                dias_protesto     INTEGER DEFAULT 3,
                dias_baixa        INTEGER DEFAULT 60,
                local_pagamento   TEXT    DEFAULT 'Pagável em qualquer banco até o vencimento',
                codigo_modalidade INTEGER DEFAULT 3,
                carteira          INTEGER DEFAULT 1,
                atualizado_em     TEXT
            );
            INSERT OR IGNORE INTO boletos_config (id) VALUES (1);

            CREATE TABLE IF NOT EXISTS boletos_sequencia (
                id            INTEGER PRIMARY KEY DEFAULT 1,
                ultimo_numero INTEGER DEFAULT 459
            );
            INSERT OR IGNORE INTO boletos_sequencia (id, ultimo_numero) VALUES (1, 459);

            CREATE TABLE IF NOT EXISTS boletos (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                vhsys_conta_id   TEXT    NOT NULL,
                vhsys_nro        TEXT,
                nosso_numero     INTEGER NOT NULL UNIQUE,
                cliente_nome     TEXT    NOT NULL,
                cliente_cpf_cnpj TEXT    NOT NULL,
                valor_nominal    REAL    NOT NULL,
                data_vencimento  TEXT    NOT NULL,
                data_emissao     TEXT    NOT NULL,
                status           TEXT    DEFAULT 'emitido',
                linha_digitavel  TEXT,
                codigo_barras    TEXT,
                qr_code          TEXT,
                pago_em          TEXT,
                valor_pago       REAL,
                sicoob_json      TEXT,
                emitido_em       TEXT    NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_boletos_nosso_numero ON boletos(nosso_numero);
            CREATE INDEX IF NOT EXISTS idx_boletos_vhsys ON boletos(vhsys_conta_id);
            CREATE INDEX IF NOT EXISTS idx_boletos_status ON boletos(status);

            CREATE TABLE IF NOT EXISTS boletos_webhook_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                evento       TEXT,
                nosso_numero INTEGER,
                payload      TEXT,
                processado   INTEGER DEFAULT 0,
                recebido_em  TEXT    NOT NULL
            );
        """)
    logger.info("[Boletos] Banco inicializado em %s", _DB_PATH)


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

def get_config() -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM boletos_config WHERE id=1").fetchone()
        return dict(row) if row else {}


def save_config(fields: dict):
    fields["atualizado_em"] = datetime.now().isoformat(timespec="seconds")
    allowed = {
        "especie_titulo", "juros_percentual", "multa_percentual",
        "dias_protesto", "dias_baixa", "local_pagamento",
        "codigo_modalidade", "carteira", "atualizado_em",
    }
    data = {k: v for k, v in fields.items() if k in allowed}
    sets = ", ".join(f"{k}=?" for k in data)
    with get_conn() as conn:
        conn.execute(f"UPDATE boletos_config SET {sets} WHERE id=1", list(data.values()))


# ─────────────────────────────────────────────────────────────────────────────
# Sequência nossoNumero
# ─────────────────────────────────────────────────────────────────────────────

def next_nosso_numero() -> int:
    """Retorna o próximo nossoNumero de forma atômica."""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT ultimo_numero FROM boletos_sequencia WHERE id=1").fetchone()
        novo = row["ultimo_numero"] + 1
        conn.execute(
            "UPDATE boletos_sequencia SET ultimo_numero=? WHERE id=1", (novo,)
        )
        conn.commit()
        return novo
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Boletos
# ─────────────────────────────────────────────────────────────────────────────

def salvar_boleto(data: dict) -> int:
    agora = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO boletos
               (vhsys_conta_id, vhsys_nro, nosso_numero, cliente_nome, cliente_cpf_cnpj,
                valor_nominal, data_vencimento, data_emissao, status,
                linha_digitavel, codigo_barras, qr_code, sicoob_json, emitido_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data.get("vhsys_conta_id"),
                data.get("vhsys_nro"),
                data["nosso_numero"],
                data["cliente_nome"],
                data["cliente_cpf_cnpj"],
                data["valor_nominal"],
                data["data_vencimento"],
                data.get("data_emissao", agora[:10]),
                "emitido",
                data.get("linha_digitavel"),
                data.get("codigo_barras"),
                data.get("qr_code"),
                json.dumps(data.get("sicoob_json", {}), ensure_ascii=False),
                agora,
            ),
        )
        return cur.lastrowid


def atualizar_status(nosso_numero: int, status: str, pago_em=None, valor_pago=None):
    with get_conn() as conn:
        conn.execute(
            """UPDATE boletos SET status=?, pago_em=?, valor_pago=?
               WHERE nosso_numero=?""",
            (status, pago_em, valor_pago, nosso_numero),
        )


def marcar_vencidos():
    """Marca como 'vencido' todos os boletos em aberto com vencimento passado."""
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE boletos SET status='vencido'
               WHERE status='emitido' AND data_vencimento < date('now')"""
        )
        if cur.rowcount:
            logger.info("[Boletos] %d boleto(s) marcado(s) como vencido", cur.rowcount)


def listar_boletos(status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM boletos"
    params: list = []
    if status:
        sql += " WHERE status=?"
        params.append(status)
    sql += " ORDER BY emitido_em DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_boleto_by_conta_id(vhsys_conta_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM boletos WHERE vhsys_conta_id=?", (str(vhsys_conta_id),)
        ).fetchone()
        return dict(row) if row else None


def get_boleto_by_nosso_numero(nosso_numero: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM boletos WHERE nosso_numero=?", (nosso_numero,)
        ).fetchone()
        return dict(row) if row else None


def listar_conta_ids_emitidos() -> set[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT vhsys_conta_id FROM boletos").fetchall()
        return {r["vhsys_conta_id"] for r in rows}


# ─────────────────────────────────────────────────────────────────────────────
# Webhook log
# ─────────────────────────────────────────────────────────────────────────────

def log_webhook(evento: str, nosso_numero: int | None, payload: str):
    agora = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO boletos_webhook_log (evento, nosso_numero, payload, recebido_em) VALUES (?,?,?,?)",
            (evento, nosso_numero, payload, agora),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Relatório WhatsApp
# ─────────────────────────────────────────────────────────────────────────────

def stats_relatorio() -> dict:
    """Retorna estatísticas para o relatório matinal."""
    from datetime import date, timedelta
    ontem_18h = f"{(date.today() - timedelta(days=1)).isoformat()}T18:00:00"
    hoje = date.today().isoformat()

    with get_conn() as conn:
        pagos = conn.execute(
            "SELECT * FROM boletos WHERE status='pago' AND pago_em >= ?", (ontem_18h,)
        ).fetchall()
        abertos = conn.execute(
            "SELECT * FROM boletos WHERE status='emitido' AND data_vencimento >= ?", (hoje,)
        ).fetchall()
        vencidos = conn.execute(
            "SELECT * FROM boletos WHERE status='vencido'"
        ).fetchall()

    total_pago = sum(r["valor_pago"] or 0 for r in pagos)
    total_aberto = sum(r["valor_nominal"] for r in abertos)
    total_vencido = sum(r["valor_nominal"] for r in vencidos)

    return {
        "pagos": [dict(r) for r in pagos],
        "abertos": [dict(r) for r in abertos],
        "vencidos": [dict(r) for r in vencidos],
        "total_pago": total_pago,
        "total_aberto": total_aberto,
        "total_vencido": total_vencido,
    }
