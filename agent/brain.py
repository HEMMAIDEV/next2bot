# agent/brain.py — OpenAI gpt-4o-mini with sales funnel context injection
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
            "description": (
                "Agenda una cita, llamada o demo en el calendario de Next2Human. "
                "Úsala SOLO cuando el prospecto haya confirmado explícitamente una fecha y hora. "
                "Antes de llamar esta función, asegúrate de tener el contexto de la reunión "
                "(para qué es, qué quieren resolver) para que Horacio vaya preparado."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "titulo":      {"type": "string", "description": "Título del evento — incluye el nombre del negocio si lo sabes"},
                    "fecha":       {"type": "string", "description": "Fecha en formato YYYY-MM-DD"},
                    "hora":        {"type": "string", "description": "Hora en formato HH:MM (24h)"},
                    "descripcion": {"type": "string", "description": "Resumen del caso del prospecto: qué problema quiere resolver y qué discutir en la llamada"},
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


def _build_funnel_context(stage: dict, historial: list[dict], score: int) -> str:
    """
    Builds a dynamic context block injected into the system prompt.
    This makes the LLM aware of where this lead is in the sales funnel.
    """
    user_msgs = [m for m in historial if m["role"] == "user"]
    msg_count = len(user_msgs)

    urgency_words = ["pronto", "urgente", "esta semana", "hoy", "mañana",
                     "ya", "cuanto antes", "rápido", "asap"]
    full_text = " ".join(m["content"] for m in user_msgs).lower()
    is_urgent = any(w in full_text for w in urgency_words)

    context = f"""

## CONTEXTO DE ESTA CONVERSACIÓN (solo para ti, no lo menciones al usuario)
- Etapa actual del funnel: {stage['name']}
- Mensajes del usuario en esta conversación: {msg_count}
- Puntaje de interés del lead: {score}/100
- Señal de urgencia detectada: {'SÍ — actúa con más decisión en el cierre' if is_urgent else 'No'}
- Tu objetivo AHORA MISMO: {stage['goal']}
- Instrucción específica para esta respuesta: {stage['instruction']}
"""
    # Add escalation hint if score is high
    if score >= 50 and stage.get("name", "").startswith("ETAPA_2"):
        context += "- NOTA: El lead tiene puntaje alto. Si ya mencionaron su problema, considera pasar a ETAPA 3 o 4 en esta respuesta.\n"

    if score >= 70:
        context += "- LEAD CALIENTE: Este prospecto tiene señales claras de interés. Propón la llamada en esta respuesta si no lo has hecho.\n"

    return context


async def generar_respuesta(mensaje: str, historial: list[dict], telefono: str = "") -> str:
    if not mensaje or len(mensaje.strip()) < 2:
        return obtener_mensaje_fallback()

    # Detect funnel stage and score for context injection
    try:
        from agent.leads import detect_funnel_stage, score_lead
        stage = await detect_funnel_stage(telefono, historial)
        score = await score_lead(telefono, historial)
    except Exception as e:
        logger.warning(f"Could not detect funnel stage: {e}")
        stage = {"name": "ETAPA_1 — CONECTAR", "goal": "Conectar con el prospecto", "instruction": "Sé cálido y curioso."}
        score = 0

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    client = AsyncOpenAI(api_key=api_key)

    # Build system prompt with injected funnel context
    system_prompt = cargar_system_prompt()
    system_prompt += _build_funnel_context(stage, historial, score)

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
            temperature=0.85,  # slightly higher for more natural, less robotic responses
        )
        latency = int((time.time() - start) * 1000)
        choice = response.choices[0]
        tokens_in = response.usage.prompt_tokens
        tokens_out = response.usage.completion_tokens

        from agent.usage_tracker import log_usage
        await log_usage("openai", "chat", tokens_in, tokens_out, latency, phone=telefono)

        if choice.finish_reason == "tool_calls":
            tool_call = choice.message.tool_calls[0]
            args = json.loads(tool_call.function.arguments)
            logger.info(f"Function call: agendar_cita {args}")

            from agent.calendar_tool import crear_evento
            link = crear_evento(
                titulo=args.get("titulo", "Llamada de Diagnóstico — Next2Human"),
                fecha=args["fecha"],
                hora=args["hora"],
                descripcion=args.get("descripcion", ""),
                telefono=telefono,
            )

            if not link.startswith("error"):
                tool_result = (
                    f"Evento creado exitosamente.\n"
                    f"Detalles: {args.get('titulo')} el {args['fecha']} a las {args['hora']}.\n"
                    f"Link del evento: {link}\n"
                    f"Instrucción: Confirma la cita con entusiasmo. Menciona la fecha y hora claramente. "
                    f"Dile que Horacio va a llegar preparado con ideas para su caso. "
                    f"Cierra con energía positiva y dile que aquí estás si necesita algo más."
                )
            else:
                tool_result = (
                    "No se pudo crear el evento automáticamente. "
                    "Instrucción: Disculpate brevemente y dile que Horacio le confirmará la cita "
                    "directamente por WhatsApp en los próximos minutos."
                )

            mensajes.append(choice.message)
            mensajes.append({"role": "tool", "tool_call_id": tool_call.id, "content": tool_result})

            response2 = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=mensajes,
                max_tokens=512,
                temperature=0.85,
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
