import logging
import config
from sicoob import Sicoob
from services.exceptions import SicoobConfigError

logger = logging.getLogger(__name__)

_client: Sicoob | None = None


def get_client() -> Sicoob:
    """Retorna o cliente SDK inicializado (singleton lazy)."""
    global _client
    if _client is None:
        _client = _inicializar()
    return _client


def _inicializar() -> Sicoob:
    try:
        config.validar()
    except RuntimeError as e:
        raise SicoobConfigError(str(e)) from e

    logger.info(
        "Inicializando cliente SICOOB SDK (sandbox=%s, numero_cliente=%s)",
        config.SANDBOX,
        config.NUMERO_CLIENTE,
    )
    environment = "sandbox" if config.SANDBOX else "production"
    client = Sicoob(
        client_id=config.CLIENT_ID,
        certificado=config.CERTIFICADO,
        chave_privada=config.CHAVE_PRIVADA,
        environment=environment,
    )
    logger.info("Cliente SICOOB inicializado com sucesso.")
    return client
