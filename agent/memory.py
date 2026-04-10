# agent/memory.py — Conversation history
from datetime import datetime
from sqlalchemy import select
from agent.database import async_session, engine
from agent.models import Base, Message


async def inicializar_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Run schema migrations (adds new columns safely to existing tables)
    try:
        from agent.migrations import run_migrations
        await run_migrations()
    except Exception as e:
        import logging
        logging.getLogger("agentkit").warning(f"Migration warning: {e}")


async def guardar_mensaje(telefono: str, role: str, content: str):
    async with async_session() as session:
        msg = Message(phone=telefono, role=role, content=content, created_at=datetime.utcnow())
        session.add(msg)
        await session.commit()


async def obtener_historial(telefono: str, limite: int = 20) -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(Message)
            .where(Message.phone == telefono)
            .order_by(Message.created_at.desc())
            .limit(limite)
        )
        msgs = list(reversed(result.scalars().all()))
        return [{"role": m.role, "content": m.content} for m in msgs]


async def limpiar_historial(telefono: str):
    async with async_session() as session:
        result = await session.execute(select(Message).where(Message.phone == telefono))
        for msg in result.scalars().all():
            await session.delete(msg)
        await session.commit()
