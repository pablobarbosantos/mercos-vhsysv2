# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Real-time webhook integration between **Mercos** (mobile sales app) and **VHSys** (ERP system) for a Brazilian agricultural business. A local Windows server receives Mercos order webhooks, persists them in a queue, processes them asynchronously into VHSys, and sends WhatsApp notifications with an admin dashboard for monitoring.

## Running the System

Two processes must run simultaneously:

```powershell
# Terminal 1 — WhatsApp server (Node.js)
cd C:\mercos_vhsys_git\whatsapp_server
node server.js

# Terminal 2 — Python API (FastAPI)
cd C:\mercos_vhsys_git
python main.py
```

O túnel público é gerenciado pelo **Tailscale Funnel** (substitui o ngrok). A URL é estável e persiste entre reinicializações — configure uma única vez no painel Mercos.

```powershell
# Ativar o Funnel (primeira vez ou após reset):
tailscale funnel 8000

# Ver a URL do webhook:
# Windows: tailscale status --json  (campo Self.DNSName)
# Linux:   bash mostrar_webhook.sh
```

A URL segue o formato: `https://<hostname>.<tailnet>.ts.net/webhook/mercos`

Automated startup on Windows boot is configured via `start_server.bat` in Windows Task Scheduler.

**Não é necessário reiniciar tudo a cada teste.** Apenas o `main.py` precisa ser reiniciado quando houver mudança de código Python. O Tailscale Funnel é gerenciado pelo serviço Tailscale em background.

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
Mercos app → webhook → Tailscale Funnel → FastAPI (port 8000)
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

### Módulo Expedição (desativado — API VHSys não expõe o módulo)
Testado em 25/03/2026. A API VHSys não tem endpoint `/expedicoes` (retorna HTTP 200 com `code:404` no corpo) e o campo `situacao_pedido` do pedido não muda quando uma expedição é criada ou concluída.

O código de suporte existe em `src/expedicao.py` e `vhsys_service.py` mas o job está comentado em `main.py`. Marcar `separado`/`enviado` manualmente via painel admin.

**Trigger manual disponível:** `POST /admin/api/expedicao/verificar-agora` (aciona fallback GET /pedidos/{id})

### Contas a Receber / Parcelas (desativado)
`gerar_parcelas()` em `vhsys_service.py` existe mas **não é mais chamada** — o lançamento de boletos/parcelas é feito manualmente no VHSys. Não reativar sem validação.

### Numeração de Pedidos VHSys = Mercos (pendente)
O pedido criado no VHSys recebe um número próprio do VHSys, diferente do `mercos_id`. O objetivo é fazer com que o número do pedido no VHSys seja idêntico ao do Mercos para facilitar rastreabilidade.

Investigar: a API VHSys (`POST /pedidos`) aceita campo `numero_pedido` (ou equivalente) para forçar a numeração. Se aceitar, passar `mercos_id` como número do pedido em `vhsys_service.py` na função que cria o pedido. Validar que não gera conflito com numeração interna do VHSys antes de implementar.

## Workflow: Commit After Testing

**After every change that is tested and validated, commit automatically to GitHub.**

Steps Claude must follow after a successful test cycle:
1. `git add` the changed files
2. `git commit` with a descriptive message
3. `git push origin main`

---

## Módulo consulta_vhsys

Backend **LOCAL FIRST** para consulta e gestão de produtos VHSys. Arquivos em `consulta_vhsys/`.
DB separado: `data/consulta_vhsys.db`. Logs: `logs/consulta_vhsys.log`.

### Estrutura

```
consulta_vhsys/
├── database/database.py       — SQLite CRUD, init_db(), get_conn()
├── services/vhsys_adapter.py  — HTTP VHSys (standalone, sem ORM)
├── services/duplicidade_service.py
├── services/product_lookup.py
├── services/sync_service.py
└── scripts/sync_inicial.py    — importação única
```

### Como usar

```bash
# Importação inicial (executar uma vez)
python consulta_vhsys/scripts/sync_inicial.py

# Verificar duplicidades
from consulta_vhsys.services.duplicidade_service import verificar_duplicidades
conflitos = verificar_duplicidades()

# Sincronizar edições locais → VHSys
from consulta_vhsys.services.sync_service import sincronizar_sujos
resultado = sincronizar_sujos()

# Atualizar base local com dados atuais do VHSys
from consulta_vhsys.services.sync_service import atualizar_base
stats = atualizar_base()
```

### Regras principais

- Busca sempre no SQLite local — API VHSys apenas para import/sync/atualização de base
- `dirty=1`: produto tem edição local pendente de sync
- EAN duplicado: **BLOQUEADO** — nunca decisão automática
- Conflito de sync: VHSys mudou externamente (preço diferente do baseline) → operador decide
- `atualizar_base()` preserva edições locais (dirty=1)

### Tabela produtos (consulta_vhsys.db)

| Campo | Descrição |
|---|---|
| `vhsys_id` | PK do produto no VHSys |
| `preco` | Preço local (pode estar editado) |
| `preco_vhsys` | Preço na última importação (baseline para detecção de conflito) |
| `estoque` | Estoque local |
| `dirty` | 1 = tem edição pendente de sync |
| `ativo` | 0 = inativado pelo operador |

### Nota sobre estoque

VHSys não expõe `qtde_produto` em todos os planos/endpoints. Se `PUT /produtos/{id}` não persistir estoque, o campo fica apenas local. Verificar resultado após primeira execução de `sincronizar_sujos()`.

---

## V1 — O que ficou incompleto / pendente

Esta seção documenta itens que foram planejados ou parcialmente implementados na V1 mas não chegaram ao estado funcional completo. Servem de base para decisão sobre o que entra na V2.

### 1. Transportadora — implementada mas nunca validada

**Arquivo:** `vhsys_service.py` — função `resolver_frete()` (linhas ~251-286)

A função existe e tenta lookup no `cache_transportadoras`, mas nunca foi validado se o campo chega corretamente ao VHSys. O CLAUDE.md instrui "não enviar campo transportadora até corrigir". Risco: campo pode estar sendo ignorado ou gerando erro silencioso no payload enviado.

**O que fazer:** Logar o payload completo enviado ao `POST /pedidos` e comparar com o que aparece no VHSys. Validar as modalidades FOB/CIF/TERCEIROS no dict `_MODALIDADE_FRETE`.

### 2. Expedição automática — código morto por limitação da API

**Arquivos:** `src/expedicao.py` (linhas 35-99), `vhsys_service.py` (linhas ~672-804), `main.py` (linhas ~393-396, comentadas)

A API VHSys não expõe `/expedicoes` (testado em 25/03/2026). O job `job_sync_expedicao` está comentado em `main.py`. As funções `buscar_expedicoes_recentes()`, `buscar_situacao_pedido()` e `sincronizar_expedicao()` em `vhsys_service.py` são código morto enquanto a API não mudar.

**Consequência:** separado/enviado são marcados manualmente via painel admin. Se a VHSys eventualmente expuser o endpoint, basta descomentar o job em `main.py`.

### 3. Parcelas automáticas — função órfã

**Arquivo:** `vhsys_service.py` — função `gerar_parcelas()` (linhas ~504-606)

Função completa e bem implementada: calcula parcelas, datas, valores, lança via `POST /contas-receber`, alerta WhatsApp em falha. Mas não é chamada em nenhum lugar — decisão consciente de fazer o lançamento manualmente no VHSys. Pode ser reativada na V2 como opção configurável.

### 4. Numeração Mercos = VHSys — nunca investigado

**Arquivo:** `vhsys_service.py` — função `lancar_pedido_venda()`

Pedido criado no VHSys recebe ID próprio, diferente do `mercos_id`. Dificulta rastreabilidade. Investigar se `POST /pedidos` aceita campo `numero_pedido` ou similar. Se aceitar, passar `mercos_id` lá. Validar antes que não gera conflito com sequência interna do VHSys.

### 5. Roteirização — motor existe, sem volante

**Arquivo:** `src/routing.py`

Funções implementadas e funcionais: `geocodificar()` (Nominatim + Photon), `obter_matriz_osrm()`, `resolver_tsp()` (NetworkX), `gerar_link_google_maps()`, `otimizar_rota()`. Mas **nenhum endpoint HTTP chama essas funções** e o painel admin não tem interface para roteirização. O motor existe, falta a integração. Ficará assim para V2.

### 6. Confirmação automática para cliente — desativada sem motivo documentado

**Arquivo:** `mercos_service.py` — linhas ~130-141 (bloco comentado)

Bloco WhatsApp para enviar confirmação ao cliente quando pedido é recebido. Comentado, motivo não registrado. Possível que tenha sido desativado durante testes para evitar spam. Avaliar reativar na V2.

### 7. Tabelas criadas no banco mas nunca usadas

**Arquivo:** `src/database.py`

- `mapa_clientes` — planejado como cache CNPJ → VHSys ID, zero inserts/selects no código
- `mapa_produtos` — planejado como cache Mercos SKU → VHSys ID, idem
- `status_customizados` — tabela criada, nunca populada, provavelmente para status customizados do webhook `pedido.atualizado`

Esses eram caches planejados que nunca foram integrados. Podem ser aproveitados na V2 ou removidos se decidirem não usar.

### 8. Campos de endereço em pedidos_fluxo — gravados, nunca lidos

**Arquivo:** `src/database.py` — tabela `pedidos_fluxo`

Campos `cidade`, `bairro`, `rua`, `numero_end`, `cep` são preenchidos em `fluxo_registrar_recebido()` mas nenhuma query os lê para relatórios ou roteirização. São dados valiosos que estão no banco esperando ser usados.

---

## V2 — Roadmap Completo

Esta seção reúne todas as melhorias planejadas para a V2, incluindo itens do usuário e sugestões técnicas identificadas na análise do código. Na hora de implementar, releia e decida o que executar.

### Prioridade Alta — Operacional Imediato

#### V2-1. Estado "Entregue" no fluxo operacional

Adicionar o estado `entregue` ao fluxo (atualmente termina em `enviado`).

Novo fluxo completo:
```
recebido → processado → separado → enviado → entregue
                                           ↘ separado (devolução)
```

**O que mudar:**
- `src/database.py`: `ALTER TABLE pedidos_fluxo ADD COLUMN entregue_em TEXT`
- `src/database.py`: nova função `fluxo_marcar_entregue(mercos_id)`
- `src/admin_routes.py`: novo endpoint `POST /admin/api/auditoria/fluxo/{id}/entregue`
- `templates/admin.html`: nova guia "Entregue" no painel

#### V2-2. Persistência de rotas no banco de dados

Criar tabelas para armazenar rotas emitidas e pedidos vinculados.

**Schema sugerido:**
```sql
CREATE TABLE rotas (
    numero       INTEGER PRIMARY KEY AUTOINCREMENT,
    data_saida   TEXT,
    data_chegada TEXT,
    motorista_id INTEGER REFERENCES motoristas(id),
    veiculo_id   INTEGER REFERENCES veiculos(id),
    km_saida     INTEGER,
    km_chegada   INTEGER,
    combustivel_saida   REAL,
    combustivel_chegada REAL,
    status       TEXT DEFAULT 'planejada',  -- planejada|saiu|chegou|concluida
    criado_em    TEXT NOT NULL
);

CREATE TABLE rota_pedidos (
    rota_numero  INTEGER REFERENCES rotas(numero),
    mercos_id    INTEGER REFERENCES pedidos_fluxo(mercos_id),
    ordem        INTEGER NOT NULL,          -- posição na rota otimizada
    status       TEXT DEFAULT 'pendente',   -- pendente|entregue|devolvido
    PRIMARY KEY (rota_numero, mercos_id)
);
```

**Arquivo:** `src/database.py` — adicionar tabelas e funções CRUD

#### V2-3. Confirmação de saída ao emitir rota

Ao gerar uma rota, pedidos mudam de `separado` → `enviado`. Se o motorista não confirmar a saída em X minutos, os pedidos voltam para `separado`.

**Fluxo:**
1. Operador clica "Emitir Rota" → pedidos vão para `enviado`, rota salva no BD com `status='planejada'`
2. Job APScheduler a cada 5min verifica rotas `planejada` com mais de 30min sem confirmação
3. Se não confirmada: pedidos voltam para `separado`, rota marcada como `cancelada`
4. Se confirmada pelo operador (botão "Caminhão Saiu"): rota vai para `status='saiu'`

**Arquivo:** `main.py` — novo job `job_verificar_rotas_pendentes`

#### V2-4. Tela de chegada da rota (conferência de entrega)

Quando o caminhão volta, o operador abre a tela de chegada e:

1. Informa KM atual do veículo e combustível
2. O sistema exibe pedidos da rota um a um
3. Para cada pedido: botão "Entregue" ou "Devolver p/ Separação"
   - Entregue: pedido vai para `entregue`, anota forma de pagamento recebida (dinheiro/Pix/boleto)
   - Devolvido: pedido volta para `separado`, campo `motivo_devolucao` opcional
4. Ao final: tela de resumo mostrando:
   - Total entregue (quantidade e valor)
   - Total devolvido
   - **Quanto dinheiro o motorista deve ter na mão** (soma dos pedidos pagos em dinheiro)
   - **Quanto Pix deve ter sido recebido** (soma dos pedidos pagos em Pix)
   - KM rodado e consumo estimado

**Arquivos:**
- `src/admin_routes.py`: endpoints `POST /admin/api/rotas/{num}/chegada` e `POST /admin/api/rotas/{num}/pedidos/{id}/conferir`
- `templates/admin.html`: tela de conferência

#### V2-5. Cadastro de motoristas e veículos

Sem isso, o controle de saída/chegada não tem contexto.

**Schema sugerido:**
```sql
CREATE TABLE motoristas (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    nome    TEXT NOT NULL,
    telefone TEXT,
    ativo   INTEGER DEFAULT 1
);

CREATE TABLE veiculos (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    placa   TEXT NOT NULL UNIQUE,
    modelo  TEXT,
    ano     INTEGER,
    ativo   INTEGER DEFAULT 1
);
```

Interface no painel admin: tela simples de cadastro/listagem.

#### V2-6. Autenticação no painel admin

**Problema atual:** o painel `/admin` não tem nenhuma autenticação. Como é exposto via Tailscale Funnel, qualquer pessoa com a URL pode acessar, ver pedidos, reprocessar fila, marcar status.

**Opções (da mais simples à mais robusta):**

- **Opção A — Senha simples com sessão** (recomendada para começar): FastAPI `SessionMiddleware` + formulário de login. Uma senha configurada via `.env`. Simples, zero dependências.
- **Opção B — TOTP (Google Authenticator)**: biblioteca `pyotp`. Gera QR code no setup, valida código de 6 dígitos a cada 30s. Mais seguro, sem senha para vazar.
- **Opção C — IP whitelist**: middleware que bloqueia qualquer IP fora de uma lista. Funciona se o operador tiver IP fixo.

**Arquivo:** `src/admin_routes.py` — adicionar middleware de autenticação

---

### Prioridade Média — Melhoria Operacional

#### V2-7. Romaneio em PDF profissional

Documento impresso que o motorista leva na saída.

**Conteúdo do romaneio:**
- Cabeçalho: logo da empresa, nº da rota, data, motorista, veículo, placa
- Lista numerada na ordem otimizada de entrega
- Por pedido: cliente, endereço completo, telefone, valor total, forma de pagamento esperada
- Rodapé: totais (qtd entregas, valor total, total dinheiro, total Pix)
- QR code do Google Maps do endereço de cada parada (opcional)

**Biblioteca:** `fpdf2` (pip install fpdf2). Não tem dependências nativas, funciona no Windows sem Ghostscript.

**Arquivo:** novo `src/romaneio.py` — função `gerar_romaneio_pdf(rota_numero) -> bytes`

#### V2-8. QR Codes Google Maps segmentados

O Google Maps suporta no máximo 9 waypoints por URL. Para rotas com mais paradas, dividir em segmentos.

**Lógica:**
```python
# Cada segmento: origem + até 8 paradas intermediárias + destino
# URL format: https://maps.google.com/maps/dir/origem/parada1/.../destino
segmentos = chunks(paradas_ordenadas, 9)
for i, seg in enumerate(segmentos):
    url = gerar_url_google_maps(seg)
    qr_code = gerar_qr(url)  # biblioteca: qrcode
    # Incluir no PDF: "Parte {i+1} de {total}"
```

**Bibliotecas:** `qrcode[pil]` para QR codes, já integrar no romaneio PDF.

#### V2-9. Numeração de pedidos Mercos = VHSys

Investigar se `POST /pedidos` da API VHSys aceita campo `numero_pedido` ou equivalente.

**Como testar:**
```python
# Em vhsys_service.py:lancar_pedido_venda(), adicionar ao payload:
payload["numero_pedido"] = dados.get("numero")  # número Mercos
# Verificar no VHSys se o pedido aparece com o número correto
# Verificar se rejeita quando numero já existe (conflito de sequência)
```

Se funcionar, o rastreio entre Mercos e VHSys fica trivial. Se rejeitar duplicatas, precisa de lógica de sufixo (ex: `12345-R1` para reprocessamentos).

#### V2-10. Integração routing com endpoints HTTP

Hoje `src/routing.py` tem o motor completo mas zero endpoints expõem isso.

**O que implementar:**
```
POST /admin/api/rotas/otimizar
  body: { "mercos_ids": [123, 456, 789] }
  response: { "ordem_otimizada": [...], "distancia_total_km": 42.5, "links_google_maps": [...] }

POST /admin/api/rotas/emitir
  body: { "mercos_ids": [123, 456, 789], "motorista_id": 1, "veiculo_id": 2 }
  response: { "rota_numero": 15, "pdf_url": "/admin/rotas/15/romaneio.pdf" }
```

**Arquivos:** `src/admin_routes.py` + `src/routing.py` (já existe, só precisa ser chamado)

---

### Prioridade Baixa — Nice to Have

#### V2-11. Dashboard de eficiência logística

Os dados já estão no banco (`pedidos_fluxo` tem todos os timestamps). Falta só a query de análise.

**Métricas úteis:**
- Custo por entrega (combustível / número de entregas por rota)
- Tempo médio da rota (saida → chegada)
- Taxa de devolução por período / por motorista
- Valor médio por pedido entregue
- Ranking de clientes por volume (usando `pedidos_fluxo.cliente`)
- Mapa de calor de entregas por bairro/cidade (dados já estão em `pedidos_fluxo.bairro`)

**Implementação:** queries SQL agregadas + gráficos simples no admin com Chart.js (sem dependência nova, apenas CDN).

#### V2-12. Confirmação automática para cliente via WhatsApp (reativar)

Bloco comentado em `mercos_service.py:130-141`. Quando o pedido é confirmado no VHSys, o cliente recebe WhatsApp automático: "Seu pedido #XXXX foi recebido e está sendo processado."

**Cuidado:** verificar se o número de telefone do cliente vem nos dados do Mercos e está no formato E.164. O `whatsapp_server/server.js` tem validação de número.

#### V2-13. Parcelas automáticas (reativar como opção)

`gerar_parcelas()` em `vhsys_service.py` está completa. Para reativar com segurança:
1. Adicionar variável `.env`: `PARCELAS_AUTO=false` (padrão desativado)
2. Em `mercos_service.py`, após `lancar_pedido_venda()` ter sucesso, chamar condicionalmente
3. Testar com um pedido real antes de ativar em produção
4. Monitorar via `GET /contas-receber` para verificar se parcelas foram criadas

#### V2-14. Integração compras ↔ estoque local

`compras/service.py:146` — lançamento de estoque comentado. NF-e são processadas mas `consulta_vhsys.db` não é atualizado.

**Fluxo desejado:** NF-e entrada confirmada → `atualizar_base()` no `consulta_vhsys` com novas quantidades → estoque local sincronizado.

**Risco:** VHSys pode não expor estoque via API em todos os planos (já documentado em consulta_vhsys). Testar antes de implementar.

#### V2-15. Alertas de eficiência de combustível

Com os dados de KM saída/chegada e combustível saída/chegada (V2-3/V2-4), calcular:
- Consumo médio por veículo (km/L)
- Alerta WhatsApp se consumo estiver muito acima da média histórica (possível problema mecânico ou rota ineficiente)
- Custo estimado por rota com base no preço do combustível (configurável via `.env`)

---

## Notas Técnicas para Implementação da V2

### Ordem recomendada de implementação

Seguir esta ordem minimiza retrabalho e mantém o sistema funcional a cada etapa:

```
1. Cadastro de motoristas e veículos (V2-5)
   — Base para tudo que envolve saída/chegada

2. Estado "Entregue" + tabelas de rotas no banco (V2-1 + V2-2)
   — Schema antes de qualquer lógica de negócio

3. Integração routing com endpoints (V2-10)
   — Motor já existe, só expor

4. Confirmação de saída + retorno para separação (V2-3)
   — Depende de rotas no banco

5. Tela de chegada e conferência (V2-4)
   — Depende de rotas no banco + estado entregue

6. Autenticação no painel (V2-6)
   — Pode ser feito a qualquer momento, mas antes de ir p/ produção com tudo novo

7. Romaneio PDF (V2-7) + QR Codes (V2-8)
   — Depois que rota funciona de ponta a ponta
```

### Dependências Python necessárias para V2

```
fpdf2          # geração de PDF (romaneio)
qrcode[pil]    # QR codes para Google Maps
pyotp          # TOTP autenticação (se escolher Opção B)
Pillow         # já provavelmente instalado; necessário para qrcode[pil]
```

### Banco de dados — estratégia de migração

Todas as alterações de schema devem ser feitas como migrações incrementais (não recriar tabelas). Padrão já estabelecido em `src/database.py`:

```python
# Exemplo de migration segura (não quebra banco existente):
conn.execute("ALTER TABLE pedidos_fluxo ADD COLUMN entregue_em TEXT")
conn.execute("ALTER TABLE pedidos_fluxo ADD COLUMN rota_numero INTEGER")
```

Adicionar as migrations em `init_db()` com `try/except` para `OperationalError: duplicate column name` (idempotente).

### Roteirização — limitações conhecidas

- Geocodificação atual usa Nominatim (OSM) com fallback Photon. Ambos são gratuitos mas têm rate limit. Para rotas grandes (>20 endereços), pode ser lento.
- OSRM para matriz de distâncias usa instância pública — para produção considerar instância local ou API paga (Google Maps Distance Matrix, R$ 0,005/elemento).
- TSP com NetworkX funciona bem até ~15 paradas. Acima disso fica lento. Para V2, solução alternativa: algoritmo greedy nearest-neighbor (já implementado como fallback em `src/routing.py`).
- Geocodificação de endereços rurais em Uberlândia-MG tem qualidade variável no OSM. Implementar cache de coordenadas por endereço para não geocodificar o mesmo cliente toda vez.
