"""
tools/ejecutar_comando.py
Tool migrada al nuevo Tool Registry: ejecutar comandos/acciones del sistema.

La logica real vive en actions/system_control.ejecutar_comando; esta clase solo
declara el contrato para el LLM y delega la ejecucion.

NOTA sobre risk_level = DESTRUCTIVE:
    Esta es la tool de MAYOR riesgo del set: es el punto de entrada para
    ejecutar comandos del sistema, por eso se etiqueta como DESTRUCTIVE.

    Importante: la ejecucion esta restringida por una allowlist interna en
    actions/system_control.ejecutar_comando: SOLO permite crear carpetas via
    'mkdir <ruta>' (rechaza cualquier otra cosa). La <ruta> ahora puede apuntar a
    cualquier ubicacion (alias de carpeta conocida de Windows, ruta absoluta
    literal o, por defecto, el perfil del usuario). El riesgo se mitiga porque (1)
    solo hace mkdir, nunca borra/sobrescribe, y (2) al ser DESTRUCTIVE dispara la
    confirmacion por voz (ya implementada y en uso: ver _run_voice_confirmation
    en main.py), que ademas anuncia la ruta resuelta completa antes de ejecutar.
"""

from __future__ import annotations

import logging

from actions.system_control import ejecutar_comando as _ejecutar_comando_action
from tools.base import RiskLevel, Tool, ToolExecutionResult
from tools.registry import register_tool
from tools.system.command_blacklist import is_blacklisted

log = logging.getLogger(__name__)


@register_tool
class EjecutarComandoTool(Tool):
    name = "ejecutar_comando"
    description = (
        "Realiza acciones limitadas del sistema de archivos de forma SEGURA. "
        "SOLO soporta crear carpetas (mkdir), en cualquier ubicación que el "
        "usuario indique. NO ejecuta PowerShell arbitrario ni comandos de red, "
        "procesos o borrado. Usa 'mkdir <ruta>' para crear una carpeta. La <ruta> "
        "puede empezar con un alias de carpeta conocida de Windows: 'escritorio', "
        "'documentos', 'descargas', 'imagenes', 'musica' o 'videos', seguido del "
        "nombre de la carpeta (ej. 'mkdir escritorio\\\\Proyectos', "
        "'mkdir documentos\\\\Fotos', 'mkdir descargas\\\\Instaladores'). También "
        "acepta una ruta absoluta literal dicha por el usuario "
        "(ej. 'mkdir C:\\\\Users\\\\juan\\\\Trabajo'). Si el usuario NO indica "
        "ubicación, usa solo el nombre (ej. 'mkdir prueba') y se creará en el "
        "perfil del usuario por defecto. Si el usuario pide algo que no sea crear "
        "una carpeta, usa 'responder_conversacional' explicando que no puedes "
        "hacerlo por seguridad."
    )
    parameters = {
        "type": "object",
        "properties": {
            "comando": {
                "type": "string",
                "description": (
                    "Acción a ejecutar. Único formato soportado: 'mkdir <ruta>'. "
                    "La ruta puede usar un alias de carpeta conocida como primer "
                    "segmento ('escritorio', 'documentos', 'descargas', "
                    "'imagenes', 'musica', 'videos'), una ruta absoluta literal, "
                    "o solo un nombre (por defecto, perfil del usuario). "
                    "Ejemplos: 'mkdir escritorio\\\\Proyectos', "
                    "'mkdir C:\\\\Users\\\\juan\\\\X', 'mkdir prueba'."
                ),
            },
        },
        "required": ["comando"],
    }
    # Punto de entrada para ejecutar comandos del sistema: el slot de mayor
    # riesgo del set. Ver la nota del docstring del modulo.
    risk_level = RiskLevel.DESTRUCTIVE

    def execute(self, comando: str) -> ToolExecutionResult:
        # Gate de seguridad: la lista negra de comandos catastroficos se evalua
        # ANTES de cualquier ejecucion real. Si coincide, se rechaza totalmente
        # (el flujo de confirmacion por voz en main.py ya lo intercepta antes de
        # preguntar; esta comprobacion es defensa en profundidad para cualquier
        # llamada directa a la tool que no pase por ese flujo).
        bloqueado, razon = is_blacklisted(comando)
        if bloqueado:
            log.warning(
                "[Seguridad] Comando bloqueado por lista negra: %r (patron: %s)",
                comando, razon,
            )
            return ToolExecutionResult(
                False,
                "Ese comando esta bloqueado por seguridad porque podria danar "
                "el sistema o el disco de forma irreversible.",
            )

        result = _ejecutar_comando_action(comando)
        return ToolExecutionResult(result.success, result.message)
