# agent/calendar_tool.py — Integración con Google Calendar
import os
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from google.oauth2 import service_account
from googleapiclient.discovery import build

logger = logging.getLogger("agentkit")

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TIMEZONE = "America/Mexico_City"
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "")


def _get_service():
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        logger.error("GOOGLE_CREDENTIALS_JSON no configurado")
        return None
    try:
        creds_dict = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=SCOPES
        )
        return build("calendar", "v3", credentials=credentials)
    except Exception as e:
        logger.error(f"Error autenticando Google Calendar: {e}")
        return None


def crear_evento(titulo: str, fecha: str, hora: str, descripcion: str = "", telefono: str = "", duracion_min: int = 60) -> str:
    """
    Crea un evento en Google Calendar.
    fecha: YYYY-MM-DD
    hora: HH:MM (24h)
    Retorna el link del evento o mensaje de error.
    """
    service = _get_service()
    if not service:
        return "error_credenciales"

    try:
        inicio = datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(TIMEZONE))
        fin = inicio + timedelta(minutes=duracion_min)

        evento = {
            "summary": titulo,
            "description": f"Agendado por Next2Bot\nCliente WhatsApp: {telefono}\n{descripcion}",
            "start": {"dateTime": inicio.isoformat(), "timeZone": TIMEZONE},
            "end": {"dateTime": fin.isoformat(), "timeZone": TIMEZONE},
        }

        resultado = service.events().insert(calendarId=CALENDAR_ID, body=evento).execute()
        link = resultado.get("htmlLink", "")
        logger.info(f"Evento creado: {titulo} el {fecha} a las {hora}")
        return link

    except Exception as e:
        logger.error(f"Error creando evento: {e}")
        return "error_calendario"
