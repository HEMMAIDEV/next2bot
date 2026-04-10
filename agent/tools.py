# agent/tools.py — Herramientas del agente Next2Bot v2.0
import os
import yaml
import logging

logger = logging.getLogger("agentkit")


def cargar_info_negocio() -> dict:
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/business.yaml no encontrado")
        return {}


def obtener_horario() -> dict:
    info = cargar_info_negocio()
    return {
        "horario": info.get("negocio", {}).get("horario", "24/7"),
        "esta_abierto": True,
    }


def buscar_en_knowledge(consulta: str) -> str:
    resultados = []
    knowledge_dir = "knowledge"
    if not os.path.exists(knowledge_dir):
        return "No hay archivos de conocimiento disponibles."
    for archivo in os.listdir(knowledge_dir):
        ruta = os.path.join(knowledge_dir, archivo)
        if archivo.startswith(".") or not os.path.isfile(ruta):
            continue
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                contenido = f.read()
                if consulta.lower() in contenido.lower():
                    resultados.append(f"[{archivo}]: {contenido[:500]}")
        except (UnicodeDecodeError, IOError):
            continue
    return "\n---\n".join(resultados) if resultados else "No encontré información específica sobre eso."


def registrar_lead(telefono: str, empresa: str, necesidad: str, interes: str) -> str:
    try:
        from datetime import datetime
        leads_file = "config/leads.txt"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entrada = f"[{timestamp}] Tel: {telefono} | Empresa: {empresa} | Necesidad: {necesidad} | Interés: {interes}\n"
        with open(leads_file, "a", encoding="utf-8") as f:
            f.write(entrada)
        logger.info(f"Lead registrado: {telefono}")
        return "Lead registrado exitosamente"
    except Exception as e:
        logger.error(f"Error registrando lead: {e}")
        return "Error al registrar lead"


def calificar_lead(tiene_necesidad: bool, tiene_operacion: bool,
                   quiere_implementar_pronto: bool, acepta_demo: bool) -> str:
    puntaje = sum([tiene_necesidad, tiene_operacion, quiere_implementar_pronto, acepta_demo])
    if puntaje >= 3:
        return "alto"
    elif puntaje == 2:
        return "medio"
    return "bajo"


def generar_propuesta_llamada(nombre_empresa: str = "", problema: str = "", urgencia: bool = False) -> str:
    """
    Generates a persuasive call-to-action to book a call with Horacio.
    Adapts based on context: company name, problem, and urgency level.
    """
    empresa_txt = f" de {nombre_empresa}" if nombre_empresa else ""
    problema_txt = f" específicamente lo de {problema}" if problema else " tu caso"

    base = (
        f"Lo que describes{empresa_txt} es exactamente el tipo de reto que Horacio resuelve bien. "
        f"Lo que haría es apartarte 20 minutos con él esta semana — es una sesión de diagnóstico "
        f"donde revisa {problema_txt} y te dice exactamente qué haría y qué impacto tendría. "
        f"Sin compromiso, sin ventas — solo claridad."
    )

    if urgencia:
        base += " Y si lo quieres resolver pronto, hay que moverse esta semana porque los espacios se llenan."

    base += " ¿Tiene sentido agendarlo?"
    return base


def generar_confirmacion_cita(titulo: str, fecha: str, hora: str, link: str) -> str:
    """
    Generates an exciting post-booking confirmation message.
    Called after a calendar event is successfully created.
    """
    return (
        f"¡Perfecto, quedó agendado! 🎯\n\n"
        f"📅 *{titulo}*\n"
        f"🗓 {fecha} a las {hora}\n"
        f"🔗 {link}\n\n"
        f"Horacio va a revisar tu caso antes de la llamada para que no pierdan tiempo en lo básico "
        f"y puedan ir directo a lo que importa. En 20 minutos vas a tener mucha más claridad "
        f"sobre cómo resolver esto y qué tan rápido se puede hacer.\n\n"
        f"Si necesitas mover la cita o tienes alguna pregunta antes, aquí estoy 🙌"
    )


def generar_mensaje_sin_respuesta(nombre: str = "") -> str:
    """
    Follow-up message for leads that went silent after initial contact.
    """
    nombre_txt = f" {nombre}" if nombre else ""
    return (
        f"Hola{nombre_txt} 👋 Solo quería checar si pudiste ver mi mensaje anterior. "
        f"Entiendo que el día a día se pone intenso. "
        f"Si te sigue llamando la atención, con gusto te cuento cómo otros negocios "
        f"similares al tuyo lo están resolviendo. "
        f"Sin presión — ¿hay un mejor momento para platicar? 😊"
    )
