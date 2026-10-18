"""
Camada de Persistência SQLite Assíncrona para o Bot da Lanchonete.
Gerencia sessões ativas de conversação no WhatsApp e registro imutável de pedidos.
"""

import json
import os
import aiosqlite
from datetime import datetime
from typing import Dict, Any, Optional, List

DB_PATH = os.getenv("SQLITE_DB_PATH", "/app/data/lanchonete.db")

async def init_db():
    """Cria as tabelas necessárias no SQLite se não existirem."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                phone TEXT PRIMARY KEY,
                state TEXT NOT NULL DEFAULT 'INICIO',
                customer_name TEXT,
                cart_json TEXT NOT NULL DEFAULT '[]',
                delivery_type TEXT DEFAULT 'DELIVERY',
                delivery_address TEXT DEFAULT '',
                payment_method TEXT DEFAULT '',
                change_for REAL DEFAULT 0.0,
                notes TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                customer_name TEXT,
                delivery_type TEXT NOT NULL,
                delivery_address TEXT,
                items_json TEXT NOT NULL,
                subtotal REAL NOT NULL,
                delivery_fee REAL NOT NULL,
                total REAL NOT NULL,
                payment_method TEXT NOT NULL,
                change_for REAL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'RECEBIDO',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()

async def get_session(phone: str) -> Dict[str, Any]:
    """Recupera ou cria uma sessão ativa para o telefone especificado."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM sessions WHERE phone = ?", (phone,)) as cursor:
            row = await cursor.fetchone()
            if row:
                session = dict(row)
                session["cart"] = json.loads(session["cart_json"])
                return session

        # Se não existe, cria sessão inicial
        initial_cart = "[]"
        now = datetime.utcnow().isoformat()
        await db.execute(
            """
            INSERT INTO sessions (phone, state, cart_json, updated_at)
            VALUES (?, 'INICIO', ?, ?)
            """,
            (phone, initial_cart, now)
        )
        await db.commit()
        return {
            "phone": phone,
            "state": "INICIO",
            "customer_name": "",
            "cart": [],
            "delivery_type": "DELIVERY",
            "delivery_address": "",
            "payment_method": "",
            "change_for": 0.0,
            "notes": "",
            "updated_at": now
        }

async def update_session(phone: str, **kwargs):
    """Atualiza campos específicos da sessão do cliente."""
    fields = []
    values = []
    
    if "cart" in kwargs:
        kwargs["cart_json"] = json.dumps(kwargs.pop("cart"), ensure_ascii=False)
        
    for k, v in kwargs.items():
        fields.append(f"{k} = ?")
        values.append(v)
        
    fields.append("updated_at = CURRENT_TIMESTAMP")
    values.append(phone)
    
    query = f"UPDATE sessions SET {', '.join(fields)} WHERE phone = ?"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(query, tuple(values))
        await db.commit()

async def reset_session(phone: str):
    """Reinicia a sessão após um pedido ser finalizado ou cancelado."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE sessions
            SET state = 'INICIO',
                cart_json = '[]',
                delivery_type = 'DELIVERY',
                delivery_address = '',
                payment_method = '',
                change_for = 0.0,
                notes = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE phone = ?
            """,
            (phone,)
        )
        await db.commit()

async def create_order(
    phone: str,
    customer_name: str,
    delivery_type: str,
    delivery_address: str,
    cart: List[Dict[str, Any]],
    subtotal: float,
    delivery_fee: float,
    total: float,
    payment_method: str,
    change_for: float = 0.0
) -> int:
    """Registra um novo pedido fechado no banco de dados e retorna o ID gerado."""
    items_json = json.dumps(cart, ensure_ascii=False)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO orders (
                phone, customer_name, delivery_type, delivery_address,
                items_json, subtotal, delivery_fee, total,
                payment_method, change_for, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RECEBIDO')
            """,
            (
                phone, customer_name, delivery_type, delivery_address,
                items_json, subtotal, delivery_fee, total,
                payment_method, change_for
            )
        )
        await db.commit()
        return cursor.lastrowid

async def get_recent_orders(limit: int = 10) -> List[Dict[str, Any]]:
    """Consulta os últimos pedidos registrados."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                item["items"] = json.loads(item["items_json"])
                results.append(item)
            return results
