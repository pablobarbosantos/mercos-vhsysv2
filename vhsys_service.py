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

    def __init__(self):
        self.access_token = os.getenv("VHSYS_ACCESS_TOKEN")
        self.secret_token = os.getenv("VHSYS_SECRET_TOKEN")

        if not self.access_token or not self.secret_token:
            raise EnvironmentError(
                "Variáveis de ambiente VHSYS_ACCESS_TOKEN e VHSYS_SECRET_TOKEN "
                "não configuradas. Defina-as no arquivo .env ou no painel do Railway."
            )

        self.base_url = "https://api.vhsys.com.br/v2"
        self.headers  = {
            "access-token":        self.access_token,
            "secret-access-token": self.secret_token,
            "Content-Type":        "application/json",
        }

        self.cache_produtos:        list[dict] = []
        self.cache_condicoes:       list[dict] = []
        self.cache_transportadoras: list[dict] = []
        self._cache_carregado = False

        logger.info("[VhsysService] Inicializando...")
        self._carregar_caches()

    # ──────────────────────────────────────────────────────────────────────────
    # CACHES
    # ──────────────────────────────────────────────────────────────────────────

    def _carregar_paginas(self, endpoint: str, nome_log: str) -> list:
        resultado = []
        offset    = 0
        limit     = 250
        pagina    = 1
        while True:
            url = f"{self.base_url}/{endpoint}?limit={limit}&offset={offset}"
            try:
                response = requests.get(url, headers=self.headers, timeout=30)
                if response.status_code != 200:
                    logger.error(f"[CACHE {nome_log}] HTTP {response.status_code}: {response.text[:200]}")
                    break
                data = response.json().get("data", [])
                resultado.extend(data)
                logger.debug(f"[CACHE {nome_log}] Página {pagina}: +{len(data)} | Total: {len(resultado)}")
                if len(data) < limit:
                    break
                offset += limit
                pagina += 1
            except Exception as e:
                logger.error(f"[CACHE {nome_log}] Erro: {e}")
                break
        return resultado

    def _carregar_caches(self):
        if self._cache_carregado:
            return
        logger.info("[CACHE] Carregando produtos...")
        self.cache_produtos = self._carregar_paginas("produtos", "PRODUTOS")
        logger.info(f"[CACHE] {len(self.cache_produtos)} produtos carregados.")

        logger.info("[CACHE] Carregando condições de pagamento...")
        self.cache_condicoes = self._carregar_paginas("condicoes-pagamento", "CONDICOES")
        logger.info(f"[CACHE] {len(self.cache_condicoes)} condições carregadas.")

        logger.info("[CACHE] Carregando transportadoras...")
        self.cache_transportadoras = self._carregar_paginas("transportadoras", "TRANSPORTADORAS")
        logger.info(f"[CACHE] {len(self.cache_transportadoras)} transportadoras carregadas.")

        self._cache_carregado = True

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
        Retorna (frete_por_pedido, transportadora_pedido).

        frete_por_pedido:
          0 = Remetente (FOB)
          1 = Destinatário (CIF)
          2 = Terceiros
          9 = Sem Frete

        transportadora_pedido: nome como texto livre para exibição no VHSYS.
        """
        if not nome:
            return 9, ""

        nome_upper = nome.upper().strip()

        if nome_upper in self._MODALIDADE_FRETE:
            codigo, nome_transp = self._MODALIDADE_FRETE[nome_upper]
            logger.info(f"[FRETE] '{nome}' → modalidade={codigo} | transportadora='{nome_transp}'")
            return codigo, nome_transp

        for t in self.cache_transportadoras:
            desc     = str(t.get("desc_transportadora", "")).upper()
            fantasia = str(t.get("fantasia_transportadora") or "").upper()
            if nome_upper in desc or desc in nome_upper or nome_upper in fantasia:
                nome_real = t.get("desc_transportadora", nome)
                logger.info(f"[FRETE] '{nome}' → transportadora cadastrada: '{nome_real}'")
                return 0, nome_real

        logger.warning(f"[FRETE] '{nome}' não reconhecida — usando como texto livre com Remetente.")
        return 0, nome

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
        try:
            response = requests.get(
                f"{self.base_url}/clientes",
                headers=self.headers,
                params={"cnpj_cliente": cnpj_formatado, "limit": 5},
                timeout=15,
            )
            if response.status_code == 200:
                data = response.json().get("data", [])
                for cliente in data:
                    if _normalizar_cnpj(cliente.get("cnpj_cliente", "")) == cnpj_normalizado:
                        logger.info(f"[CLIENTE] Encontrado! ID: {cliente['id_cliente']} | Nome: {cliente.get('razao_cliente')}")
                        return {
                            "id_cliente":   str(cliente["id_cliente"]),
                            "razao_social": cliente.get("razao_cliente", ""),
                        }
            logger.info(f"[CLIENTE] CNPJ {cnpj_formatado} não encontrado.")
            return None
        except Exception as e:
            logger.error(f"[CLIENTE] Erro ao buscar: {e}", exc_info=True)
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
        try:
            response = requests.post(
                f"{self.base_url}/clientes",
                json=payload,
                headers=self.headers,
                timeout=15,
            )
            if response.status_code in (200, 201):
                data    = response.json().get("data", {})
                id_novo = data.get("id_cliente")
                logger.info(f"[CLIENTE] Cadastrado! ID: {id_novo} | Nome: {razao}")
                return {"id_cliente": str(id_novo), "razao_social": razao}
            else:
                logger.error(f"[CLIENTE] Falha HTTP {response.status_code}: {response.text[:400]}")
                return None
        except Exception as e:
            logger.error(f"[CLIENTE] Erro ao cadastrar: {e}", exc_info=True)
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
        # Regra fixa: Remetente (FOB) — modalidade 0
        frete_codigo = 0
        frete_nome   = "FOB"
        logger.info("[FRETE] Fixo: Remetente/FOB (modalidade=0)")

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
        try:
            response = requests.post(
                f"{self.base_url}/pedidos",
                json=payload,
                headers=self.headers,
                timeout=30,
            )
            logger.debug(f"[PEDIDO] HTTP {response.status_code} | {response.elapsed.total_seconds():.2f}s")
            logger.debug(f"[PEDIDO] Resposta: {response.text}")

            if response.status_code in (200, 201):
                resultado   = response.json()
                pedido_data = resultado.get("data", [{}])[0]
                id_vhsys    = pedido_data.get("id_ped", "?")
                logger.info(f"[PEDIDO] ✅ Criado! ID VHSYS: {id_vhsys}")

                valor_total = float(pedido_data.get("valor_total_nota", 0) or 0)
                self.gerar_parcelas(
                    id_ped=str(id_vhsys),
                    numero_pedido=numero_pedido,
                    id_cliente=id_cliente,
                    nome_cliente=nome_cliente,
                    valor_total=valor_total,
                    data_pedido=dados.get("data", ""),
                    id_condicao=id_condicao,
                )
                return resultado
            else:
                logger.error(f"[PEDIDO] ❌ HTTP {response.status_code}: {response.text[:800]}")
                return None
        except Exception as e:
            logger.error(f"[PEDIDO] Erro: {e}", exc_info=True)
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

        for i in range(1, qtde + 1):
            dias       = primeira + intervalo * (i - 1)
            vencimento = (data_base + timedelta(days=dias)).strftime("%Y-%m-%d")
            valor      = valor_parcela if i < qtde else valor_ultima

            payload = {
                "nome_conta":      f"Pedido {numero_pedido}",
                "identificacao":   f"Ped_{id_ped}",
                "id_banco":        1287072,
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

            try:
                response = requests.post(
                    f"{self.base_url}/contas-receber",
                    json=payload,
                    headers=self.headers,
                    timeout=15,
                )
                if response.status_code in (200, 201):
                    id_conta = response.json().get("data", {}).get("id_conta_rec")
                    logger.info(f"[PARCELAS] ✅ Parcela {i} criada! ID: {id_conta}")
                    parcelas_criadas.append(id_conta)
                else:
                    logger.error(f"[PARCELAS] ❌ Parcela {i} falhou: {response.text[:300]}")
            except Exception as e:
                logger.error(f"[PARCELAS] Erro na parcela {i}: {e}")

        return parcelas_criadas
