"""
Processamento e Transcrição de Áudio para Mensagens de Voz do WhatsApp.
Suporta Gemini Flash nativo para áudio em português e fallback local.
"""

import os
import base64
import logging
import httpx
from typing import Optional

logger = logging.getLogger("audio_transcriber")

class AudioTranscriber:
    def __init__(self, gemini_api_key: Optional[str] = None):
        self.gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "")

    async def transcribe_base64_audio(self, audio_base64: str, mime_type: str = "audio/ogg") -> Optional[str]:
        """
        Transcreve áudio codificado em base64 diretamente para texto em português.
        """
        if not audio_base64:
            return None

        # Limpa prefixos data:audio/...;base64, se houver
        if "," in audio_base64:
            audio_base64 = audio_base64.split(",", 1)[1]

        # 1. Tentativa via Gemini Flash (Altíssima precisão e velocidade para áudios em pt-BR)
        if self.gemini_api_key:
            transcription = await self._transcribe_with_gemini(audio_base64, mime_type)
            if transcription:
                return transcription

        # 2. Se nenhuma chave externa estiver configurada, loga e avisa
        logger.warning("Nenhum motor de transcrição de áudio configurado (defina GEMINI_API_KEY no .env).")
        return None

    async def _transcribe_with_gemini(self, audio_base64: str, mime_type: str) -> Optional[str]:
        """Envia os bytes de áudio para o Gemini 1.5 Flash via REST assíncrono."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.gemini_api_key}"
        
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": "Transcreva o áudio a seguir com máxima fidelidade. Retorne APENAS o texto falado em português do Brasil, sem comentários, sem aspas, sem pontuações extras."
                        },
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": audio_base64
                            }
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 200
            }
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                        return text.strip()
                else:
                    logger.error(f"Erro na API do Gemini: HTTP {resp.status_code} - {resp.text}")
                    return None
        except Exception as e:
            logger.error(f"Exceção ao transcrever áudio com Gemini: {e}")
            return None
