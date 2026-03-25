"""
VhsysService
============
- Busca cliente por CNPJ na API (sem CSV)
- Cadastra cliente automaticamente se não existir
- Cache de produtos, condições de pagamento e transportadoras
- Condição de pagamento mapeada pelo nome
- Frete: modalidade numérica + nome da transportadora como texto
"""

import logging
import os
import re
import time

import requests

logger = logging.getLogger(__name__)


def _normalizar_cnpj(cnpj: str) -> str:
    return re.sub(r"[.\-/\s]", "", str(cnpj)).strip()


def _formatar_cnpj(cnpj: str) -> str:
    n = _normalizar_cnpj(cnpj)
    if len(n) == 14:
        return f"{n[:2]}.{n[2:5]}.{n[5:8]}/{n[8:12]}-{n[12:]}"
    return cnpj


def _normalizar_nome(nome: str) -> str:
    return re.sub(r"\s*/\s*", "/", str(nome).strip()).upper()


class VhsysService:

    # Modalidade de frete Mercos → (codigo_vhsys, nome_exibicao)
    # codigo: 0=Remetente, 1=Destinatário, 2=Terceiros, 9=Sem Frete
    _MODALIDADE_FRETE = {
        "FOB":          (0, "FOB"),
        "REMETENTE":    (0, "FOB"),
        "CIF":          (1, "CIF"),
        "DESTINATARIO": (1, "CIF"),
        "DESTINATÁRIO": (1, "CIF"),
        "TERCEIROS":    (2, "Terceiros"),
        "SEM FRETE":    (9, ""),
        "SEM_FRETE":    (9, ""),
    }

    # Statuses HTTP que merecem retry (transitórios)
    _RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(self):
        self.access_token = os.getenv("VHSYS_ACCESS_TOKEN")
        self.secret_token = os.getenv("VHSYS_SECRET_TOKEN")

        if not self.access_token or not self.secret_token:
            raise EnvironmentError(
                "Variáveis de ambiente VHSYS_ACCESS_TOKEN e VHSYS_SECRET_TOKEN "
                "não configuradas. Defina-as no arquivo .env ou no painel do Railway."
            )

        self.base_url = os.getenv("VHSYS_BASE_URL", "https://api.vhsys.com.br/v2").rstrip("/")
        self.id_banco = int(os.getenv("VHSYS_ID_BANCO", "1287072"))
        self.headers  = {
            "access-token":        self.access_token,
            "secret-access-token": self.secret_token,
            "Content-Type":        "application/json",
        }

        self.cache_produtos:        list[dict] = []
        self.cache_condicoes:       list[dict] = []
        self.cache_transportadoras: list[dict] = []
        self._cache_carregado    = False
        self._cache_carregado_em: float = 0.0
        self._cache_ttl_seg      = int(os.getenv("VHSYS_CACHE_TTL_HORAS", "4")) * 3600

        logger.info("[VhsysService] Inicializando...")
        self._carregar_caches()

    # ──────────────────────────────────────────────────────────────────────────
    # RETRY HTTP
    # ──────────────────────────────────────────────────────────────────────────

    def _requisitar_com_retry(
        self,
        method: str,
        url: str,
        json_body: dict | None = None,
        params: dict | None = None,
        max_tentativas: int = 3,
        timeout: int = 30,
    ) -> requests.Response | None:
        """
        Faz requisição HTTP com retry em erros transitórios.
        Retorna Response na primeira tentativa bem-sucedida, ou None após esgotar.
        Não retenta 400/404/422 (erros definitivos da requisição).
        """
        for tentativa in range(1, max_tentativas + 1):
            try:
                if method == "GET":
                    resp = requests.get(url, headers=self.headers, params=params, timeout=timeout)
                else:
                    resp = requests.post(url, headers=self.headers, json=json_body, timeout=timeout)

                if resp.status_code not in self._RETRY_STATUS:
                    return resp  # sucesso ou erro definitivo

                logger.warning(
                    f"[HTTP] {method} {url} → HTTP {resp.status_code} "
                    f"(tentativa {tentativa}/{max_tentativas})"
                )

            except (requests.Timeout, requests.ConnectionError) as e:
                logger.warning(
                    f"[HTTP] {method} {url} → {type(e).__name__} "
                    f"(tentativa {tentativa}/{max_tentativas}): {e}"
                )
                if tentativa == max_tentativas:
                    return None

            if tentativa < max_tentativas:
                delay = 2 ** tentativa  # 2s, 4s, 8s
                logger.info(f"[HTTP] Aguardando {delay}s antes da próxima tentativa...")
                time.sleep(delay)

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # CACHES
    # ──────────────────────────────────────────────────────────────────────────

    def _carregar_paginas(self, endpoint: str, nome_log: str) -> list:
        resultado = []
        offset    = 0
        limit     = 250
        pagina    = 1
        while True:
            url  = f"{self.base_url}/{endpoint}"
            resp = self._requisitar_com_retry(
                "GET", url, params={"limit": limit, "offset": offset}, timeout=30
            )
            if resp is None or resp.status_code != 200:
                status = resp.status_code if resp else "sem_resposta"
                logger.error(f"[CACHE {nome_log}] HTTP {status} — interrompendo paginação.")
                break
            data = resp.json().get("data", [])
            resultado.extend(data)
            logger.debug(f"[CACHE {nome_log}] Página {pagina}: +{len(data)} | Total: {len(resultado)}")
            if len(data) < limit:
                break
            offset += limit
            pagina += 1
        return resultado

    def _carregar_caches(self, forcar: bool = False):
        agora = time.monotonic()
        if not forcar and self._cache_carregado and (agora - self._cache_carregado_em) < self._cache_ttl_seg:
            return

        logger.info(f"[CACHE] {'Forçando recarga' if forcar else 'Carregando'} caches VHSys...")
        self.cache_produtos        = self._carregar_paginas("produtos",              "PRODUTOS")
        self.cache_condicoes       = self._carregar_paginas("condicoes-pagamento",   "CONDICOES")
        self.cache_transportadoras = self._carregar_paginas("transportadoras",       "TRANSPORTADORAS")
        self._cache_carregado    = True
        self._cache_carregado_em = time.monotonic()
        logger.info(
            f"[CACHE] Concluído — {len(self.cache_produtos)} produtos | "
            f"{len(self.cache_condicoes)} condições | "
            f"{len(self.cache_transportadoras)} transportadoras."
        )

    def forcar_refresh_cache(self):
        """Chamado pelo APScheduler para renovar cache periodicamente."""
        self._carregar_caches(forcar=True)

    def carregar_todos_produtos(self):
        """Mantido para compatibilidade com MercosService."""
        self._carregar_caches()

    # ──────────────────────────────────────────────────────────────────────────
    # LOOKUPS
    # ──────────────────────────────────────────────────────────────────────────

    # Mapa direto: nome Mercos (lower) → ID VHSys
    # Fonte: correlação manual Pablo Agro 2026-03-18
    _MAPA_CONDICOES_ID = {
        # Parcelado
        "14 / 21":  "185163",
        "14/21":    "185163",
        "14/":      "185164",
        "14 dias":  "185164",
        "21 / 28":  "185165",
        "21/28":    "185165",
        "21/":      "185166",
        "21 dias":  "185166",
        "28/":      "185167",
        "28 dias":  "185167",
        "30 dias":  "185167",   # aproximação: 28 dias é o mais próximo
        "7 / 14":   "185168",
        "7/14":     "185168",
        "7/":       "185169",
        "7 dias":   "185169",
        # Formas especiais
        "dinheiro": "185170",
        "a vista":  "185170",
        "à vista":  "185170",
        "pix":      "185171",
        "assinar":  "185172",
    }

    def buscar_id_condicao(self, nome: str) -> str | None:
        """
        Busca id_condicao pelo nome.
        Tenta: 1) mapa direto por ID  2) match exato cache  3) match parcial cache
        """
        if not nome:
            return None

        # 1) Mapa direto — IDs confirmados manualmente
        nome_lower = nome.strip().lower()
        id_direto = self._MAPA_CONDICOES_ID.get(nome_lower)
        if id_direto:
            logger.info(f"[CONDICAO] '{nome}' -> id={id_direto} (mapa direto)")
            return id_direto

        # 2) Match exato normalizado no cache
        nome_norm = _normalizar_nome(nome)
        for c in self.cache_condicoes:
            if _normalizar_nome(c.get("nome_condicao", "")) == nome_norm:
                logger.info(f"[CONDICAO] '{nome}' (exato cache) -> id={c['id_condicao']}")
                return str(c["id_condicao"])

        # 3) Match parcial no cache
        for c in self.cache_condicoes:
            if nome_norm in _normalizar_nome(c.get("nome_condicao", "")):
                logger.info(f"[CONDICAO] '{nome}' (parcial cache) -> id={c['id_condicao']}")
                return str(c["id_condicao"])

        logger.warning(f"[CONDICAO] '{nome}' nao encontrada.")
        return None

    def resolver_frete(self, nome: str) -> tuple:
        """
        Retorna (frete_por_pedido, transportadora_pedido, id_transportadora).

        frete_por_pedido:
          0 = Remetente (FOB)
          1 = Destinatário (CIF)
          2 = Terceiros
          9 = Sem Frete

        transportadora_pedido: nome para exibição no VHSys.
        id_transportadora: ID do cadastro no VHSys (0 se não encontrado).
        """
        if not nome:
            return 9, "", 0

        nome_upper = nome.upper().strip()

        # Busca primeiro no cache pelo nome — retorna o ID cadastrado
        for t in self.cache_transportadoras:
            desc     = str(t.get("desc_transportadora", "")).upper()
            fantasia = str(t.get("fantasia_transportadora") or "").upper()
            if nome_upper == desc or nome_upper == fantasia or nome_upper in desc or nome_upper in fantasia:
                id_transp = int(t.get("id_transportadora", 0) or 0)
                nome_real = t.get("desc_transportadora", nome)
                logger.info(f"[FRETE] '{nome}' → transportadora cadastrada: '{nome_real}' (id={id_transp})")
                return 0, nome_real, id_transp

        # Fallback para mapa de modalidades (FOB/CIF/etc.)
        if nome_upper in self._MODALIDADE_FRETE:
            codigo, nome_transp = self._MODALIDADE_FRETE[nome_upper]
            logger.info(f"[FRETE] '{nome}' → modalidade={codigo} | nome='{nome_transp}'")
            return codigo, nome_transp, 0

        logger.warning(f"[FRETE] '{nome}' não reconhecida — usando como texto livre com Remetente.")
        return 0, nome, 0

    # ──────────────────────────────────────────────────────────────────────────
    # CLIENTE
    # ──────────────────────────────────────────────────────────────────────────

    def buscar_cliente_por_cnpj(self, cnpj: str) -> dict | None:
        cnpj_normalizado = _normalizar_cnpj(cnpj)
        if not cnpj_normalizado:
            logger.error("[CLIENTE] CNPJ vazio.")
            return None
        cnpj_formatado = _formatar_cnpj(cnpj_normalizado)
        logger.debug(f"[CLIENTE] Buscando CNPJ: {cnpj_formatado}")

        resp = self._requisitar_com_retry(
            "GET",
            f"{self.base_url}/clientes",
            params={"cnpj_cliente": cnpj_formatado, "limit": 5},
            timeout=15,
        )
        if resp is None:
            logger.error(f"[CLIENTE] Falha ao buscar CNPJ {cnpj_formatado} após retries.")
            return None
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            for cliente in data:
                if _normalizar_cnpj(cliente.get("cnpj_cliente", "")) == cnpj_normalizado:
                    logger.info(f"[CLIENTE] Encontrado! ID: {cliente['id_cliente']} | Nome: {cliente.get('razao_cliente')}")
                    return {
                        "id_cliente":   str(cliente["id_cliente"]),
                        "razao_social": cliente.get("razao_cliente", ""),
                    }
        logger.info(f"[CLIENTE] CNPJ {cnpj_formatado} não encontrado.")
        return None

    def cadastrar_cliente(self, dados_mercos: dict) -> dict | None:
        cnpj      = dados_mercos.get("cliente_cnpj", "")
        razao     = dados_mercos.get("cliente_razao_social", "")
        fantasia  = dados_mercos.get("cliente_nome_fantasia", "") or razao
        telefones = dados_mercos.get("cliente_telefone", [])
        telefone  = telefones[0] if isinstance(telefones, list) and telefones else ""

        if not razao or not cnpj:
            logger.error("[CLIENTE] Razão social ou CNPJ ausente.")
            return None

        payload = {
            "razao_cliente":    razao,
            "tipo_pessoa":      "PJ",
            "tipo_cadastro":    "Cliente",
            "cnpj_cliente":     _formatar_cnpj(_normalizar_cnpj(cnpj)),
            "fantasia_cliente": fantasia,
            "fone_cliente":     telefone,
            "endereco_cliente": dados_mercos.get("cliente_rua", ""),
            "numero_cliente":   dados_mercos.get("cliente_numero", ""),
            "bairro_cliente":   dados_mercos.get("cliente_bairro", ""),
            "cidade_cliente":   dados_mercos.get("cliente_cidade", ""),
            "uf_cliente":       dados_mercos.get("cliente_estado", ""),
            "situacao_cliente": "Ativo",
        }

        logger.info(f"[CLIENTE] Cadastrando: {razao} | CNPJ: {cnpj}")
        resp = self._requisitar_com_retry(
            "POST",
            f"{self.base_url}/clientes",
            json_body=payload,
            timeout=15,
        )
        if resp is None:
            logger.error(f"[CLIENTE] Falha ao cadastrar {razao} após retries.")
            return None
        if resp.status_code in (200, 201):
            data    = resp.json().get("data", {})
            id_novo = data.get("id_cliente")
            logger.info(f"[CLIENTE] Cadastrado! ID: {id_novo} | Nome: {razao}")
            return {"id_cliente": str(id_novo), "razao_social": razao}
        else:
            logger.error(f"[CLIENTE] Falha HTTP {resp.status_code}: {resp.text[:400]}")
            return None

    def buscar_ou_cadastrar_cliente(self, dados_mercos: dict) -> dict | None:
        cnpj    = dados_mercos.get("cliente_cnpj", "")
        cliente = self.buscar_cliente_por_cnpj(cnpj)
        if cliente:
            return cliente
        logger.info(f"[CLIENTE] Não encontrado — cadastrando. CNPJ: {cnpj}")
        return self.cadastrar_cliente(dados_mercos)

    # ──────────────────────────────────────────────────────────────────────────
    # PEDIDO
    # ──────────────────────────────────────────────────────────────────────────

    def lancar_pedido_venda(self, dados: dict) -> dict | None:
        logger.info(f"[PEDIDO] Iniciando | CNPJ: {dados.get('cliente_cnpj')} | Pedido Mercos: #{dados.get('numero')}")

        # ── Cliente ───────────────────────────────────────────────────────────
        cliente = self.buscar_ou_cadastrar_cliente(dados)
        if not cliente:
            logger.error("[PEDIDO] Cliente não encontrado — abortando.")
            return None

        id_cliente   = cliente["id_cliente"]
        nome_cliente = cliente["razao_social"]

        # ── Condição de pagamento ─────────────────────────────────────────────
        nome_condicao = dados.get("condicao_pagamento", "")
        id_condicao   = self.buscar_id_condicao(nome_condicao)

        # ── Frete ─────────────────────────────────────────────────────────────
        nome_transportadora = dados.get("transportadora_nome", "FOB")
        frete_codigo, frete_nome, frete_id = self.resolver_frete(nome_transportadora)

        # ── Itens ─────────────────────────────────────────────────────────────
        numero_pedido = dados.get("numero") or dados.get("id", "?")
        itens_payload = []

        for idx, item in enumerate(dados.get("itens", []), 1):
            sku   = str(item.get("codigo_referencia", "")).strip()
            qtd   = float(item.get("quantidade", 0))
            valor = float(item.get("valor_unitario", 0))
            desc  = item.get("_descricao", f"Item {idx}")

            if qtd <= 0 or valor <= 0:
                logger.warning(f"[ITEM {idx}] Inválido → pulando")
                continue

            id_produto_vhsys = None
            for prod in self.cache_produtos:
                if str(prod.get("cod_produto", "")).strip() == sku:
                    id_produto_vhsys = prod.get("id_produto")
                    logger.info(f"[PRODUTO] SKU '{sku}' → id={id_produto_vhsys}")
                    break

            if not id_produto_vhsys:
                nome_upper = desc.upper().strip()
                for prod in self.cache_produtos:
                    nome_vhsys = str(prod.get("desc_produto", "")).upper().strip()
                    if nome_upper in nome_vhsys or nome_vhsys in nome_upper:
                        id_produto_vhsys = prod.get("id_produto")
                        logger.info(f"[PRODUTO] Nome '{desc}' → id={id_produto_vhsys}")
                        break

            if not id_produto_vhsys:
                logger.warning(f"[PRODUTO] '{desc}' (SKU: {sku}) não encontrado.")

            itens_payload.append({
                "id_produto":          str(id_produto_vhsys) if id_produto_vhsys else "",
                "desc_produto":        desc,
                "qtde_produto":        qtd,
                "valor_unit_produto":  valor,
                "valor_total_produto": round(qtd * valor, 2),
            })

        if not itens_payload:
            logger.error("[PEDIDO] Nenhum item válido — abortando.")
            return None

        # ── Payload final ─────────────────────────────────────────────────────
        payload = {
            "id_cliente":            id_cliente,
            "nome_cliente":          nome_cliente,
            "status_pedido":         "Em Aberto",
            "data_pedido":           dados.get("data", ""),
            "obs_pedido":            f"Origem Mercos - Pedido #{numero_pedido} | Cond: {nome_condicao}",
            "frete_por_pedido":      frete_codigo,
            "transportadora_pedido": frete_nome,
            "id_transportadora":     frete_id,
            "produtos":              itens_payload,
        }

        if id_condicao:
            payload["condicao_pagamento_id"] = id_condicao
            logger.info(f"[PEDIDO] Condição: '{nome_condicao}' (id={id_condicao})")
        else:
            payload["condicao_pagamento"] = nome_condicao
            logger.warning(f"[PEDIDO] Condição '{nome_condicao}' não encontrada — enviando como texto.")

        logger.info(f"[PEDIDO] Frete: modalidade={frete_codigo} | transportadora='{frete_nome}'")
        logger.debug(f"[PEDIDO] Payload: {payload}")

        # ── Envio ─────────────────────────────────────────────────────────────
        resp = self._requisitar_com_retry(
            "POST",
            f"{self.base_url}/pedidos",
            json_body=payload,
            timeout=30,
        )
        if resp is None:
            logger.error("[PEDIDO] Falha ao enviar pedido ao VHSys após retries.")
            return None

        logger.debug(f"[PEDIDO] HTTP {resp.status_code} | {resp.elapsed.total_seconds():.2f}s")
        logger.debug(f"[PEDIDO] Resposta: {resp.text}")

        if resp.status_code in (200, 201):
            resultado   = resp.json()
            pedido_data = resultado.get("data", [{}])[0]
            id_vhsys    = pedido_data.get("id_ped", "?")
            logger.info(f"[PEDIDO] ✅ Criado! ID VHSYS: {id_vhsys}")

            return resultado
        else:
            logger.error(f"[PEDIDO] ❌ HTTP {resp.status_code}: {resp.text[:800]}")
            return None

    # ──────────────────────────────────────────────────────────────────────────
    # PARCELAS
    # ──────────────────────────────────────────────────────────────────────────

    def gerar_parcelas(self, id_ped: str, numero_pedido, id_cliente: str,
                       nome_cliente: str, valor_total: float,
                       data_pedido: str, id_condicao: str) -> list:
        """
        Gera as contas a receber (parcelas) para um pedido recém-criado.
        Usa os dados da condição de pagamento do cache para calcular
        vencimentos, quantidade e forma de pagamento.
        Alerta via WhatsApp se alguma parcela falhar.
        """
        from datetime import datetime, timedelta

        if not id_condicao:
            logger.warning("[PARCELAS] Sem condição de pagamento — parcelas não geradas.")
            return []

        condicao = None
        for c in self.cache_condicoes:
            if str(c.get("id_condicao")) == str(id_condicao):
                condicao = c
                break

        if not condicao:
            logger.warning(f"[PARCELAS] Condição id={id_condicao} não encontrada no cache.")
            return []

        qtde            = condicao.get("qtde_parcelas", 1)
        primeira        = condicao.get("primeira_parcela_pagamento", 30)
        intervalo       = condicao.get("intervalo_pagamento", 30)
        forma_pagamento = condicao.get("forma_pagamento", "Boleto")

        try:
            data_base = datetime.strptime(data_pedido, "%Y-%m-%d")
        except Exception:
            data_base = datetime.now()

        valor_parcela = round(valor_total / qtde, 2)
        valor_ultima  = round(valor_total - valor_parcela * (qtde - 1), 2)

        parcelas_criadas = []
        parcelas_falhas  = []

        for i in range(1, qtde + 1):
            dias       = primeira + intervalo * (i - 1)
            vencimento = (data_base + timedelta(days=dias)).strftime("%Y-%m-%d")
            valor      = valor_parcela if i < qtde else valor_ultima

            payload = {
                "nome_conta":      f"Pedido {numero_pedido}",
                "identificacao":   f"Ped_{id_ped}",
                "id_banco":        self.id_banco,
                "id_cliente":      id_cliente,
                "nome_cliente":    nome_cliente,
                "vencimento_rec":  vencimento,
                "valor_rec":       f"{valor:.2f}",
                "data_emissao":    data_pedido,
                "n_documento_rec": f"{numero_pedido}-{i}",
                "observacoes_rec": f"Pedido nro. {numero_pedido}",
                "liquidado_rec":   "Nao",
                "forma_pagamento": forma_pagamento,
            }

            logger.info(
                f"[PARCELAS] Criando parcela {i}/{qtde} | "
                f"Vencimento: {vencimento} | Valor: R$ {valor:.2f} | "
                f"Forma: {forma_pagamento}"
            )

            resp = self._requisitar_com_retry(
                "POST",
                f"{self.base_url}/contas-receber",
                json_body=payload,
                timeout=15,
            )
            if resp is not None and resp.status_code in (200, 201):
                id_conta = resp.json().get("data", {}).get("id_conta_rec")
                logger.info(f"[PARCELAS] ✅ Parcela {i} criada! ID: {id_conta}")
                parcelas_criadas.append(id_conta)
            else:
                status_code = resp.status_code if resp else "sem_resposta"
                logger.error(f"[PARCELAS] ❌ Parcela {i}/{qtde} falhou: HTTP {status_code}")
                parcelas_falhas.append(i)

        if parcelas_falhas:
            from src import database as db_mod
            msg = (
                f"Pedido VHSys {id_ped} (Mercos #{numero_pedido}): "
                f"parcelas {parcelas_falhas} de {qtde} NÃO criadas. "
                f"Verifique contas-a-receber no VHSys."
            )
            db_mod.registrar_erro("parcelas", str(id_ped), msg)
            logger.error(f"[PARCELAS] {msg}")
            try:
                from src.whatsapp import get_whatsapp
                get_whatsapp().notificar_pedido_erro(
                    numero_pedido=numero_pedido,
                    mercos_id=0,
                    cliente=nome_cliente,
                    motivo=f"Parcelas {parcelas_falhas}/{qtde} falharam — verificar VHSys",
                )
            except Exception:
                pass

        return parcelas_criadas

    # ──────────────────────────────────────────────────────────────────────────
    # BOLETOS VENCIDOS
    # ──────────────────────────────────────────────────────────────────────────

    def buscar_boletos_vencidos(self) -> list[dict]:
        """
        Consulta /contas-receber no VHSys e retorna boletos em aberto
        com vencimento até ontem (ou antes).
        """
        from datetime import date, timedelta
        ate = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        resultados = []
        pagina = 1

        while True:
            resp = self._requisitar_com_retry(
                "GET",
                f"{self.base_url}/contas-receber",
                params={
                    "liquidado_rec": "Nao",
                    "vencimento_rec_fim": ate,
                    "limit": 100,
                    "offset": (pagina - 1) * 100,
                },
                timeout=20,
            )
            if resp is None or resp.status_code != 200:
                logger.warning(f"[Boletos] Falha ao buscar contas-receber: HTTP {resp.status_code if resp else 'sem_resposta'}")
                break

            data = resp.json()
            itens = data.get("data", [])
            if not itens:
                break

            resultados.extend(itens)
            if len(itens) < 100:
                break
            pagina += 1

        logger.info(f"[Boletos] {len(resultados)} boleto(s) vencido(s) encontrado(s).")
        return resultados
