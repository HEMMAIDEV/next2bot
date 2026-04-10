# agent/calendar_tool.py — Google Calendar integration
import os
import json
import logging
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger("agentkit")

SCOPES      = ["https://www.googleapis.com/auth/calendar"]
TIMEZONE    = "America/Mexico_City"
CALENDAR_ID = (
    os.getenv("GOOGLE_CALENDAR_ID", "")
    .replace("%40", "@").replace("%0A", "").replace("\n", "").replace(" ", "").strip()
)

logger.info(f"CALENDAR_ID cargado: '{CALENDAR_ID}'")


# ── AUTH ─────────────────────────────────────────────────────────────────────

def _get_service():
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        logger.error("GOOGLE_CREDENTIALS_JSON no configurado")
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds_dict  = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=SCOPES
        )
        return build("calendar", "v3", credentials=credentials)
    except Exception as e:
        logger.error(f"Error autenticando Google Calendar: {e}")
        return None


# ── CREATE EVENT ─────────────────────────────────────────────────────────────

def crear_evento(
    titulo: str,
    fecha: str,
    hora: str,
    descripcion: str = "",
    telefono: str = "",
    duracion_min: int = 60,
) -> str:
    """
    Crea un evento de 60 min en Google Calendar.
    fecha: YYYY-MM-DD  |  hora: HH:MM (24h, hora Ciudad de México)
    Retorna el link HTML del evento, o "error_*" si falla.
    """
    service = _get_service()
    if not service:
        return "error_credenciales"
    try:
        inicio = datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M").replace(
            tzinfo=ZoneInfo(TIMEZONE)
        )
        fin = inicio + timedelta(minutes=duracion_min)

        evento = {
            "summary": titulo,
            "description": (
                f"Agendado por Next2Bot\n"
                f"Cliente WhatsApp: {telefono}\n"
                f"{descripcion}"
            ),
            "start": {"dateTime": inicio.isoformat(), "timeZone": TIMEZONE},
            "end":   {"dateTime": fin.isoformat(),   "timeZone": TIMEZONE},
        }

        resultado = service.events().insert(calendarId=CALENDAR_ID, body=evento).execute()
        link = resultado.get("htmlLink", "")
        logger.info(f"Evento creado: {titulo} el {fecha} a las {hora}")
        return link

    except Exception as e:
        logger.error(f"Error creando evento: {e}")
        return "error_calendario"


# ── READ EVENTS ──────────────────────────────────────────────────────────────

def get_events_for_date(target_date: date) -> list[dict]:
    """
    Returns all Google Calendar events for a specific date.
    Each dict has: title, start (datetime), end (datetime).
    Returns [] on error (fail-safe).
    """
    service = _get_service()
    if not service:
        return []
    try:
        tz       = ZoneInfo(TIMEZONE)
        day_start = datetime(target_date.year, target_date.month, target_date.day,
                             0, 0, 0, tzinfo=tz)
        day_end   = day_start + timedelta(days=1)

        result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=day_start.isoformat(),
            timeMax=day_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = []
        for item in result.get("items", []):
            start_raw = item.get("start", {})
            end_raw   = item.get("end", {})

            # Handle all-day events (date only, no dateTime)
            if "dateTime" in start_raw:
                start_dt = datetime.fromisoformat(start_raw["dateTime"])
                end_dt   = datetime.fromisoformat(end_raw["dateTime"])
            else:
                # All-day event — blocks the whole day
                start_dt = day_start
                end_dt   = day_end

            events.append({
                "title": item.get("summary", "Evento"),
                "start": start_dt,
                "end":   end_dt,
            })
        return events

    except Exception as e:
        logger.warning(f"Error reading calendar for {target_date}: {e}")
        return []


def get_booked_periods_for_date(target_date: date) -> list[tuple[time, time]]:
    """
    Returns (start_time, end_time) tuples of booked periods for a date.
    Used by availability.py to subtract from free windows.
    """
    events  = get_events_for_date(target_date)
    tz      = ZoneInfo(TIMEZONE)
    periods = []
    for ev in events:
        start = ev["start"].astimezone(tz).time()
        end   = ev["end"].astimezone(tz).time()
        periods.append((start, end))
    return periods


def get_events_for_week(week_start: date) -> dict[str, list[dict]]:
    """
    Returns events keyed by 'YYYY-MM-DD' for the 7 days starting at week_start.
    """
    result = {}
    for i in range(7):
        d = week_start + timedelta(days=i)
        result[d.isoformat()] = get_events_for_date(d)
    return result


# ── SLOT AVAILABILITY CHECK ───────────────────────────────────────────────────

def check_slot_available(fecha: str, hora: str, slot_minutes: int = 60) -> bool:
    """
    Returns True if the given 1-hour slot is free on Google Calendar
    AND falls within Horacio's availability window.
    Used to confirm after booking.
    """
    try:
        target_date = date.fromisoformat(fecha)
        slot_start  = time(*map(int, hora.split(":")))
        slot_end_m  = slot_start.hour * 60 + slot_start.minute + slot_minutes
        slot_end    = time(slot_end_m // 60, slot_end_m % 60)

        booked = get_booked_periods_for_date(target_date)
        for b_start, b_end in booked:
            if slot_start < b_end and slot_end > b_start:
                return False   # Overlaps a booked period → not available
        return True

    except Exception as e:
        logger.warning(f"check_slot_available error: {e}")
        return False


# ── FREE SLOTS SUMMARY (for bot / dashboard) ─────────────────────────────────

def get_free_slots_for_week_sync(
    week_start: date,
    rules: list,           # list of AvailabilityRule objects
    slot_minutes: int = 60,
) -> dict[str, list[str]]:
    """
    Synchronous helper — returns dict of {date_str: [free_slot_strings]}.
    Called from dashboard routes (which can't easily await inside sync templates).
    """
    from agent.availability import compute_free_slots

    result   = {}
    rule_map = {r.day_of_week: r for r in rules}

    for i in range(7):
        d    = week_start + timedelta(days=i)
        dow  = d.weekday()
        rule = rule_map.get(dow)
        if not rule or not rule.is_active:
            result[d.isoformat()] = []
            continue
        booked = get_booked_periods_for_date(d)
        result[d.isoformat()] = compute_free_slots(rule, booked, slot_minutes)

    return result
