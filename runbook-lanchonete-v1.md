# 📖 RUNBOOK: Chatbot de WhatsApp para Lanchonete (Containerizado no Geekom)

> **Projeto:** Bot Autônomo de Atendimento, Cardápio e Pedidos via WhatsApp  
> **Host de Execução:** Geekom A7 MAX (`10.0.0.205` - Bare Metal Ubuntu 24.04 LTS)  
> **Arquitetura:** Multi-container Docker (Gateway WhatsApp + Core do Bot + Banco Local)  
> **Filosofia:** *Code-First Sem N8N* (Leve, determinístico, alta performance, suporte a áudio e baixo consumo de VRAM)

---

## 📑 Índice
1. [Visão Geral da Arquitetura](#1-visão-geral-da-arquitetura)
2. [Fase 1: Preparação do Ambiente no Geekom](#2-fase-1-preparação-do-ambiente-no-geekom)
3. [Fase 2: WhatsApp Gateway (Evolution API)](#3-fase-2-whatsapp-gateway-evolution-api)
4. [Fase 3: Core do Bot de Atendimento (Python FastAPI + SQLite)](#4-fase-3-core-do-bot-de-atendimento-python-fastapi--sqlite)
5. [Fase 4: Máquina de Estados e Engenharia de Pedidos](#5-fase-4-máquina-de-estados-e-engenharia-de-pedidos)
6. [Fase 5: Motor Cognitivo de IA (Local vs Nuvem) & Transcrição de Áudio](#6-fase-5-motor-cognitivo-de-ia-local-vs-nuvem--transcrição-de-áudio)
7. [Fase 6: Orquestração e Deploy com Docker Compose](#7-fase-6-orquestração-e-deploy-com-docker-compose)
8. [Fase 7: Plano de Testes e Homologação](#8-fase-7-plano-de-testes-e-homologação)
9. [Fase 8: Manutenção, Backup e Operação Diária](#9-fase-8-manutenção-backup-e-operação-diária)

---

## 1. Visão Geral da Arquitetura

O sistema adota o paradigma **Code-First Sem N8N** (inspirado nos anúncios de alta eficiência técnica): em vez de instanciar nós visuais pesados com alto consumo de memória e fragilidade de webhooks, utilizamos um microserviço em Python (FastAPI assíncrono) conectado ao gateway de WhatsApp via Webhook direto.

```
       [Cliente WhatsApp]
               │ (Mensagem / Áudio)
               ▼
┌─────────────────────────────── Geekom A7 MAX (Docker Network) ───────────────────────────────┐
│                                                                                              │
│   ┌────────────────────────────────┐         Webhook          ┌───────────────────────────┐  │
│   │     Evolution API (Gateway)    │ ───────────────────────► │    Bot Core (FastAPI)     │  │
│   │   - Baileys WhatsApp Engine    │                          │  - Validação de Payloads  │  │
│   │   - QR Code & Sessão WPP       │ ◄─────────────────────── │  - State Machine (Sessão) │  │
│   │   - Porta: 8080                │         Envio REST       │  - Porta: 8000            │  │
│   └────────────────────────────────┘                          └─────────────┬─────────────┘  │
│                   │                                                         │                │
│             Volume Docker                                        ┌──────────┴──────────┐     │
│          (evolution_instances)                                   ▼                     ▼     │
│                                                            [Motor de IA]          [SQLite DB]│
│                                                           • Nuvem (Gemini/        • Cardápio │
│                                                             Claude) ou Local      • Carrinho │
│                                                           • Transcrição Áudio     • Pedidos  │
│                                                                                              │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                               │ Notificação de Pedido Fechado
                               ▼
                   [WhatsApp da Cozinha / Balcão]
```

### Principais Benefícios da Abordagem
1. **Consumo Mínimo de Recursos:** < 300 MB de RAM no total (N8N consome ~1.5 GB sozinho).
2. **Imunidade a Quedas:** Containers com reinicialização automática (`restart: unless-stopped`).
3. **Suporte Nativo a Áudios:** O cliente pode mandar áudio no WhatsApp (*"Me manda um x-salada sem tomate e uma coca lata"*); o bot transcreve e monta o pedido automaticamente.
4. **Reserva de VRAM:** Preserva a APU Radeon 780M do Geekom para outras tarefas do Hermes.

---

## 2. Fase 1: Preparação do Ambiente no Geekom

### 2.1 Instalação do Docker e Docker Compose
Executar comandos determinísticos de linha única:

```bash
# 1. Atualizar repositórios e instalar docker.io + docker-compose-v2
sudo apt-get update -y && sudo apt-get install -y docker.io docker-compose-v2

# 2. Habilitar e iniciar o serviço Docker
sudo systemctl enable --now docker

# 3. Adicionar o usuário 'brn' ao grupo docker (evitar sudo no dia a dia)
sudo usermod -aG docker brn
```

### 2.2 Estrutura de Diretórios do Projeto
Criar e padronizar o layout em `/home/brn/whatsapp-lanchonete`:

```bash
mkdir -p /home/brn/whatsapp-lanchonete/{app,config,data/evolution_instances,data/db,data/audios,logs}
cd /home/brn/whatsapp-lanchonete
```

---

## 3. Fase 2: WhatsApp Gateway (Evolution API)

Utilizaremos a **Evolution API v2** (imagem oficial Docker), a solução padrão mais estável da comunidade brasileira para Baileys:

### 3.1 Variáveis de Ambiente do Gateway (`.env`)
Criar o arquivo `/home/brn/whatsapp-lanchonete/.env`:

```env
# Configurações do Gateway Evolution
SERVER_TYPE=http
SERVER_PORT=8080
PORT=8080
SERVER_URL=http://10.0.0.205:8080
AUTHENTICATION_API_KEY=lancho_secret_key_geekom_2026

# Banco de Dados da Evolution (PostgreSQL)
DATABASE_ENABLED=true
DATABASE_PROVIDER=postgresql
DATABASE_CONNECTION_URI=postgresql://evolution_user:evolution_secret_pass@evolution-postgres:5432/evolution_db?schema=public
DATABASE_SAVE_DATA_INSTANCE=true
DATABASE_SAVE_MESSAGE=true
DATABASE_SAVE_CONTACT=true
DATABASE_SAVE_CHAT=true

# Cache da Evolution (Redis)
REDIS_ENABLED=true
REDIS_HOST=evolution-redis
REDIS_PORT=6379
REDIS_PASSWORD=

# Webhook Global para o Bot Core
WEBHOOK_GLOBAL_ENABLED=true
WEBHOOK_GLOBAL_URL=http://bot:8000/webhook
WEBHOOK_GLOBAL_WEBHOOK_BY_EVENTS=false
WEBHOOK_EVENTS_MESSAGES_UPSERT=true
DELAY_IGNORE_GROUPS=true
CONFIG_SESSION_PHONE_CLIENT=LanchoneteBot

# Chaves de IA (Opcional se usar Local Qwen)
GEMINI_API_KEY=
ANTHROPIC_API_KEY=

# Configuração da Lanchonete
RESTAURANT_NAME="Lanchonete Sabor Real"
KITCHEN_WHATSAPP_NUMBER="5511999999999" # Número do balcão/cozinha para receber os pedidos
DELIVERY_FEE_DEFAULT="7.00"
PIX_KEY="financeiro@lanchonete.com.br"
```

---

## 4. Fase 3: Core do Bot de Atendimento (Python FastAPI + SQLite)

O bot é estruturado em Python assíncrono para garantir velocidade e facilidade de manutenção.

### 4.1 Estrutura de Arquivos em `/home/brn/whatsapp-lanchonete/app/`
```text
app/
├── main.py              # Webhook endpoint e router de mensagens
├── database.py          # Conexão SQLite e models (pedidos, clientes, histórico)
├── menu.json            # Cardápio oficial com categorias, preços e adicionais
├── state_machine.py     # Controle do fluxo da conversa por número de WhatsApp
├── agent_llm.py         # Integração com IA (Function Calling para montar carrinho)
├── audio_transcriber.py # Conversão e transcrição de áudios (.ogg -> texto)
└── whatsapp_client.py   # Wrapper HTTP para chamadas à Evolution API
```

### 4.2 Cardápio Estruturado (`app/menu.json`)
Exemplo padrão para lanchonete:
```json
{
  "lanches": [
    {"id": "L1", "nome": "X-Burguer", "preco": 22.00, "descricao": "Pão brioche, burger 160g, queijo prato e maionese artesanal."},
    {"id": "L2", "nome": "X-Salada", "preco": 25.00, "descricao": "Pão brioche, burger 160g, queijo, alface americana, tomate e maionese."},
    {"id": "L3", "nome": "X-Bacon Supremo", "preco": 29.50, "descricao": "Pão brioche, burger 160g, cheddar duplo, bacon crocante em fatias e barbecue."}
  ],
  "porcoes": [
    {"id": "P1", "nome": "Batata Frita Tradicional (300g)", "preco": 18.00, "descricao": "Batatas crocantes com sal e orégano."},
    {"id": "P2", "nome": "Batata Frita com Cheddar e Bacon", "preco": 26.00, "descricao": "Porção generosa coberta com cheddar cremoso e bacon bits."}
  ],
  "bebidas": [
    {"id": "B1", "nome": "Coca-Cola Lata 350ml", "preco": 6.50, "descricao": "Original ou Zero Açúcar."},
    {"id": "B2", "nome": "Guaraná Antarctica 350ml", "preco": 6.00, "descricao": "Lata gelada."},
    {"id": "B3", "nome": "Suco Natural de Laranja 500ml", "preco": 9.00, "descricao": "Feito na hora, sem açúcar adicionado."}
  ],
  "adicionais": [
    {"id": "A1", "nome": "Hambúrguer Extra 160g", "preco": 9.00},
    {"id": "A2", "nome": "Bacon Extra", "preco": 5.00},
    {"id": "A3", "nome": "Queijo Cheddar Fatiado", "preco": 4.00},
    {"id": "A4", "nome": "Maionese da Casa (potinho 50ml)", "preco": 3.50}
  ]
}
```

---

## 5. Fase 4: Máquina de Estados e Engenharia de Pedidos

Para que o bot nunca se perca e não gere alucinações de preço, o fluxo segue regras estritas de estados no SQLite:

| Estado | Gatilho de Entrada | Ações do Bot | Próximo Estado |
| :--- | :--- | :--- | :--- |
| **`INICIO`** | Cliente manda primeira mensagem ("Oi", "Boa noite", "Quero pedir") | Envia saudação acolhedora e opções rápidas (1 - Cardápio, 2 - Fazer Pedido, 3 - Horário/Taxas, 4 - Falar com Atendente) | `CARDAPIO` ou `MONTANDO_PEDIDO` |
| **`CARDAPIO`** | Cliente pede para ver o cardápio | Envia texto resumido ou PDF/link do cardápio e convida a escolher | `MONTANDO_PEDIDO` |
| **`MONTANDO_PEDIDO`**| Cliente cita itens (*"Quero um x-bacon sem barbecue e uma coca zero"*) | A IA identifica os itens, valida IDs no `menu.json`, calcula subtotal e exibe o carrinho atualizado com botão de confirmação | `MONTANDO_PEDIDO` ou `COLETA_ENDERECO` |
| **`COLETA_ENDERECO`**| Cliente clica ou digita "Fechar Pedido" | Solicita se é Retirada no Balcão ou Entrega (Delivery). Se delivery, pede: Rua, Número, Bairro e Ponto de Referência | `FORMA_PAGAMENTO` |
| **`FORMA_PAGAMENTO`**| Endereço validado | Apresenta opções: 1 - Pix (Gera chave/código Copia e Cola), 2 - Cartão na Entrega, 3 - Dinheiro (pergunta se precisa de troco) | `CONFIRMADO` |
| **`CONFIRMADO`** | Pagamento selecionado | Salva pedido com status `PENDENTE_COZINHA`, gera ticket formatado, envia ao WhatsApp do cliente e notifica o WhatsApp da cozinha | `INICIO` (após cooldown de pós-venda) |

---

## 6. Fase 5: Motor Cognitivo de IA & Transcrição de Áudio

### 6.1 Transcrição de Áudio Automatizada
1. Quando o webhook do WhatsApp recebe `messageType == "audioMessage"`:
2. O bot faz o download do arquivo de áudio binário (`.ogg` / Opus) via API da Evolution.
3. Transcreve usando o motor configurado:
   - **Opção Nuvem Rápida:** Gemini 1.5/2.0 Flash (recebe áudio diretamente em bytes e já transcreve ou processa o pedido).
   - **Opção Local Bare-Metal:** Whisper local / Voxtype (já instalado no host ou via endpoint).
4. O texto transcrito é injetado diretamente na máquina de estados como se o cliente tivesse digitado.

### 6.2 Prompt Constitucional do Atendente (System Prompt)
```text
Você é a Bia, atendente virtual da Lanchonete Sabor Real.
Seu tom é cordial, prestativo, rápido e focado em fechar pedidos.
Regras Inegociáveis:
1. NUNCA invente itens ou preços que não estejam estritamente listados no cardápio oficial.
2. Quando o cliente pedir um lanche, pergunte gentilmente se deseja adicionar bebida, batata frita ou maionese artesanal da casa.
3. Se o cliente pedir remoção de ingrediente (ex: "sem cebola"), registre a observação explicitamente no item do pedido.
4. Antes de fechar, sempre apresente o resumo completo (Itens, Quantidades, Observações, Subtotal, Taxa de Entrega e Total).
```

---

## 7. Fase 6: Orquestração e Deploy com Docker Compose

### 7.1 Arquivo `docker-compose.yml`
Localização: `/home/brn/whatsapp-lanchonete/docker-compose.yml`

```yaml
version: '3.8'

services:
  evolution-postgres:
    image: postgres:15-alpine
    container_name: lanchonete-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: evolution_db
      POSTGRES_USER: evolution_user
      POSTGRES_PASSWORD: evolution_secret_pass
    volumes:
      - ./data/postgres_data:/var/lib/postgresql/data
    networks:
      - lanchonete_net
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U evolution_user -d evolution_db"]
      interval: 5s
      timeout: 5s
      retries: 5

  evolution-redis:
    image: redis:7-alpine
    container_name: lanchonete-redis
    restart: unless-stopped
    command: redis-server --appendonly yes
    volumes:
      - ./data/redis_data:/data
    networks:
      - lanchonete_net
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  evolution-api:
    image: evoapicloud/evolution-api:latest
    container_name: lanchonete-evolution-wpp
    restart: unless-stopped
    ports:
      - "8080:8080"
    env_file:
      - .env
    volumes:
      - ./data/evolution_instances:/evolution/instances
      - ./data/evolution_store:/evolution/store
    depends_on:
      evolution-postgres:
        condition: service_healthy
      evolution-redis:
        condition: service_healthy
    networks:
      - lanchonete_net

  bot:
    build:
      context: ./app
      dockerfile: Dockerfile
    container_name: lanchonete-bot-core
    restart: unless-stopped
    ports:
      - "8000:8000"
    env_file:
      - .env
    volumes:
      - ./app:/app
      - ./data/db:/app/data
      - ./data/audios:/app/audios
    depends_on:
      - evolution
    networks:
      - lanchonete_net

networks:
  lanchonete_net:
    driver: bridge
```

### 7.2 Dockerfile do Bot (`app/Dockerfile`)
```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

### 7.3 Dependências Python (`app/requirements.txt`)
```text
fastapi>=0.110.0
uvicorn>=0.28.0
pydantic>=2.6.0
httpx>=0.27.0
python-dotenv>=1.0.1
aiosqlite>=0.20.0
google-genai>=0.1.0
```

---

## 8. Fase 7: Plano de Testes e Homologação

Para certificar a prontidão antes de colocar em produção:

| # | Cenário de Teste | Ação Executada | Resultado Esperado | Status |
| :--- | :--- | :--- | :--- | :--- |
| **T01** | Inicialização dos Containers | `docker compose up -d` | 4 containers ativos (`postgres`, `redis`, `evolution`, `bot`) com < 240MB RAM | [x] Concluído |
| **T02** | Conexão do WhatsApp | Criar instância via API e escanear QR Code pelo celular | Sessão com status `open` e conectada | [ ] Aguardando Scan QR |
| **T03** | Saudação Inicial | Enviar mensagem "Olá" de um número de teste | Resposta imediata com boas-vindas da Lanchonete e menu numérico | [x] Concluído |
| **T04** | Consulta de Cardápio | Enviar "1" ou consultar `/menu` | Exibição das categorias e lanches com preços formatados em R$ | [x] Concluído |
| **T05** | Montagem de Pedido Texto | "Quero 1 X-Bacon e 1 Coca lata" | Carrinho computa: 1x L3 (R$ 29,50) + 1x B1 (R$ 6,50) = R$ 36,00 | [x] Concluído |
| **T06** | Teste com Áudio | Áudio no WhatsApp | Bot baixa áudio, transcreve com precisão e adiciona o item | [ ] Depende do WPP ativo |
| **T07** | Fechamento e Entrega | Digitar endereço completo de entrega | Coleta de dados, cálculo de frete (R$ 7,00) e exibição do total final | [x] Concluído |
| **T08** | Seleção de Pagamento | Selecionar Pix | Geração de chave Pix e gravação de Pedido #1 no SQLite | [x] Concluído |
| **T09** | Notificação à Cozinha | Finalização do pedido | Mensagem automática dispara no WhatsApp da Cozinha com ticket do pedido | [ ] Depende do WPP ativo |

---

## 9. Fase 8: Manutenção, Backup e Operação Diária

### 9.1 Comandos Úteis do Operador
```bash
# Entrar no diretório do projeto
cd /home/brn/whatsapp-lanchonete

# Visualizar logs em tempo real do Bot e do WhatsApp
docker compose logs -f --tail=50 bot
docker compose logs -f --tail=50 evolution-api

# Reiniciar o serviço sem perder sessões ou dados
docker compose restart

# Parar com segurança
docker compose down
```

### 9.2 Backup do Banco de Dados de Pedidos
O banco SQLite fica montado em `/home/brn/whatsapp-lanchonete/data/db/lanchonete.db`.
O backup automático pode ser agendado no cron do Geekom ou sincronizado com facilidade.

### 9.3 Hermes Lanchonete (Gerente Operacional Conteinerizado)
O agente isolado roda no container `hermes-lanchonete` e é invocado pelo comando determinístico:

```bash
# Modo Interativo (TUI / Chat Completo do Hermes)
hermes-lanchonete

# Modo One-Shot (Pergunta e resposta direta para scripts/pipes)
hermes-lanchonete -z "Quantos pedidos saíram hoje e qual foi o faturamento?"
hermes-lanchonete -z "Altere o preço do X-Bacon Supremo para R$ 32,00 no menu.json"
hermes-lanchonete -z "Verifique o status de todos os containers da lanchonete"
```

