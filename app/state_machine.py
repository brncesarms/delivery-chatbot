"""
Máquina de Estados Finita e Lógica de Conversação para o Chatbot de Lanchonete.
Garante fluxo linear, cálculo determinístico de carrinho e fechamento de pedidos.
"""

import os
import logging
from typing import Dict, Any, List, Tuple
from database import get_session, update_session, reset_session, create_order
from agent_llm import MenuEngine, LLMAgent
from whatsapp_client import WhatsAppClient

logger = logging.getLogger("state_machine")

class ConversationFlow:
    def __init__(self, menu_engine: MenuEngine, llm_agent: LLMAgent, wpp_client: WhatsAppClient):
        self.menu_engine = menu_engine
        self.llm_agent = llm_agent
        self.wpp_client = wpp_client
        self.kitchen_number = os.getenv("KITCHEN_WHATSAPP_NUMBER", "")
        self.default_delivery_fee = float(os.getenv("DELIVERY_FEE_DEFAULT", "7.00"))
        self.pix_key = os.getenv("PIX_KEY", "financeiro@lanchonete.com.br")
        self.restaurant_name = os.getenv("RESTAURANT_NAME", "Lanchonete Sabor Real")

    async def handle_incoming_message(self, phone: str, text: str, customer_name: str = "") -> str:
        """Processa a mensagem recebida de acordo com o estado atual da sessão."""
        session = await get_session(phone)
        state = session.get("state", "INICIO")
        clean_text = text.strip()
        lower_text = clean_text.lower()

        # Comandos universais
        if lower_text in ["menu", "inicio", "reiniciar", "cancelar"]:
            await reset_session(phone)
            return self._msg_welcome(customer_name)

        if state == "INICIO":
            return await self._state_inicio(phone, clean_text, session, customer_name)
        elif state == "MONTANDO_PEDIDO":
            return await self._state_montando_pedido(phone, clean_text, session)
        elif state == "COLETA_ENTREGA":
            return await self._state_coleta_entrega(phone, clean_text, session)
        elif state == "COLETA_ENDERECO":
            return await self._state_coleta_endereco(phone, clean_text, session)
        elif state == "FORMA_PAGAMENTO":
            return await self._state_forma_pagamento(phone, clean_text, session)
        elif state == "TROCO_DINHEIRO":
            return await self._state_troco_dinheiro(phone, clean_text, session)
        else:
            await reset_session(phone)
            return self._msg_welcome(customer_name)

    # =========================================================================
    # ESTADO: INICIO
    # =========================================================================
    async def _state_inicio(self, phone: str, text: str, session: Dict[str, Any], customer_name: str) -> str:
        lower = text.lower()

        # Opção 1: Ver cardápio
        if lower in ["1", "cardapio", "cardápio", "ver cardapio", "ver cardápio"]:
            await update_session(phone, state="MONTANDO_PEDIDO", customer_name=customer_name)
            menu_text = self.menu_engine.format_menu_text()
            return f"{menu_text}\n\nQual item você deseja pedir?"

        # Opção 2: Fazer pedido
        if lower in ["2", "pedir", "fazer pedido", "quero pedir"]:
            await update_session(phone, state="MONTANDO_PEDIDO", customer_name=customer_name)
            return "Perfeito! Pode me dizer o que você deseja pedir hoje (ou digite o código/nome do lanche):"

        # Opção 3: Horários e Informações
        if lower in ["3", "horario", "horário", "taxa", "entrega", "duvidas", "dúvidas"]:
            return (
                f"ℹ️ *INFORMAÇÕES DA {self.restaurant_name.upper()}*\n"
                "─────────────────────────────\n"
                "⏰ *Horário:* Terça a Domingo das 18h00 às 23h30\n"
                f"🛵 *Taxa de Entrega:* R$ {self.default_delivery_fee:.2f} (para a região central/bairros próximos)\n"
                "📍 *Retirada:* Também atendemos com retirada no balcão!\n\n"
                "Digite *1* para ver o cardápio ou *2* para pedir agora."
            )

        # Opção 4: Atendente humano
        if lower in ["4", "humano", "atendente", "falar com atendente"]:
            return "Um de nossos atendentes humanos já foi notificado e falará com você em instantes. Aguarde um momento por favor!"

        # Se o cliente já mandou direto o pedido (ex: "Quero um X-Bacon e uma Coca")
        nlu_res = await self.llm_agent.interpret_customer_intent(text, [])
        if nlu_res.get("items"):
            await update_session(phone, state="MONTANDO_PEDIDO", cart=nlu_res["items"], customer_name=customer_name)
            return self._format_cart_response(nlu_res["items"], "Item(ns) adicionado(s) com sucesso!")

        return self._msg_welcome(customer_name)

    # =========================================================================
    # ESTADO: MONTANDO_PEDIDO
    # =========================================================================
    async def _state_montando_pedido(self, phone: str, text: str, session: Dict[str, Any]) -> str:
        lower = text.lower()
        cart = session.get("cart", [])

        # Comando para ver cardápio novamente
        if lower in ["cardapio", "cardápio", "ver cardapio"]:
            return self.menu_engine.format_menu_text()

        # Comando para limpar carrinho
        if lower in ["limpar", "cancelar carrinho", "esvaziar"]:
            await update_session(phone, cart=[])
            return "Seu carrinho foi esvaziado. O que você gostaria de pedir?"

        # Comando para fechar pedido
        if lower in ["fechar", "fechar pedido", "finalizar", "concluir", "ok", "pronto", "1"] and len(cart) > 0:
            await update_session(phone, state="COLETA_ENTREGA")
            subtotal = sum(i["preco"] * i["qtd"] for i in cart)
            return (
                f"Subtotal dos itens: *R$ {subtotal:.2f}*\n\n"
                "Como você deseja receber o pedido?\n"
                "1️⃣ - *Delivery* (Entrega no seu endereço)\n"
                "2️⃣ - *Retirada no Balcão* (Você retira aqui sem taxa)"
            )

        # Tenta interpretar itens para adicionar ao carrinho
        nlu_res = await self.llm_agent.interpret_customer_intent(text, cart)
        new_items = nlu_res.get("items", [])

        if new_items:
            # Concatena itens ao carrinho atual
            cart.extend(new_items)
            await update_session(phone, cart=cart)
            return self._format_cart_response(cart, "Adicionado ao pedido!")

        return (
            "Não consegui identificar o item no cardápio. 🤔\n"
            "Digite o código ou nome (Ex: *X-Bacon*, *Batata Frita*, *Coca lata*), "
            "ou digite *Cardápio* para ver as opções, ou *Fechar* para concluir o pedido atual."
        )

    # =========================================================================
    # ESTADO: COLETA_ENTREGA (Delivery vs Balcão)
    # =========================================================================
    async def _state_coleta_entrega(self, phone: str, text: str, session: Dict[str, Any]) -> str:
        lower = text.lower()
        if "1" in lower or "delivery" in lower or "entrega" in lower:
            await update_session(phone, delivery_type="DELIVERY", state="COLETA_ENDERECO")
            return (
                "🛵 *Entrega Selecionada!*\n\n"
                "Por favor, digite seu endereço completo de entrega:\n"
                "*(Rua, Número, Bairro e Ponto de Referência)*"
            )
        elif "2" in lower or "balcao" in lower or "balcão" in lower or "retirada" in lower:
            await update_session(phone, delivery_type="BALCAO", delivery_address="Retirada no Balcão", state="FORMA_PAGAMENTO")
            return self._msg_payment_options(session, delivery_fee=0.0)
        else:
            return "Por favor, escolha digitando:\n1️⃣ para *Delivery*\n2️⃣ para *Retirada no Balcão*"

    # =========================================================================
    # ESTADO: COLETA_ENDERECO
    # =========================================================================
    async def _state_coleta_endereco(self, phone: str, text: str, session: Dict[str, Any]) -> str:
        if len(text.strip()) < 5:
            return "Por favor, informe um endereço válido (Rua, Número e Bairro):"

        await update_session(phone, delivery_address=text.strip(), state="FORMA_PAGAMENTO")
        return self._msg_payment_options(session, delivery_fee=self.default_delivery_fee)

    # =========================================================================
    # ESTADO: FORMA_PAGAMENTO
    # =========================================================================
    async def _state_forma_pagamento(self, phone: str, text: str, session: Dict[str, Any]) -> str:
        lower = text.lower()

        if "1" in lower or "pix" in lower:
            await update_session(phone, payment_method="PIX")
            return await self._finalize_order(phone, session, payment_method="PIX")
        elif "2" in lower or "cartao" in lower or "cartão" in lower:
            await update_session(phone, payment_method="CARTAO")
            return await self._finalize_order(phone, session, payment_method="CARTÃO (Máquina na entrega)")
        elif "3" in lower or "dinheiro" in lower:
            await update_session(phone, payment_method="DINHEIRO", state="TROCO_DINHEIRO")
            return "💵 *Pagamento em Dinheiro!*\nPrecisa de troco? Se sim, digite o valor da nota (Ex: *Troco para 50*). Se for dinheiro trocado, digite *Não*."
        else:
            return "Por favor, selecione uma opção válida:\n1️⃣ - Pix\n2️⃣ - Cartão\n3️⃣ - Dinheiro"

    # =========================================================================
    # ESTADO: TROCO_DINHEIRO
    # =========================================================================
    async def _state_troco_dinheiro(self, phone: str, text: str, session: Dict[str, Any]) -> str:
        lower = text.lower()
        change_val = 0.0
        import re
        numbers = re.findall(r"\d+(?:[.,]\d+)?", text)
        if numbers and lower not in ["nao", "não", "sem troco"]:
            try:
                change_val = float(numbers[0].replace(",", "."))
            except:
                change_val = 0.0

        await update_session(phone, change_for=change_val)
        method_str = f"DINHEIRO (Troco para R$ {change_val:.2f})" if change_val > 0 else "DINHEIRO (Valor exato, sem troco)"
        return await self._finalize_order(phone, session, payment_method=method_str, change_for=change_val)

    # =========================================================================
    # FINALIZAÇÃO E REGISTRO DO PEDIDO
    # =========================================================================
    async def _finalize_order(
        self,
        phone: str,
        session: Dict[str, Any],
        payment_method: str,
        change_for: float = 0.0
    ) -> str:
        cart = session.get("cart", [])
        delivery_type = session.get("delivery_type", "DELIVERY")
        delivery_address = session.get("delivery_address", "Balcão")
        customer_name = session.get("customer_name", "Cliente")

        subtotal = sum(i["preco"] * i["qtd"] for i in cart)
        delivery_fee = self.default_delivery_fee if delivery_type == "DELIVERY" else 0.0
        total = subtotal + delivery_fee

        # Salva o pedido no SQLite
        order_id = await create_order(
            phone=phone,
            customer_name=customer_name,
            delivery_type=delivery_type,
            delivery_address=delivery_address,
            cart=cart,
            subtotal=subtotal,
            delivery_fee=delivery_fee,
            total=total,
            payment_method=payment_method,
            change_for=change_for
        )

        # Monta o Ticket do Pedido
        ticket_lines = [
            f"🧾 *PEDIDO #{order_id} CONFIRMADO!*",
            f"🏠 *{self.restaurant_name}*",
            "─────────────────────────────",
            f"👤 *Cliente:* {customer_name} ({phone})",
            f"📍 *Tipo:* {'Entrega (Delivery)' if delivery_type == 'DELIVERY' else 'Retirada no Balcão'}",
            f"📌 *Endereço:* {delivery_address}",
            "─────────────────────────────",
            "📦 *ITENS DO PEDIDO:*"
        ]
        for it in cart:
            obs_str = f"   ↳ _{it['observacoes']}_" if it.get("observacoes") else ""
            ticket_lines.append(f"• {it['qtd']}x {it['nome']} - R$ {it['preco'] * it['qtd']:.2f}")
            if obs_str:
                ticket_lines.append(obs_str)

        ticket_lines.extend([
            "─────────────────────────────",
            f"Subtotal: R$ {subtotal:.2f}",
            f"Taxa de Entrega: R$ {delivery_fee:.2f}",
            f"💰 *TOTAL A PAGAR:* *R$ {total:.2f}*",
            f"💳 *Forma de Pagamento:* {payment_method}",
            "─────────────────────────────"
        ])

        if "PIX" in payment_method:
            ticket_lines.extend([
                "🔑 *Chave PIX:*",
                f"`{self.pix_key}`",
                "\n_Por favor, realize a transferência e envie o comprovante por aqui._"
            ])

        ticket_lines.append("\n⏱️ *Tempo estimado:* 35 a 50 minutos.")
        ticket_lines.append("Agradecemos a sua preferência! Seu lanche já está entrando na chapa. 🍔🔥")

        final_ticket = "\n".join(ticket_lines)

        # Notifica o WhatsApp da cozinha/balcão em background
        if self.kitchen_number and self.kitchen_number != "5511999999999":
            try:
                await self.wpp_client.send_kitchen_notification(self.kitchen_number, final_ticket)
            except Exception as e:
                logger.error(f"Erro ao notificar cozinha: {e}")

        # Reinicia a sessão para novas conversas futuras
        await reset_session(phone)

        return final_ticket

    # =========================================================================
    # HELPERS DE FORMATAÇÃO
    # =========================================================================
    def _msg_welcome(self, name: str) -> str:
        greeting = f"Olá {name}! " if name else "Olá! "
        return (
            f"🍔 *Bem-vindo(a) à {self.restaurant_name}!* 🍟\n\n"
            "Como posso te atender hoje?\n"
            "1️⃣ - *Ver Cardápio Completo*\n"
            "2️⃣ - *Fazer um Pedido*\n"
            "3️⃣ - *Horários e Taxa de Entrega*\n"
            "4️⃣ - *Falar com Atendente*\n\n"
            "_(Dica: você também pode mandar áudio ou digitar direto o que deseja pedir!)_"
        )

    def _msg_payment_options(self, session: Dict[str, Any], delivery_fee: float) -> str:
        cart = session.get("cart", [])
        subtotal = sum(i["preco"] * i["qtd"] for i in cart)
        total = subtotal + delivery_fee
        return (
            f"📋 *Resumo Financeiro:*\n"
            f"• Subtotal: R$ {subtotal:.2f}\n"
            f"• Taxa de Entrega: R$ {delivery_fee:.2f}\n"
            f"• *Total a Pagar:* *R$ {total:.2f}*\n\n"
            "Qual será a forma de pagamento?\n"
            "1️⃣ - *Pix* (Chave instantânea)\n"
            "2️⃣ - *Cartão* (Débito ou Crédito na entrega/balcão)\n"
            "3️⃣ - *Dinheiro* (Com ou sem troco)"
        )

    def _format_cart_response(self, cart: List[Dict[str, Any]], alert: str) -> str:
        lines = [f"✅ *{alert}*\n", "🛒 *SEU PEDIDO ATÉ AGORA:*"]
        subtotal = 0.0
        for idx, it in enumerate(cart, 1):
            item_total = it["preco"] * it["qtd"]
            subtotal += item_total
            obs = f" ({it['observacoes']})" if it.get("observacoes") else ""
            lines.append(f"{idx}. {it['qtd']}x *{it['nome']}*{obs} - R$ {item_total:.2f}")

        lines.extend([
            f"\n💰 *Subtotal Parcial:* R$ {subtotal:.2f}\n",
            "O que deseja fazer agora?",
            "• Adicionar mais itens: basta digitar o que quer",
            "• *1* ou *Fechar*: Concluir pedido e ir para entrega",
            "• Digite *Limpar*: para recomeçar o carrinho"
        ])
        return "\n".join(lines)
