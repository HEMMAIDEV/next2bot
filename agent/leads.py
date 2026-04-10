# agent/leads.py — Lead capture, scoring, funnel stage detection
import logging
from datetime import datetime
from sqlalchemy import select
from agent.database import async_session
from agent.models import Lead, FunnelEvent

logger = logging.getLogger("agentkit")

STATUSES = ["new", "qualified", "follow_up", "demo_booked", "won", "lost"]

# ── Funnel stage definitions ──────────────────────────────────────────────────

STAGES = {
    1: {
        "name": "ETAPA_1 — CONECTAR",
        "goal": "Rompe el hielo con calidez. Haz UNA pregunta sobre su negocio. No menciones servicios todavía.",
        "instruction": "Sé muy cálido y breve. Tu único objetivo es que te cuenten a qué se dedican.",
    },
    2: {
        "name": "ETAPA_2 — DESCUBRIR",
        "goal": "Entiende su problema real. Que lo verbalicen ellos, no tú.",
        "instruction": "Haz preguntas de diagnóstico sobre su operación. Refleja lo que dicen. UNA pregunta por mensaje.",
    },
    3: {
        "name": "ETAPA_3 — AMPLIFICAR",
        "goal": "Haz que sientan el costo real de su problema. Introduce social proof con naturalidad.",
        "instruction": "Conecta su problema con consecuencias reales. Usa future pacing ('imagina que...'). Introduce social proof breve.",
    },
    4: {
        "name": "ETAPA_4 — CONVERTIR",
        "goal": "Proponer la llamada con Horacio como el paso natural y valioso.",
        "instruction": "Propón 20 minutos de diagnóstico con Horacio. Menciona disponibilidad limitada esta semana. Cierra con '¿tiene sentido?'",
    },
    5: {
        "name": "ETAPA_5 — AGENDAR",
        "goal": "Completar el agendado y hacer que se sientan emocionados por la llamada.",
        "instruction": "Pide fecha y hora. Cuando confirmes, usa energía y diles qué esperar. Finaliza con un mensaje cálido de cierre.",
    },
}

# ── Keyword signal banks ──────────────────────────────────────────────────────

BUSINESS_SIGNALS = [
    "empresa", "negocio", "consultorio", "despacho", "salón", "salon",
    "clínica", "clinica", "startup", "tienda", "agencia", "compañía",
    "soy dueño", "tengo un", "trabajo en", "mi equipo", "mis clientes",
    "atendemos", "vendemos", "ofrecemos",
]

PAIN_SIGNALS = [
    "necesito", "quiero", "problema", "problema", "tengo un problema",
    "no puedo", "no alcanzamos", "perdemos", "se nos van", "no contestamos",
    "tardamos", "manual", "repetitivo", "caótico", "desorganizado",
    "muchos mensajes", "muchas consultas", "no tenemos tiempo",
    "nos llegan muchos", "saturado", "saturados", "eficiencia",
    "mejorar", "resolver", "automatizar", "agilizar",
]

DEMO_SIGNALS = [
    "demo", "llamada", "reunión", "reunion", "agendar", "cita", "platicar",
    "hablar", "ver cómo", "me interesa", "quiero saber más", "más información",
    "cuánto cuesta", "cuanto cuesta", "precio", "me convence", "prueba",
    "me gustaría", "me gustaria", "sí quiero", "si quiero",
]

URGENCY_SIGNALS = [
    "pronto", "urgente", "esta semana", "hoy", "mañana", "ya",
    "cuanto antes", "lo antes posible", "rápido", "rapido", "inmediato",
    "asap", "necesito ya", "para ayer",
]

OBJECTION_SIGNALS = [
    "no me interesa", "no gracias", "no por ahora", "tal vez después",
    "lo pienso", "déjame pensar", "deja lo pienso", "no tengo presupuesto",
    "ya tenemos algo", "estamos bien así", "no es para nosotros",
]

# ── CRUD ──────────────────────────────────────────────────────────────────────

async def upsert_lead(phone: str) -> Lead:
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.phone == phone))
        lead = result.scalar_one_or_none()
        if lead is None:
            lead = Lead(phone=phone, last_seen_at=datetime.utcnow())
            session.add(lead)
            logger.info(f"New lead: {phone}")
        else:
            lead.last_seen_at = datetime.utcnow()
        await session.commit()
        await session.refresh(lead)
        return lead


async def update_lead_field(phone: str, **fields):
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.phone == phone))
        lead = result.scalar_one_or_none()
        if lead:
            for key, value in fields.items():
                setattr(lead, key, value)
            lead.updated_at = datetime.utcnow()
            await session.commit()


async def update_lead_status(phone: str, new_status: str, triggered_by: str = "agent"):
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

# ── Scoring ───────────────────────────────────────────────────────────────────

async def score_lead(phone: str, historial: list[dict]) -> int:
    """
    Score 0-100 based on conversation signals.
    - Business mention: 20 pts
    - Pain/problem signal: 25 pts
    - Demo/call interest: 30 pts
    - Urgency signal: 15 pts
    - Objection detected: -20 pts (penalize, not disqualify)
    """
    full_text = " ".join(m["content"] for m in historial if m["role"] == "user").lower()
    score = 0

    if any(w in full_text for w in BUSINESS_SIGNALS):
        score += 20
    if any(w in full_text for w in PAIN_SIGNALS):
        score += 25
    if any(w in full_text for w in DEMO_SIGNALS):
        score += 30
    if any(w in full_text for w in URGENCY_SIGNALS):
        score += 15
    if any(w in full_text for w in OBJECTION_SIGNALS):
        score -= 20

    score = max(0, min(score, 100))
    await update_lead_field(phone, score=score)
    return score

# ── Funnel Stage Detection ─────────────────────────────────────────────────────

async def detect_funnel_stage(phone: str, historial: list[dict]) -> dict:
    """
    Detects which sales funnel stage this conversation is in.
    Returns the stage dict with name, goal, and instruction for the LLM.
    """
    if not historial:
        return STAGES[1]

    full_text = " ".join(m["content"] for m in historial if m["role"] == "user").lower()
    user_messages = [m for m in historial if m["role"] == "user"]
    msg_count = len(user_messages)

    # Explicit booking intent → Stage 5
    booking_words = ["agendar", "agéndame", "agendame", "quiero la cita",
                     "pon la cita", "reserva", "apartar", "confirmar"]
    if any(w in full_text for w in booking_words):
        return STAGES[5]

    # Demo interest + problem clear → Stage 4
    has_pain = any(w in full_text for w in PAIN_SIGNALS)
    has_demo_interest = any(w in full_text for w in DEMO_SIGNALS)
    has_urgency = any(w in full_text for w in URGENCY_SIGNALS)
    has_business = any(w in full_text for w in BUSINESS_SIGNALS)

    if has_pain and (has_demo_interest or has_urgency):
        return STAGES[4]

    # Business identified + pain surfacing → Stage 3
    if has_business and has_pain:
        return STAGES[3]

    # Business mentioned but no pain yet → Stage 2
    if has_business or msg_count >= 2:
        return STAGES[2]

    # Default: first contact
    return STAGES[1]
