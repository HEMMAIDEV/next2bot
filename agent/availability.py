# agent/availability.py — Horacio's weekly availability engine
"""
Manages the owner's weekly availability schedule:
- Stores rules per day-of-week in DB (editable from dashboard)
- Computes free 1-hour slots for any date by subtracting Google Calendar events
- Provides a human-readable summary for the bot to share with leads
"""
import logging
from datetime import datetime, date, time, timedelta
from sqlalchemy import select
from agent.database import async_session
from agent.models import AvailabilityRule

logger = logging.getLogger("agentkit")

DAYS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
DAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Default schedule: Mon-Fri 17:00-21:00, Sat-Sun 13:00-21:00
DEFAULT_RULES = [
    {"day_of_week": 0, "start_time": "17:00", "end_time": "21:00", "is_active": True},
    {"day_of_week": 1, "start_time": "17:00", "end_time": "21:00", "is_active": True},
    {"day_of_week": 2, "start_time": "17:00", "end_time": "21:00", "is_active": True},
    {"day_of_week": 3, "start_time": "17:00", "end_time": "21:00", "is_active": True},
    {"day_of_week": 4, "start_time": "17:00", "end_time": "21:00", "is_active": True},
    {"day_of_week": 5, "start_time": "13:00", "end_time": "21:00", "is_active": True},
    {"day_of_week": 6, "start_time": "13:00", "end_time": "21:00", "is_active": True},
]


# ── SEED ─────────────────────────────────────────────────────────────────────

async def seed_default_availability() -> None:
    """Inserts default rules if the table is empty. Safe to call repeatedly."""
    async with async_session() as session:
        existing = (await session.execute(select(AvailabilityRule))).scalars().first()
        if existing:
            return
        for r in DEFAULT_RULES:
            session.add(AvailabilityRule(
                **r,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            ))
        await session.commit()
        logger.info("Seeded default availability rules")


# ── CRUD ─────────────────────────────────────────────────────────────────────

async def get_rules() -> list:
    """Returns all 7 rules ordered by day_of_week."""
    async with async_session() as session:
        result = await session.execute(
            select(AvailabilityRule).order_by(AvailabilityRule.day_of_week)
        )
        return result.scalars().all()


async def upsert_rule(day_of_week: int, start_time: str, end_time: str, is_active: bool) -> None:
    """Create or update a single day's rule."""
    async with async_session() as session:
        rule = (await session.execute(
            select(AvailabilityRule).where(AvailabilityRule.day_of_week == day_of_week)
        )).scalar_one_or_none()

        if rule:
            rule.start_time = start_time
            rule.end_time   = end_time
            rule.is_active  = is_active
            rule.updated_at = datetime.utcnow()
        else:
            session.add(AvailabilityRule(
                day_of_week=day_of_week, start_time=start_time,
                end_time=end_time, is_active=is_active,
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            ))
        await session.commit()


# ── SLOT COMPUTATION ─────────────────────────────────────────────────────────

def _time_from_str(s: str) -> time:
    h, m = map(int, s.split(":"))
    return time(h, m)


def compute_free_slots(
    rule: AvailabilityRule,
    booked_periods: list[tuple[time, time]],
    slot_minutes: int = 60,
) -> list[str]:
    """
    Given an availability rule and a list of (start, end) booked time tuples,
    returns sorted list of free slot strings in "HH:MM" format.
    """
    if not rule or not rule.is_active:
        return []

    window_start = _time_from_str(rule.start_time)
    window_end   = _time_from_str(rule.end_time)
    free_slots   = []

    current = window_start
    while True:
        # Compute slot end
        slot_end_minutes = current.hour * 60 + current.minute + slot_minutes
        slot_end_h = slot_end_minutes // 60
        slot_end_m = slot_end_minutes % 60
        if slot_end_h > 23:
            break
        slot_end = time(slot_end_h, slot_end_m)
        if slot_end > window_end:
            break

        # Overlap check against booked periods
        is_free = all(
            not (current < b_end and slot_end > b_start)
            for b_start, b_end in booked_periods
        )
        if is_free:
            free_slots.append(current.strftime("%H:%M"))

        # Advance by slot_minutes
        next_minutes = current.hour * 60 + current.minute + slot_minutes
        if next_minutes >= window_end.hour * 60 + window_end.minute:
            break
        current = time(next_minutes // 60, next_minutes % 60)

    return free_slots


async def get_free_slots_for_date(
    target_date: date,
    booked_periods: list[tuple[time, time]],
    slot_minutes: int = 60,
) -> list[str]:
    """Returns free slot strings for a specific date, given booked periods."""
    rules = await get_rules()
    rule  = next((r for r in rules if r.day_of_week == target_date.weekday()), None)
    return compute_free_slots(rule, booked_periods, slot_minutes)


# ── BOT-FACING SUMMARY ────────────────────────────────────────────────────────

async def get_availability_summary_for_bot(
    days_ahead: int = 7,
    slot_minutes: int = 60,
) -> str:
    """
    Returns a human-readable (Spanish) summary of available slots for the
    next N days — injected into the bot's context when checking availability.
    """
    from agent.calendar_tool import get_booked_periods_for_date

    today = date.today()
    rules = await get_rules()
    rule_map = {r.day_of_week: r for r in rules}

    lines = []
    for i in range(days_ahead):
        d = today + timedelta(days=i)
        dow = d.weekday()
        rule = rule_map.get(dow)
        if not rule or not rule.is_active:
            continue

        booked = get_booked_periods_for_date(d)
        free   = compute_free_slots(rule, booked, slot_minutes)
        if not free:
            continue

        day_name = DAYS_ES[dow]
        fecha_str = d.strftime("%d/%m")
        slots_str = ", ".join(free) + " hrs"
        lines.append(f"• {day_name} {fecha_str}: {slots_str}")

    if not lines:
        return "No hay horarios disponibles en los próximos días."

    return "Horarios disponibles de Horacio (Ciudad de México):\n" + "\n".join(lines)
