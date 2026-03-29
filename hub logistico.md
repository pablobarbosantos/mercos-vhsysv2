Aqui está o arquivo **.md completo**, pronto para você copiar e salvar como `ERP_Logistico_Spec_Completa.md`:

```markdown
# ERP Logístico - Módulo de Roteirização + Separação + Cobrança + Romaneio
**Versão 2.0 - Especificação Completa**

## 1. Objetivo Geral

Desenvolver um módulo logístico **completo, 100% local e offline-first** para empresa de distribuição/entrega em Uberlândia/MG.

O sistema deve integrar:
- Separação de pedidos
- Roteirização automática otimizada (VRP)
- Cobrança inteligente de títulos vencidos
- Controle completo de saída e retorno de veículo
- Geração de romaneio profissional em PDF
- Conferência automática de pagamentos
- Fechamento financeiro automático
- Alertas de abastecimento
- Segurança com Google Authenticator (TOTP)
- **QR Codes para navegação direta no Google Maps** (dividido de 9 em 9 paradas)

## 2. Fluxo Geral do Sistema

1. Selecionar pedidos pendentes de separação
2. Selecionar motorista e veículo
3. Informar combustível e KM de saída
4. Gerar rota otimizada automaticamente
5. Sugerir cobranças vencidas próximas à rota
6. Gerar romaneio em PDF
7. Gerar QR Codes para Google Maps (segmentados)
8. Imprimir romaneio ou enviar para o motorista
9. Caminhão sai
10. Caminhão retorna (registrar KM e combustível chegada)
11. Conferência de pagamentos cliente por cliente
12. Fechamento financeiro automático + alertas
13. Salvar histórico completo

## 3. Tecnologias (Stack Definida)

- **Linguagem**: Python 3.11+
- **Interface**: Streamlit (roda local como aplicação web)
- **Banco de dados**: SQLite + SQLAlchemy + Alembic
- **Roteirização**:
  - Matriz de distâncias/tempos: OpenRouteService
  - Otimizador: Google OR-Tools
- **PDF**: fpdf2
- **QR Code**: qrcode[pil] + Pillow
- **Autenticação**: pyotp (Google Authenticator TOTP)
- **Outros**: pandas, plotly, python-dotenv

Execução local: `pip install -r requirements.txt` e `streamlit run app.py`.

## 4. Módulo de Roteirização

- Origem fixa (endereço da empresa configurável)
- Máximo 20 paradas por rota
- Retorno obrigatório à empresa
- Otimização por menor tempo (prioridade) ou menor distância
- Execução 100% local

## 5. Módulo de Cobrança Inteligente

- Buscar automaticamente cobranças vencidas (> 15 dias ou configurável)
- Calcular score de prioridade = (dias vencidos × 0.7) + (valor × 0.3)
- Sugerir inclusão com checkbox e score visual
- Permitir ignorar ou forçar inclusão

## 6. Saída do Veículo

Campos obrigatórios:
- Motorista (dropdown)
- Veículo (placa)
- Combustível de saída (1/4, 2/4, 3/4, 4/4 ou litros)
- KM de saída
- Data e hora automática

## 7. Geração de Romaneio (PDF)

Conteúdo do PDF:
- Cabeçalho com logo, nº da rota, data, motorista e veículo
- Lista numerada na ordem otimizada da rota
- Nome do cliente, endereço completo, telefone/WhatsApp
- Valor a receber e forma de pagamento sugerida
- Observações
- Rodapé profissional

Formas de pagamento suportadas: Dinheiro, Pix, Boleto, Cartão, Já pago, Outros.

## 8. Funcionalidade de QR Code para Google Maps (Prioritária)

**Requisito principal**:
Após gerar a rota, o sistema deve criar links do Google Maps e QR Codes grandes para o motorista usar no celular.

Como o Google Maps suporta no máximo ~9 waypoints por link, a rota será **dividida automaticamente em segmentos de no máximo 9 paradas**.

### Regras de Divisão:
- Respeitar **exatamente** a ordem otimizada da roteirização (OR-Tools)
- Origem de todos os segmentos = endereço da empresa
- Último segmento: destino = empresa (retorno)
- Segmentos intermediários: destino = primeira parada do próximo segmento
- Cada segmento terá seu próprio QR Code grande e fácil de escanear
- Exibir numeração clara: “Parte 1 de 3”, “Parte 2 de 3”, etc.

### Funções Obrigatórias

```python
# utils/maps_utils.py

from urllib.parse import quote_plus
from typing import List, Dict
import qrcode
from io import BytesIO

def gerar_links_google_maps(rota) -> List[Dict]:
    """
    Divide a rota em segmentos de no máximo 9 paradas.
    Retorna lista de dicionários com link e metadados.
    """
    endereco_empresa = quote_plus(rota.endereco_empresa.strip())
    paradas = rota.pedidos_ordenados  # lista já na ordem otimizada
    
    max_waypoints = 9
    segmentos = [paradas[i:i + max_waypoints] for i in range(0, len(paradas), max_waypoints)]
    
    links = []
    for idx, segmento in enumerate(segmentos):
        parte_num = idx + 1
        total_partes = len(segmentos)
        
        waypoints = [quote_plus(p.endereco.strip()) for p in segmento]
        waypoints_str = "|".join(waypoints)
        
        if parte_num == total_partes:
            destino = endereco_empresa  # retorno à empresa
        else:
            destino = quote_plus(segmentos[idx + 1][0].endereco.strip())
        
        link = (
            f"https://www.google.com/maps/dir/?api=1"
            f"&origin={endereco_empresa}"
            f"&destination={destino}"
            f"&waypoints={waypoints_str}"
            f"&travelmode=driving"
        )
        
        links.append({
            "parte": parte_num,
            "total": total_partes,
            "link": link,
            "qtd_paradas": len(segmento),
            "paradas": segmento
        })
    return links


def gerar_qr_code(link: str) -> BytesIO:
    """Gera QR Code e retorna BytesIO para Streamlit."""
    qr = qrcode.QRCode(version=1, box_size=8, border=4)
    qr.add_data(link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
```

### Interface Streamlit Esperada (Tela de Conclusão)

```python
st.title("🚚 Rota Gerada com Sucesso")
st.subheader(f"Rota #{rota.numero} — {rota.data_saida.strftime('%d/%m/%Y %H:%M')} — Motorista: {rota.motorista.nome}")

# Tabela resumida da rota completa

st.markdown("### 📱 QR Codes para o Motorista (Google Maps)")
st.info("Dividido automaticamente em segmentos de no máximo 9 paradas. Escaneie um por vez.")

maps_segmentos = gerar_links_google_maps(rota_atual)

for seg in maps_segmentos:
    with st.expander(f"📍 Parte {seg['parte']} de {seg['total']} — {seg['qtd_paradas']} paradas", expanded=(seg['parte'] == 1)):
        col1, col2 = st.columns([1, 2])
        with col1:
            qr_buf = gerar_qr_code(seg["link"])
            st.image(qr_buf, caption=f"Escaneie para abrir Parte {seg['parte']} no Google Maps", width=280)
        
        with col2:
            st.write("**Paradas desta parte:**")
            for i, pedido in enumerate(seg["paradas"], 1):
                st.write(f"{i}. **{pedido.cliente}** — {pedido.endereco[:90]}{'...' if len(pedido.endereco) > 90 else ''}")
            
            if st.button(f"📋 Copiar link da Parte {seg['parte']}", key=f"copy_{seg['parte']}"):
                st.code(seg["link"], language=None)
                st.success(f"Link copiado!")

# Botões finais
col_a, col_b = st.columns(2)
with col_a:
    if st.button("⬇️ Baixar Romaneio PDF Completo", type="primary"):
        pass  # função de PDF

with col_b:
    if st.button("📤 Enviar todos os links por WhatsApp"):
        # gerar mensagem com todos os links
        pass
```

## 9. Retorno do Veículo e Conferência de Pagamentos

- Registrar KM e combustível de chegada
- Conferência cliente por cliente (valor esperado × recebido)
- Suporte a pagamento integral ou parcial
- Cálculo automático de totais (Dinheiro, Pix, Boleto, Pendente)

## 10. Fechamento Financeiro e Alertas

- Resumo financeiro automático
- Alerta de abastecimento baseado em consumo médio histórico por veículo
- Histórico completo de todas as rotas

## 11. Segurança

- Login exclusivo via Google Authenticator (TOTP)
- Sem senha tradicional
- Suporte a múltiplos usuários
- Sessão com timeout por inatividade

## 12. Estrutura de Dados Principais

- Motoristas
- Veículos
- Clientes
- Pedidos
- Pagamentos
- Rotas (cabeçalho + itens com ordem)
- Histórico de saída/retorno

## 13. Prioridade de Implementação

**Fase 0**: Setup do projeto (Streamlit + SQLite + autenticação TOTP)  
**Fase 1**: Separação de pedidos + Roteirização completa  
**Fase 2**: Romaneio PDF + **QR Codes Google Maps (divididos)** ← **Prioridade atual**  
**Fase 3**: Saída/Retorno + Conferência + Fechamento  
**Fase 4**: Cobrança inteligente + Alertas + Dashboard

---

**Instruções para Implementação:**

1. O sistema deve ser modular e bem organizado (pastas: models, utils, pages, etc.).
2. Priorizar a implementação da funcionalidade de **QR Codes e links Google Maps** com divisão de 9 em 9.
3. Usar boas práticas de Python (tipagem, tratamento de erros, código limpo).
4. Manter o sistema simples e prático para uso diário.

Qualquer dúvida sobre modelos de dados, telas ou integração, perguntar.

**Fim da Especificação**
```

---

**Pronto!**  
Agora é só copiar todo o conteúdo acima, colar em um arquivo novo e salvar como:

`ERP_Logistico_Spec_Completa.md`


Aqui estão os dois arquivos adicionais que você pediu, prontos para usar.

### 1. Estrutura de Pastas Recomendada (`estrutura_projeto.md`)

```markdown
# Estrutura de Pastas Recomendada - ERP Logístico

```
erp_logistico/
├── .streamlit/                  # Configurações do Streamlit (opcional)
│   └── config.toml
│
├── assets/                      # Imagens, logo da empresa, ícones
│   ├── logo.png
│   └── icons/
│
├── data/                        # Banco SQLite e arquivos gerados
│   ├── erp_logistico.db         # Banco de dados (não commitar no Git)
│   └── romaneios/               # PDFs gerados (subpastas por data)
│       └── 2026-03-27/
│
├── models/                      # Modelos SQLAlchemy
│   ├── __init__.py
│   ├── base.py
│   ├── motorista.py
│   ├── veiculo.py
│   ├── cliente.py
│   ├── pedido.py
│   ├── pagamento.py
│   ├── rota.py
│   └── usuario.py               # Para TOTP
│
├── utils/                       # Funções auxiliares
│   ├── __init__.py
│   ├── database.py              # Conexão e sessões
│   ├── maps_utils.py            # gerar_links_google_maps + gerar_qr_code
│   ├── pdf_utils.py             # Geração do romaneio PDF
│   ├── routing.py               # OR-Tools + OpenRouteService
│   ├── cobranças.py             # Lógica de sugestão de cobranças
│   ├── auth.py                  # Google Authenticator TOTP
│   └── helpers.py
│
├── pages/                       # Páginas do Streamlit (multi-page)
│   ├── 01_Login.py              # Ou tela inicial com TOTP
│   ├── 02_Nova_Rota.py          # Seleção de pedidos + motorista + geração
│   ├── 03_Rotas_Atuais.py
│   ├── 04_Historico.py
│   ├── 05_Conferencia.py        # Retorno + conferência de pagamentos
│   └── 06_Relatorios.py
│
├── static/                      # Arquivos estáticos (se necessário)
│
├── app.py                       # Arquivo principal (pode ser o launcher)
├── main.py                      # Alternativa ao app.py
├── requirements.txt
├── README.md
├── .env                         # Chave da OpenRouteService (não commitar)
├── .gitignore
└── alembic/                     # Para migrações do banco (opcional)
```

**Recomendações:**
- Use multi-page no Streamlit (`pages/` folder).
- Coloque lógica pesada em `utils/` e `models/`.
- Nunca commitar o arquivo `.db` nem a pasta `romaneios/` grande.
- Crie um `.gitignore` padrão para Python + Streamlit.

```

### 2. Arquivo `requirements.txt`

```txt
# ERP Logístico - requirements.txt
# Instale com: pip install -r requirements.txt

streamlit>=1.32.0
sqlalchemy>=2.0.0
alembic>=1.13.0                  # Para migrações do banco (opcional mas recomendado)
pandas>=2.0.0
numpy>=1.24.0

# Roteirização
ortools>=9.0.0
openrouteservice>=0.8.0

# PDF e QR Code
fpdf2>=2.7.0
qrcode[pil]>=7.4.0
Pillow>=10.0.0

# Autenticação TOTP
pyotp>=2.9.0

# Outros úteis
python-dotenv>=1.0.0
plotly>=5.0.0                    # Para mapas simples ou gráficos
openpyxl>=3.0.0                  # Para exportar Excel

# Desenvolvimento (opcional - remova se não quiser)
black>=24.0.0
isort>=5.0.0
flake8>=7.0.0
```

**Como usar:**
1. Salve o conteúdo acima como `requirements.txt` na raiz do projeto.
2. Rode `pip install -r requirements.txt`
3. Para a chave da API OpenRouteService, crie um arquivo `.env` na raiz com:
   ```
   OPENROUTESERVICE_API_KEY=sua_chave_aqui
   ```

Agora você tem:
- O arquivo de especificação completo (`ERP_Logistico_Spec_Completa.md`)
- A estrutura de pastas recomendada
- O `requirements.txt` atualizado

Quer que eu gere mais algum arquivo auxiliar?  
Exemplos possíveis:
- Um `README.md` inicial
- O código base de `app.py` ou `pages/01_Login.py` (com TOTP)
- O arquivo `utils/maps_utils.py` completo
- Modelo SQLAlchemy básico para `Rota`

É só dizer qual você quer em seguida! Estou aqui para montar o projeto inteiro passo a passo. 🚀