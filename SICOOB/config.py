import os
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("SICOOB_CLIENT_ID", "")
CERTIFICADO = os.getenv("SICOOB_CERTIFICADO", "")
CHAVE_PRIVADA = os.getenv("SICOOB_CHAVE_PRIVADA", "")
NUMERO_CLIENTE = int(os.getenv("SICOOB_NUMERO_CLIENTE", "0"))
NUMERO_CONTA_CORRENTE = os.getenv("SICOOB_NUMERO_CONTA_CORRENTE", "")
SANDBOX = os.getenv("SICOOB_SANDBOX", "true").lower() == "true"
TIMEOUT = int(os.getenv("SICOOB_TIMEOUT", "30"))


def validar():
    faltando = [k for k, v in {
        "SICOOB_CLIENT_ID": CLIENT_ID,
        "SICOOB_CERTIFICADO": CERTIFICADO,
        "SICOOB_CHAVE_PRIVADA": CHAVE_PRIVADA,
        "SICOOB_NUMERO_CLIENTE": str(NUMERO_CLIENTE),
    }.items() if not v or v == "0"]
    if faltando:
        raise RuntimeError(f"Variáveis de ambiente não configuradas: {', '.join(faltando)}")
