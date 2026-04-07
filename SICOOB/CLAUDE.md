# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Module Does

API integration with **SICOOB** (cooperative bank, code 756) for boleto emission and cobrança bancária management. Uses the `sicoob` Python SDK, which handles authentication (mTLS certificate + client_id) and wraps the SICOOB Open Finance REST API.

This module is part of the larger [Mercos-VHSys integration](../CLAUDE.md). Its role in the payment workflow:

```
VHSys order confirmed
    → gerar_parcelas() creates installments in VHSys (vhsys_service.py:~504)
    → SICOOB module emits corresponding boletos via API
    → Payment webhooks from SICOOB update pedidos_fluxo state
```

## Authentication

SICOOB API uses **mTLS**: a client certificate + private key pair issued by the bank, plus a `client_id` from the developer portal. Sandbox mode is available and makes no real charges.

Required `.env` variables:

```env
SICOOB_CLIENT_ID=seu_client_id_aqui
SICOOB_CERTIFICADO=/caminho/absoluto/para/certificado.pem
SICOOB_CHAVE_PRIVADA=/caminho/absoluto/para/chave_privada.key
SICOOB_SANDBOX=true          # false in production
SICOOB_NUMERO_CLIENTE=123456  # número do convênio/cliente no SICOOB
```

## Structure

```
SICOOB/
├── app.py                     # FastAPI standalone — porta 8001
├── config.py                  # Carrega .env com os.getenv()
├── requirements.txt
├── .env.example
├── services/
│   ├── sicoob_client.py       # Singleton lazy do SDK Sicoob
│   ├── boleto_service.py      # emitir, consultar, alterar, baixar, segunda_via
│   └── exceptions.py          # SicoobError, BoletoError, BoletoNaoEncontrado
└── webhooks/
    └── sicoob_webhook.py      # Notificações de pagamento (futuro)
```

## Key Service Methods

`BoletoService` in `services/boleto_service.py` exposes:

| Method | SDK call | Description |
|--------|----------|-------------|
| `emitir(payload)` | `api.emitir_boleto()` | Create and register boleto |
| `consultar(numero_cliente, nosso_numero)` | `api.consultar_boleto()` | Get boleto status |
| `alterar(numero_cliente, nosso_numero, dados)` | `api.alterar_boleto()` | Update due date/value |
| `baixar(numero_cliente, nosso_numero)` | `api.baixar_boleto()` | Write off boleto |
| `segunda_via(numero_cliente, nosso_numero)` | `api.segunda_via_boleto()` | Get PDF second copy |

## Running the App

```bash
cd C:\mercos_vhsys_git\SICOOB

# Instalar dependências (primeira vez)
pip install -r requirements.txt

# Rodar
python app.py
# ou com reload automático:
uvicorn app:app --reload --port 8001
```

API disponível em `http://localhost:8001`. Documentação interativa em `http://localhost:8001/docs`.

Antes de rodar, copie `.env.example` para `.env` e preencha as credenciais.

## Integration Points (Parent Project)

- **Trigger**: after `lancar_pedido_venda()` in `../vhsys_service.py` succeeds
- **Input data**: parcelas from `gerar_parcelas()` in `../vhsys_service.py` (~line 504)
- **Mount webhook router**: in `../main.py` via `app.include_router(sicoob_router)`
- **WhatsApp alerts on failure**: use `../src/whatsapp.py` — same pattern as other services

## Sample Remessa File

`../REMESSA_75619032026_084238.txt` is a real CNAB 240 remessa file from SICOOB (56 lines, fixed-width). Useful as a reference for the data model and field names even though this module uses the REST API, not file-based remessa.

## Logging Convention

Follow the parent project pattern: standard `logging` module + `RotatingFileHandler`. Do **not** introduce `loguru` unless the rest of the project adopts it first — keep one logging stack.

```python
import logging
logger = logging.getLogger(__name__)
```
