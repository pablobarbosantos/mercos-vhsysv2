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
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
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
                cidade          TEXT,
                bairro          TEXT,
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

            -- ────────────────────────────────────────────────────────
            -- NOVO: Fila persistente de eventos (anti-perda de pedidos)
            -- status: pendente | processando | ok | erro_permanente
            -- ────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS fila_eventos (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                evento            TEXT NOT NULL,
                mercos_id         INTEGER,
                payload_json      TEXT NOT NULL,
                status            TEXT DEFAULT 'pendente',
                tentativas        INTEGER DEFAULT 0,
                proxima_tentativa TEXT,
                ultimo_erro       TEXT,
                criado_em         TEXT NOT NULL,
                atualizado_em     TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_fila_status
                ON fila_eventos(status, proxima_tentativa);

            -- ────────────────────────────────────────────────────────
            -- Itens de pedido (para ranking de produtos e analytics)
            -- ────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS itens_pedido (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                mercos_id     INTEGER NOT NULL,
                sku           TEXT,
                nome_produto  TEXT,
                quantidade    REAL,
                valor_unit    REAL,
                valor_total   REAL,
                processado_em TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_itens_mercos ON itens_pedido(mercos_id);
            CREATE INDEX IF NOT EXISTS idx_itens_sku ON itens_pedido(sku);

            -- ────────────────────────────────────────────────────────
            -- NOVO: Registro de ações manuais no painel admin
            -- ────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS admin_acoes (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                acao      TEXT NOT NULL,
                mercos_id INTEGER,
                descricao TEXT,
                ip_origem TEXT,
                feito_em  TEXT NOT NULL
            );
        """)
    # Migrations seguras (ADD COLUMN é idempotente no SQLite via try/except)
    for col, typedef in [("cidade", "TEXT"), ("bairro", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE pedidos_fluxo ADD COLUMN {col} {typedef}")
        except Exception:
            pass  # coluna já existe

    logger.info("[DB] Banco inicializado.")


# ──────────────────────────────────────────────────────────────
# Pedidos (existente)
# ──────────────────────────────────────────────────────────────

def pedido_ja_processado(mercos_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM pedidos_processados WHERE mercos_id = ? AND status = 'ok'", (mercos_id,)
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

def fluxo_registrar_recebido(mercos_id: int, numero: str, cliente: str,
                              valor: float = 0, cidade: str = "", bairro: str = ""):
    """Chamado quando o webhook chega — primeira etapa do fluxo."""
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO pedidos_fluxo
                (mercos_id, numero, cliente, valor, cidade, bairro, recebido_em, status_fluxo)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'recebido')
        """, (mercos_id, str(numero), cliente, valor, cidade or "", bairro or "", agora))
        # Atualiza valor/cidade/bairro mesmo se row já existia (INSERT OR IGNORE não atualiza)
        if valor > 0 or cidade or bairro:
            conn.execute("""
                UPDATE pedidos_fluxo
                SET valor  = CASE WHEN ? > 0 THEN ? ELSE valor END,
                    cidade = CASE WHEN ? != '' THEN ? ELSE cidade END,
                    bairro = CASE WHEN ? != '' THEN ? ELSE bairro END
                WHERE mercos_id = ?
            """, (valor, valor, cidade or "", cidade or "", bairro or "", bairro or "", mercos_id))


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


def fluxo_listar_para_sync_expedicao(limit: int = 50) -> list[dict]:
    """
    Retorna pedidos em 'processado' ou 'separado' que já têm vhsys_id,
    candidatos a terem expedição criada/concluída no VHSys.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT f.mercos_id, f.numero, f.cliente, f.valor,
                   f.status_fluxo, f.processado_em, f.separado_em,
                   p.vhsys_id
            FROM pedidos_fluxo f
            INNER JOIN pedidos_processados p
                ON f.mercos_id = p.mercos_id AND p.status = 'ok'
            WHERE f.status_fluxo IN ('processado', 'separado')
              AND f.processado_em IS NOT NULL
              AND p.vhsys_id IS NOT NULL AND p.vhsys_id != ''
            ORDER BY f.processado_em ASC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def reconciliar_pendentes_hoje() -> dict:
    """
    Detecta pedidos recebidos hoje que não foram processados com sucesso.
    Reseta automaticamente os que estão em erro_permanente para pendente.
    Retorna stats: {total, reenfileirados, em_andamento, inconsistentes}
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT f.mercos_id, f.numero, f.cliente, f.status_fluxo,
                   fe.id as fila_id, fe.status as fila_status,
                   fe.ultimo_erro, fe.tentativas
            FROM pedidos_fluxo f
            LEFT JOIN fila_eventos fe
                ON f.mercos_id = fe.mercos_id AND fe.evento = 'pedido.gerado'
            WHERE f.status_fluxo NOT IN ('processado','separado','enviado','cancelado')
              AND DATE(f.recebido_em) = DATE('now','localtime')
        """).fetchall()

        reenfileirados, em_andamento, inconsistentes = [], [], []

        for r in rows:
            if r["fila_status"] == "erro_permanente":
                conn.execute("""
                    UPDATE fila_eventos
                    SET status='pendente', tentativas=0, ultimo_erro=NULL,
                        proxima_tentativa=NULL, atualizado_em=?
                    WHERE id=?
                """, (datetime.now(timezone.utc).isoformat(), r["fila_id"]))
                reenfileirados.append(dict(r))
            elif r["fila_status"] in ("pendente", "processando"):
                em_andamento.append(dict(r))
            else:
                inconsistentes.append(dict(r))

    return {
        "total": len(rows),
        "reenfileirados": reenfileirados,
        "em_andamento": em_andamento,
        "inconsistentes": inconsistentes,
    }


# ──────────────────────────────────────────────────────────────
# NOVO: Auditoria de sequência
# ──────────────────────────────────────────────────────────────

def auditoria_listar_buracos(apenas_abertos: bool = True, horas_recentes: int = 0) -> list[dict]:
    """Lista buracos de sequência.
    horas_recentes > 0 → só retorna buracos detectados nas últimas N horas (0 = sem filtro).
    """
    with get_conn() as conn:
        conditions = []
        params: list = []
        if apenas_abertos:
            conditions.append("resolvido = 0")
        if horas_recentes > 0:
            conditions.append("detectado_em >= datetime('now', ?)")
            params.append(f"-{horas_recentes} hours")
        query = "SELECT * FROM auditoria_sequencia"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY mercos_id DESC"
        rows = conn.execute(query, params).fetchall()
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


# ──────────────────────────────────────────────────────────────
# Fila persistente de eventos
# ──────────────────────────────────────────────────────────────

FILA_MAX_TENTATIVAS = int(os.getenv("FILA_MAX_TENTATIVAS", "5"))


def fila_enfileirar(evento: str, mercos_id: int | None, payload_json: str) -> int:
    """Persiste evento na fila antes de qualquer processamento. Retorna o id inserido."""
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO fila_eventos
                (evento, mercos_id, payload_json, status, criado_em, atualizado_em)
            VALUES (?, ?, ?, 'pendente', ?, ?)
        """, (evento, mercos_id, payload_json, agora, agora))
        return cur.lastrowid


def fila_pegar_proximos(limite: int = 5) -> list[dict]:
    """Retorna itens prontos para processar (pendente + proxima_tentativa <= agora)."""
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM fila_eventos
            WHERE status = 'pendente'
              AND (proxima_tentativa IS NULL OR proxima_tentativa <= ?)
            ORDER BY id ASC
            LIMIT ?
        """, (agora, limite)).fetchall()
    return [dict(r) for r in rows]


def fila_marcar_processando(fila_id: int):
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("""
            UPDATE fila_eventos SET status = 'processando', atualizado_em = ?
            WHERE id = ?
        """, (agora, fila_id))


def fila_marcar_ok(fila_id: int):
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("""
            UPDATE fila_eventos SET status = 'ok', atualizado_em = ?
            WHERE id = ?
        """, (agora, fila_id))


def fila_marcar_erro(fila_id: int, erro: str, tentativas: int):
    """Calcula backoff exponencial. Após FILA_MAX_TENTATIVAS → erro_permanente."""
    from datetime import timedelta
    agora = datetime.now(timezone.utc)
    if tentativas >= FILA_MAX_TENTATIVAS:
        novo_status = "erro_permanente"
        proxima = None
    else:
        novo_status = "pendente"
        delay_seg = 30 * (4 ** (tentativas - 1))  # 30s, 2min, 8min, 30min, 2h
        proxima = (agora + timedelta(seconds=delay_seg)).isoformat()
    with get_conn() as conn:
        conn.execute("""
            UPDATE fila_eventos
            SET status = ?, tentativas = ?, ultimo_erro = ?,
                proxima_tentativa = ?, atualizado_em = ?
            WHERE id = ?
        """, (novo_status, tentativas, str(erro)[:500], proxima, agora.isoformat(), fila_id))


def fila_recuperar_travados() -> int:
    """
    Chamado no startup. Rows em 'processando' indicam crash durante processamento.
    Reseta para 'pendente' para reprocessar. Retorna qtd de itens recuperados.
    """
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute("""
            UPDATE fila_eventos
            SET status = 'pendente', ultimo_erro = 'Recuperado após crash do servidor',
                atualizado_em = ?
            WHERE status = 'processando'
        """, (agora,))
        return cur.rowcount


def fila_stats() -> dict:
    """Retorna contagem de itens por status."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as qtd FROM fila_eventos GROUP BY status"
        ).fetchall()
    return {r["status"]: r["qtd"] for r in rows}


# ──────────────────────────────────────────────────────────────
# Audit trail de ações manuais no admin
# ──────────────────────────────────────────────────────────────

def admin_registrar_acao(acao: str, mercos_id: int | None, descricao: str = "", ip: str = ""):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO admin_acoes (acao, mercos_id, descricao, ip_origem, feito_em)
            VALUES (?, ?, ?, ?, ?)
        """, (acao, mercos_id, descricao, ip, datetime.now(timezone.utc).isoformat()))


def admin_listar_acoes(limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM admin_acoes ORDER BY feito_em DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────────
# Itens de pedido (analytics)
# ──────────────────────────────────────────────────────────────

def salvar_itens_pedido(mercos_id: int, itens: list[dict]):
    """
    Persiste os itens de um pedido para análise posterior.
    Cada item deve ter: sku, nome_produto, quantidade, valor_unit, valor_total.
    Usa INSERT OR IGNORE para idempotência — re-processar não duplica.
    """
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        # Remove itens anteriores deste pedido antes de reinserir (reprocessamento)
        conn.execute("DELETE FROM itens_pedido WHERE mercos_id = ?", (mercos_id,))
        for item in itens:
            conn.execute("""
                INSERT INTO itens_pedido
                    (mercos_id, sku, nome_produto, quantidade, valor_unit, valor_total, processado_em)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                mercos_id,
                item.get("sku") or item.get("codigo"),
                item.get("nome_produto") or item.get("descricao") or item.get("nome"),
                item.get("quantidade", 0),
                item.get("valor_unit") or item.get("preco_unitario") or item.get("valor_unitario", 0),
                item.get("valor_total") or item.get("total", 0),
                agora,
            ))
