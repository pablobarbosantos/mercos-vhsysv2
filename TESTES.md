# Guia de Testes — Hardening Mercos → VHSys

Execute os testes **na ordem abaixo**. Cada teste tem o que esperar ver nos logs e como confirmar que passou.

---

## Pré-requisito: servidor rodando

```powershell
cd C:\mercos_vhsys_git
python main.py
```

Deixe o terminal aberto e visível para acompanhar os logs em tempo real.

---

## TESTE 1 — WAL mode ativo

**O que testa:** SQLite configurado para WAL (sem bloqueios de leitura durante escrita).

```powershell
cd C:\mercos_vhsys_git
python -c "
import sqlite3
conn = sqlite3.connect('data/sync.db')
modo = conn.execute('PRAGMA journal_mode').fetchone()[0]
print('Modo:', modo)
conn.close()
"
```

**Esperado:**
```
Modo: wal
```

Se aparecer `delete` ou `journal`, o banco ainda não foi inicializado com o novo código — suba o servidor uma vez para forçar `init_db()`.

---

## TESTE 2 — Tabelas novas criadas

**O que testa:** `fila_eventos` e `admin_acoes` existem no banco.

```powershell
python -c "
import sqlite3
conn = sqlite3.connect('data/sync.db')
tabelas = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\").fetchall()]
print('Tabelas:', tabelas)
conn.close()
"
```

**Esperado:** lista deve conter `fila_eventos` e `admin_acoes`.

---

## TESTE 3 — Webhook persiste na fila (não processa direto)

**O que testa:** o webhook retorna 200 imediatamente e salva na fila, sem chamar o VHSys.

```powershell
curl -X POST http://localhost:8000/webhook/mercos `
  -H "Content-Type: application/json" `
  -d '[{"evento":"pedido.gerado","dados":{"id":99999,"numero":"T001","cliente_cnpj":"00.000.000/0001-00","cliente_razao_social":"Cliente Teste","valor_total":100,"itens":[]}}]'
```

**Esperado no terminal do servidor:**
```
[Webhook] Evento: 'pedido.gerado'
[Webhook] Pedido #T001 persistido na fila (id=1).
```

**Confirmar no banco:**
```powershell
python -c "
import sqlite3
conn = sqlite3.connect('data/sync.db')
rows = conn.execute('SELECT id, evento, mercos_id, status, tentativas FROM fila_eventos').fetchall()
for r in rows: print(dict(r))
conn.close()
"
```

**Esperado:** 1 linha com `status='pendente'` ou `status='processando'` ou `status='ok'` (depende do timing).

---

## TESTE 4 — Worker processa a fila automaticamente

**O que testa:** o job `_job_processar_fila` roda a cada 10s e tenta processar o item.

Após o TESTE 3, aguarde **10–15 segundos** e observe os logs:

**Esperado nos logs:**
```
[Fila] Processando fila_id=1 | evento=pedido.gerado | tentativa=1
```

O processamento vai falhar (cliente de teste inválido) — isso é esperado. O que importa é que **a fila foi lida e tentada**.

**Confirmar status após falha:**
```powershell
python -c "
import sqlite3
conn = sqlite3.connect('data/sync.db')
rows = conn.execute('SELECT id, status, tentativas, ultimo_erro, proxima_tentativa FROM fila_eventos WHERE mercos_id=99999').fetchall()
for r in rows: print(dict(r))
conn.close()
"
```

**Esperado:** `status='pendente'`, `tentativas=1`, `ultimo_erro` com mensagem de erro, `proxima_tentativa` com data futura (backoff 30s).

---

## TESTE 5 — Retry com backoff exponencial

**O que testa:** após falha, a fila aguarda antes de tentar novamente.

Continue observando os logs após o TESTE 4. A sequência deve ser:

| Tentativa | Aguarda antes |
|-----------|---------------|
| 1ª falha  | 30 segundos   |
| 2ª falha  | 2 minutos     |
| 3ª falha  | 8 minutos     |
| 4ª falha  | 30 minutos    |
| 5ª falha  | → erro_permanente |

Para testar mais rápido, force a próxima tentativa zeando o campo:
```powershell
python -c "
import sqlite3
conn = sqlite3.connect('data/sync.db')
conn.execute(\"UPDATE fila_eventos SET proxima_tentativa=NULL WHERE mercos_id=99999\")
conn.commit()
conn.close()
print('Proxima tentativa zerada — aguarde 10s')
"
```

Observe nos logs a tentativa 2 ocorrendo em até 10s.

---

## TESTE 6 — Crash recovery (anti-perda de pedidos)

**O que testa:** se o Python morrer com um item em `processando`, ele é recuperado no restart.

**Passo 1:** Insira manualmente um item preso em `processando`:
```powershell
python -c "
import sqlite3
from datetime import datetime, timezone
conn = sqlite3.connect('data/sync.db')
conn.execute('''
  INSERT INTO fila_eventos (evento, mercos_id, payload_json, status, criado_em, atualizado_em)
  VALUES (\"pedido.gerado\", 88888, \"{}\", \"processando\", datetime(\"now\"), datetime(\"now\"))
''')
conn.commit()
print('Item preso em processando inserido.')
conn.close()
"
```

**Passo 2:** Pare e reinicie o servidor:
```powershell
# Ctrl+C no terminal do servidor, depois:
python main.py
```

**Esperado nos logs do startup:**
```
[Startup] Fila recuperada — 1 item(s) resetados para reprocessamento (crash anterior detectado).
```

**Confirmar:**
```powershell
python -c "
import sqlite3
conn = sqlite3.connect('data/sync.db')
r = conn.execute('SELECT status, ultimo_erro FROM fila_eventos WHERE mercos_id=88888').fetchone()
print(dict(r))
conn.close()
"
```

**Esperado:** `status='pendente'`, `ultimo_erro='Recuperado após crash do servidor'`.

---

## TESTE 7 — Erro permanente e alerta

**O que testa:** após 5 falhas, item vai para `erro_permanente` e dispara alerta.

```powershell
python -c "
import sqlite3
conn = sqlite3.connect('data/sync.db')
# Simula 4 tentativas já feitas, proxima_tentativa no passado
conn.execute(\"\"\"
  UPDATE fila_eventos
  SET tentativas=4, proxima_tentativa=datetime('now','-1 minute')
  WHERE mercos_id=99999
\"\"\")
conn.commit()
conn.close()
print('Configurado para ultima tentativa.')
"
```

Aguarde 10–15s. Nos logs deve aparecer:
```
[Fila] fila_id=X erro (tentativa 5): ...
```

```powershell
python -c "
import sqlite3
conn = sqlite3.connect('data/sync.db')
r = conn.execute('SELECT status, tentativas FROM fila_eventos WHERE mercos_id=99999').fetchone()
print(dict(r))
conn.close()
"
```

**Esperado:** `status='erro_permanente'`, `tentativas=5`.

---

## TESTE 8 — Monitor da fila via auditoria

**O que testa:** `verificar_fila_eventos()` detecta o `erro_permanente` e alerta.

```powershell
curl -X POST http://localhost:8000/admin/api/auditoria/verificar-agora
```

Nos logs deve aparecer:
```
[Auditoria/Fila] ⛔ FILA: 1 pedido(s) em ERRO PERMANENTE — intervenção manual necessária.
```

E se WhatsApp estiver ativo, Pablo receberá o alerta no celular.

---

## TESTE 9 — Stats da fila via API

**O que testa:** endpoint `GET /admin/api/fila`.

```powershell
curl http://localhost:8000/admin/api/fila
```

**Esperado:**
```json
{
  "stats": {
    "erro_permanente": 1,
    "pendente": 0,
    "ok": 1
  }
}
```

---

## TESTE 10 — Audit trail de ações admin

**O que testa:** ações manuais no painel ficam registradas com timestamp e IP.

**Passo 1:** Execute uma ação manual (precisa de um pedido no fluxo):
```powershell
# Primeiro veja quais mercos_id existem no fluxo:
python -c "
import sqlite3
conn = sqlite3.connect('data/sync.db')
rows = conn.execute('SELECT mercos_id, numero, status_fluxo FROM pedidos_fluxo LIMIT 5').fetchall()
for r in rows: print(dict(r))
conn.close()
"
```

Se não tiver nenhum, insira um:
```powershell
python -c "
import sqlite3
from datetime import datetime, timezone
conn = sqlite3.connect('data/sync.db')
conn.execute(\"INSERT OR IGNORE INTO pedidos_fluxo (mercos_id, numero, cliente, valor, recebido_em, status_fluxo) VALUES (77777, 'T002', 'Teste', 500, datetime('now'), 'processado')\")
conn.commit()
conn.close()
"
```

**Passo 2:** Marque como separado:
```powershell
curl -X POST http://localhost:8000/admin/api/auditoria/fluxo/77777/separado
```

**Passo 3:** Consulte o audit trail:
```powershell
curl http://localhost:8000/admin/api/acoes
```

**Esperado:**
```json
{
  "acoes": [
    {
      "id": 1,
      "acao": "separado",
      "mercos_id": 77777,
      "ip_origem": "127.0.0.1",
      "feito_em": "2026-..."
    }
  ],
  "total": 1
}
```

---

## TESTE 11 — Cache TTL do VHSys

**O que testa:** cache de produtos/clientes expira e é recarregado.

Nos logs do startup deve aparecer:
```
[CACHE] Carregando caches VHSys...
[CACHE] Concluído — X produtos | Y condições | Z transportadoras.
```

Para forçar um refresh manual agora:
```powershell
python -c "
from dotenv import load_dotenv
load_dotenv()
from vhsys_service import VhsysService
v = VhsysService()
v.forcar_refresh_cache()
print('Cache recarregado.')
"
```

---

## TESTE 12 — Retry HTTP do VHSys (simulado)

**O que testa:** `_requisitar_com_retry()` tenta 3x antes de desistir.

```powershell
python -c "
import os
os.environ['VHSYS_ACCESS_TOKEN'] = 'invalido'
os.environ['VHSYS_SECRET_TOKEN'] = 'invalido'
os.environ['VHSYS_BASE_URL'] = 'http://localhost:9999'  # porta inexistente
from vhsys_service import VhsysService
v = VhsysService.__new__(VhsysService)
v.access_token = 'x'
v.secret_token = 'x'
v.base_url = 'http://localhost:9999'
v.headers = {}
v._RETRY_STATUS = {429, 500, 502, 503, 504}
import time
inicio = time.time()
r = v._requisitar_com_retry('GET', 'http://localhost:9999/teste', max_tentativas=3, timeout=2)
print(f'Resultado: {r} | Tempo total: {time.time()-inicio:.1f}s')
"
```

**Esperado:** retorna `None` após ~14s total (tentativas com backoff 2s + 4s), com 3 warnings nos logs.

---

## TESTE 13 — WhatsApp retry

**O que testa:** `_enviar()` tenta 3x se o servidor Node não responder.

```powershell
python -c "
import os
os.environ['WHATSAPP_ENABLED'] = 'true'
os.environ['WHATSAPP_NOTIFY_NUMBER'] = '5534999999999'
os.environ['WHATSAPP_API_URL'] = 'http://localhost:9998'  # porta inexistente
from src.whatsapp import WhatsAppClient
import logging
logging.basicConfig(level=logging.INFO)
w = WhatsAppClient()
import time
inicio = time.time()
r = w._enviar('5534999999999', 'Teste retry', max_tentativas=3)
print(f'Resultado: {r} | Tempo: {time.time()-inicio:.1f}s')
"
```

**Esperado:** `Resultado: False` após ~6s (delays 2s + 4s), com 3 warnings e 1 error no log.

---

## TESTE 14 — Lock de pedido com timeout

**O que testa:** processamento concorrente do mesmo pedido aguarda 30s antes de abortar.

```powershell
python -c "
import threading, time, logging
logging.basicConfig(level=logging.WARNING)
from dotenv import load_dotenv
load_dotenv()
from mercos_service import MercosService
svc = MercosService()

# Trava o lock manualmente
lock = svc._get_lock_para_pedido(55555)
lock.acquire()

inicio = time.time()

def tentar():
    r = svc.processar_para_vhsys({'id': 55555, 'numero': 'LOCK_TEST'})
    print(f'Resultado: {r} | Tempo: {time.time()-inicio:.1f}s')

t = threading.Thread(target=tentar)
t.start()

# Libera após 3s (antes do timeout de 30s)
time.sleep(3)
lock.release()
t.join()
"
```

**Esperado:** a thread aguarda ~3s (até o lock ser liberado), depois prossegue normalmente. Não aborta instantaneamente como antes.

---

## TESTE 15 — Limpeza do dict de locks

**O que testa:** `limpar_locks_antigos()` remove locks ociosos da memória.

```powershell
python -c "
from dotenv import load_dotenv
load_dotenv()
from mercos_service import MercosService
svc = MercosService()

# Cria alguns locks
for i in range(1000, 1010):
    svc._get_lock_para_pedido(i)

print(f'Locks antes: {len(svc._pedido_locks)}')
svc.limpar_locks_antigos()
print(f'Locks depois: {len(svc._pedido_locks)}')
"
```

**Esperado:**
```
Locks antes: 10
Locks depois: 0
```

---

## Checklist Final

| # | Teste | Status |
|---|-------|--------|
| 1 | WAL mode ativo | ☐ |
| 2 | Tabelas novas existem | ☐ |
| 3 | Webhook persiste na fila | ☐ |
| 4 | Worker processa a fila | ☐ |
| 5 | Backoff exponencial | ☐ |
| 6 | Crash recovery no startup | ☐ |
| 7 | Erro permanente após 5 falhas | ☐ |
| 8 | Monitor da fila detecta erro_permanente | ☐ |
| 9 | API /admin/api/fila retorna stats | ☐ |
| 10 | Audit trail de ações admin | ☐ |
| 11 | Cache TTL carrega no startup | ☐ |
| 12 | Retry HTTP (3 tentativas) | ☐ |
| 13 | WhatsApp retry (3 tentativas) | ☐ |
| 14 | Lock aguarda com timeout | ☐ |
| 15 | Limpeza do dict de locks | ☐ |

---

## Limpeza após os testes

Remove os registros de teste inseridos durante os testes:

```powershell
python -c "
import sqlite3
conn = sqlite3.connect('data/sync.db')
conn.execute('DELETE FROM fila_eventos WHERE mercos_id IN (99999, 88888)')
conn.execute('DELETE FROM pedidos_fluxo WHERE mercos_id = 77777')
conn.execute('DELETE FROM admin_acoes WHERE mercos_id = 77777')
conn.commit()
conn.close()
print('Dados de teste removidos.')
"
```
