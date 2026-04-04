# agent/main.py — FastAPI app: WhatsApp webhook + internal dashboard
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial
from agent.providers import obtener_proveedor
from agent.leads import upsert_lead, score_lead

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
logging.basicConfig(level=logging.DEBUG if ENVIRONMENT == "development" else logging.INFO)
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await inicializar_db()
    logger.info(f"Next2Bot running on port {PORT} | provider: {proveedor.__class__.__name__}")
    yield


app = FastAPI(title="Next2Bot", version="1.0.0", lifespan=lifespan)

# Mount dashboard
from dashboard.router import router as dashboard_router
app.include_router(dashboard_router)


@app.get("/")
async def health_check():
    return {"status": "ok", "service": "next2bot"}


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


@app.post("/webhook")
@app.post("/webhook/messages")
async def webhook_handler(request: Request):
    try:
        mensajes = await proveedor.parsear_webhook(request)
        for msg in mensajes:
            if msg.es_propio or not msg.texto:
                continue

            logger.info(f"Message from {msg.telefono}: {msg.texto}")

            # Ensure lead exists
            await upsert_lead(msg.telefono)

            historial = await obtener_historial(msg.telefono)
            respuesta = await generar_respuesta(msg.texto, historial, telefono=msg.telefono)

            await guardar_mensaje(msg.telefono, "user", msg.texto)
            await guardar_mensaje(msg.telefono, "assistant", respuesta)

            # Update lead score after each message
            full_history = await obtener_historial(msg.telefono)
            await score_lead(msg.telefono, full_history)

            await proveedor.enviar_mensaje(msg.telefono, respuesta)
            logger.info(f"Reply to {msg.telefono}: {respuesta[:80]}")

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
