# agent/tools.py — Herramientas del agente Next2Bot
# Generado por AgentKit

"""
Herramientas específicas de Next2Human.
Principalmente enfocadas en calificación de leads, FAQ y agendado de demos.
"""

import os
import yaml
import logging

logger = logging.getLogger("agentkit")


def cargar_info_negocio() -> dict:
    """Carga la información del negocio desde business.yaml."""
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("config/business.yaml no encontrado")
        return {}


def obtener_horario() -> dict:
    """Retorna el horario de atención del negocio."""
    info = cargar_info_negocio()
    return {
        "horario": info.get("negocio", {}).get("horario", "24/7"),
        "esta_abierto": True,  # Next2Bot siempre está activo
    }


def buscar_en_knowledge(consulta: str) -> str:
    """
    Busca información relevante en los archivos de /knowledge.
    Retorna el contenido más relevante encontrado.
    """
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

    if resultados:
        return "\n---\n".join(resultados)
    return "No encontré información específica sobre eso en mis archivos."


def registrar_lead(telefono: str, empresa: str, necesidad: str, interes: str) -> str:
    """
    Registra un lead calificado para seguimiento del equipo de Next2Human.
    Por ahora guarda en un archivo local; en producción conectaría con un CRM.
    """
    try:
        leads_file = "config/leads.txt"
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entrada = f"[{timestamp}] Tel: {telefono} | Empresa: {empresa} | Necesidad: {necesidad} | Interés: {interes}\n"
        with open(leads_file, "a", encoding="utf-8") as f:
            f.write(entrada)
        logger.info(f"Lead registrado: {telefono}")
        return "Lead registrado exitosamente"
    except Exception as e:
        logger.error(f"Error registrando lead: {e}")
        return "Error al registrar lead"


def calificar_lead(tiene_necesidad: bool, tiene_operacion: bool, quiere_implementar_pronto: bool, acepta_demo: bool) -> str:
    """
    Califica un lead según los criterios de Next2Human.
    Retorna: 'alto', 'medio' o 'bajo'
    """
    puntaje = sum([
        tiene_necesidad,
        tiene_operacion,
        quiere_implementar_pronto,
        acepta_demo,
    ])
    if puntaje >= 3:
        return "alto"
    elif puntaje == 2:
        return "medio"
    else:
        return "bajo"


def generar_mensaje_demo(nombre_empresa: str = "") -> str:
    """Genera el mensaje estándar para proponer una demo."""
    empresa_txt = f" de {nombre_empresa}" if nombre_empresa else ""
    return (
        f"Perfecto. Creo que valdría la pena revisarlo en una llamada o demo "
        f"para entender mejor el caso{empresa_txt} y proponerte algo más aterrizado. "
        f"¿Te gustaría que te ayudara a agendarlo?"
    )
