# agent/brain.py — Cerebro del agente: OpenAI gpt-4o-mini
# Generado por AgentKit

import os
import yaml
import logging
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")


def cargar_config_prompts() -> dict:
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        return {}


def cargar_system_prompt() -> str:
    config = cargar_config_prompts()
    return config.get("system_prompt", "Eres un asistente útil. Responde en español.")


def obtener_mensaje_error() -> str:
    config = cargar_config_prompts()
    return config.get("error_message", "Lo siento, estoy teniendo problemas técnicos. Por favor intenta de nuevo en unos minutos.")


def obtener_mensaje_fallback() -> str:
    config = cargar_config_prompts()
    return config.get("fallback_message", "Disculpa, no entendí tu mensaje. ¿Podrías reformularlo?")


async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    if not mensaje or len(mensaje.strip()) < 2:
        return obtener_mensaje_fallback()

    system_prompt = cargar_system_prompt()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    logger.info(f"OPENAI_API_KEY presente: {bool(api_key)} | primeros 10 chars: {api_key[:10] if api_key else 'VACIA'}")

    client = AsyncOpenAI(api_key=api_key)
    mensajes = [{"role": "system", "content": system_prompt}]
    for msg in historial:
        mensajes.append({"role": msg["role"], "content": msg["content"]})
    mensajes.append({"role": "user", "content": mensaje})

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=mensajes,
            max_tokens=1024,
        )
        respuesta = response.choices[0].message.content
        logger.info(f"OpenAI OK — tokens: {response.usage.total_tokens}")
        return respuesta

    except Exception as e:
        logger.error(f"Error OpenAI: {e}")
        return obtener_mensaje_error()
