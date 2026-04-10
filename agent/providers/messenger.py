# agent/providers/messenger.py — Facebook Messenger adapter
import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")

GRAPH_URL = "https://graph.facebook.com/v21.0"


class ProveedorMessenger(ProveedorWhatsApp):
    """Facebook Messenger via Meta Graph API."""

    def __init__(self):
        self.access_token = os.getenv("META_PAGE_ACCESS_TOKEN", "")
        self.verify_token = os.getenv("META_VERIFY_TOKEN", "next2bot-verify")

    async def validar_webhook(self, request: Request):
        params = request.query_params
        if (params.get("hub.mode") == "subscribe" and
                params.get("hub.verify_token") == self.verify_token):
            return int(params.get("hub.challenge", 0))
        return None

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        body = await request.json()
        mensajes = []
        if body.get("object") != "page":
            return mensajes
        for entry in body.get("entry", []):
            for event in entry.get("messaging", []):
                msg = event.get("message", {})
                text = msg.get("text", "")
                sender_id = event.get("sender", {}).get("id", "")
                if text and sender_id:
                    mensajes.append(MensajeEntrante(
                        telefono=f"fb_{sender_id}",
                        texto=text,
                        mensaje_id=msg.get("mid", ""),
                        es_propio=False,
                    ))
        return mensajes

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        sender_id = telefono.replace("fb_", "")
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{GRAPH_URL}/me/messages",
                params={"access_token": self.access_token},
                json={"recipient": {"id": sender_id}, "message": {"text": mensaje}},
            )
            if r.status_code != 200:
                logger.error(f"Messenger error: {r.status_code} {r.text}")
            return r.status_code == 200
