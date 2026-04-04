# agent/brain.py — OpenAI gpt-4o-mini with function calling + usage tracking
import os
import json
import yaml
import time
import logging
from datetime import datetime
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "agendar_cita",
            "description": "Agenda una cita, llamada o demo en el calendario de Next2Human cuando el prospecto confirma querer reunirse y proporciona fecha y hora.",
            "parameters": {
                "type": "object",
                "properties": {
                    "titulo":      {"type": "string", "description": "Título del evento"},
                    "fecha":       {"type": "string", "description": "Fecha en formato YYYY-MM-DD"},
                    "hora":        {"type": "string", "description": "Hora en formato HH:MM (24h)"},
                    "descripcion": {"type": "string", "description": "Descripción del caso del prospecto"},
                },
                "required": ["titulo", "fecha", "hora"],
            },
        },
    }
]


def cargar_config_prompts() -> dict:
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def cargar_system_prompt() -> str:
    config = cargar_config_prompts()
    hoy = datetime.now().strftime("%Y-%m-%d (%A)")
    base = config.get("system_prompt", "Eres un asistente útil.")
    return f"{base}\n\nFecha de hoy: {hoy}."


def obtener_mensaje_error() -> str:
    return cargar_config_prompts().get("error_message", "Lo siento, estoy teniendo problemas técnicos.")


def obtener_mensaje_fallback() -> str:
    return cargar_config_prompts().get("fallback_message", "Disculpa, no entendí tu mensaje.")


async def generar_respuesta(mensaje: str, historial: list[dict], telefono: str = "") -> str:
    if not mensaje or len(mensaje.strip()) < 2:
        return obtener_mensaje_fallback()

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    client = AsyncOpenAI(api_key=api_key)
    system_prompt = cargar_system_prompt()

    mensajes = [{"role": "system", "content": system_prompt}]
    for msg in historial:
        mensajes.append({"role": msg["role"], "content": msg["content"]})
    mensajes.append({"role": "user", "content": mensaje})

    start = time.time()
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=mensajes,
            tools=OPENAI_TOOLS,
            tool_choice="auto",
            max_tokens=1024,
        )
        latency = int((time.time() - start) * 1000)
        choice = response.choices[0]
        tokens_in = response.usage.prompt_tokens
        tokens_out = response.usage.completion_tokens

        # Log usage
        from agent.usage_tracker import log_usage
        await log_usage("openai", "chat", tokens_in, tokens_out, latency, phone=telefono)

        # Handle function call
        if choice.finish_reason == "tool_calls":
            tool_call = choice.message.tool_calls[0]
            args = json.loads(tool_call.function.arguments)
            logger.info(f"Function call: agendar_cita {args}")

            from agent.calendar_tool import crear_evento
            link = crear_evento(
                titulo=args.get("titulo", "Demo Next2Human"),
                fecha=args["fecha"],
                hora=args["hora"],
                descripcion=args.get("descripcion", ""),
                telefono=telefono,
            )

            tool_result = f"Evento creado exitosamente. Link: {link}" if not link.startswith("error") else "No se pudo crear el evento."

            mensajes.append(choice.message)
            mensajes.append({"role": "tool", "tool_call_id": tool_call.id, "content": tool_result})

            response2 = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=mensajes,
                max_tokens=512,
            )
            await log_usage("openai", "chat_tool_followup",
                            response2.usage.prompt_tokens, response2.usage.completion_tokens,
                            phone=telefono)
            return response2.choices[0].message.content

        return choice.message.content

    except Exception as e:
        latency = int((time.time() - start) * 1000)
        from agent.usage_tracker import log_usage
        await log_usage("openai", "chat", latency_ms=latency, success=False, error=str(e), phone=telefono)
        logger.error(f"Error OpenAI: {e}")
        return obtener_mensaje_error()
