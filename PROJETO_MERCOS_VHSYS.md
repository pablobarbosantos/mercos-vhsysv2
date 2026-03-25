# Pablo Agro — Webhook Mercos → VHSys
## Resumo Técnico do Projeto

---

## 🏗️ Visão Geral

Sistema de integração automática entre o app de força de vendas **Mercos** e o ERP **VHSys**, rodando como servidor local no Windows da loja Pablo Agro (Uberlândia/MG).

**Repositório:** https://github.com/pablobarbosantos/mercos-vhsysv2

---

## 🖥️ Infraestrutura

| Componente | Tecnologia | Porta |
|---|---|---|
| API principal (webhook) | Python + FastAPI + Uvicorn | 8000 |
| WhatsApp local | Node.js + whatsapp-web.js + Express | 3000 |
| Túnel público | ngrok (free) | — |
| Banco de dados | SQLite (`data/sync.db`) | — |
| Scheduler | APScheduler | — |

**Máquina:** Windows local (não VM), usuário `rdpadmin`  
**Python:** `C:\Users\rdpadmin\AppData\Local\Python\pythoncore-3.14-64\python.exe`  
**Node:** v25.8.1  
**URL pública Mercos:** `https://foamiest-avowable-jutta.ngrok-free.dev/webhook/mercos`  

---

## 📁 Estrutura de Arquivos

```
mercos_vhsys_git/
├── main.py                    ← FastAPI + scheduler + webhook handler
├── mercos_service.py          ← Lógica de processamento do pedido
├── vhsys_service.py           ← Cliente API VHSys
├── requirements.txt
├── start_server.bat           ← Script de inicialização automática
├── .env                       ← Tokens e configurações
│
├── src/
│   ├── database.py            ← SQLite: pedidos, fluxo, auditoria
│   ├── whatsapp.py            ← Cliente HTTP → servidor Node local
│   ├── auditoria.py           ← Engine de auditoria de sequência e fluxo
│   └── admin_routes.py        ← Painel admin (FastAPI router)
│
├── templates/
│   └── admin.html             ← Painel admin (3 abas)
│
├── whatsapp_server/
│   └── server.js              ← Servidor Node WhatsApp (whatsapp-web.js)
│
├── data/
│   └── sync.db                ← Banco SQLite (não vai pro GitHub)
│
└── logs/
    └── sync.log               ← Logs rotativos (não vai pro GitHub)
```

---

## ⚙️ Variáveis de Ambiente (.env)

```env
MERCOS_APPLICATION_TOKEN=d39001ac-0b14-11f0-8ed7-6e1485be00f2
MERCOS_COMPANY_TOKEN=c18fb990-2187-11f1-b99a-ee2d6ee9060f
MERCOS_BASE_URL=https://sandbox.mercos.com
VHSYS_ACCESS_TOKEN=SUJq1wX91NhTgI6Z5gZ6btjcvmZcSg
VHSYS_SECRET_ACCESS_TOKEN=ViyMeIw8rmi3hlZUjfffytjKq8xrdly
VHSYS_BASE_URL=https://api.vhsys.com/v2

# WhatsApp local
WHATSAPP_ENABLED=true
WHATSAPP_API_URL=http://localhost:3000
WHATSAPP_NOTIFY_NUMBER=5534991027738

# Auditoria (opcionais - têm defaults)
AUDIT_SEQ_INTERVAL_MIN=15
AUDIT_FLUXO_INTERVAL_MIN=30
AUDIT_FECHAMENTO_HORA=20
AUDIT_JANELA_SEQUENCIA_DIAS=7
AUDIT_COOLDOWN_HORAS=4
```

---

## 🔄 Fluxo de Funcionamento

```
Vendedor faz pedido no Mercos (app)
    ↓
Mercos dispara webhook POST → ngrok → localhost:8000/webhook/mercos
    ↓
main.py recebe evento:
  • pedido.gerado  → processa imediatamente
  • pedido.faturado → verifica se já existe no VHSys
                      SE NÃO: cria (segunda chance anti-perda)
                      SE SIM: ignora
  • pedido.atualizado → atualiza status no fluxo operacional
  • pedido.cancelado → marca como cancelado
    ↓
mercos_service.py:
  1. Verifica idempotência (lock por pedido + banco)
  2. Traduz dados Mercos → formato VHSys
  3. Chama vhsys_service.py → POST /v2/pedidos
  4. Cria parcelas em /v2/contas-receber
  5. Salva no banco SQLite
  6. Envia notificação WhatsApp interno
    ↓
WhatsApp: POST http://localhost:3000/send
    ↓
Mensagem chega no celular do Pablo (5534991027738)
```

---

## 🗄️ Banco de Dados (SQLite)

### Tabelas principais

| Tabela | Função |
|---|---|
| `pedidos_processados` | Idempotência — pedidos já enviados ao VHSys |
| `pedidos_fluxo` | Rastreio de etapas: recebido → processado → separado → enviado |
| `auditoria_sequencia` | Buracos detectados na sequência de IDs Mercos |
| `mapa_clientes` | CNPJ → ID VHSys (cache) |
| `mapa_produtos` | SKU Mercos → ID VHSys (cache) |
| `erros_log` | Log estruturado de erros |
| `sync_timestamps` | Último timestamp de sync por entidade |

### Importante sobre IDs
O campo `mercos_id` no banco armazena o **ID interno do Mercos** (ex: `153412954`), não o número do pedido (ex: `2876`). Isso causa range muito grande na auditoria de sequência se misturado com IDs de teste pequenos.

---

## 🤖 Auditoria Automática (APScheduler)

### Jobs agendados

| Job | Frequência | Função |
|---|---|---|
| `_job_sequencia` | A cada 15min | Detecta buracos na sequência de IDs (janela: últimos 7 dias) |
| `_job_fluxo` | A cada 30min | Detecta pedidos travados em etapas |
| `_job_fechamento` | Todo dia às 20h | Resumo diário via WhatsApp |

### Limites do verificador de fluxo
- **>30min sem processar** → alerta alta prioridade
- **>2h sem separação** → alerta média prioridade  
- **>4h sem envio** → alerta média prioridade

---

## 📲 WhatsApp (server.js)

Servidor Node.js local usando `whatsapp-web.js` + Puppeteer (Chromium headless).

**Autenticação:** salva em `whatsapp_server/auth_info/` (persistente, não reinicia QR)

**Endpoint de envio:**
```
POST http://localhost:3000/send
Body: { "numero": "5534991027738", "mensagem": "texto" }
```

**Lógica de número:** tenta com e sem dígito 9 extra via `getNumberId()` antes de enviar.

**Notificações ativas:**
- ✅ Pedido processado com sucesso (para Pablo)
- ❌ Erro ao criar pedido no VHSys (para Pablo)
- ⚠️ Buracos na sequência de pedidos (para Pablo)
- 📦 Pedidos travados no fluxo (para Pablo)
- 📊 Fechamento do dia às 20h (para Pablo)

**Notificações desativadas (comentadas):**
- Confirmação de pedido para o cliente final

---

## 🖥️ Inicialização Automática (Windows)

### Agendador de Tarefas

| Tarefa | Trigger | Ação |
|---|---|---|
| `MercosVhsys` | Na inicialização do Windows | Executa `start_server.bat` como `rdpadmin` |
| `ReinicioNoturno` | Todo dia às 03:00 | `shutdown /r /t 60` |

**start_server.bat:**
```bat
@echo off
cd /d C:\mercos_vhsys_git
"C:\Users\rdpadmin\AppData\Local\Python\pythoncore-3.14-64\python.exe" main.py >> C:\mercos_vhsys_git\logs\startup.log 2>&1
```

⚠️ O servidor Node (WhatsApp) **não** está no Agendador de Tarefas — precisa subir manualmente ou adicionar tarefa separada.

---

## 🚀 Comandos para Subir o Sistema

```powershell
# Parar tudo
Get-Process python, node -ErrorAction SilentlyContinue | Stop-Process -Force

# PowerShell 1 — WhatsApp
cd "C:\mercos_vhsys_git\whatsapp_server"
node server.js

# PowerShell 2 — Servidor Python
cd "C:\mercos_vhsys_git"
python main.py

# PowerShell 3 — ngrok
& "C:\mercos_vhsys_git\ngrok.exe" http 8000
```

---

## 🔗 Endpoints da API

| Endpoint | Método | Função |
|---|---|---|
| `/` | GET | Health check |
| `/webhook/mercos` | POST | Recebe eventos do Mercos |
| `/admin/` | GET | Painel HTML |
| `/admin/api/pedidos` | GET | Lista pedidos processados |
| `/admin/api/auditoria/fluxo` | GET | Status do fluxo operacional |
| `/admin/api/auditoria/sequencia` | GET | Buracos de sequência |
| `/admin/api/auditoria/verificar-agora` | POST | Força auditoria imediata |
| `/admin/api/auditoria/fluxo/{id}/separado` | POST | Marca como separado (manual) |
| `/admin/api/auditoria/fluxo/{id}/enviado` | POST | Marca como enviado (manual) |
| `/admin/api/reprocessar/{id}` | POST | Reprocessa pedido com erro |

---

## ⚠️ Problemas Conhecidos / Pendências

1. **Servidor Node não sobe automaticamente** após reinício — precisa adicionar tarefa no Agendador de Tarefas
2. **ngrok URL muda** a cada reinício (plano free) — precisa atualizar no Mercos manualmente ou contratar plano pago com URL fixa
3. **node_modules** não está no .gitignore corretamente — verificar antes do próximo push
4. **Pasta `files/`** foi para o GitHub acidentalmente — pode limpar
5. **Confirmação para o cliente** está comentada em `mercos_service.py` — descomentar quando quiser ativar
6. **data_emissao vazia** em alguns pedidos — campo `data_pedido` fica em branco no VHSys (não crítico)
7. **Parcelas falham** quando `data_emissao` não é enviada (erro 403 VHSys) — investigar

---

## 📦 Dependências

### Python (requirements.txt)
```
fastapi
uvicorn[standard]
python-dotenv
requests
apscheduler
jinja2
```

### Node (whatsapp_server/package.json)
```json
{
  "dependencies": {
    "whatsapp-web.js": "latest",
    "qrcode-terminal": "^0.12.0",
    "express": "^5.2.1"
  }
}
```

---

## 🔑 Git

```powershell
# Commit e push
cd "C:\mercos_vhsys_git"
& "C:\Program Files\Git\bin\git.exe" add .
& "C:\Program Files\Git\bin\git.exe" commit -m "mensagem"
& "C:\Program Files\Git\bin\git.exe" push origin main
```

**Usuário configurado:** `Pablo Barbosa` / `pablo@pabloagro.com.br`
