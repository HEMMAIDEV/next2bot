# agent/calendar_tool.py — Google Calendar integration + BookedMeeting local storage
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
        creds      = service_account.Credentials.from_service_account_info(
            json.loads(creds_json), scopes=SCOPES
        )
        return build("calendar", "v3", credentials=creds)
    except Exception as e:
        logger.error(f"Error autenticando Google Calendar: {e}")
        return None


# ── CREATE EVENT ─────────────────────────────────────────────────────────────

def crear_evento(
    titulo: str,
    fecha: str,
    hora: str,
    descripcion: str = "",
    telefono: str   = "",
    duracion_min: int = 60,
    nombre_cliente: str = "",
    nicho: str          = "",
    necesidades: str    = "",
) -> dict:
    """
    Crea un evento de 60 min en Google Calendar y guarda en BookedMeeting.
    Retorna dict: {"link": str, "event_id": str, "error": str|None}
    """
    service = _get_service()
    if not service:
        return {"link": None, "event_id": None, "error": "error_credenciales"}

    try:
        tz     = ZoneInfo(TIMEZONE)
        inicio = datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        fin    = inicio + timedelta(minutes=duracion_min)

        # Structured description so we can parse it back for calendar display
        description_lines = [
            "Agendado por Next2Bot",
            f"Cliente WhatsApp: {telefono}",
        ]
        if nombre_cliente: description_lines.append(f"Nombre: {nombre_cliente}")
        if nicho:          description_lines.append(f"Nicho: {nicho}")
        if necesidades:    description_lines.append(f"Necesidades: {necesidades}")
        if descripcion:    description_lines.append(f"Contexto: {descripcion}")

        import uuid
        evento = {
            "summary":     titulo,
            "description": "\n".join(description_lines),
            "start": {"dateTime": inicio.isoformat(), "timeZone": TIMEZONE},
            "end":   {"dateTime": fin.isoformat(),    "timeZone": TIMEZONE},
            "conferenceData": {
                "createRequest": {
                    "requestId": f"{telefono}-{fecha}-{hora}-{uuid.uuid4().hex[:8]}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }

        result   = service.events().insert(
            calendarId=CALENDAR_ID, body=evento, conferenceDataVersion=1
        ).execute()
        event_id = result.get("id", "")

        # Extract real Google Meet link from conferenceData
        meet_link = ""
        for ep in result.get("conferenceData", {}).get("entryPoints", []):
            if ep.get("entryPointType") == "video":
                meet_link = ep.get("uri", "")
                break
        link = meet_link or result.get("htmlLink", "")
        logger.info(f"Evento creado: {titulo} el {fecha} a las {hora}")

        # Persist locally for dashboard calendar + reminder engine
        _save_booked_meeting(
            gcal_event_id=event_id,
            gcal_link=link,
            titulo=titulo,
            telefono=telefono,
            nombre_cliente=nombre_cliente,
            nicho=nicho,
            necesidades=necesidades,
            inicio=inicio,
            fin=fin,
        )

        return {"link": link, "event_id": event_id, "error": None}

    except Exception as e:
        logger.error(f"Error creando evento: {e}")
        return {"link": None, "event_id": None, "error": "error_calendario"}


def _save_booked_meeting(
    gcal_event_id, gcal_link, titulo, telefono,
    nombre_cliente, nicho, necesidades, inicio, fin
):
    """Fire-and-forget async save of BookedMeeting to local DB."""
    import asyncio
    from datetime import timezone as _tz

    async def _save():
        try:
            from agent.database import async_session
            from agent.models import BookedMeeting
            # Convert to UTC naive datetime for storage
            inicio_utc = inicio.astimezone(_tz.utc).replace(tzinfo=None)
            fin_utc    = fin.astimezone(_tz.utc).replace(tzinfo=None)
            async with async_session() as session:
                bm = BookedMeeting(
                    gcal_event_id=gcal_event_id or None,
                    gcal_link=gcal_link or None,
                    title=titulo,
                    client_phone=telefono or None,
                    client_name=nombre_cliente or None,
                    client_niche=nicho or None,
                    client_needs=necesidades or None,
                    meeting_at=inicio_utc,
                    ends_at=fin_utc,
                    reminder_sent=False,
                    created_at=datetime.utcnow(),
                )
                session.add(bm)
                await session.commit()
        except Exception as e:
            logger.warning(f"BookedMeeting save warning: {e}")

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_save())
    except Exception:
        pass


# ── READ EVENTS FROM GOOGLE CALENDAR ─────────────────────────────────────────

def get_events_for_date(target_date: date) -> list[dict]:
    """
    Returns all Google Calendar events for a specific date.
    Each dict: {title, start(datetime), end(datetime), description}
    """
    service = _get_service()
    if not service:
        return []
    try:
        tz        = ZoneInfo(TIMEZONE)
        day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=tz)
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
            s = item.get("start", {})
            e = item.get("end",   {})
            if "dateTime" in s:
                start_dt = datetime.fromisoformat(s["dateTime"])
                end_dt   = datetime.fromisoformat(e["dateTime"])
            else:
                start_dt = day_start
                end_dt   = day_end
            events.append({
                "title":       item.get("summary", "Evento"),
                "start":       start_dt,
                "end":         end_dt,
                "description": item.get("description", ""),
                "event_id":    item.get("id", ""),
                "link":        item.get("htmlLink", ""),
            })
        return events
    except Exception as e:
        logger.warning(f"Error reading calendar for {target_date}: {e}")
        return []


def get_booked_periods_for_date(target_date: date) -> list[tuple[time, time]]:
    """Returns (start_time, end_time) tuples of GCal-booked periods for slot subtraction."""
    tz      = ZoneInfo(TIMEZONE)
    periods = []
    for ev in get_events_for_date(target_date):
        start = ev["start"].astimezone(tz).time()
        end   = ev["end"].astimezone(tz).time()
        periods.append((start, end))
    return periods


# ── SLOT AVAILABILITY CHECK ───────────────────────────────────────────────────

def check_slot_available(fecha: str, hora: str, slot_minutes: int = 60) -> bool:
    """Returns True if the given slot is free on Google Calendar."""
    try:
        target_date = date.fromisoformat(fecha)
        slot_start  = time(*map(int, hora.split(":")))
        slot_end_m  = slot_start.hour * 60 + slot_start.minute + slot_minutes
        slot_end    = time(slot_end_m // 60, slot_end_m % 60)
        booked      = get_booked_periods_for_date(target_date)
        return all(
            not (slot_start < b_end and slot_end > b_start)
            for b_start, b_end in booked
        )
    except Exception as e:
        logger.warning(f"check_slot_available error: {e}")
        return False


# ── WEEKLY GRID BUILDER (for dashboard calendar) ─────────────────────────────

GRID_HOURS = list(range(10, 21))   # 10:00 … 20:00 inclusive


def build_week_grid(
    week_start: date,
    rules: list,            # list[AvailabilityRule]
    blocked_times: list,    # list[BlockedTime] for this week
    meetings: list,         # list[BookedMeeting] for this week
    tz_name: str = TIMEZONE,
) -> dict:
    """
    Builds the data structure consumed by the calendar template.

    Returns:
    {
        "hours": [10, 11, ..., 20],
        "days": [
            {
                "date": date, "date_str": "YYYY-MM-DD",
                "label": "Lun 14", "is_today": bool,
                "cells": {
                    10: {"type": "outside|available|meeting|blocked_custom|blocked_schedule"},
                    ...
                }
            }, ...
        ]
    }
    """
    tz         = ZoneInfo(tz_name)
    today      = date.today()
    rule_map   = {r.day_of_week: r for r in rules}
    DAY_LABELS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

    # Compute grid hours dynamically from active rules so the grid grows/shrinks with the schedule
    active_starts = [_parse_time(r.start_time).hour for r in rules if r and r.is_active]
    active_ends   = [_parse_time(r.end_time).hour   for r in rules if r and r.is_active]
    if active_starts and active_ends:
        min_hour   = min(min(active_starts), 10)   # never start later than 10
        max_hour   = max(max(active_ends), 21)     # end is exclusive: 21 → shows up to 20:xx
    else:
        min_hour, max_hour = 10, 21
    grid_hours = list(range(min_hour, max_hour))

    # Index blocked times by date
    bt_map: dict[str, list] = {}
    for bt in blocked_times:
        bt_map.setdefault(bt.blocked_date, []).append(bt)

    # Index meetings by date → list of {hour, data}
    mtg_map: dict[str, list] = {}
    for m in meetings:
        local_dt  = m.meeting_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        d_str     = local_dt.date().isoformat()
        mtg_map.setdefault(d_str, []).append({
            "hour":  local_dt.hour,
            "title": m.title,
            "name":  m.client_name or "",
            "niche": m.client_niche or "",
            "needs": (m.client_needs or "")[:100],
            "link":  m.gcal_link or "",
            "time":  local_dt.strftime("%H:%M"),
            "end":   m.ends_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime("%H:%M"),
            "phone": m.client_phone or "",
            "reminder_sent": m.reminder_sent,
        })

    days = []
    for i in range(7):
        d       = week_start + timedelta(days=i)
        dow     = d.weekday()
        d_str   = d.isoformat()
        rule    = rule_map.get(dow)
        bts     = bt_map.get(d_str, [])
        mtgs    = {m["hour"]: m for m in mtg_map.get(d_str, [])}

        win_start = _parse_time(rule.start_time) if rule and rule.is_active else None
        win_end   = _parse_time(rule.end_time)   if rule and rule.is_active else None

        cells = {}
        for h in grid_hours:
            t = time(h, 0)
            t_end = time(h + 1, 0) if h < 23 else time(23, 59)

            # Priority 1: booked meeting
            if h in mtgs:
                cells[h] = {"type": "meeting", "data": mtgs[h]}
                continue

            # Priority 2: custom blocked time (all-day or overlapping this hour)
            blocked_match = _find_blocking(bts, t, t_end)
            if blocked_match:
                cells[h] = {"type": "blocked_custom", "data": {"title": blocked_match.title}}
                continue

            # Priority 3: outside availability window
            if win_start is None or not (win_start <= t < win_end):
                cells[h] = {"type": "outside", "data": None}
                continue

            # Available
            cells[h] = {"type": "available", "data": None}

        days.append({
            "date":     d,
            "date_str": d_str,
            "label":    f"{DAY_LABELS[dow]} {d.day}",
            "is_today": d == today,
            "rule":     rule,
            "cells":    cells,
        })

    return {"hours": grid_hours, "days": days}


def _parse_time(s: str) -> time:
    h, m = map(int, s.split(":"))
    return time(h, m)


def _find_blocking(blocked_times, t_start: time, t_end: time):
    """Returns the first BlockedTime that covers this hour, or None."""
    for bt in blocked_times:
        if bt.all_day:
            return bt
        if bt.start_time and bt.end_time:
            bt_s = _parse_time(bt.start_time)
            bt_e = _parse_time(bt.end_time)
            if t_start < bt_e and t_end > bt_s:
                return bt
    return None


# ── WEEK SYNC helper (for availability.py) ───────────────────────────────────

def get_free_slots_for_week_sync(
    week_start: date,
    rules: list,
    slot_minutes: int = 60,
) -> dict[str, list[str]]:
    """Returns {date_iso: [free_slot_strings]} for the 7 days starting at week_start."""
    from agent.availability import compute_free_slots
    rule_map = {r.day_of_week: r for r in rules}
    result   = {}
    for i in range(7):
        d     = week_start + timedelta(days=i)
        rule  = rule_map.get(d.weekday())
        if not rule or not rule.is_active:
            result[d.isoformat()] = []
            continue
        booked = get_booked_periods_for_date(d)
        result[d.isoformat()] = compute_free_slots(rule, booked, slot_minutes)
    return result
