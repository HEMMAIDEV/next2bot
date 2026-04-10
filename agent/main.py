# agent/main.py — FastAPI app: WhatsApp webhook + dashboard + background tasks
import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial
from agent.providers import obtener_proveedor
from agent.providers.messenger import ProveedorMessenger
from agent.providers.instagram import ProveedorInstagram
from agent.leads import upsert_lead, score_lead

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
logging.basicConfig(level=logging.DEBUG if ENVIRONMENT == "development" else logging.INFO)
logger = logging.getLogger("agentkit")

proveedor           = obtener_proveedor()
proveedor_messenger = ProveedorMessenger()
proveedor_instagram = ProveedorInstagram()
PORT = int(os.getenv("PORT", 8000))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB tables (creates new ones, leaves existing intact)
    await inicializar_db()

    # Seed default billing records on first run
    try:
        from agent.cleanup import seed_default_billing
        await seed_default_billing()
    except Exception as e:
        logger.warning(f"Billing seed failed: {e}")

    # Seed default availability rules on first run
    try:
        from agent.cleanup import seed_default_availability_rules
        await seed_default_availability_rules()
    except Exception as e:
        logger.warning(f"Availability seed failed: {e}")

    # Start background maintenance loop (cleanup + payment status refresh)
    try:
        from agent.cleanup import run_cleanup_loop
        cleanup_task = asyncio.create_task(run_cleanup_loop())
    except Exception as e:
        logger.warning(f"Cleanup loop failed to start: {e}")
        cleanup_task = None

    logger.info(f"Next2Bot running on port {PORT} | provider: {proveedor.__class__.__name__}")
    yield

    # Graceful shutdown
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Next2Bot", version="2.0.0", lifespan=lifespan)

# Mount dashboard
from dashboard.router import router as dashboard_router
app.include_router(dashboard_router)


@app.get("/")
async def health_check():
    return {"status": "ok", "service": "next2bot", "version": "2.0.0"}


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

            logger.info(f"Message from {msg.telefono}: {msg.texto[:60]}")

            await upsert_lead(msg.telefono)
            historial = await obtener_historial(msg.telefono)
            respuesta = await generar_respuesta(msg.texto, historial, telefono=msg.telefono)

            await guardar_mensaje(msg.telefono, "user", msg.texto)
            await guardar_mensaje(msg.telefono, "assistant", respuesta)

            full_history = await obtener_historial(msg.telefono)
            await score_lead(msg.telefono, full_history)

            # CRM: auto-enrich lead with name/company/need (runs async, non-blocking)
            asyncio.create_task(_enrich_lead_async(msg.telefono, full_history))

            await proveedor.enviar_mensaje(msg.telefono, respuesta)
            logger.info(f"Reply to {msg.telefono}: {respuesta[:80]}")

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _enrich_lead_async(phone: str, historial: list[dict]) -> None:
    """Non-blocking CRM enrichment — runs in background after each message."""
    try:
        from agent.crm import auto_enrich_lead
        await auto_enrich_lead(phone, historial)
    except Exception as e:
        logger.warning(f"Background CRM enrichment error: {e}")


async def _process_messages(mensajes, proveedor_instance):
    """Shared logic: process messages from any channel."""
    for msg in mensajes:
        if msg.es_propio or not msg.texto:
            continue
        logger.info(f"Message from {msg.telefono}: {msg.texto[:60]}")
        await upsert_lead(msg.telefono)
        historial = await obtener_historial(msg.telefono)
        respuesta = await generar_respuesta(msg.texto, historial, telefono=msg.telefono)
        await guardar_mensaje(msg.telefono, "user", msg.texto)
        await guardar_mensaje(msg.telefono, "assistant", respuesta)
        full_history = await obtener_historial(msg.telefono)
        await score_lead(msg.telefono, full_history)
        asyncio.create_task(_enrich_lead_async(msg.telefono, full_history))
        await proveedor_instance.enviar_mensaje(msg.telefono, respuesta)
        logger.info(f"Reply to {msg.telefono}: {respuesta[:80]}")


@app.get("/webhook/meta")
async def meta_webhook_verify(request: Request):
    result = await proveedor_messenger.validar_webhook(request)
    if result is not None:
        return PlainTextResponse(str(result))
    return {"status": "ok"}


@app.post("/webhook/meta")
async def meta_webhook_handler(request: Request):
    try:
        body = await request.json()
        obj = body.get("object", "")

        class _FakeRequest:
            async def json(self_): return body
            query_params = request.query_params

        fake = _FakeRequest()
        if obj == "page":
            mensajes = await proveedor_messenger.parsear_webhook(fake)
            await _process_messages(mensajes, proveedor_messenger)
        elif obj == "instagram":
            mensajes = await proveedor_instagram.parsear_webhook(fake)
            await _process_messages(mensajes, proveedor_instagram)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Meta webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Pattern learning trigger ──────────────────────────────────────────────────
# Called from dashboard when a lead status changes to won/demo_booked

@app.post("/internal/learn/{phone}")
async def trigger_pattern_learning(phone: str, outcome: str = "demo_booked"):
    """
    Internal endpoint: trigger pattern extraction when a lead is marked won/booked.
    Called by the dashboard's status update flow.
    """
    from agent.crm import extract_and_store_pattern
    asyncio.create_task(extract_and_store_pattern(phone.replace("-", "@"), outcome))
    return {"status": "learning", "phone": phone, "outcome": outcome}
