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
            "name": "verificar_disponibilidad",
            "description": (
                "Verifica los horarios disponibles de Horacio para esta semana o un día específico. "
                "Úsala cuando el prospecto pregunte cuándo puede hablar, pida una cita, o quiera saber "
                "los horarios disponibles. SIEMPRE úsala antes de agendar_cita para mostrar opciones reales."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha": {
                        "type": "string",
                        "description": (
                            "Fecha específica en formato YYYY-MM-DD. "
                            "Si no se especifica, muestra los próximos 7 días."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agendar_cita",
            "description": (
                "Agenda una cita de 1 hora en el calendario de Horacio. "
                "Úsala cuando el prospecto confirme fecha y hora. "
                "Agenda de inmediato — no bloquees el flujo pidiendo datos adicionales. "
                "Si el nombre, nicho o necesidades ya surgieron en la conversación, inclúyelos; si no, omítelos. "
                "Nunca pidas información que el prospecto no haya ofrecido por su cuenta."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "titulo":          {"type": "string", "description": "Título del evento — incluye el nombre del negocio si lo sabes"},
                    "fecha":           {"type": "string", "description": "Fecha en formato YYYY-MM-DD"},
                    "hora":            {"type": "string", "description": "Hora en formato HH:MM (24h)"},
                    "descripcion":     {"type": "string", "description": "Resumen del caso del prospecto: qué problema quiere resolver"},
                    "nombre_cliente":  {"type": "string", "description": "Nombre de la persona o negocio (ej. 'Clínica Dental Pérez', 'Laura Gómez')"},
                    "nicho":           {"type": "string", "description": "Industria del cliente (ej. Dental, Legal, Belleza, Médico, Restaurante, Retail, etc.)"},
                    "necesidades":     {"type": "string", "description": "Resumen breve de lo que el cliente quiere resolver o mejorar con IA/automatización"},
                },
                "required": ["titulo", "fecha", "hora"],
            },
        },
    },
]


def cargar_config_prompts() -> dict:
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def cargar_system_prompt() -> str:
    from agent.availability import DAYS_ES
    config = cargar_config_prompts()
    now = datetime.now()
    dia_es = DAYS_ES[now.weekday()]
    hoy = f"{now.strftime('%Y-%m-%d')} ({dia_es})"
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

    context += (
        "\n## INSTRUCCIONES PARA AGENDAR CITAS\n"
        "Cuando el prospecto confirme fecha y hora, llama a agendar_cita DE INMEDIATO.\n"
        "NO pidas nombre, nicho ni necesidades si el prospecto no los mencionó — eso es suficiente.\n"
        "Si esa información ya apareció naturalmente en la conversación, pásala; si no, omítela.\n"
        "Prioridad: agendar rápido > recopilar datos perfectos.\n"
    )

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

    # Load learned patterns from successful past conversations
    patterns_context = ""
    try:
        from agent.crm import get_active_patterns
        patterns = await get_active_patterns(limit=3)
        if patterns:
            patterns_context = "\n\n## PATRONES QUE HAN FUNCIONADO (aprende de estos)\n"
            for p in patterns:
                patterns_context += f"\n**Tipo: {p['type']} | Resultado: {p['outcome']}**\n"
                patterns_context += f"Qué funcionó: {p['summary']}\n"
                if p.get("example"):
                    patterns_context += f"Ejemplo real:\n{p['example']}\n"
    except Exception as e:
        logger.warning(f"Could not load learned patterns: {e}")

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    client = AsyncOpenAI(api_key=api_key)

    # Build system prompt with injected funnel context + learned patterns
    system_prompt = cargar_system_prompt()
    system_prompt += _build_funnel_context(stage, historial, score)
    system_prompt += patterns_context

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
            max_tokens=280,
            temperature=0.9,
        )
        latency = int((time.time() - start) * 1000)
        choice = response.choices[0]
        tokens_in = response.usage.prompt_tokens
        tokens_out = response.usage.completion_tokens

        from agent.usage_tracker import log_usage
        await log_usage("openai", "chat", tokens_in, tokens_out, latency, phone=telefono)

        if choice.finish_reason == "tool_calls":
            tool_call   = choice.message.tool_calls[0]
            fn_name     = tool_call.function.name
            args        = json.loads(tool_call.function.arguments)
            logger.info(f"Function call: {fn_name} {args}")

            # ── TOOL: verificar_disponibilidad ────────────────────────────
            if fn_name == "verificar_disponibilidad":
                try:
                    from agent.availability import get_availability_summary_for_bot
                    from datetime import date as _date
                    if args.get("fecha"):
                        # Single date: show slots for that day only
                        target = _date.fromisoformat(args["fecha"])
                        from agent.calendar_tool import get_booked_periods_for_date
                        from agent.availability import get_rules, compute_free_slots
                        rules    = await get_rules()
                        rule_map = {r.day_of_week: r for r in rules}
                        rule     = rule_map.get(target.weekday())
                        booked   = get_booked_periods_for_date(target)
                        slots    = compute_free_slots(rule, booked)
                        from agent.availability import DAYS_ES
                        day_label = DAYS_ES[target.weekday()]
                        if slots:
                            summary = (f"Para el {day_label} {target.strftime('%d/%m/%Y')} [{target.isoformat()}] "
                                       f"los horarios libres son: {', '.join(slots)} hrs.")
                        else:
                            summary = f"El {day_label} {target.strftime('%d/%m/%Y')} [{target.isoformat()}] no hay horarios disponibles."
                    else:
                        summary = await get_availability_summary_for_bot(days_ahead=5)
                    tool_result = (
                        f"{summary}\n\n"
                        f"Instrucción: Presenta estos horarios de forma corta y conversacional — como lo haría una persona real por WhatsApp. "
                        f"NO uses markdown (no asteriscos, no guiones, no negrita). "
                        f"Muestra máximo los primeros 3 días disponibles. "
                        f"Por cada día, menciona solo 3-4 opciones de horario representativas (no las listes todas). "
                        f"Ejemplo de formato ideal: 'El lunes puedo a las 17, 18 o 19hrs. El martes igual. ¿Cuál te queda mejor?' "
                        f"IMPORTANTE: Usa los días y fechas EXACTAMENTE como aparecen arriba — no recalcules ni cambies ninguna fecha. "
                        f"Cada línea tiene el formato 'Día DD/MM/YYYY [YYYY-MM-DD]' — usa ese día y esa fecha tal cual. "
                        f"Cuando el prospecto elija, llama a agendar_cita usando la fecha ISO [YYYY-MM-DD] correspondiente."
                    )
                except Exception as e:
                    logger.error(f"verificar_disponibilidad error: {e}")
                    tool_result = (
                        "No pude consultar el calendario en este momento. "
                        "Instrucción: Dile al prospecto que Horacio le enviará sus horarios disponibles "
                        "directamente por WhatsApp en los próximos minutos."
                    )

            # ── TOOL: agendar_cita ────────────────────────────────────────
            elif fn_name == "agendar_cita":
                from agent.calendar_tool import crear_evento, check_slot_available
                result = crear_evento(
                    titulo=args.get("titulo", "Llamada de Diagnóstico — Next2Human"),
                    fecha=args["fecha"],
                    hora=args["hora"],
                    descripcion=args.get("descripcion", ""),
                    telefono=telefono,
                    nombre_cliente=args.get("nombre_cliente", ""),
                    nicho=args.get("nicho", ""),
                    necesidades=args.get("necesidades", ""),
                )

                link  = result.get("link") or ""
                error = result.get("error")

                if not error:
                    # Post-booking sync confirmation
                    slot_now_taken = not check_slot_available(args["fecha"], args["hora"])
                    sync_status = (
                        "✅ Confirmado en calendario — el horario ya aparece como ocupado."
                        if slot_now_taken else
                        "⚠️ El evento fue creado pero el calendario puede tardar unos segundos en sincronizar."
                    )
                    nombre_str   = args.get("nombre_cliente", "")
                    nicho_str    = args.get("nicho", "")
                    needs_str    = args.get("necesidades", "")
                    client_info  = ""
                    if nombre_str: client_info += f" | Cliente: {nombre_str}"
                    if nicho_str:  client_info += f" | Nicho: {nicho_str}"
                    if needs_str:  client_info += f" | Necesidades: {needs_str}"
                    # Compute correct day name from date — never let the model guess this
                    from datetime import date as _date
                    from agent.availability import DAYS_ES
                    fecha_obj = _date.fromisoformat(args["fecha"])
                    dia_semana = DAYS_ES[fecha_obj.weekday()]
                    fecha_display = f"{dia_semana} {fecha_obj.strftime('%d/%m/%Y')}"
                    link_instruction = (
                        f"IMPORTANTE: Incluye este enlace exacto en tu respuesta de WhatsApp, "
                        f"cópialo tal cual sin modificarlo: {link}"
                        if link else
                        "No hay enlace de calendario disponible en este momento — no menciones ningún enlace."
                    )
                    tool_result = (
                        f"Evento creado exitosamente.\n"
                        f"Título: {args.get('titulo')} | "
                        f"Día: {fecha_display} | Hora: {args['hora']} hrs (15 minutos)"
                        f"{client_info}\n"
                        f"Link del evento: {link}\n"
                        f"Sincronización: {sync_status}\n\n"
                        f"Instrucción: Confirma la cita con una respuesta corta y humana (máx 3 oraciones). "
                        f"Menciona exactamente: '{fecha_display} a las {args['hora']} hrs' — usa estos valores, no los recalcules. "
                        f"Di que son 15 minutos rápidos. "
                        f"{link_instruction} "
                        f"Cierra diciendo que Horacio le va a escribir para confirmar. "
                        f"USA ||| para separar en 2 mensajes si hace sentido naturalmente."
                    )
                else:
                    # Fire-and-forget alert (non-blocking)
                    import asyncio as _aio
                    try:
                        from agent.alerts import create_booking_failed_alert
                        _aio.create_task(create_booking_failed_alert(
                            phone=telefono,
                            fecha=args.get("fecha", ""),
                            hora=args.get("hora", ""),
                            error_detail=str(error),
                        ))
                    except Exception:
                        pass
                    tool_result = (
                        "No se pudo crear el evento automáticamente. "
                        "Instrucción: Discúlpate brevemente y dile que Horacio confirmará la cita "
                        "directamente por WhatsApp en los próximos minutos."
                    )

            else:
                tool_result = f"Herramienta '{fn_name}' no reconocida."

            mensajes.append({
                "role": "assistant",
                "content": choice.message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in choice.message.tool_calls
                ],
            })
            mensajes.append({"role": "tool", "tool_call_id": tool_call.id, "content": tool_result})

            response2 = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=mensajes,
                max_tokens=250,
                temperature=0.9,
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
