"""
REST API para o frontend THE_ONE/prototype.
Todos os endpoints começam com /api/
"""

from datetime import datetime, timezone, date
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from src import database as db

router = APIRouter(prefix="/api", tags=["frontend"])


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return date.today().isoformat()


# ──────────────────────────────────────────────────────────────
# Pedidos
# ──────────────────────────────────────────────────────────────

@router.get("/pedidos")
def listar_pedidos(
    status: Optional[str] = None,
    tipo: Optional[str] = None,
    busca: Optional[str] = None,
    data_de: Optional[str] = None,
    data_ate: Optional[str] = None,
    limit: int = Query(default=500, le=2000),
):
    with db.get_conn() as conn:
        conds, params = [], []
        if status:
            conds.append("status_fluxo = ?"); params.append(status)
        if tipo:
            conds.append("tipo = ?"); params.append(tipo)
        if busca:
            conds.append("(cliente LIKE ? OR numero LIKE ?)")
            params += [f"%{busca}%", f"%{busca}%"]
        if data_de:
            conds.append("DATE(recebido_em) >= ?"); params.append(data_de)
        if data_ate:
            conds.append("DATE(recebido_em) <= ?"); params.append(data_ate)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = conn.execute(
            f"""
            SELECT pf.*,
                   cb.fantasia,
                   COALESCE(NULLIF(pf.rua,''),      cb.endereco) AS rua,
                   COALESCE(NULLIF(pf.numero_end,''), cb.numero)  AS numero_end,
                   COALESCE(NULLIF(pf.bairro,''),   cb.bairro)   AS bairro
            FROM pedidos_fluxo pf
            LEFT JOIN clientes_base cb ON pf.cnpj_cpf = cb.cnpj_cpf
            {where}
            ORDER BY pf.recebido_em DESC LIMIT ?
            """,
            params + [limit]
        ).fetchall()
    return [dict(r) for r in rows]


class PedidoManualIn(BaseModel):
    numero: str
    cliente_nome: str
    cliente_cnpj: str = ""
    valor: float = 0
    cidade: str = ""
    tipo: str = "atacado"
    precisa_nfe: bool = False


@router.post("/pedidos", status_code=201)
def criar_pedido_manual(body: PedidoManualIn):
    pid = db.pedido_criar_manual(
        numero=body.numero,
        cliente_cnpj=body.cliente_cnpj,
        cliente_nome=body.cliente_nome,
        valor=body.valor,
        cidade=body.cidade,
        tipo=body.tipo,
        precisa_nfe=body.precisa_nfe,
    )
    return {"id": pid, "status": "recebido"}


class StageIn(BaseModel):
    stage: str
    precisa_nfe: Optional[bool] = None


_TRANSICOES_VALIDAS = {
    "recebido":   ["separado", "cancelado", "finalizado"],
    "processado": ["separado", "cancelado"],
    "separado":   ["enviado",  "cancelado", "processado", "finalizado"],
    "enviado":    ["entregue", "devolvido", "separado"],
    "entregue":   ["finalizado"],
    "finalizado": [],
    "cancelado":  [],
    "erro":       ["recebido"],
}


@router.patch("/pedidos/{mercos_id}/stage")
def mover_stage(mercos_id: int, body: StageIn):
    pedido = db.fluxo_get_pedido(mercos_id)
    if not pedido:
        raise HTTPException(404, "Pedido não encontrado")

    atual = pedido["status_fluxo"]
    novo  = body.stage

    if novo not in _TRANSICOES_VALIDAS.get(atual, []):
        raise HTTPException(400, f"Transição inválida: {atual} → {novo}")

    agora = _now()
    with db.get_conn() as conn:
        updates = ["status_fluxo = ?"]
        params  = [novo]

        if novo == "separado":
            updates.append("separado_em = ?"); params.append(agora)
        elif novo == "enviado":
            updates.append("enviado_em = ?"); params.append(agora)
        elif novo in ("entregue", "devolvido"):
            updates.append("entregue_em = ?"); params.append(agora)
        elif novo == "cancelado":
            updates.append("cancelado_em = ?"); params.append(agora)
        elif novo == "finalizado":
            updates.append("entregue_em = ?"); params.append(agora)

        if body.precisa_nfe is not None:
            updates.append("precisa_nfe = ?"); params.append(1 if body.precisa_nfe else 0)

        params.append(mercos_id)
        conn.execute(
            f"UPDATE pedidos_fluxo SET {', '.join(updates)} WHERE mercos_id = ?",
            params
        )

    return {"ok": True, "mercos_id": mercos_id, "stage": novo}


@router.patch("/pedidos/{mercos_id}/flags")
def atualizar_flags(mercos_id: int, precisa_nfe: Optional[bool] = None,
                    tipo: Optional[str] = None):
    updates, params = [], []
    if precisa_nfe is not None:
        updates.append("precisa_nfe = ?"); params.append(1 if precisa_nfe else 0)
    if tipo is not None:
        updates.append("tipo = ?"); params.append(tipo)
    if not updates:
        raise HTTPException(400, "Nenhum campo para atualizar")
    params.append(mercos_id)
    with db.get_conn() as conn:
        conn.execute(f"UPDATE pedidos_fluxo SET {', '.join(updates)} WHERE mercos_id=?", params)
    return {"ok": True}


# ──────────────────────────────────────────────────────────────
# Clientes
# ──────────────────────────────────────────────────────────────

@router.get("/clientes")
def listar_clientes(
    busca: Optional[str] = None,
    uf: Optional[str] = None,
    situacao: Optional[str] = None,
    limit: int = Query(default=500, le=2000),
):
    return db.clientes_listar(
        busca=busca or "",
        uf=uf or "",
        situacao=situacao or "",
        limit=limit,
    )


@router.get("/clientes/count")
def contar_clientes():
    return {"total": db.clientes_count()}


# ──────────────────────────────────────────────────────────────
# Fornecedores
# ──────────────────────────────────────────────────────────────

@router.get("/fornecedores")
def listar_fornecedores(
    busca: Optional[str] = None,
    uf: Optional[str] = None,
    situacao: Optional[str] = None,
    limit: int = Query(default=500, le=2000),
):
    return db.fornecedores_listar(
        busca=busca or "",
        uf=uf or "",
        situacao=situacao or "",
        limit=limit,
    )


@router.get("/fornecedores/count")
def contar_fornecedores():
    return {"total": db.fornecedores_count()}


# ──────────────────────────────────────────────────────────────
# Produtos
# ──────────────────────────────────────────────────────────────

@router.get("/produtos")
def listar_produtos(
    busca: Optional[str] = None,
    familia: Optional[str] = None,
    situacao: Optional[str] = "Ativo",
    estoque_critico: bool = False,
    limit: int = Query(default=1000, le=5000),
):
    return db.produtos_listar(
        busca=busca or "",
        familia=familia or "",
        situacao=situacao or "",
        estoque_critico=estoque_critico,
        limit=limit,
    )


@router.get("/produtos/count")
def contar_produtos():
    return {"total": db.produtos_count()}


# ──────────────────────────────────────────────────────────────
# Romaneios
# ──────────────────────────────────────────────────────────────

class RomaneioIn(BaseModel):
    data: Optional[str] = None
    motorista: str = ""
    veiculo: str = ""
    pedido_ids: list[int] = []


@router.post("/romaneios", status_code=201)
def criar_romaneio(body: RomaneioIn):
    if not body.pedido_ids:
        raise HTTPException(400, "pedido_ids é obrigatório")
    rom_id = db.romaneio_criar(
        data=body.data or _today(),
        motorista=body.motorista,
        veiculo=body.veiculo,
        pedido_ids=body.pedido_ids,
    )
    return {"id": rom_id}


@router.get("/romaneios")
def listar_romaneios(status: Optional[str] = None, limit: int = 50):
    return db.romaneio_listar(status=status or "", limit=limit)


@router.get("/romaneios/{rom_id}")
def detalhe_romaneio(rom_id: int):
    rom = db.romaneio_get(rom_id)
    if not rom:
        raise HTTPException(404, "Romaneio não encontrado")
    return rom


@router.patch("/romaneios/{rom_id}/iniciar")
def iniciar_romaneio(rom_id: int):
    ok = db.romaneio_iniciar(rom_id)
    if not ok:
        raise HTTPException(400, "Romaneio não pode ser iniciado (status inválido ou não existe)")
    return {"ok": True, "status": "saiu"}


class RetornoIn(BaseModel):
    resultado: str   # entregue | devolvido | parcial
    forma_pgto: str  # dinheiro | pix | boleto | assinou | cartao
    assinou: bool = False
    obs: str = ""


@router.patch("/romaneios/{rom_id}/pedidos/{mercos_id}/retorno")
def registrar_retorno(rom_id: int, mercos_id: int, body: RetornoIn):
    if body.resultado not in ("entregue", "devolvido", "parcial"):
        raise HTTPException(400, "resultado inválido")
    ok = db.romaneio_registrar_retorno(
        rom_id=rom_id,
        mercos_id=mercos_id,
        resultado=body.resultado,
        forma_pgto=body.forma_pgto,
        assinou=body.assinou,
        obs=body.obs,
    )
    if not ok:
        raise HTTPException(404, "Parada não encontrada neste romaneio")
    return {"ok": True}


@router.patch("/romaneios/{rom_id}/finalizar")
def finalizar_romaneio(rom_id: int):
    ok = db.romaneio_finalizar(rom_id)
    if not ok:
        raise HTTPException(400, "Romaneio não pode ser finalizado (precisa estar em status 'saiu')")
    return {"ok": True, "status": "finalizado"}


# ──────────────────────────────────────────────────────────────
# Títulos / Contas a receber (proxy dos boletos)
# ──────────────────────────────────────────────────────────────

@router.get("/titulos")
def listar_titulos(
    status: Optional[str] = None,
    data_de: Optional[str] = None,
    data_ate: Optional[str] = None,
    limit: int = Query(default=300, le=1000),
):
    try:
        from boletos.database import listar_boletos
        rows = listar_boletos(status=status)
        if data_de:
            rows = [r for r in rows if (r.get("data_vencimento") or "") >= data_de]
        if data_ate:
            rows = [r for r in rows if (r.get("data_vencimento") or "") <= data_ate]
        return rows[:limit]
    except Exception:
        return []


@router.get("/titulos/stats")
def stats_titulos():
    try:
        from boletos.database import stats_relatorio
        return stats_relatorio()
    except Exception:
        return {"pagos": [], "abertos": [], "vencidos": [], "total_pago": 0, "total_aberto": 0, "total_vencido": 0}


@router.post("/relatorios/email-manha", status_code=202)
def disparar_email_manha():
    """Dispara o relatório matinal por e-mail manualmente."""
    try:
        from boletos.email_report import relatorio_email
        ok = relatorio_email()
        return {"ok": ok, "msg": "Enviado" if ok else "SMTP não configurado ou erro ao enviar"}
    except Exception as e:
        raise HTTPException(500, str(e))


# ──────────────────────────────────────────────────────────────
# Dashboard KPIs
# ──────────────────────────────────────────────────────────────

@router.get("/dashboard/kpis")
def kpis_dashboard(tipo: Optional[str] = None):
    with db.get_conn() as conn:
        cond = "AND tipo = ?" if tipo and tipo != "todos" else ""
        params_tipo = [tipo] if tipo and tipo != "todos" else []

        hoje = _today()
        mes_inicio = hoje[:7] + "-01"

        # Pedidos do mês
        row = conn.execute(
            f"SELECT COUNT(*) AS qtd, COALESCE(SUM(valor),0) AS total "
            f"FROM pedidos_fluxo WHERE DATE(recebido_em) >= ? {cond}",
            [mes_inicio] + params_tipo
        ).fetchone()
        pedidos_mes = row["qtd"]
        faturamento_mes = row["total"]

        # Na fila (aguardando separação)
        fila_qtd = conn.execute(
            f"SELECT COUNT(*) FROM pedidos_fluxo WHERE status_fluxo='recebido' {cond}",
            params_tipo
        ).fetchone()[0]

        # Em separação / em rota
        separacao_qtd = conn.execute(
            f"SELECT COUNT(*) FROM pedidos_fluxo WHERE status_fluxo='separado' {cond}",
            params_tipo
        ).fetchone()[0]

        enviados_qtd = conn.execute(
            f"SELECT COUNT(*) FROM pedidos_fluxo WHERE status_fluxo='enviado' {cond}",
            params_tipo
        ).fetchone()[0]

        # Entregues hoje
        entregues_hoje = conn.execute(
            f"SELECT COUNT(*) FROM pedidos_fluxo "
            f"WHERE DATE(entregue_em) = ? {cond}",
            [hoje] + params_tipo
        ).fetchone()[0]

    return {
        "pedidos_mes":    pedidos_mes,
        "faturamento_mes": faturamento_mes,
        "fila":           fila_qtd,
        "separacao":      separacao_qtd,
        "em_rota":        enviados_qtd,
        "entregues_hoje": entregues_hoje,
    }


# ──────────────────────────────────────────────────────────────
# Roteirização do romaneio
# ──────────────────────────────────────────────────────────────

@router.post("/romaneios/{rom_id}/otimizar")
def otimizar_rota_romaneio(rom_id: int):
    rom = db.romaneio_get(rom_id)
    if not rom:
        raise HTTPException(404, "Romaneio não encontrado")
    if rom["status"] != "aberto":
        raise HTTPException(400, "Romaneio já foi iniciado")

    paradas = rom.get("paradas", [])
    if not paradas:
        raise HTTPException(400, "Romaneio sem paradas")

    enderecos = []
    for p in paradas:
        partes = [x for x in [p.get("rua"), p.get("numero_end"), p.get("bairro"), p.get("cidade")] if x]
        end_str = ", ".join(partes) if partes else (p.get("cidade") or "Uberlândia, MG")
        enderecos.append({
            "endereco": end_str,
            "cep": p.get("cep") or "",
            "label": p.get("cliente") or str(p.get("mercos_id")),
            "mercos_id": p.get("mercos_id"),
        })

    try:
        from src.routing import otimizar_rota
        resultado = otimizar_rota(enderecos)
    except Exception as e:
        raise HTTPException(500, f"Erro na roteirização: {e}")

    if resultado.get("status") != "sucesso":
        raise HTTPException(422, resultado.get("erro", "Falha na roteirização"))

    # Reordena as paradas no banco
    ordem_ids = resultado.get("ordem", [])
    if ordem_ids:
        agora = _now()
        with db.get_conn() as conn:
            for nova_pos, idx_original in enumerate(ordem_ids):
                if idx_original < len(paradas):
                    mid = paradas[idx_original].get("mercos_id")
                    conn.execute(
                        "UPDATE romaneio_pedidos SET ordem=?, atualizado_em=? WHERE romaneio_id=? AND mercos_id=?",
                        (nova_pos, agora, rom_id, mid)
                    )

    return {
        "link_maps": resultado.get("link_maps", ""),
        "duracao_segundos": resultado.get("duracao_segundos", 0),
        "pontos": resultado.get("pontos", []),
        "ordem": ordem_ids,
    }


@router.get("/romaneios/{rom_id}/pdf")
def pdf_romaneio(rom_id: int):
    from fastapi.responses import Response
    rom = db.romaneio_get(rom_id)
    if not rom:
        raise HTTPException(404, "Romaneio não encontrado")
    try:
        from src.romaneio_pdf import gerar_pdf
        pdf_bytes = gerar_pdf(rom)
    except Exception as e:
        raise HTTPException(500, f"Erro ao gerar PDF: {e}")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="romaneio_{rom_id}.pdf"'},
    )


# ──────────────────────────────────────────────────────────────
# Motoristas e veículos
# ──────────────────────────────────────────────────────────────

@router.get("/motoristas")
def listar_motoristas():
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM motoristas WHERE ativo=1 ORDER BY nome"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/veiculos")
def listar_veiculos():
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM veiculos WHERE ativo=1 ORDER BY placa"
        ).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────────
# Vendas Cartão (PDV + entregas)
# ──────────────────────────────────────────────────────────────

class VendaCartaoIn(BaseModel):
    valor: float
    desconto_pct: Optional[float] = None
    obs: str = ""


@router.post("/vendas-cartao", status_code=201)
def registrar_venda_cartao(body: VendaCartaoIn):
    agora = _now()
    hoje  = _today()
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO vendas_cartao (data, valor, desconto_pct, origem, obs, criado_em) VALUES (?,?,?,?,?,?)",
            (hoje, body.valor, body.desconto_pct, "pdv", body.obs, agora)
        )
    return {"id": cur.lastrowid}


@router.get("/vendas-cartao")
def listar_vendas_cartao(data_de: Optional[str] = None, data_ate: Optional[str] = None):
    with db.get_conn() as conn:
        conds, params = [], []
        if data_de:
            conds.append("data >= ?"); params.append(data_de)
        if data_ate:
            conds.append("data <= ?"); params.append(data_ate)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = conn.execute(
            f"SELECT * FROM vendas_cartao {where} ORDER BY criado_em DESC LIMIT 500",
            params
        ).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────────
# NF-e Emitidas
# ──────────────────────────────────────────────────────────────

@router.get("/nfe-emitidas")
def listar_nfe_emitidas(limit: int = Query(default=200, le=1000)):
    try:
        from nfe_emitidas.database import registro_listar
        return registro_listar(limit=limit)
    except Exception:
        return []


# ──────────────────────────────────────────────────────────────
# Compras / NF-e Entrada
# ──────────────────────────────────────────────────────────────

@router.get("/compras/notas")
def listar_notas_entrada(limit: int = Query(default=200, le=1000)):
    try:
        from compras.database import nota_listar
        return nota_listar(limit=limit)
    except Exception:
        return []


@router.get("/compras/stats")
def stats_compras():
    try:
        from compras.database import nota_stats
        return nota_stats()
    except Exception:
        return {}


# ──────────────────────────────────────────────────────────────
# Analytics / Relatórios
# ──────────────────────────────────────────────────────────────

def _date_cond(data_de: Optional[str], data_ate: Optional[str], col: str = "DATE(recebido_em)"):
    conds, params = [], []
    if data_de:
        conds.append(f"{col} >= ?"); params.append(data_de)
    if data_ate:
        conds.append(f"{col} <= ?"); params.append(data_ate)
    return conds, params


@router.get("/analytics/resumo-filtrado")
def resumo_filtrado(
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    cliente: Optional[str] = None,
    cidade: Optional[str] = None,
    bairro: Optional[str] = None,
):
    conds, params = _date_cond(data_inicio, data_fim)
    conds.append("status_fluxo NOT IN ('cancelado')")
    if cliente:
        conds.append("cliente LIKE ?"); params.append(f"%{cliente}%")
    if cidade:
        conds.append("cidade = ?"); params.append(cidade)
    if bairro:
        conds.append("bairro = ?"); params.append(bairro)
    where = "WHERE " + " AND ".join(conds) if conds else ""

    with db.get_conn() as conn:
        row = conn.execute(
            f"SELECT COALESCE(SUM(valor),0), COUNT(*) FROM pedidos_fluxo {where}", params
        ).fetchone()
        fat_total = row[0] or 0
        num_ped   = row[1] or 0

        top_rows = conn.execute(
            f"SELECT cliente, COUNT(*) as np, SUM(valor) as vt, MAX(recebido_em) as ult "
            f"FROM pedidos_fluxo {where} GROUP BY cliente ORDER BY vt DESC LIMIT 10",
            params
        ).fetchall()

        bairro_rows = conn.execute(
            f"SELECT COALESCE(bairro,'—') as bairro, COUNT(*) as np, SUM(valor) as fat "
            f"FROM pedidos_fluxo {where} GROUP BY bairro ORDER BY fat DESC LIMIT 20",
            params
        ).fetchall()

    top_clientes = []
    for r in top_rows:
        top_clientes.append({
            "cliente": r[0], "num_pedidos": r[1], "valor_total": r[2],
            "ticket_medio": r[2] / r[1] if r[1] else 0,
            "ultima_compra": r[3] or "",
        })

    por_bairro = [{"bairro": r[0], "num_pedidos": r[1], "faturamento": r[2]} for r in bairro_rows]

    return {
        "faturamento_total": fat_total,
        "num_pedidos": num_ped,
        "ticket_medio": fat_total / num_ped if num_ped else 0,
        "top_clientes": top_clientes,
        "por_bairro": por_bairro,
    }


@router.get("/analytics/produtos")
def analytics_produtos(
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    top: int = Query(default=10, le=100),
    dias_parado: int = Query(default=30, le=365),
):
    conds, params = _date_cond(data_inicio, data_fim, col="DATE(i.processado_em)")
    where = "WHERE " + " AND ".join(conds) if conds else ""

    with db.get_conn() as conn:
        mais = conn.execute(
            f"SELECT i.nome_produto, SUM(i.quantidade) as qt, SUM(i.valor_total) as vt, "
            f"COUNT(DISTINCT i.mercos_id) as np "
            f"FROM itens_pedido i {where} "
            f"GROUP BY i.nome_produto ORDER BY vt DESC LIMIT ?",
            params + [top]
        ).fetchall()

        parados = conn.execute(
            "SELECT i.nome_produto, SUM(i.quantidade) as qt, MAX(i.processado_em) as ult "
            "FROM itens_pedido i "
            "WHERE DATE(i.processado_em) < DATE('now', ? || ' days') "
            "GROUP BY i.nome_produto ORDER BY ult ASC LIMIT ?",
            [f"-{dias_parado}", top]
        ).fetchall()

    hoje = date.today()
    return {
        "mais_vendidos": [
            {"nome_produto": r[0], "qtd_total": r[1], "valor_total": r[2], "num_pedidos": r[3]}
            for r in mais
        ],
        "parados": [
            {
                "nome_produto": r[0], "qtd_total": r[1], "ultima_venda": r[2] or "",
                "dias_sem_venda": (hoje - date.fromisoformat((r[2] or hoje.isoformat())[:10])).days
            }
            for r in parados
        ],
    }


@router.get("/relatorios/abc-clientes")
def abc_clientes(
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    top: int = Query(default=100, le=500),
):
    conds, params = _date_cond(data_inicio, data_fim)
    conds.append("status_fluxo NOT IN ('cancelado')")
    where = "WHERE " + " AND ".join(conds) if conds else ""

    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT cliente, COUNT(*) as np, SUM(valor) as vt, MAX(recebido_em) as ult "
            f"FROM pedidos_fluxo {where} GROUP BY cliente ORDER BY vt DESC LIMIT ?",
            params + [top]
        ).fetchall()

    total = sum(r[2] for r in rows) or 1
    itens, cum = [], 0.0
    for i, r in enumerate(rows):
        pct = r[2] / total * 100
        cum += pct
        itens.append({
            "cliente": r[0], "num_pedidos": r[1], "valor_total": r[2],
            "ticket_medio": r[2] / r[1] if r[1] else 0,
            "ultima_compra": r[3] or "",
            "pct_receita": round(pct, 2),
            "pct_acumulado": round(cum, 2),
            "classe": "A" if cum <= 80 else ("B" if cum <= 95 else "C"),
        })

    return {"itens": itens, "total_geral": total}


@router.get("/relatorios/abc")
def abc_produtos(
    mes: Optional[str] = None,
    familia: Optional[str] = None,
    top: int = Query(default=100, le=500),
):
    conds, params = [], []
    if mes:
        conds.append("strftime('%Y-%m', i.processado_em) = ?"); params.append(mes)
    if familia:
        conds.append("p.familia = ?"); params.append(familia)
    where = "WHERE " + " AND ".join(conds) if conds else ""

    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT i.sku, i.nome_produto, COALESCE(p.familia,'—') as fam, "
            f"SUM(i.quantidade) as qt, SUM(i.valor_total) as fat "
            f"FROM itens_pedido i "
            f"LEFT JOIN produtos_base p ON p.codigo = i.sku "
            f"{where} GROUP BY i.sku ORDER BY fat DESC LIMIT ?",
            params + [top]
        ).fetchall()

    if not rows:
        return {"produtos": []}

    total = sum(r[4] for r in rows) or 1
    produtos, cum = [], 0.0
    for i, r in enumerate(rows):
        pct = r[4] / total * 100
        cum += pct
        produtos.append({
            "sku": r[0], "nome": r[1], "familia": r[2],
            "qtd": r[3], "fat": r[4],
            "pct_receita": round(pct, 2),
            "pct_acumulado": round(cum, 2),
            "classe": "A" if cum <= 80 else ("B" if cum <= 95 else "C"),
        })

    return {"produtos": produtos}


@router.get("/relatorios/margem")
def relatorio_margem(
    data_de: Optional[str] = None,
    data_ate: Optional[str] = None,
    top: int = Query(default=50, le=200),
):
    conds, params = _date_cond(data_de, data_ate, col="DATE(i.processado_em)")
    where = "WHERE " + " AND ".join(conds) if conds else ""

    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT i.sku, i.nome_produto, COALESCE(p.familia,'—') as fam, "
            f"SUM(i.quantidade) as qt, SUM(i.valor_total) as venda, "
            f"COALESCE(p.preco_custo, 0) as custo_unit "
            f"FROM itens_pedido i "
            f"LEFT JOIN produtos_base p ON p.codigo = i.sku "
            f"{where} "
            f"GROUP BY i.sku ORDER BY venda DESC LIMIT ?",
            params + [top]
        ).fetchall()

        familia_rows = conn.execute(
            f"SELECT COALESCE(p.familia,'—') as fam, "
            f"SUM(i.valor_total) as venda, "
            f"SUM(i.quantidade * COALESCE(p.preco_custo, 0)) as custo "
            f"FROM itens_pedido i "
            f"LEFT JOIN produtos_base p ON p.codigo = i.sku "
            f"{where} GROUP BY fam ORDER BY venda DESC",
            params
        ).fetchall()

    por_produto = []
    total_venda = total_custo = 0.0
    for r in rows:
        custo_total = r[3] * r[5]
        margem = r[4] - custo_total
        margem_pct = margem / r[4] * 100 if r[4] else 0
        total_venda += r[4]
        total_custo += custo_total
        por_produto.append({
            "sku": r[0], "nome": r[1], "familia": r[2],
            "qtd": r[3], "valor_venda": round(r[4], 2),
            "valor_custo": round(custo_total, 2),
            "margem": round(margem, 2),
            "margem_pct": round(margem_pct, 1),
        })

    por_familia = []
    for r in familia_rows:
        m = r[1] - r[2]
        por_familia.append({
            "familia": r[0], "valor_venda": round(r[1], 2),
            "valor_custo": round(r[2], 2),
            "margem": round(m, 2),
            "margem_pct": round(m / r[1] * 100 if r[1] else 0, 1),
        })

    margem_bruta = total_venda - total_custo
    return {
        "resumo": {
            "faturamento": round(total_venda, 2),
            "custo": round(total_custo, 2),
            "margem_bruta": round(margem_bruta, 2),
            "margem_pct": round(margem_bruta / total_venda * 100 if total_venda else 0, 1),
        },
        "por_produto": por_produto,
        "por_familia": por_familia,
    }


# ──────────────────────────────────────────────────────────────
# Alertas operacionais (topbar badge)
# ──────────────────────────────────────────────────────────────

@router.get("/alertas")
def get_alertas():
    items = []
    with db.get_conn() as conn:
        n_erros = conn.execute(
            "SELECT COUNT(*) FROM pedidos_fluxo WHERE status_fluxo='erro_faturamento'"
        ).fetchone()[0]
        if n_erros:
            items.append({"tipo": "danger", "icone": "bi-exclamation-triangle-fill",
                          "titulo": f"{n_erros} erro(s) de faturamento", "link": "controle.html"})

        n_entregues = conn.execute(
            "SELECT COUNT(*) FROM pedidos_fluxo WHERE status_fluxo='entregue'"
        ).fetchone()[0]
        if n_entregues:
            items.append({"tipo": "warn", "icone": "bi-truck",
                          "titulo": f"{n_entregues} entregue(s) sem finalização", "link": "controle.html"})

    criticos = db.produtos_listar(estoque_critico=True, limit=100)
    if criticos:
        items.append({"tipo": "warn", "icone": "bi-box-seam",
                      "titulo": f"{len(criticos)} produto(s) em estoque crítico",
                      "link": "estoque-produtos.html"})

    return {"total": len(items), "items": items}
