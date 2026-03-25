# 🔧 Hardening Sistema Mercos → VHSys

## 🎯 Objetivo
Garantir:
- Zero perda de pedidos
- Processamento confiável
- Recuperação automática de falhas
- Visibilidade total do sistema

---

## 🚨 Problemas Atuais

1. Webhook processa direto → risco de perda
2. Sem retry automático
3. Sem persistência do payload original
4. SQLite pode travar (concorrência)
5. Sem controle claro de estado do pedido
6. Dependência de ngrok
7. WhatsApp como único alerta
8. Sem fila de erro (dead letter)
9. Node não sobe automático
10. Sem timeout nas APIs externas

---

## 🧱 Etapa 1 — Fila de Eventos (CRÍTICO)

Criar tabela `fila_eventos` com:
- id
- mercos_id
- tipo_evento
- payload (JSON completo)
- status (PENDENTE, PROCESSANDO, ERRO, CONCLUIDO)
- tentativas
- ultimo_erro
- criado_em
- atualizado_em

Motivo:
- Evita perda de webhook
- Permite retry
- Dá controle total

---

## 🔁 Etapa 2 — Worker Assíncrono

Processo que:
- Busca eventos PENDENTES
- Marca PROCESSANDO
- Executa integração
- Atualiza status

Motivo:
- Desacopla webhook
- Evita timeout

---

## 🔄 Etapa 3 — Retry Automático

Regra:
- 1: imediato
- 2: +1 min
- 3: +5 min
- 4: +15 min
- 5: falha definitiva

Motivo:
- Recuperar falhas temporárias

---

## 💾 Etapa 4 — Salvar Payload

Salvar JSON original

Motivo:
- Debug
- Reprocessamento

---

## 🧠 Etapa 5 — Estado do Pedido

- RECEBIDO
- PROCESSANDO
- ERRO
- ENVIADO_VHSYS
- CANCELADO

Motivo:
- Visibilidade total

---

## ⏱️ Etapa 6 — Timeout API

Adicionar timeout nas requisições

Motivo:
- Evitar travamentos

---

## 🧱 Etapa 7 — SQLite

Ativar WAL e check_same_thread=False

Motivo:
- Evitar lock

---

## 🧯 Etapa 8 — Dead Letter

Se tentativas >= 5 → ERRO_PERMANENTE

Motivo:
- Evitar loop infinito

---

## 📲 Etapa 9 — Alertas

- Alertar só erro crítico
- Criar endpoint de erros

Motivo:
- Evitar spam

---

## ⚙️ Etapa 10 — Auto Start

Garantir:
- Python
- Node
- ngrok

Motivo:
- Subir automático

---

## ⚠️ Etapa 11 — ngrok

Problema:
- Instável

Opção:
- Migrar VPS (ideal)

---

## 🧪 Testes

- Queda de energia
- VHSys fora
- Internet instável
- Pedido duplicado

Objetivo:
- Não perder
- Não duplicar
- Recuperar sozinho

---

## ✅ Ordem

1. Fila
2. Worker
3. Retry
4. Payload
5. Timeout
6. SQLite
7. Dead letter
8. Auto start
9. Alertas

---

## 🎯 Resultado

Sistema:
- Estável
- Confiável
- Autônomo
