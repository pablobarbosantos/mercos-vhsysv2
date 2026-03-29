import os
import sys
import time
import logging
import requests
from dotenv import load_dotenv

if getattr(sys, "frozen", False):
    load_dotenv(os.path.join(os.path.dirname(sys.executable), ".env"))
else:
    load_dotenv()

VHSYS_BASE_URL    = os.getenv("VHSYS_BASE_URL", "https://api.vhsys.com.br/v2").rstrip("/")
VHSYS_ACCESS_TOKEN = os.getenv("VHSYS_ACCESS_TOKEN")
VHSYS_SECRET_TOKEN = os.getenv("VHSYS_SECRET_TOKEN")

_RETRY_STATUS = {429, 500, 502, 503, 504}

logger = logging.getLogger("consulta_vhsys.vhsys_adapter")


def _headers() -> dict:
    return {
        "access-token":        VHSYS_ACCESS_TOKEN,
        "secret-access-token": VHSYS_SECRET_TOKEN,
        "Content-Type":        "application/json",
    }


def requisitar(
    method: str,
    path: str,
    params: dict | None = None,
    body: dict | None = None,
    max_tentativas: int = 3,
) -> dict | None:
    """Faz requisição ao VHSys com retry e backoff exponencial."""
    url = f"{VHSYS_BASE_URL}/{path.lstrip('/')}"
    for tentativa in range(1, max_tentativas + 1):
        try:
            resp = requests.request(
                method,
                url,
                headers=_headers(),
                params=params,
                json=body,
                timeout=30,
            )
            if resp.status_code in _RETRY_STATUS:
                delay = 2 ** tentativa
                logger.warning(
                    "[VHSYS] HTTP %s em %s — tentativa %d/%d, aguardando %ds",
                    resp.status_code, path, tentativa, max_tentativas, delay,
                )
                if tentativa < max_tentativas:
                    time.sleep(delay)
                continue
            return resp.json()
        except (requests.Timeout, requests.ConnectionError) as exc:
            delay = 2 ** tentativa
            logger.warning(
                "[VHSYS] Erro de rede em %s — tentativa %d/%d: %s",
                path, tentativa, max_tentativas, exc,
            )
            if tentativa < max_tentativas:
                time.sleep(delay)
    logger.error("[VHSYS] Todas as tentativas falharam para %s", path)
    return None


def listar_produtos_paginado() -> list[dict]:
    """Busca todos os produtos do VHSys com paginação automática."""
    resultado = []
    offset = 0
    limit  = 250
    pagina = 1
    while True:
        data = requisitar("GET", "produtos", params={"limit": limit, "offset": offset, "lixeira": "Nao"})
        if data is None or data.get("code") != 200:
            code = data.get("code") if data else "sem_resposta"
            logger.error("[VHSYS] GET /produtos falhou (code=%s) — interrompendo paginação", code)
            break
        items = data.get("data", [])
        resultado.extend(items)
        logger.debug("[VHSYS] Página %d: +%d produtos | total: %d", pagina, len(items), len(resultado))
        if len(items) < limit:
            break
        offset += limit
        pagina += 1
    logger.info("[VHSYS] Total de produtos obtidos: %d", len(resultado))
    return resultado


def get_produto(vhsys_id: int) -> dict | None:
    """Busca um produto específico pelo ID."""
    data = requisitar("GET", f"produtos/{vhsys_id}")
    if data is None or data.get("code") != 200:
        logger.warning("[VHSYS] GET /produtos/%d falhou: %s", vhsys_id, data)
        return None
    return data.get("data")


def criar_produto(dados: dict) -> dict | None:
    """
    Cria produto no VHSys via POST /produtos.
    dados: desc_produto, unidade_produto, valor_produto, id_categoria (opt),
           codigo_barra_produto (opt), valor_custo_produto (opt).
    Retorna o dict do produto criado (com id_produto) ou None se falhar.
    """
    body = {
        "desc_produto":    dados["desc_produto"],
        "unidade_produto": dados.get("unidade_produto", "UN"),
        "valor_produto":   dados.get("valor_produto", 0),
        "status_produto":  "Ativo",
        "tipo_produto":    "Produto",
    }
    if dados.get("id_categoria"):
        body["id_categoria"] = dados["id_categoria"]
    if dados.get("codigo_barra_produto"):
        body["codigo_barra_produto"] = dados["codigo_barra_produto"]
    if dados.get("valor_custo_produto"):
        body["valor_custo_produto"] = dados["valor_custo_produto"]

    data = requisitar("POST", "produtos", body=body)
    if data is None or data.get("code") not in (200, 201):
        logger.error("[VHSYS] POST /produtos falhou: %s", data)
        return None
    logger.info("[VHSYS] Produto criado: id=%s nome=%s", data.get("data", {}).get("id_produto"), dados["desc_produto"])
    return data.get("data")


def listar_categorias() -> list[dict]:
    """Retorna todas as categorias ativas do VHSys."""
    resultado = []
    offset = 0
    limit = 100
    while True:
        data = requisitar("GET", "categorias", params={"limit": limit, "offset": offset})
        if data is None or data.get("code") != 200:
            break
        items = data.get("data", [])
        resultado.extend(items)
        if len(items) < limit:
            break
        offset += limit
    return resultado


def atualizar_produto(vhsys_id: int, preco: float, ean: str | None = None) -> bool:
    """
    Atualiza preço e EAN no VHSys via PUT /produtos/{id}.
    Estoque é tratado separadamente por lancar_movimento_estoque().
    Retorna True se sucesso.
    """
    body: dict = {"valor_produto": preco}
    if ean:
        body["codigo_barra_produto"] = ean
    data = requisitar("PUT", f"produtos/{vhsys_id}", body=body)
    if data is None or data.get("code") not in (200, 201):
        logger.error("[VHSYS] PUT /produtos/%d falhou: %s", vhsys_id, data)
        return False
    logger.info("[VHSYS] PUT /produtos/%d ok — preco=%.2f", vhsys_id, preco)
    return True


def lancar_movimento_estoque(vhsys_id: int, delta: float, obs: str = "") -> bool:
    """
    Lança movimento de estoque via POST /produtos/{id}/estoque.
    delta > 0 → Entrada | delta < 0 → Saída.
    Retorna True se sucesso.
    """
    if abs(delta) < 0.001:
        return True
    tipo = "Entrada" if delta > 0 else "Saida"
    body = {
        "tipo_estoque": tipo,
        "qtde_estoque": str(abs(round(delta, 4))),
        "obs_estoque":  obs or "Ajuste via Consulta VHSys",
    }
    data = requisitar("POST", f"produtos/{vhsys_id}/estoque", body=body)
    if data is None or data.get("code") not in (200, 201):
        logger.error("[VHSYS] POST /produtos/%d/estoque falhou: %s", vhsys_id, data)
        return False
    logger.info("[VHSYS] Movimento %s de %.4f unidades lançado para produto %d", tipo, abs(delta), vhsys_id)
    return True
