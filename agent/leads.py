# agent/leads.py — Lead capture, scoring, funnel stage detection
import json
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

# ── Qualitative lead categories ───────────────────────────────────────────────
#
# Used in the dashboard instead of (or alongside) the raw 0-100 score.
# Each category has a display label, emoji, description, and Tailwind color token.

LEAD_CATEGORIES: dict[str, dict] = {
    "cold": {
        "key":         "cold",
        "label":       "Frío",
        "emoji":       "🧊",
        "description": "Sin señales claras de interés todavía",
        "color_bg":    "bg-slate-800/60",
        "color_text":  "text-slate-300",
        "color_border":"border-slate-700",
        "color_dot":   "bg-slate-500",
    },
    "warming": {
        "key":         "warming",
        "label":       "Observando",
        "emoji":       "👀",
        "description": "Reconoció su negocio pero aún sin problema claro",
        "color_bg":    "bg-blue-900/40",
        "color_text":  "text-blue-300",
        "color_border":"border-blue-800",
        "color_dot":   "bg-blue-400",
    },
    "needs_attention": {
        "key":         "needs_attention",
        "label":       "Necesita Atención",
        "emoji":       "💬",
        "description": "Dolor detectado pero sin intención de cierre aún",
        "color_bg":    "bg-yellow-900/40",
        "color_text":  "text-yellow-300",
        "color_border":"border-yellow-800",
        "color_dot":   "bg-yellow-400",
    },
    "interested": {
        "key":         "interested",
        "label":       "Interesado",
        "emoji":       "🔥",
        "description": "Dolor claro + interés en explorar la solución",
        "color_bg":    "bg-orange-900/40",
        "color_text":  "text-orange-300",
        "color_border":"border-orange-800",
        "color_dot":   "bg-orange-400",
    },
    "hot": {
        "key":         "hot",
        "label":       "Lead Caliente",
        "emoji":       "⚡",
        "description": "Urgencia + intención de agendar — actúa hoy",
        "color_bg":    "bg-emerald-900/40",
        "color_text":  "text-emerald-300",
        "color_border":"border-emerald-700",
        "color_dot":   "bg-emerald-400",
    },
}


def category_for_lead(lead) -> dict:
    """
    Returns the LEAD_CATEGORIES dict for a lead object (or fallback to 'cold').
    Safe to call even if lead_category is None (legacy leads pre-scoring).
    """
    key = getattr(lead, "lead_category", None) or "cold"
    return LEAD_CATEGORIES.get(key, LEAD_CATEGORIES["cold"])


# ── Keyword signal banks ──────────────────────────────────────────────────────

BUSINESS_SIGNALS = [
    "empresa", "negocio", "consultorio", "despacho", "salón", "salon",
    "clínica", "clinica", "startup", "tienda", "agencia", "compañía",
    "soy dueño", "tengo un", "trabajo en", "mi equipo", "mis clientes",
    "atendemos", "vendemos", "ofrecemos", "restaurante", "hotel",
    "farmacia", "gimnasio", "escuela", "colegio", "inmobiliaria",
]

PAIN_SIGNALS = [
    "necesito", "tengo un problema", "no puedo", "no alcanzamos",
    "perdemos", "se nos van", "no contestamos", "tardamos", "manual",
    "repetitivo", "caótico", "desorganizado", "muchos mensajes",
    "muchas consultas", "no tenemos tiempo", "nos llegan muchos",
    "saturado", "saturados", "mejorar", "resolver", "automatizar",
    "agilizar", "eficiencia", "caemos en", "nos falla", "nos cuesta",
    "demasiado", "nos roba tiempo", "estamos perdiendo",
]

DEMO_SIGNALS = [
    "demo", "llamada", "reunión", "reunion", "agendar", "cita", "platicar",
    "hablar", "ver cómo", "me interesa", "quiero saber más", "más información",
    "cuánto cuesta", "cuanto cuesta", "precio", "me convence", "prueba",
    "me gustaría", "me gustaria", "sí quiero", "si quiero", "quisiera",
    "me llama la atención", "cuéntame más", "cuéntame", "dime más",
    "qué incluye", "cómo funciona", "podemos hablar",
]

URGENCY_SIGNALS = [
    "pronto", "urgente", "esta semana", "hoy", "mañana", "ya",
    "cuanto antes", "lo antes posible", "rápido", "rapido", "inmediato",
    "asap", "necesito ya", "para ayer", "lo necesito urgente",
    "no puede esperar", "en los próximos días",
]

OBJECTION_SIGNALS = [
    "no me interesa", "no gracias", "no por ahora", "tal vez después",
    "lo pienso", "déjame pensar", "deja lo pienso", "no tengo presupuesto",
    "ya tenemos algo", "estamos bien así", "no es para nosotros",
    "quizás más adelante", "no aplica", "no es el momento",
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


# ── Scoring engine ─────────────────────────────────────────────────────────────

def _find_matches(text: str, signals: list[str]) -> list[str]:
    """Return signal phrases that appear verbatim in the text."""
    return [w for w in signals if w in text]


def _classify(
    has_business: bool,
    has_pain: bool,
    has_demo: bool,
    has_urgency: bool,
    has_objection: bool,
) -> str:
    """
    Map signal combination to a qualitative category key.

    Priority order (highest first):
      hot              — urgency + demo interest
      interested       — pain + demo interest
      needs_attention  — pain present but no conversion intent,
                         OR business + objection raised
      warming          — business mentioned, no pain
      cold             — no signals at all
    """
    if has_urgency and has_demo:
        return "hot"
    if has_pain and has_demo:
        return "interested"
    if has_pain or (has_business and has_objection):
        return "needs_attention"
    if has_business:
        return "warming"
    return "cold"


def _build_signal_reasons(
    biz: list, pain: list, demo: list, urg: list, obj: list
) -> list[str]:
    """
    Produce a human-readable list of detected signals for the dashboard.
    Shows the first 2-3 triggering words so Horacio can see what was said.
    """
    reasons = []
    if biz:
        reasons.append(f"Mencionó su negocio — «{' / '.join(biz[:3])}»")
    if pain:
        reasons.append(f"Expresó un problema — «{' / '.join(pain[:3])}»")
    if demo:
        reasons.append(f"Interés en hablar o explorar — «{' / '.join(demo[:3])}»")
    if urg:
        reasons.append(f"Urgencia detectada — «{' / '.join(urg[:2])}»")
    if obj:
        reasons.append(f"Objeciones levantadas — «{' / '.join(obj[:2])}»")
    if not reasons:
        reasons.append("Sin señales conversacionales detectadas aún")
    return reasons


async def score_lead(phone: str, historial: list[dict]) -> int:
    """
    Analyses the full user-side conversation and:
      1. Computes a 0-100 numeric score (for backward-compat with brain.py).
      2. Maps that to a qualitative category (cold / warming / needs_attention /
         interested / hot) based on the actual signal combination — not just the
         number.
      3. Builds a human-readable list of detected signals (stored as JSON) so
         the dashboard can show WHY a lead received its category.

    Stores score, lead_category, and lead_signals back to the DB.
    Returns the numeric score (int).
    """
    full_text = " ".join(
        m["content"] for m in historial if m["role"] == "user"
    ).lower()

    # Detect which specific phrases triggered each signal group
    biz_matched  = _find_matches(full_text, BUSINESS_SIGNALS)
    pain_matched = _find_matches(full_text, PAIN_SIGNALS)
    demo_matched = _find_matches(full_text, DEMO_SIGNALS)
    urg_matched  = _find_matches(full_text, URGENCY_SIGNALS)
    obj_matched  = _find_matches(full_text, OBJECTION_SIGNALS)

    has_business  = bool(biz_matched)
    has_pain      = bool(pain_matched)
    has_demo      = bool(demo_matched)
    has_urgency   = bool(urg_matched)
    has_objection = bool(obj_matched)

    # Numeric score (kept for brain.py threshold checks and history)
    score = 0
    if has_business:  score += 20
    if has_pain:      score += 25
    if has_demo:      score += 30
    if has_urgency:   score += 15
    if has_objection: score -= 20
    score = max(0, min(score, 100))

    # Qualitative category (signal-logic, not just a number bracket)
    category = _classify(has_business, has_pain, has_demo, has_urgency, has_objection)

    # Human-readable signal evidence
    reasons = _build_signal_reasons(biz_matched, pain_matched, demo_matched,
                                     urg_matched, obj_matched)
    signals_json = json.dumps(reasons, ensure_ascii=False)

    await update_lead_field(
        phone,
        score=score,
        lead_category=category,
        lead_signals=signals_json,
    )
    return score


# ── Funnel Stage Detection ────────────────────────────────────────────────────

async def detect_funnel_stage(phone: str, historial: list[dict]) -> dict:
    """
    Detects which sales funnel stage this conversation is in.
    Returns the stage dict with name, goal, and instruction for the LLM.
    """
    if not historial:
        return STAGES[1]

    full_text = " ".join(
        m["content"] for m in historial if m["role"] == "user"
    ).lower()
    user_messages = [m for m in historial if m["role"] == "user"]
    msg_count = len(user_messages)

    # Explicit booking intent → Stage 5
    booking_words = ["agendar", "agéndame", "agendame", "quiero la cita",
                     "pon la cita", "reserva", "apartar", "confirmar"]
    if any(w in full_text for w in booking_words):
        return STAGES[5]

    has_pain         = bool(_find_matches(full_text, PAIN_SIGNALS))
    has_demo_interest= bool(_find_matches(full_text, DEMO_SIGNALS))
    has_urgency      = bool(_find_matches(full_text, URGENCY_SIGNALS))
    has_business     = bool(_find_matches(full_text, BUSINESS_SIGNALS))

    # Demo interest + problem clear → Stage 4
    if has_pain and (has_demo_interest or has_urgency):
        return STAGES[4]

    # Business identified + pain surfacing → Stage 3
    if has_business and has_pain:
        return STAGES[3]

    # Business mentioned but no pain yet → Stage 2
    if has_business or msg_count >= 2:
        return STAGES[2]

    return STAGES[1]
