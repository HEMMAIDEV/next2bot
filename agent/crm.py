# agent/crm.py — Auto contact registration + pattern learning engine
"""
Two responsibilities:
1. CRM: Extract structured contact info from conversations and enrich Lead records.
2. Patterns: When a lead is won/booked, extract what worked and store it so the
   bot learns from success over time.
"""
import os
import logging
from datetime import datetime
from sqlalchemy import select
from agent.database import async_session
from agent.models import Lead, Message, LearnedPattern

logger = logging.getLogger("agentkit")


# ── CONTACT AUTO-REGISTRATION ────────────────────────────────────────────────

async def auto_enrich_lead(phone: str, historial: list[dict]) -> None:
    """
    Uses GPT to extract name, company, and business need from the conversation
    and updates the Lead record. Runs asynchronously after each message.
    Only fires when there are at least 4 user messages (enough context).
    """
    user_msgs = [m for m in historial if m["role"] == "user"]
    if len(user_msgs) < 4:
        return  # Not enough context yet

    # Only re-enrich if the lead is still missing key fields
    async with async_session() as session:
        lead = (await session.execute(
            select(Lead).where(Lead.phone == phone)
        )).scalar_one_or_none()

    if not lead:
        return

    if lead.name and lead.company and lead.business_need:
        return  # Already fully enriched

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())

        conversation = "\n".join(
            f"{'Usuario' if m['role'] == 'user' else 'Bot'}: {m['content']}"
            for m in historial[-12:]
        )

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": (
                    "Analiza esta conversación de ventas de WhatsApp y extrae la siguiente "
                    "información del USUARIO (no del bot). Responde SOLO en JSON válido con "
                    "estos campos (usa null si no está disponible):\n"
                    '{"name": "nombre de la persona", "company": "nombre del negocio o empresa", '
                    '"business_need": "resumen en 1 frase de qué necesitan resolver"}\n\n'
                    f"Conversación:\n{conversation}"
                )
            }],
            max_tokens=150,
            temperature=0,
        )

        import json
        raw = response.choices[0].message.content.strip()
        # Strip markdown code blocks if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())

        update_fields = {}
        if not lead.name and data.get("name"):
            update_fields["name"] = data["name"]
        if not lead.company and data.get("company"):
            update_fields["company"] = data["company"]
        if not lead.business_need and data.get("business_need"):
            update_fields["business_need"] = data["business_need"]

        if update_fields:
            from agent.leads import update_lead_field
            await update_lead_field(phone, **update_fields)
            logger.info(f"CRM enriched lead {phone}: {update_fields}")

    except Exception as e:
        logger.warning(f"CRM enrichment failed for {phone}: {e}")


# ── PATTERN LEARNING ─────────────────────────────────────────────────────────

async def extract_and_store_pattern(phone: str, outcome: str) -> None:
    """
    Called when a lead is marked 'demo_booked' or 'won'.
    Uses GPT to analyze what worked in the conversation and stores it
    as a LearnedPattern that future conversations will benefit from.
    """
    async with async_session() as session:
        messages = (await session.execute(
            select(Message).where(Message.phone == phone).order_by(Message.created_at)
        )).scalars().all()

    if len(messages) < 4:
        return

    conversation = "\n".join(
        f"{'Usuario' if m.role == 'user' else 'Bot'}: {m.content}"
        for m in messages
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": (
                    f"Esta conversación de ventas terminó exitosamente con resultado: {outcome}.\n\n"
                    "Analiza qué hizo el bot que funcionó bien. Responde en JSON:\n"
                    '{"pattern_type": "opener|close|objection_handle|discovery", '
                    '"summary": "en 2-3 frases qué técnica funcionó y por qué", '
                    '"example_exchange": "copia los 2-4 mensajes clave que llevaron al éxito"}\n\n'
                    f"Conversación:\n{conversation[-3000:]}"
                )
            }],
            max_tokens=400,
            temperature=0,
        )

        import json
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())

        async with async_session() as session:
            pattern = LearnedPattern(
                pattern_type=data.get("pattern_type", "close"),
                lead_phone=phone,
                outcome=outcome,
                summary=data.get("summary", ""),
                example_exchange=data.get("example_exchange", ""),
                used_count=0,
                effectiveness=1.0,
                active=True,
                created_at=datetime.utcnow(),
            )
            session.add(pattern)
            await session.commit()
            logger.info(f"Learned pattern stored from {phone} → {outcome}")

    except Exception as e:
        logger.warning(f"Pattern extraction failed for {phone}: {e}")


async def get_active_patterns(limit: int = 3) -> list[dict]:
    """
    Returns the most effective active patterns to inject into the bot's prompt.
    Sorted by effectiveness × used_count (most proven first).
    """
    async with async_session() as session:
        result = await session.execute(
            select(LearnedPattern)
            .where(LearnedPattern.active == True)
            .order_by(
                (LearnedPattern.effectiveness * LearnedPattern.used_count).desc(),
                LearnedPattern.created_at.desc()
            )
            .limit(limit)
        )
        patterns = result.scalars().all()

    return [
        {
            "type": p.pattern_type,
            "outcome": p.outcome,
            "summary": p.summary,
            "example": p.example_exchange,
        }
        for p in patterns
    ]


async def increment_pattern_usage(pattern_ids: list[int]) -> None:
    """Increments used_count for patterns that were included in a prompt."""
    if not pattern_ids:
        return
    async with async_session() as session:
        for pid in pattern_ids:
            p = (await session.execute(
                select(LearnedPattern).where(LearnedPattern.id == pid)
            )).scalar_one_or_none()
            if p:
                p.used_count += 1
        await session.commit()
