"""
Cliente HTTP Assíncrono para integração com a Evolution API v2.
Responsável por disparar mensagens, notificações e baixar mídias do WhatsApp.
"""

import os
import logging
import httpx
from typing import Optional, Dict, Any

logger = logging.getLogger("whatsapp_client")

class WhatsAppClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        instance_name: Optional[str] = None
    ):
        self.base_url = (base_url or os.getenv("EVOLUTION_API_URL") or os.getenv("SERVER_URL", "http://evolution-api:8080")).rstrip("/")
        self.api_key = api_key or os.getenv("AUTHENTICATION_API_KEY", "lancho_secret_key_geekom_2026")
        self.instance_name = instance_name or os.getenv("CONFIG_SESSION_PHONE_CLIENT", "LanchoneteBot")
        self.headers = {
            "apikey": self.api_key,
            "Content-Type": "application/json"
        }

    async def send_text(self, phone: str, text: str, delay: int = 1000) -> bool:
        """
        Envia uma mensagem de texto simples ou formatada para o número do cliente.
        Formata o número limpando caracteres especiais.
        """
        clean_phone = "".join(filter(str.isdigit, phone))
        endpoint = f"{self.base_url}/message/sendText/{self.instance_name}"
        payload = {
            "number": clean_phone,
            "text": text,
            "delay": delay,
            "linkPreview": False
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(endpoint, json=payload, headers=self.headers)
                if resp.status_code in (200, 201):
                    logger.info(f"Mensagem enviada com sucesso para {clean_phone}")
                    return True
                else:
                    logger.error(f"Erro ao enviar mensagem para {clean_phone}: HTTP {resp.status_code} - {resp.text}")
                    return False
        except Exception as e:
            logger.error(f"Exceção ao conectar na Evolution API ({endpoint}): {e}")
            return False

    async def send_kitchen_notification(self, kitchen_number: str, order_ticket: str) -> bool:
        """Envia o ticket do pedido formatado diretamente para a cozinha/balcão."""
        header = "🔔 *NOVO PEDIDO RECEBIDO!*\n" + ("=" * 28) + "\n"
        return await self.send_text(kitchen_number, header + order_ticket, delay=500)

    async def get_media_base64(self, message_obj: Dict[str, Any]) -> Optional[str]:
        """
        Solicita à Evolution API o download e conversão de uma mensagem de áudio ou mídia para base64.
        """
        endpoint = f"{self.base_url}/chat/getBase64FromMediaMessage/{self.instance_name}"
        payload = {
            "message": message_obj,
            "convertToMp4": False
        }

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(endpoint, json=payload, headers=self.headers)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    return data.get("base64")
                else:
                    logger.error(f"Falha ao extrair base64 da mídia: {resp.status_code} - {resp.text}")
                    return None
        except Exception as e:
            logger.error(f"Exceção ao baixar mídia da Evolution API: {e}")
            return None

    async def get_instance_status(self) -> Dict[str, Any]:
        """Verifica o status de conexão da instância do WhatsApp."""
        endpoint = f"{self.base_url}/instance/connectionState/{self.instance_name}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(endpoint, headers=self.headers)
                if resp.status_code == 200:
                    return resp.json()
                return {"status": "offline", "detail": resp.text}
        except Exception as e:
            return {"status": "unreachable", "error": str(e)}

    async def create_instance_if_not_exists(self) -> Dict[str, Any]:
        """Cria a instância do WhatsApp caso ela ainda não tenha sido inicializada."""
        endpoint = f"{self.base_url}/instance/create"
        payload = {
            "instanceName": self.instance_name,
            "token": self.api_key,
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS"
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(endpoint, json=payload, headers=self.headers)
                return resp.json()
        except Exception as e:
            return {"error": str(e)}
