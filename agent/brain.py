# agent/brain.py — Cerebro del agente: conexión con IA (OpenAI o Anthropic)
# Generado por AgentKit

"""
Lógica de IA del agente. Lee el system prompt de prompts.yaml
y genera respuestas usando OpenAI o Anthropic según AI_PROVIDER en .env.
"""

import os
import yaml
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

AI_PROVIDER = os.getenv("AI_PROVIDER", "anthropic").lower()


def cargar_config_prompts() -> dict:
    """Lee toda la configuración desde config/prompts.yaml."""
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        return {}


def cargar_system_prompt() -> str:
    """Lee el system prompt desde config/prompts.yaml."""
    config = cargar_config_prompts()
    return config.get("system_prompt", "Eres un asistente útil. Responde en español.")


def obtener_mensaje_error() -> str:
    config = cargar_config_prompts()
    return config.get("error_message", "Lo siento, estoy teniendo problemas técnicos. Por favor intenta de nuevo en unos minutos.")


def obtener_mensaje_fallback() -> str:
    config = cargar_config_prompts()
    return config.get("fallback_message", "Disculpa, no entendí tu mensaje. ¿Podrías reformularlo?")


async def _generar_con_openai(mensaje: str, historial: list[dict], system_prompt: str) -> str:
    """Genera respuesta usando OpenAI (gpt-4o-mini)."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    mensajes = [{"role": "system", "content": system_prompt}]
    for msg in historial:
        mensajes.append({"role": msg["role"], "content": msg["content"]})
    mensajes.append({"role": "user", "content": mensaje})

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=mensajes,
        max_tokens=1024,
    )
    respuesta = response.choices[0].message.content
    logger.info(f"OpenAI — tokens usados: {response.usage.total_tokens}")
    return respuesta


async def _generar_con_anthropic(mensaje: str, historial: list[dict], system_prompt: str) -> str:
    """Genera respuesta usando Anthropic Claude."""
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    mensajes = []
    for msg in historial:
        mensajes.append({"role": msg["role"], "content": msg["content"]})
    mensajes.append({"role": "user", "content": mensaje})

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system_prompt,
        messages=mensajes,
    )
    respuesta = response.content[0].text
    logger.info(f"Anthropic — tokens: {response.usage.input_tokens} in / {response.usage.output_tokens} out")
    return respuesta


async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    """
    Genera una respuesta usando el proveedor de IA configurado en .env (AI_PROVIDER).

    Args:
        mensaje: El mensaje nuevo del usuario
        historial: Lista de mensajes anteriores [{"role": "user/assistant", "content": "..."}]

    Returns:
        La respuesta generada por la IA
    """
    if not mensaje or len(mensaje.strip()) < 2:
        return obtener_mensaje_fallback()

    system_prompt = cargar_system_prompt()

    try:
        if AI_PROVIDER == "openai":
            return await _generar_con_openai(mensaje, historial, system_prompt)
        else:
            return await _generar_con_anthropic(mensaje, historial, system_prompt)

    except Exception as e:
        logger.error(f"Error {AI_PROVIDER} API: {e}")
        return obtener_mensaje_error()
