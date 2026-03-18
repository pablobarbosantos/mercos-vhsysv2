import os
import time
import logging
import requests

logger = logging.getLogger(__name__)


class VhsysClient:
    """
    Cliente para a API vhsys v2.
    URL base: https://api.vhsys.com/v2
    Autenticação: headers access-token + secret-access-token

    ⚠️  ATENÇÃO: o payload exato de POST /pedidos precisa ser
    validado contra a documentação em developers.vhsys.com.br
    (requer login). Os campos abaixo são o melhor mapeamento
    possível baseado no padrão REST da v2. Ajuste conforme
    os erros 400 retornados na primeira execução.
    """

    def __init__(self):
        self.base_url = os.getenv("VHSYS_BASE_URL", "https://api.vhsys.com/v2")
        self.headers = {
            "access-token":        os.getenv("VHSYS_ACCESS_TOKEN"),
            "secret-access-token": os.getenv("VHSYS_SECRET_ACCESS_TOKEN"),
            "Content-Type":        "application/json",
        }
        self._validate_config()

    def _validate_config(self):
        missing = [k for k, v in self.headers.items() if not v or "seu_" in str(v)]
        if missing:
            raise EnvironmentError(
                f"Tokens vhsys não configurados: {missing}. Verifique o .env"
            )

    # ──────────────────────────────────────────────────────────
    # Requisição base com retry em 429 e 5xx
    # ──────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, **kwargs) -> dict | list | None:
        url = f"{self.base_url}{path}"
        delays = [1, 2, 4]  # backoff exponencial

        for attempt, delay in enumerate(delays + [None]):
            try:
                response = requests.request(
                    method, url, headers=self.headers, timeout=30, **kwargs
                )

                if response.status_code == 429:
                    wait = int(response.headers.get("Retry-After", 10))
                    logger.warning(f"[vhsys] Rate limit. Aguardando {wait}s...")
                    time.sleep(wait)
                    continue

                if response.status_code in (500, 502, 503, 504) and delay:
                    logger.warning(f"[vhsys] Erro {response.status_code}, retry em {delay}s...")
                    time.sleep(delay)
                    continue

                if response.status_code == 404:
                    return None

                if not response.ok:
                    # Loga o corpo do erro para facilitar o debug do payload
                    logger.error(
                        f"[vhsys] Erro {response.status_code} em {method} {path}: "
                        f"{response.text[:500]}"
                    )
                    response.raise_for_status()

                if not response.content:
                    return {}

                return response.json()

            except requests.exceptions.Timeout:
                if delay:
                    logger.warning(f"[vhsys] Timeout, retry em {delay}s...")
                    time.sleep(delay)
                else:
                    raise

        raise Exception(f"[vhsys] Falha após {len(delays)+1} tentativas em {url}")

    # ──────────────────────────────────────────────────────────
    # Pedidos
    # ──────────────────────────────────────────────────────────

    def criar_pedido(self, payload: dict) -> dict:
        """
        POST /pedidos
        Retorna o pedido criado com o ID gerado pelo vhsys.

        ⚠️  Campos obrigatórios estimados (confirmar na doc oficial):
          - codigo_cliente (int)
          - data_pedido (str "YYYY-MM-DD")
          - itens (list de dicts com codigo_produto, quantidade, valor_unitario)
          - condicao_pagamento (int - ID)
        """
        result = self._request("POST", "/pedidos/", json=payload)
        if result:
            logger.info(f"[vhsys] Pedido criado. ID: {result.get('id') or result.get('codigo')}")
        return result

    def buscar_pedido(self, pedido_id: int) -> dict | None:
        """GET /pedidos/{id} — verifica se pedido já existe (evita duplicata)."""
        return self._request("GET", f"/pedidos/{pedido_id}/")

    def listar_clientes(self) -> list:
        """GET /clientes — para montar o mapa cnpj/cpf → id vhsys."""
        result = self._request("GET", "/clientes/", params={"limite": 100})
        if isinstance(result, dict):
            return result.get("data", result.get("results", []))
        return result or []

    def buscar_cliente_por_documento(self, documento: str) -> dict | None:
        """Busca cliente pelo CPF/CNPJ para obter o ID interno do vhsys."""
        doc = documento.replace(".", "").replace("-", "").replace("/", "")
        result = self._request("GET", "/clientes/", params={"cnpj_cpf": doc})
        if isinstance(result, dict):
            items = result.get("data", result.get("results", []))
            return items[0] if items else None
        return None

    def listar_produtos(self) -> list:
        """GET /produtos — para montar o mapa codigo → id vhsys."""
        result = self._request("GET", "/produtos/", params={"limite": 100})
        if isinstance(result, dict):
            return result.get("data", result.get("results", []))
        return result or []
