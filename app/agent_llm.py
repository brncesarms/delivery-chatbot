"""
Motor de Interpretação e NLU para o Chatbot de Lanchonete.
Combina parser determinístico com LLM (Gemini Flash ou Qwen Local) para extrair itens e adicionais.
"""

import os
import json
import re
import logging
import httpx
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("agent_llm")

class MenuEngine:
    def __init__(self, menu_path: str = "/app/menu.json"):
        self.menu_data = self._load_menu(menu_path)
        self.items_index = self._build_index()

    def _load_menu(self, path: str) -> Dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Erro ao carregar {path}: {e}")
            return {"lanches": [], "porcoes": [], "bebidas": [], "adicionais": []}

    def _build_index(self) -> Dict[str, Dict[str, Any]]:
        index = {}
        for category in ["lanches", "porcoes", "bebidas", "adicionais"]:
            for item in self.menu_data.get(category, []):
                item_copy = dict(item)
                item_copy["categoria"] = category
                index[item["id"].upper()] = item_copy
                index[item["nome"].lower()] = item_copy
                for alias in item.get("aliases", []):
                    index[alias.lower()] = item_copy
        return index

    def format_menu_text(self) -> str:
        """Gera a mensagem formatada e bonita do cardápio para o WhatsApp."""
        lines = [
            "🍔 *CARDÁPIO OFICIAL - LANCHONETE SABOR REAL* 🍟",
            "─────────────────────────────",
            "✨ *LANCHES ARTESANAIS*"
        ]
        for l in self.menu_data.get("lanches", []):
            lines.append(f"• *{l['id']} - {l['nome']}* | R$ {l['preco']:.2f}")
            lines.append(f"  _{l['descricao']}_")

        lines.append("\n🍟 *PORÇÕES CROCANTES*")
        for p in self.menu_data.get("porcoes", []):
            lines.append(f"• *{p['id']} - {p['nome']}* | R$ {p['preco']:.2f}")
            lines.append(f"  _{p['descricao']}_")

        lines.append("\n🥤 *BEBIDAS GELADAS*")
        for b in self.menu_data.get("bebidas", []):
            lines.append(f"• *{b['id']} - {b['nome']}* | R$ {b['preco']:.2f}")

        lines.append("\n🥓 *ADICIONAIS EXTRAS*")
        for a in self.menu_data.get("adicionais", []):
            lines.append(f"• *{a['id']} - {a['nome']}* | +R$ {a['preco']:.2f}")

        lines.append("─────────────────────────────")
        lines.append("💡 *Dica:* Para pedir, basta digitar o nome do lanche ou o código (Ex: *1 X-Bacon e 1 Coca lata* ou *L3 e B1*).")
        return "\n".join(lines)

    def find_item_deterministic(self, query: str) -> Optional[Dict[str, Any]]:
        """Busca um item no catálogo por ID, nome ou alias."""
        clean = query.strip().lower()
        if clean.upper() in self.items_index:
            return self.items_index[clean.upper()]
        return self.items_index.get(clean)


class LLMAgent:
    def __init__(self, menu_engine: MenuEngine):
        self.menu_engine = menu_engine
        self.gemini_key = os.getenv("GEMINI_API_KEY", "")
        self.llama_url = os.getenv("LLAMA_CPP_BASE_URL", "http://127.0.0.1:18434/v1")

    async def interpret_customer_intent(self, user_text: str, current_cart: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analisa a mensagem do usuário e extrai itens, quantidades e observações.
        Prioriza Gemini Flash se chave disponível; caso contrário executa parsing determinístico.
        """
        # 1. Se houver chave Gemini, utiliza raciocínio semântico avançado
        if self.gemini_key:
            llm_result = await self._call_gemini_nlu(user_text, current_cart)
            if llm_result:
                return llm_result

        # 2. Parsing determinístico de alta precisão (Zero alucinação, 100% offline)
        return self._deterministic_nlu(user_text)

    def _deterministic_nlu(self, text: str) -> Dict[str, Any]:
        """Parser por regex e correspondência de cardápio."""
        text_lower = text.lower()
        items_found = []

        # Padrões comuns: "2 x-bacon", "1 coca lata", "L3", "quero uma batata frita"
        # Itera sobre todos os itens do cardápio
        for cat in ["lanches", "porcoes", "bebidas"]:
            for item in self.menu_engine.menu_data.get(cat, []):
                # Verifica aliases e ID
                matched = False
                matched_alias = ""
                
                # Checa ID
                id_pattern = rf"\b{re.escape(item['id'].lower())}\b"
                if re.search(id_pattern, text_lower):
                    matched = True
                    matched_alias = item['id'].lower()
                else:
                    for alias in item.get("aliases", []):
                        if alias in text_lower:
                            matched = True
                            matched_alias = alias
                            break

                if matched:
                    # Tenta descobrir quantidade antes do item (ex: "2 x-salada" ou "dois x-salada")
                    qty = 1
                    qty_match = re.search(rf"(\d+)\s*(?:x\s*)?{re.escape(matched_alias)}", text_lower)
                    if qty_match:
                        try:
                            qty = int(qty_match.group(1))
                        except:
                            qty = 1
                    elif f"dois {matched_alias}" in text_lower or f"duas {matched_alias}" in text_lower:
                        qty = 2
                    elif f"tres {matched_alias}" in text_lower or f"três {matched_alias}" in text_lower:
                        qty = 3

                    # Extrai observações básicas (ex: sem cebola, sem molho)
                    obs = ""
                    obs_match = re.search(rf"{re.escape(matched_alias)}.*?(sem\s+[a-zà-ú]+)", text_lower)
                    if obs_match:
                        obs = obs_match.group(1).strip()

                    items_found.append({
                        "id": item["id"],
                        "nome": item["nome"],
                        "preco": item["preco"],
                        "qtd": qty,
                        "observacoes": obs
                    })

        if items_found:
            return {
                "action": "ADD_ITEMS",
                "items": items_found,
                "notes": ""
            }

        return {
            "action": "UNKNOWN",
            "items": [],
            "notes": ""
        }

    async def _call_gemini_nlu(self, text: str, current_cart: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Chamada estruturada ao Gemini Flash para NLU em formato JSON estrito."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.gemini_key}"
        
        system_prompt = f"""
Você é o assistente NLU da Lanchonete Sabor Real.
Cardápio Oficial:
{json.dumps(self.menu_engine.menu_data, ensure_ascii=False)}

Carrinho Atual:
{json.dumps(current_cart, ensure_ascii=False)}

Analise a mensagem do cliente e retorne APENAS um JSON válido no formato:
{{
  "action": "ADD_ITEMS" | "REMOVE_ITEM" | "CLEAR_CART" | "REQUEST_MENU" | "FINISH_ORDER" | "OTHER",
  "items": [
    {{
      "id": "ID_DO_CARDAPIO",
      "nome": "NOME_DO_CARDAPIO",
      "preco": 0.00,
      "qtd": 1,
      "observacoes": "observação de ponto ou ingredientes removidos/adicionados"
    }}
  ]
}}
NUNCA invente itens ou preços que não estejam na lista oficial.
"""
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": f"Mensagem do cliente: {text}"}]}
            ],
            "system_instruction": {
                "parts": [{"text": system_prompt}]
            },
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.1
            }
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        raw_json = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                        return json.loads(raw_json)
        except Exception as e:
            logger.error(f"Erro ao processar Gemini NLU: {e}")
            return None
