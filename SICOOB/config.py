import logging
import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SICOOB
# ---------------------------------------------------------------------------
CLIENT_ID             = os.getenv("SICOOB_CLIENT_ID", "")
CERTIFICADO           = os.getenv("SICOOB_CERTIFICADO", "")
CHAVE_PRIVADA         = os.getenv("SICOOB_CHAVE_PRIVADA", "")
NUMERO_CLIENTE        = int(os.getenv("SICOOB_NUMERO_CLIENTE", "0"))
NUMERO_CONTA_CORRENTE = os.getenv("SICOOB_NUMERO_CONTA_CORRENTE", "")
SANDBOX               = os.getenv("SICOOB_SANDBOX", "true").lower() == "true"
TIMEOUT               = int(os.getenv("SICOOB_TIMEOUT", "30"))
SICOOB_WEBHOOK_SECRET = os.getenv("SICOOB_WEBHOOK_SECRET", "")

# ---------------------------------------------------------------------------
# VHSys (somente leitura — pré-preenchimento de formulário)
# ---------------------------------------------------------------------------
VHSYS_BASE_URL     = os.getenv("VHSYS_BASE_URL", "https://api.vhsys.com.br/v2").rstrip("/")
VHSYS_ACCESS_TOKEN = os.getenv("VHSYS_ACCESS_TOKEN", "")
VHSYS_SECRET_TOKEN = os.getenv("VHSYS_SECRET_TOKEN", "")


def validar():
    faltando = [k for k, v in {
        "SICOOB_CLIENT_ID":      CLIENT_ID,
        "SICOOB_CERTIFICADO":    CERTIFICADO,
        "SICOOB_CHAVE_PRIVADA":  CHAVE_PRIVADA,
        "SICOOB_NUMERO_CLIENTE": str(NUMERO_CLIENTE),
    }.items() if not v or v == "0"]
    if faltando:
        raise RuntimeError(f"Variáveis de ambiente não configuradas: {', '.join(faltando)}")

    if not VHSYS_ACCESS_TOKEN or not VHSYS_SECRET_TOKEN:
        logger.warning(
            "VHSYS_ACCESS_TOKEN / VHSYS_SECRET_TOKEN não configurados — "
            "busca de pedidos VHSys desabilitada."
        )
