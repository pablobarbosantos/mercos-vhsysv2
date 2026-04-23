import json
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_DIR = Path(__file__).parent.parent / "data"
DB_PATH = _DB_DIR / "boletos.db"

# Máquina de estados: tipo_evento → novo status_atual
_TRANSICOES = {
    "EMITIDO":      "EMITIDO",
    "PAGO_SICOOB":  "LIQUIDADO",
    "PAGO_EXTERNO": "PAGO_EXTERNO",
    "BAIXADO":      "BAIXADO",
    "REEMITIDO":    "EMITIDO",
    "VENCIDO":      "VENCIDO",
    # SINCRONIZADO e CRIADO não alteram status_atual por si só
}


def get_conn() -> sqlite3.Connection:
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS boletos (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                nosso_numero     TEXT    NOT NULL UNIQUE,
                seu_numero       TEXT,
                vhsys_pedido_id  INTEGER,
                cliente_nome     TEXT,
                cliente_doc      TEXT,
                valor            REAL    NOT NULL,
                vencimento       TEXT    NOT NULL,
                linha_digitavel  TEXT,
                codigo_barras    TEXT,
                status_atual     TEXT    NOT NULL DEFAULT 'EMITIDO',
                origem_pagamento TEXT,
                criado_em        TEXT    NOT NULL,
                atualizado_em    TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS eventos_boleto (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                boleto_id  INTEGER NOT NULL REFERENCES boletos(id),
                tipo       TEXT    NOT NULL,
                origem     TEXT    NOT NULL,
                dados      TEXT,
                usuario    TEXT,
                criado_em  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_boletos_vencimento   ON boletos(vencimento);
            CREATE INDEX IF NOT EXISTS idx_boletos_status       ON boletos(status_atual);
            CREATE INDEX IF NOT EXISTS idx_boletos_vhsys        ON boletos(vhsys_pedido_id);
            CREATE INDEX IF NOT EXISTS idx_eventos_boleto_id    ON eventos_boleto(boleto_id);
        """)
    logger.info("DB boletos inicializado em %s", DB_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert_boleto(nosso_numero: str, campos: dict) -> int:
    """Insert ou update. Retorna o id do boleto."""
    agora = _now()
    campos = {k: v for k, v in campos.items() if v is not None}

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM boletos WHERE nosso_numero = ?", (str(nosso_numero),)
        ).fetchone()

        if row is None:
            campos.setdefault("status_atual", "EMITIDO")
            campos["criado_em"] = agora
            campos["atualizado_em"] = agora
            campos["nosso_numero"] = str(nosso_numero)
            cols = ", ".join(campos.keys())
            placeholders = ", ".join("?" * len(campos))
            conn.execute(
                f"INSERT INTO boletos ({cols}) VALUES ({placeholders})",
                list(campos.values()),
            )
            boleto_id = conn.execute(
                "SELECT id FROM boletos WHERE nosso_numero = ?", (str(nosso_numero),)
            ).fetchone()["id"]
        else:
            boleto_id = row["id"]
            campos["atualizado_em"] = agora
            campos.pop("nosso_numero", None)
            campos.pop("criado_em", None)
            sets = ", ".join(f"{k} = ?" for k in campos)
            conn.execute(
                f"UPDATE boletos SET {sets} WHERE id = ?",
                [*campos.values(), boleto_id],
            )

    return boleto_id


def registrar_evento(
    boleto_id: int,
    tipo: str,
    origem: str,
    dados: dict | None = None,
    usuario: str | None = None,
) -> None:
    """Registra evento e atualiza status_atual se a transição existir."""
    agora = _now()
    dados_json = json.dumps(dados, ensure_ascii=False) if dados else None

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO eventos_boleto (boleto_id, tipo, origem, dados, usuario, criado_em)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (boleto_id, tipo, origem, dados_json, usuario, agora),
        )
        novo_status = _TRANSICOES.get(tipo)
        if novo_status:
            conn.execute(
                "UPDATE boletos SET status_atual = ?, atualizado_em = ? WHERE id = ?",
                (novo_status, agora, boleto_id),
            )


def get_boleto(nosso_numero: str) -> dict | None:
    """Retorna boleto + lista de eventos, ou None se não encontrado."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM boletos WHERE nosso_numero = ?", (str(nosso_numero),)
        ).fetchone()
        if row is None:
            return None
        boleto = dict(row)
        eventos = conn.execute(
            "SELECT * FROM eventos_boleto WHERE boleto_id = ? ORDER BY criado_em ASC",
            (boleto["id"],),
        ).fetchall()
        boleto["eventos"] = [dict(e) for e in eventos]
    return boleto


def listar_boletos(
    status: list[str] | None = None,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    tipo_data: str = "vencimento",   # "vencimento" | "criado_em"
    cliente: str | None = None,
    valor_min: float | None = None,
    valor_max: float | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    where = []
    params: list = []

    if status:
        placeholders = ",".join("?" * len(status))
        where.append(f"status_atual IN ({placeholders})")
        params.extend(status)

    col_data = tipo_data if tipo_data in ("vencimento", "criado_em") else "vencimento"
    if data_inicio:
        where.append(f"{col_data} >= ?")
        params.append(data_inicio)
    if data_fim:
        where.append(f"{col_data} <= ?")
        params.append(data_fim + "T23:59:59Z" if "T" not in data_fim else data_fim)

    if cliente:
        where.append("(cliente_nome LIKE ? OR cliente_doc LIKE ?)")
        params.extend([f"%{cliente}%", f"%{cliente}%"])

    if valor_min is not None:
        where.append("valor >= ?")
        params.append(valor_min)
    if valor_max is not None:
        where.append("valor <= ?")
        params.append(valor_max)

    sql = "SELECT * FROM boletos"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY vencimento DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def checar_duplicidade(vhsys_pedido_id: int) -> bool:
    """True se já existe boleto emitido para esse pedido VHSys."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM boletos WHERE vhsys_pedido_id = ? AND status_atual NOT IN ('BAIXADO')",
            (vhsys_pedido_id,),
        ).fetchone()
    return row is not None


def stats() -> dict:
    """Contagens por status para o dashboard."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status_atual, COUNT(*) as qtd, SUM(valor) as total FROM boletos GROUP BY status_atual"
        ).fetchall()
    result = {"total": 0, "total_valor": 0.0}
    for r in rows:
        result[r["status_atual"].lower()] = {"qtd": r["qtd"], "valor": r["total"] or 0.0}
        result["total"] += r["qtd"]
        result["total_valor"] += r["total"] or 0.0
    return result
