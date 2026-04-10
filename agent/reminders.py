# agent/reminders.py — WhatsApp meeting reminder engine
"""
Runs every 5 minutes in the background.
Finds booked meetings starting within the next 60 minutes (±15 min window)
and sends a WhatsApp confirmation message to the client via Whapi.
Marks reminder_sent=True so it never fires twice for the same meeting.
"""
import logging
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select
from agent.database import async_session
from agent.models import BookedMeeting

logger     = logging.getLogger("agentkit")
TIMEZONE   = ZoneInfo("America/Mexico_City")
CHECK_INTERVAL_SECONDS = 5 * 60   # every 5 minutes
REMIND_WINDOW_MIN_MIN  = 45       # fire if meeting starts in ≥45 min
REMIND_WINDOW_MAX_MIN  = 75       # fire if meeting starts in ≤75 min


# ── REMINDER MESSAGE ─────────────────────────────────────────────────────────

def _build_reminder_message(meeting: BookedMeeting) -> str:
    local_dt   = meeting.meeting_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(TIMEZONE)
    time_str   = local_dt.strftime("%H:%M")
    date_str   = local_dt.strftime("%A %d de %B").capitalize()
    name_part  = f" {meeting.client_name}" if meeting.client_name else ""
    link_part  = f"\n🔗 {meeting.gcal_link}" if meeting.gcal_link else ""
    needs_part = (
        f"\n\nTema: _{meeting.client_needs[:120]}_"
        if meeting.client_needs else ""
    )
    return (
        f"¡Hola{name_part}! 👋\n\n"
        f"Te recuerdo que en *1 hora* tienes tu sesión con *Horacio* de Next2Human.\n\n"
        f"📅 {meeting.title}\n"
        f"🗓 {date_str}\n"
        f"⏰ {time_str} hrs (Ciudad de México)"
        f"{link_part}"
        f"{needs_part}\n\n"
        f"Si necesitas mover la cita, responde este mensaje y te ayudamos. "
        f"¡Nos vemos pronto! 🚀"
    )


# ── SEND REMINDER ────────────────────────────────────────────────────────────

async def _send_whatsapp_reminder(phone: str, message: str) -> bool:
    """Sends a WhatsApp message via Whapi to the given phone number."""
    try:
        import os
        import httpx
        token = os.getenv("WHAPI_TOKEN", "")
        if not token:
            logger.warning("WHAPI_TOKEN not set — reminder not sent")
            return False

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "https://gate.whapi.cloud/messages/text",
                json={"to": phone, "body": message},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
        if r.status_code == 200:
            logger.info(f"Reminder sent to {phone}")
            return True
        else:
            logger.warning(f"Whapi reminder failed: {r.status_code} {r.text[:80]}")
            return False
    except Exception as e:
        logger.error(f"Reminder send error for {phone}: {e}")
        return False


# ── MAIN CHECK ────────────────────────────────────────────────────────────────

async def check_and_send_reminders() -> int:
    """
    Finds meetings in the reminder window and sends WhatsApp messages.
    Returns the count of reminders sent this cycle.
    """
    now     = datetime.utcnow()
    win_min = now + timedelta(minutes=REMIND_WINDOW_MIN_MIN)
    win_max = now + timedelta(minutes=REMIND_WINDOW_MAX_MIN)
    sent    = 0

    async with async_session() as session:
        result = await session.execute(
            select(BookedMeeting).where(
                BookedMeeting.reminder_sent == False,
                BookedMeeting.meeting_at >= win_min,
                BookedMeeting.meeting_at <= win_max,
                BookedMeeting.client_phone != None,
            )
        )
        meetings = result.scalars().all()

        for meeting in meetings:
            msg     = _build_reminder_message(meeting)
            success = await _send_whatsapp_reminder(meeting.client_phone, msg)
            if success:
                meeting.reminder_sent    = True
                meeting.reminder_sent_at = now
                sent += 1

        if sent:
            await session.commit()
            logger.info(f"Reminder engine: {sent} reminder(s) sent")

    return sent


# ── BACKGROUND LOOP ───────────────────────────────────────────────────────────

async def run_reminder_loop() -> None:
    """
    Infinite async loop started from main.py lifespan.
    Checks for upcoming meetings every CHECK_INTERVAL_SECONDS.
    """
    logger.info("Reminder loop started")
    await asyncio.sleep(30)  # brief startup delay

    while True:
        try:
            await check_and_send_reminders()
        except Exception as e:
            logger.error(f"Reminder loop error: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
