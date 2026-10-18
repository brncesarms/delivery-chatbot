"""
Ponto de Entrada da Aplicação FastAPI - Webhook e Orquestrador do Chatbot de Lanchonete.
Recebe webhooks da Evolution API, processa textos e áudios, e responde assincronamente.
"""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

# Carrega variáveis de ambiente
load_dotenv("/home/brn/whatsapp-lanchonete/.env")

from database import init_db, get_recent_orders
from agent_llm import MenuEngine, LLMAgent
from audio_transcriber import AudioTranscriber
from whatsapp_client import WhatsAppClient
from state_machine import ConversationFlow

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("bot_main")

# Instâncias dos Componentes
menu_engine = None
llm_agent = None
audio_transcriber = None
wpp_client = None
flow = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global menu_engine, llm_agent, audio_transcriber, wpp_client, flow
    logger.info("Inicializando o Banco de Dados e Serviços do Bot...")
    await init_db()

    # Inicializa serviços
    menu_engine = MenuEngine(os.getenv("MENU_PATH", "menu.json"))
    llm_agent = LLMAgent(menu_engine)
    audio_transcriber = AudioTranscriber()
    wpp_client = WhatsAppClient()
    flow = ConversationFlow(menu_engine, llm_agent, wpp_client)

    logger.info("Chatbot de Lanchonete pronto e escutando webhooks.")
    yield
    logger.info("Desligando o Bot de Lanchonete...")

app = FastAPI(title="Lanchonete WhatsApp Bot", version="1.0.0", lifespan=lifespan)

@app.get("/health")
async def health_check():
    """Verifica a saúde do bot e conectividade com a Evolution API."""
    inst_status = await wpp_client.get_instance_status() if wpp_client else {}
    return {
        "status": "healthy",
        "service": "Lanchonete WhatsApp Core",
        "evolution_gateway": inst_status
    }

@app.get("/menu")
async def get_menu():
    """Retorna o cardápio oficial em formato JSON."""
    return menu_engine.menu_data if menu_engine else {}

@app.get("/orders")
async def list_orders(limit: int = 15):
    """Consulta os últimos pedidos registrados no banco SQLite."""
    orders = await get_recent_orders(limit=limit)
    return {"total": len(orders), "orders": orders}

async def process_webhook_message(data: dict):
    """Executa a lógica de interpretação e resposta em background task."""
    try:
        key = data.get("key", {})
        remote_jid = key.get("remoteJid", "")
        from_me = key.get("fromMe", False)
        push_name = data.get("pushName", "")
        message = data.get("message", {})
        message_type = data.get("messageType", "")

        # 1. Ignora mensagens enviadas pelo próprio bot (evita loops)
        if from_me:
            return

        # 2. Ignora mensagens de grupos do WhatsApp
        if not remote_jid or "@g.us" in remote_jid or "status@broadcast" in remote_jid:
            return

        clean_phone = remote_jid.split("@")[0]
        user_text = ""
        is_audio = False

        # 3. Extração de Mensagem de Texto
        if message_type == "conversation":
            user_text = message.get("conversation", "")
        elif message_type == "extendedTextMessage":
            user_text = message.get("extendedTextMessage", {}).get("text", "")
        elif "conversation" in message:
            user_text = message.get("conversation", "")
        elif "extendedTextMessage" in message:
            user_text = message.get("extendedTextMessage", {}).get("text", "")

        # 4. Extração e Transcrição de Mensagem de Áudio
        elif message_type == "audioMessage" or "audioMessage" in message:
            is_audio = True
            logger.info(f"Recebida mensagem de áudio de {clean_phone}. Baixando mídia...")
            audio_base64 = await wpp_client.get_media_base64(data)
            if audio_base64:
                transcription = await audio_transcriber.transcribe_base64_audio(audio_base64)
                if transcription:
                    user_text = transcription
                    logger.info(f"Áudio transcrito com sucesso: '{user_text}'")
                else:
                    await wpp_client.send_text(
                        clean_phone,
                        "Não consegui compreender o áudio com clareza. Você poderia digitar seu pedido por favor?"
                    )
                    return
            else:
                logger.error("Falha ao recuperar base64 do áudio da Evolution API.")
                return

        # Se não há texto identificável, ignora
        if not user_text or not user_text.strip():
            return

        logger.info(f"Mensagem recebida de {clean_phone} ({push_name}): '{user_text}'")

        # 5. Processamento pela Máquina de Estados
        reply_text = await flow.handle_incoming_message(clean_phone, user_text, customer_name=push_name)

        # Se foi áudio, adiciona feedback cordial ao cliente
        if is_audio:
            reply_text = f"🎙️ _Entendi seu áudio:_ \"*{user_text}*\"\n\n{reply_text}"

        # 6. Disparo da Resposta via WhatsApp
        await wpp_client.send_text(clean_phone, reply_text)

    except Exception as e:
        logger.error(f"Erro ao processar webhook em background: {e}", exc_info=True)

@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Webhook receptor global de eventos da Evolution API.
    Processa payloads de 'messages.upsert' de forma não bloqueante.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    event = body.get("event")
    data = body.get("data", {})

    if event == "messages.upsert" and data:
        background_tasks.add_task(process_webhook_message, data)

    return {"status": "received"}
