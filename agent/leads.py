# agent/leads.py — Lead capture, scoring, and funnel management
import logging
from datetime import datetime
from sqlalchemy import select
from agent.database import async_session
from agent.models import Lead, FunnelEvent

logger = logging.getLogger("agentkit")

STATUSES = ["new", "qualified", "follow_up", "demo_booked", "won", "lost"]


async def upsert_lead(phone: str) -> Lead:
    """Create lead if new, update last_seen if existing."""
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.phone == phone))
        lead = result.scalar_one_or_none()
        if lead is None:
            lead = Lead(phone=phone, last_seen_at=datetime.utcnow())
            session.add(lead)
            logger.info(f"New lead created: {phone}")
        else:
            lead.last_seen_at = datetime.utcnow()
        await session.commit()
        await session.refresh(lead)
        return lead


async def update_lead_field(phone: str, **fields):
    """Update any field on a lead."""
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.phone == phone))
        lead = result.scalar_one_or_none()
        if lead:
            for key, value in fields.items():
                setattr(lead, key, value)
            lead.updated_at = datetime.utcnow()
            await session.commit()


async def update_lead_status(phone: str, new_status: str, triggered_by: str = "agent"):
    """Move lead through funnel and log the event."""
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.phone == phone))
        lead = result.scalar_one_or_none()
        if not lead or lead.status == new_status:
            return
        event = FunnelEvent(
            phone=phone,
            from_status=lead.status,
            to_status=new_status,
            triggered_by=triggered_by,
        )
        lead.status = new_status
        lead.updated_at = datetime.utcnow()
        session.add(event)
        await session.commit()
        logger.info(f"Lead {phone} → {new_status}")


async def score_lead(phone: str, historial: list[dict]) -> int:
    """
    Score 0-100 based on conversation signals.
    25 pts each: has company mention, has clear need, wants demo, urgency signals.
    """
    full_text = " ".join(m["content"] for m in historial if m["role"] == "user").lower()
    score = 0
    if any(w in full_text for w in ["empresa", "company", "negocio", "business", "startup"]):
        score += 25
    if any(w in full_text for w in ["necesito", "quiero", "problema", "need", "want", "issue"]):
        score += 25
    if any(w in full_text for w in ["demo", "llamada", "reunión", "call", "meeting", "agendar"]):
        score += 25
    if any(w in full_text for w in ["pronto", "urgente", "esta semana", "soon", "asap", "urgent"]):
        score += 25
    await update_lead_field(phone, score=score)
    return score
