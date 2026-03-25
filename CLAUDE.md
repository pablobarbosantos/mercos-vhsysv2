# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Real-time webhook integration between **Mercos** (mobile sales app) and **VHSys** (ERP system) for a Brazilian agricultural business. A local Windows server receives Mercos order webhooks, persists them in a queue, processes them asynchronously into VHSys, and sends WhatsApp notifications with an admin dashboard for monitoring.

## Running the System

Three processes must run simultaneously:

```powershell
# Terminal 1 — WhatsApp server (Node.js)
cd C:\mercos_vhsys_git\whatsapp_server
node server.js

# Terminal 2 — Python API (FastAPI)
cd C:\mercos_vhsys_git
python main.py

# Terminal 3 — ngrok tunnel (exposes port 8000 publicly)
C:\mercos_vhsys_git\ngrok.exe http 8000
```

Automated startup on Windows boot is configured via `start_server.bat` in Windows Task Scheduler.

**Não é necessário reiniciar tudo a cada teste.** Apenas o `main.py` precisa ser reiniciado quando houver mudança de código Python. Node e ngrok ficam rodando continuamente.

**ngrok e main.py devem rodar na mesma máquina** — o Mercos envia webhooks para a URL do ngrok, então onde estiver o ngrok precisa estar o `main.py`.

## Environment Setup

Copy `.env.example` to `.env` and fill in credentials. Required variables:

```
VHSYS_ACCESS_TOKEN=...
VHSYS_SECRET_TOKEN=...
VHSYS_BASE_URL=https://api.vhsys.com.br/v2
VHSYS_ID_BANCO=1287072
MERCOS_APPLICATION_TOKEN=...
MERCOS_COMPANY_TOKEN=...
WHATSAPP_ENABLED=true
WHATSAPP_NOTIFY_NUMBER=5534XXXXXXXXX
```

Optional variables (all have defaults):

```
FILA_MAX_TENTATIVAS=5          # max retry attempts before erro_permanente
FILA_WORKER_INTERVAL_SEG=10    # queue worker polling interval (seconds)
VHSYS_CACHE_TTL_HORAS=4        # how often to refresh product/client cache
AUDIT_SEQ_INTERVAL_MIN=15
AUDIT_FLUXO_INTERVAL_MIN=30
AUDIT_FECHAMENTO_HORA=20
```

## Architecture

### Data Flow

```
Mercos app → webhook → ngrok → FastAPI (port 8000)
                                    ↓
                         fila_eventos (SQLite) ← persist FIRST, return 200
                                    ↓
                    _job_processar_fila() — APScheduler, every 10s
                                    ↓
                            mercos_service.py  (translate order format)
                                    ↓
                            vhsys_service.py   (HTTP retry, client autocreation)
                                    ↓
                            src/database.py    (idempotency + state tracking)
                                    ↓
                            src/whatsapp.py → Node.js (port 3000) → WhatsApp
```

### Queue / Retry Pattern

Webhooks are **never processed directly** — they are persisted to `fila_eventos` first, then picked up by the worker. This prevents order loss on crashes.

- Worker runs every 10s, processes up to 5 items per run
- On startup, items stuck in `processando` (crash recovery) are reset to `pendente`
- Retry backoff on failure: 30s → 2min → 8min → 30min → `erro_permanente`
- HTTP calls to VHSys also retry internally (3 attempts, 2s/4s/8s backoff) on network errors or 5xx

### APScheduler Jobs

| Job | Interval | Function |
|---|---|---|
| `worker_fila_eventos` | every 10s | processes pending queue items |
| `auditoria_sequencia` | every 15min | detects gaps in Mercos order ID sequences |
| `auditoria_fluxo` | every 30min | alerts if orders stuck in workflow too long |
| `auditoria_fila_eventos` | every 15min | alerts if `erro_permanente` items exist |
| `refresh_cache_vhsys` | every 4h | refreshes product/client/payment cache |
| `fechamento_dia` | daily at 20h | WhatsApp daily summary |

## Key Files

| File | Responsibility |
|---|---|
| `main.py` | FastAPI app, all APScheduler jobs, webhook handler (persist-only) |
| `mercos_service.py` | Order translation, thread-safe per-order locks, idempotency |
| `vhsys_service.py` | VHSys API client with `_requisitar_com_retry()`, cache TTL |
| `src/database.py` | SQLite schema (9 tables), queue helpers, audit trail |
| `src/auditoria.py` | Audit jobs + `verificar_fila_eventos()` |
| `src/whatsapp.py` | HTTP client to Node.js with retry (3 attempts) |
| `src/admin_routes.py` | Admin dashboard endpoints + audit trail for manual actions |
| `whatsapp_server/server.js` | Express/whatsapp-web.js, number validation |

## Webhook Events

- `pedido.gerado` — persisted to queue, processed by worker
- `pedido.faturado` — persisted to queue only if order not yet in VHSys (fallback)
- `pedido.atualizado` — processed immediately (no queue), updates workflow state
- `pedido.cancelado` — processed immediately, marks as cancelled

## Database (SQLite — `data/sync.db`)

- `pedidos_processados` — idempotency: mercos_id → vhsys_id
- `pedidos_fluxo` — workflow states: recebido → processado → separado → enviado → cancelado/erro
- `fila_eventos` — persistent event queue (status: pendente/processando/ok/erro_permanente)
- `auditoria_sequencia` — gap detection in Mercos ID sequences
- `admin_acoes` — audit trail of manual actions from admin panel
- `mapa_clientes` — CNPJ/CPF → VHSys client ID cache
- `mapa_produtos` — Mercos SKU → VHSys product ID cache
- `erros_log` — error tracking
- `sync_timestamps` — last sync time per entity

SQLite runs in **WAL mode** (`PRAGMA journal_mode=WAL`) — safe for concurrent reads while writing.

## Admin API Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /admin/api/fila` | Queue stats by status |
| `GET /admin/api/acoes` | Audit trail of manual actions |
| `POST /admin/api/auditoria/verificar-agora` | Force immediate audit run |
| `POST /admin/api/auditoria/fluxo/{id}/separado` | Manually mark order as separated |
| `POST /admin/api/auditoria/fluxo/{id}/enviado` | Manually mark order as shipped |
| `POST /admin/api/reprocessar/{id}` | Re-queue a failed order |

## Utility Scripts

Located in `scripts/`:
- `testar_whatsapp.py` — test WhatsApp connection
- `resumo_diario.py` — generate daily summary
- `lembrete_boletos.py` — billing reminders

## Test Suite

All 15 tests are documented in `TESTES.md`. Helper script: `testes_ps.py`.

To run in PowerShell (venv must be active, `python main.py` must be running):
- Tests 1–2, 11–15: standalone, no server needed
- Tests 3–10: require server + use `Invoke-WebRequest` (not `curl`) and `testes_ps.py`

Known issue fixed: `verificar_sequencia()` in `src/auditoria.py` previously iterated `range(id_min, id_max+1)` causing O(153M) queries. Fixed to compare consecutive IDs — O(n).

**PowerShell compatibility notes:**
- Use `Invoke-WebRequest -Method POST` instead of `curl -X POST`
- Use `testes_ps.py` for multi-line Python SQL instead of `python -c` with complex quotes
- Venv: `.\venv\Scripts\activate` then `python` (not `py`)

## Problemas Conhecidos / Próximos Passos

### Transportadora (pendente)
Mapeamento em `resolver_frete()` em `vhsys_service.py` está com problemas. Revisar lógica de mapeamento nome → código VHSys e integração com cache de transportadoras. Não enviar campo transportadora até corrigido.

### Módulo Expedição (pendente)
Implementar fluxo de expedição: quando pedido é marcado como expedido/enviado no VHSys ou no Mercos, registrar no fluxo e notificar. Ainda não existe nenhuma lógica para isso.

### Contas a Receber / Parcelas (desativado)
`gerar_parcelas()` em `vhsys_service.py` existe mas **não é mais chamada** — o lançamento de boletos/parcelas é feito manualmente no VHSys. Não reativar sem validação.

## Workflow: Commit After Testing

**After every change that is tested and validated, commit automatically to GitHub.**

Steps Claude must follow after a successful test cycle:
1. `git add` the changed files
2. `git commit` with a descriptive message
3. `git push origin main`
