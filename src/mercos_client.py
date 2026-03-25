import os
import time
import logging
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class MercosClient:
    """
    Cliente para a API Mercos.
    Gerencia autenticação, paginação e throttling (429).
    Documentação: https://mercos.docs.apiary.io/
    """

    def __init__(self):
        self.base_url = os.getenv("MERCOS_BASE_URL", "https://sandbox.mercos.com")
        self.headers = {
            "ApplicationToken": os.getenv("MERCOS_APPLICATION_TOKEN"),
            "CompanyToken": os.getenv("MERCOS_COMPANY_TOKEN"),
            "Content-Type": "application/json",
        }
        self._validate_config()

    def _validate_config(self):
        missing = [
            k for k, v in self.headers.items()
            if k != "Content-Type" and (not v or "seu_" in str(v))
        ]
        if missing:
            raise EnvironmentError(
                f"Tokens Mercos não configurados: {missing}. Verifique o .env"
            )

    # ──────────────────────────────────────────────────────────
    # Requisição base com retry automático em 429
    # ──────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, **kwargs) -> dict | list:
        url = f"{self.base_url}{path}"
        max_retries = 5

        for attempt in range(max_retries):
            response = requests.request(
                method, url, headers=self.headers, timeout=30, **kwargs
            )

            if response.status_code == 429:
                try:
                    body = response.json()
                    wait = int(body.get("tempo_ate_permitir_novamente", 10))
                except Exception:
                    wait = 10
                logger.warning(f"[Mercos] Rate limit atingido. Aguardando {wait}s...")
                time.sleep(wait)
                continue

            if response.status_code == 404:
                return None

            response.raise_for_status()

            if response.status_code == 204 or not response.content:
                return {}

            return response.json()

        raise Exception(f"[Mercos] Falha após {max_retries} tentativas em {url}")

    # ──────────────────────────────────────────────────────────
    # GET com paginação automática
    # ──────────────────────────────────────────────────────────

    def _get_all(self, path: str, alterado_apos: str = None) -> list:
        params = {}
        if alterado_apos:
            params["alterado_apos"] = alterado_apos

        results = []
        url = path

        while url:
            data = self._request("GET", url, params=params)
            params = {}

            if isinstance(data, list):
                results.extend(data)
                break

            if isinstance(data, dict):
                results.extend(data.get("results", []))
                next_url = data.get("next")

                if next_url and next_url.startswith("http"):
                    next_url = "/" + "/".join(next_url.split("/")[3:])

                url = next_url
            else:
                break

        return results

    # ──────────────────────────────────────────────────────────
    # Pedidos
    # ──────────────────────────────────────────────────────────

    def get_pedidos(self, alterado_apos: str = None) -> list:
        pedidos = self._get_all("/v1/pedidos/", alterado_apos=alterado_apos)
        return pedidos

    def atualizar_status_pedido(self, pedido_id: int, status_id: int, detalhe: str = "") -> bool:
        payload = {
            "status_customizado_id": status_id,
            "detalhe": detalhe,
        }

        result = self._request(
            "POST",
            f"/v1/pedidos/{pedido_id}/status/",
            json=payload,
        )

        logger.info(f"[Mercos] Status do pedido {pedido_id} atualizado.")
        return result is not None

    def get_status_customizados(self) -> list:
        return self._get_all("/v1/statuspedidocustomizado/")

    def testar_conexao(self) -> bool:
        result = self._request("GET", "/v1/token_auth_status")
        logger.info(f"[Mercos] Conexão OK: {result}")
        return result is not None