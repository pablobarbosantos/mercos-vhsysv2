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
    # ── Tabelas ERP (dados mestres e fluxo completo) ─────────────────────────
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS clientes_base (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                cnpj_cpf        TEXT UNIQUE NOT NULL,
                razao_social    TEXT,
                fantasia        TEXT,
                endereco        TEXT,
                numero          TEXT,
                bairro          TEXT,
                complemento     TEXT,
                cep             TEXT,
                cidade          TEXT,
                uf              TEXT,
                telefone        TEXT,
                celular         TEXT,
                email           TEXT,
                ie              TEXT,
                situacao        TEXT DEFAULT 'Ativo',
                tipo_pessoa     TEXT DEFAULT 'PJ',
                regime_trib     TEXT,
                vendedor        TEXT,
                obs             TEXT,
                criado_em       TEXT NOT NULL,
                atualizado_em   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS produtos_base (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo          TEXT UNIQUE NOT NULL,
                nome            TEXT NOT NULL,
                tipo            TEXT DEFAULT 'Produto',
                fornecedor      TEXT,
                marca           TEXT,
                unidade         TEXT,
                estoque_minimo  REAL DEFAULT 0,
                estoque_maximo  REAL DEFAULT 0,
                estoque_atual   REAL DEFAULT 0,
                preco_venda     REAL DEFAULT 0,
                preco_custo     REAL DEFAULT 0,
                peso            REAL DEFAULT 0,
                peso_liq        REAL DEFAULT 0,
                ncm             TEXT,
                ean             TEXT,
                situacao        TEXT DEFAULT 'Ativo',
                familia         TEXT,
                criado_em       TEXT NOT NULL,
                atualizado_em   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fornecedores_base (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                cnpj_cpf        TEXT UNIQUE NOT NULL,
                razao_social    TEXT,
                fantasia        TEXT,
                endereco        TEXT,
                numero          TEXT,
                bairro          TEXT,
                complemento     TEXT,
                cep             TEXT,
                cidade          TEXT,
                uf              TEXT,
                telefone        TEXT,
                celular         TEXT,
                email           TEXT,
                ie              TEXT,
                situacao        TEXT DEFAULT 'Ativo',
                tipo_pessoa     TEXT DEFAULT 'PJ',
                regime_trib     TEXT,
                vendedor        TEXT,
                obs             TEXT,
                criado_em       TEXT NOT NULL,
                atualizado_em   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversoes_unidade (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                unidade_origem  TEXT NOT NULL,
                unidade_destino TEXT NOT NULL,
                fator           REAL NOT NULL,
                UNIQUE(unidade_origem, unidade_destino)
            );

            CREATE TABLE IF NOT EXISTS romaneios (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                data            TEXT NOT NULL,
                motorista       TEXT,
                veiculo         TEXT,
                status          TEXT DEFAULT 'aberto',
                -- aberto | saiu | finalizado
                criado_em       TEXT NOT NULL,
                finalizado_em   TEXT
            );

            CREATE TABLE IF NOT EXISTS romaneio_pedidos (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                romaneio_id     INTEGER NOT NULL REFERENCES romaneios(id),
                mercos_id       INTEGER NOT NULL,
                ordem           INTEGER DEFAULT 0,
                resultado       TEXT,
                -- entregue | devolvido | parcial
                forma_pgto      TEXT,
                -- dinheiro | pix | boleto | assinou | cartao
                assinou_nota    INTEGER DEFAULT 0,
                obs             TEXT,
                atualizado_em   TEXT,
                UNIQUE(romaneio_id, mercos_id)
            );
            CREATE INDEX IF NOT EXISTS idx_romaneio_pedidos_rom ON romaneio_pedidos(romaneio_id);
            CREATE INDEX IF NOT EXISTS idx_romaneio_pedidos_mer ON romaneio_pedidos(mercos_id);

            CREATE TABLE IF NOT EXISTS motoristas (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                nome    TEXT NOT NULL,
                cnh     TEXT,
                tel     TEXT,
                ativo   INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS veiculos (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                placa   TEXT UNIQUE NOT NULL,
                modelo  TEXT,
                cap_kg  REAL DEFAULT 0,
                ativo   INTEGER DEFAULT 1
            );
        """)

    # Migrations seguras (ADD COLUMN é idempotente no SQLite via try/except)
    for col, typedef in [("cidade", "TEXT"), ("bairro", "TEXT"), ("rua", "TEXT"), ("numero_end", "TEXT"), ("cep", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE pedidos_fluxo ADD COLUMN {col} {typedef}")
        except Exception:
            pass  # coluna já existe
    try:
        conn.execute("ALTER TABLE pedidos_processados ADD COLUMN vhsys_nro TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE pedidos_fluxo ADD COLUMN ultimo_alerta_fluxo_em TEXT")
    except Exception:
        pass
    # Tabela de vendas no cartão (PDV + entregas)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vendas_cartao (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                data        TEXT NOT NULL,
                valor       REAL NOT NULL,
                desconto_pct REAL DEFAULT 2.5,
                origem      TEXT DEFAULT 'manual',
                obs         TEXT,
                criado_em   TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_vendas_cartao_data ON vendas_cartao(data)")
    except Exception:
        pass
    # Novos campos do fluxo completo de entrega
    for col, typedef in [
        ("tipo",               "TEXT DEFAULT 'atacado'"),
        ("precisa_nfe",        "INTEGER DEFAULT 0"),
        ("romaneio_id",        "INTEGER"),
        ("resultado_entrega",  "TEXT"),
        ("forma_pgto_retorno", "TEXT"),
        ("assinou_nota",       "INTEGER DEFAULT 0"),
        ("entregue_em",        "TEXT"),
        ("cancelado_em",       "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE pedidos_fluxo ADD COLUMN {col} {typedef}")
        except Exception:
            pass
    try:
        conn.execute("ALTER TABLE pedidos_fluxo ADD COLUMN cnpj_cpf TEXT")
    except Exception:
        pass

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


def salvar_pedido_processado(mercos_id: int, vhsys_id: str, status: str = "ok", vhsys_nro: str = None):
    """Regra Mercos: obrigatório gravar ID e timestamp de retorno após POST."""
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO pedidos_processados (mercos_id, vhsys_id, vhsys_nro, processado_em, status)
            VALUES (?, ?, ?, ?, ?)
        """, (mercos_id, str(vhsys_id), vhsys_nro, datetime.now(timezone.utc).isoformat(), status))
    logger.debug(f"[DB] Pedido Mercos {mercos_id} → vhsys {vhsys_id} (nro={vhsys_nro}) salvo.")


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
                              valor: float = 0, cidade: str = "", bairro: str = "",
                              rua: str = "", numero_end: str = "", cep: str = "",
                              cnpj_cpf: str = ""):
    """Chamado quando o webhook chega — primeira etapa do fluxo."""
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO pedidos_fluxo
                (mercos_id, numero, cliente, valor, cidade, bairro, rua, numero_end, cep, cnpj_cpf, recebido_em, status_fluxo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'recebido')
        """, (mercos_id, str(numero), cliente, valor, cidade or "", bairro or "", rua or "", numero_end or "", cep or "", cnpj_cpf or "", agora))
        # Atualiza campos de endereço mesmo se row já existia
        if valor > 0 or cidade or bairro or rua or numero_end or cep or cnpj_cpf:
            conn.execute("""
                UPDATE pedidos_fluxo
                SET valor      = CASE WHEN ? > 0  THEN ? ELSE valor      END,
                    cidade     = CASE WHEN ? != '' THEN ? ELSE cidade     END,
                    bairro     = CASE WHEN ? != '' THEN ? ELSE bairro     END,
                    rua        = CASE WHEN ? != '' THEN ? ELSE rua        END,
                    numero_end = CASE WHEN ? != '' THEN ? ELSE numero_end END,
                    cep        = CASE WHEN ? != '' THEN ? ELSE cep        END,
                    cnpj_cpf   = CASE WHEN ? != '' THEN ? ELSE cnpj_cpf  END
                WHERE mercos_id = ?
            """, (valor, valor,
                  cidade or "", cidade or "",
                  bairro or "", bairro or "",
                  rua or "", rua or "",
                  numero_end or "", numero_end or "",
                  cep or "", cep or "",
                  cnpj_cpf or "", cnpj_cpf or "",
                  mercos_id))


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


def fluxo_marcar_separado_lote(mercos_ids: list) -> int:
    """Marca múltiplos pedidos como separado em uma única query."""
    if not mercos_ids:
        return 0
    ts = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" * len(mercos_ids))
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE pedidos_fluxo SET separado_em=?, status_fluxo='separado' WHERE mercos_id IN ({placeholders}) AND status_fluxo='processado'",
            [ts] + list(mercos_ids)
        )
        return cur.rowcount


def fluxo_marcar_enviado_lote(mercos_ids: list) -> int:
    """Marca múltiplos pedidos como enviado em uma única query."""
    if not mercos_ids:
        return 0
    ts = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" * len(mercos_ids))
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE pedidos_fluxo SET enviado_em=?, status_fluxo='enviado' WHERE mercos_id IN ({placeholders}) AND status_fluxo='separado'",
            [ts] + list(mercos_ids)
        )
        return cur.rowcount


def fluxo_regredir_status(mercos_id: int, para: str) -> bool:
    """
    Volta um pedido para o status anterior.
    Transições permitidas: enviado→separado, separado→processado.
    Retorna True se a linha foi atualizada.
    """
    _transicoes = {
        "separado":   ("enviado",   "enviado_em"),
        "processado": ("separado",  "separado_em"),
    }
    if para not in _transicoes:
        return False
    status_atual, campo_limpar = _transicoes[para]
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE pedidos_fluxo SET {campo_limpar}=NULL, status_fluxo=? WHERE mercos_id=? AND status_fluxo=?",
            (para, mercos_id, status_atual)
        )
        return cur.rowcount > 0


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


# ──────────────────────────────────────────────────────────────
# Clientes base (importados do CSV Omie)
# ──────────────────────────────────────────────────────────────

def clientes_upsert(clientes: list[dict]) -> int:
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        for c in clientes:
            conn.execute("""
                INSERT INTO clientes_base
                    (cnpj_cpf, razao_social, fantasia, endereco, numero, bairro,
                     complemento, cep, cidade, uf, telefone, celular, email, ie,
                     situacao, tipo_pessoa, regime_trib, vendedor, obs,
                     criado_em, atualizado_em)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(cnpj_cpf) DO UPDATE SET
                    razao_social=excluded.razao_social,
                    fantasia=excluded.fantasia,
                    endereco=excluded.endereco,
                    numero=excluded.numero,
                    bairro=excluded.bairro,
                    complemento=excluded.complemento,
                    cep=excluded.cep,
                    cidade=excluded.cidade,
                    uf=excluded.uf,
                    telefone=excluded.telefone,
                    celular=excluded.celular,
                    email=excluded.email,
                    ie=excluded.ie,
                    situacao=excluded.situacao,
                    tipo_pessoa=excluded.tipo_pessoa,
                    regime_trib=excluded.regime_trib,
                    vendedor=excluded.vendedor,
                    obs=excluded.obs,
                    atualizado_em=excluded.atualizado_em
            """, (
                c.get("cnpj_cpf", ""), c.get("razao_social", ""), c.get("fantasia", ""),
                c.get("endereco", ""), c.get("numero", ""), c.get("bairro", ""),
                c.get("complemento", ""), c.get("cep", ""), c.get("cidade", ""),
                c.get("uf", ""), c.get("telefone", ""), c.get("celular", ""),
                c.get("email", ""), c.get("ie", ""), c.get("situacao", "Ativo"),
                c.get("tipo_pessoa", "PJ"), c.get("regime_trib", ""), c.get("vendedor", ""),
                c.get("obs", ""), agora, agora,
            ))
    return len(clientes)


def clientes_listar(busca: str = "", uf: str = "", situacao: str = "", limit: int = 500) -> list[dict]:
    with get_conn() as conn:
        conds, params = [], []
        if busca:
            conds.append("(razao_social LIKE ? OR fantasia LIKE ? OR cnpj_cpf LIKE ?)")
            params += [f"%{busca}%", f"%{busca}%", f"%{busca}%"]
        if uf:
            conds.append("uf = ?"); params.append(uf)
        if situacao:
            conds.append("situacao = ?"); params.append(situacao)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = conn.execute(
            f"SELECT * FROM clientes_base {where} ORDER BY fantasia, razao_social LIMIT ?",
            params + [limit]
        ).fetchall()
    return [dict(r) for r in rows]


def clientes_count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM clientes_base").fetchone()[0]


# ──────────────────────────────────────────────────────────────
# Fornecedores base
# ──────────────────────────────────────────────────────────────

def fornecedores_upsert(fornecedores: list[dict]) -> int:
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        for f in fornecedores:
            conn.execute("""
                INSERT INTO fornecedores_base
                    (cnpj_cpf, razao_social, fantasia, endereco, numero, bairro,
                     complemento, cep, cidade, uf, telefone, celular, email, ie,
                     situacao, tipo_pessoa, regime_trib, vendedor, obs,
                     criado_em, atualizado_em)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(cnpj_cpf) DO UPDATE SET
                    razao_social=excluded.razao_social, fantasia=excluded.fantasia,
                    endereco=excluded.endereco, numero=excluded.numero,
                    bairro=excluded.bairro, complemento=excluded.complemento,
                    cep=excluded.cep, cidade=excluded.cidade, uf=excluded.uf,
                    telefone=excluded.telefone, celular=excluded.celular,
                    email=excluded.email, ie=excluded.ie,
                    situacao=excluded.situacao, tipo_pessoa=excluded.tipo_pessoa,
                    regime_trib=excluded.regime_trib, vendedor=excluded.vendedor,
                    obs=excluded.obs, atualizado_em=excluded.atualizado_em
            """, (
                f.get("cnpj_cpf", ""), f.get("razao_social", ""), f.get("fantasia", ""),
                f.get("endereco", ""), f.get("numero", ""), f.get("bairro", ""),
                f.get("complemento", ""), f.get("cep", ""), f.get("cidade", ""),
                f.get("uf", ""), f.get("telefone", ""), f.get("celular", ""),
                f.get("email", ""), f.get("ie", ""), f.get("situacao", "Ativo"),
                f.get("tipo_pessoa", "PJ"), f.get("regime_trib", ""), f.get("vendedor", ""),
                f.get("obs", ""), agora, agora,
            ))
    return len(fornecedores)


def fornecedores_listar(busca: str = "", uf: str = "", situacao: str = "", limit: int = 500) -> list[dict]:
    with get_conn() as conn:
        conds, params = [], []
        if busca:
            conds.append("(razao_social LIKE ? OR fantasia LIKE ? OR cnpj_cpf LIKE ?)")
            params += [f"%{busca}%", f"%{busca}%", f"%{busca}%"]
        if uf:
            conds.append("uf = ?"); params.append(uf)
        if situacao:
            conds.append("situacao = ?"); params.append(situacao)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = conn.execute(
            f"SELECT * FROM fornecedores_base {where} ORDER BY fantasia, razao_social LIMIT ?",
            params + [limit]
        ).fetchall()
    return [dict(r) for r in rows]


def fornecedores_count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM fornecedores_base").fetchone()[0]


# ──────────────────────────────────────────────────────────────
# Produtos base (importados do CSV Omie)
# ──────────────────────────────────────────────────────────────

def produtos_upsert(produtos: list[dict]) -> int:
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        for p in produtos:
            conn.execute("""
                INSERT INTO produtos_base
                    (codigo, nome, tipo, fornecedor, marca, unidade,
                     estoque_minimo, estoque_maximo, estoque_atual,
                     preco_venda, preco_custo, peso, peso_liq, ncm, ean,
                     situacao, familia, criado_em, atualizado_em)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(codigo) DO UPDATE SET
                    nome=excluded.nome,
                    tipo=excluded.tipo,
                    fornecedor=excluded.fornecedor,
                    marca=excluded.marca,
                    unidade=excluded.unidade,
                    estoque_minimo=excluded.estoque_minimo,
                    estoque_maximo=excluded.estoque_maximo,
                    estoque_atual=excluded.estoque_atual,
                    preco_venda=excluded.preco_venda,
                    preco_custo=excluded.preco_custo,
                    peso=excluded.peso,
                    peso_liq=excluded.peso_liq,
                    ncm=excluded.ncm,
                    ean=excluded.ean,
                    situacao=excluded.situacao,
                    familia=excluded.familia,
                    atualizado_em=excluded.atualizado_em
            """, (
                p.get("codigo", ""), p.get("nome", ""), p.get("tipo", "Produto"),
                p.get("fornecedor", ""), p.get("marca", ""), p.get("unidade", "un"),
                float(p.get("estoque_minimo") or 0), float(p.get("estoque_maximo") or 0),
                float(p.get("estoque_atual") or 0),
                float(p.get("preco_venda") or 0), float(p.get("preco_custo") or 0),
                float(p.get("peso") or 0), float(p.get("peso_liq") or 0),
                p.get("ncm", ""), p.get("ean", ""), p.get("situacao", "Ativo"),
                p.get("familia", ""), agora, agora,
            ))
    return len(produtos)


def produtos_listar(busca: str = "", familia: str = "", situacao: str = "Ativo",
                    estoque_critico: bool = False, limit: int = 1000) -> list[dict]:
    with get_conn() as conn:
        conds, params = [], []
        if busca:
            conds.append("(nome LIKE ? OR codigo LIKE ? OR ean LIKE ?)")
            params += [f"%{busca}%", f"%{busca}%", f"%{busca}%"]
        if familia:
            conds.append("familia = ?"); params.append(familia)
        if situacao:
            conds.append("situacao = ?"); params.append(situacao)
        if estoque_critico:
            conds.append("estoque_atual <= estoque_minimo AND estoque_minimo > 0")
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = conn.execute(
            f"SELECT * FROM produtos_base {where} ORDER BY nome LIMIT ?",
            params + [limit]
        ).fetchall()
    return [dict(r) for r in rows]


def produtos_count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM produtos_base").fetchone()[0]


# ──────────────────────────────────────────────────────────────
# Romaneios
# ──────────────────────────────────────────────────────────────

def romaneio_criar(data: str, motorista: str = "", veiculo: str = "",
                   pedido_ids: list | None = None) -> int:
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO romaneios (data, motorista, veiculo, status, criado_em)
            VALUES (?, ?, ?, 'aberto', ?)
        """, (data, motorista, veiculo, agora))
        rom_id = cur.lastrowid
        if pedido_ids:
            for i, mid in enumerate(pedido_ids):
                conn.execute("""
                    INSERT OR IGNORE INTO romaneio_pedidos (romaneio_id, mercos_id, ordem, atualizado_em)
                    VALUES (?, ?, ?, ?)
                """, (rom_id, mid, i, agora))
            conn.execute("""
                UPDATE pedidos_fluxo SET romaneio_id=?, status_fluxo='separado', separado_em=?
                WHERE mercos_id IN ({})
            """.format(",".join("?" * len(pedido_ids))),
                [rom_id, agora] + list(pedido_ids)
            )
    return rom_id


def romaneio_get(rom_id: int) -> dict | None:
    with get_conn() as conn:
        rom = conn.execute("SELECT * FROM romaneios WHERE id=?", (rom_id,)).fetchone()
        if not rom:
            return None
        paradas = conn.execute("""
            SELECT rp.*, pf.numero, pf.cliente, pf.valor, pf.cidade,
                   pf.bairro, pf.rua, pf.numero_end, pf.cep, pf.status_fluxo,
                   pf.precisa_nfe, pf.tipo
            FROM romaneio_pedidos rp
            LEFT JOIN pedidos_fluxo pf ON pf.mercos_id = rp.mercos_id
            WHERE rp.romaneio_id = ?
            ORDER BY rp.ordem
        """, (rom_id,)).fetchall()
    result = dict(rom)
    result["paradas"] = [dict(p) for p in paradas]
    return result


def romaneio_listar(status: str = "", limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        where = "WHERE status=?" if status else ""
        params = [status] if status else []
        rows = conn.execute(
            f"SELECT * FROM romaneios {where} ORDER BY criado_em DESC LIMIT ?",
            params + [limit]
        ).fetchall()
    return [dict(r) for r in rows]


def romaneio_iniciar(rom_id: int) -> bool:
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE romaneios SET status='saiu' WHERE id=? AND status='aberto'", (rom_id,)
        )
        if cur.rowcount == 0:
            return False
        conn.execute("""
            UPDATE pedidos_fluxo SET status_fluxo='enviado', enviado_em=?
            WHERE romaneio_id=? AND status_fluxo='separado'
        """, (agora, rom_id))
    return True


def romaneio_registrar_retorno(rom_id: int, mercos_id: int,
                               resultado: str, forma_pgto: str,
                               assinou: bool = False, obs: str = "") -> bool:
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute("""
            UPDATE romaneio_pedidos
            SET resultado=?, forma_pgto=?, assinou_nota=?, obs=?, atualizado_em=?
            WHERE romaneio_id=? AND mercos_id=?
        """, (resultado, forma_pgto, 1 if assinou else 0, obs, agora, rom_id, mercos_id))
        if cur.rowcount == 0:
            return False
        novo_status = "finalizado" if resultado == "entregue" and not assinou else "entregue"
        conn.execute("""
            UPDATE pedidos_fluxo
            SET resultado_entrega=?, forma_pgto_retorno=?, assinou_nota=?,
                entregue_em=?, status_fluxo=?
            WHERE mercos_id=?
        """, (resultado, forma_pgto, 1 if assinou else 0, agora, novo_status, mercos_id))
    return True


def romaneio_finalizar(rom_id: int) -> bool:
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE romaneios SET status='finalizado', finalizado_em=? WHERE id=? AND status='saiu'",
            (agora, rom_id)
        )
    return cur.rowcount > 0


def pedido_criar_manual(numero: str, cliente_cnpj: str, cliente_nome: str,
                        valor: float, cidade: str = "", tipo: str = "atacado",
                        precisa_nfe: bool = False) -> int:
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO pedidos_fluxo
                (mercos_id, numero, cliente, valor, cidade, recebido_em,
                 status_fluxo, tipo, precisa_nfe)
            VALUES (
                (SELECT COALESCE(MIN(mercos_id), 0) - 1 FROM pedidos_fluxo WHERE mercos_id < 0),
                ?, ?, ?, ?, ?, 'recebido', ?, ?
            )
        """, (numero, cliente_nome, valor, cidade, agora, tipo, 1 if precisa_nfe else 0))
    return cur.lastrowid
