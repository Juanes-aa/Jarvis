"""
tools/system/clipboard.py
Tools de portapapeles mediante pyperclip.

Fase 1.2. Dos tools, ambas RiskLevel.SAFE:
- leer_portapapeles()
- escribir_portapapeles(texto)

CONSIDERACION DE PRIVACIDAD:
    leer_portapapeles puede exponer datos sensibles (contrasenas copiadas,
    tokens, informacion personal, etc.). No se bloquea la lectura, pero NUNCA se
    loguea el contenido del portapapeles (ni al leer ni al escribir): solo se
    deja constancia en el log de que la accion ocurrio.
"""

from __future__ import annotations

import logging

from tools.base import RiskLevel, Tool, ToolExecutionResult
from tools.registry import register_tool

log = logging.getLogger(__name__)

_PYPERCLIP_FALTA = (
    "No se pudo importar pyperclip. Instala con: pip install pyperclip"
)


@register_tool
class LeerPortapapelesTool(Tool):
    name = "leer_portapapeles"
    description = (
        "Lee y devuelve el texto que hay actualmente en el portapapeles del "
        "sistema (lo ultimo que el usuario copio)."
    )
    parameters = {"type": "object", "properties": {}}
    risk_level = RiskLevel.SAFE

    def execute(self) -> ToolExecutionResult:
        # Privacidad: se loguea la accion, NUNCA el contenido leido.
        log.info("[leer_portapapeles] leyendo portapapeles (contenido no logueado)")
        try:
            import pyperclip
        except ImportError:
            return ToolExecutionResult(False, _PYPERCLIP_FALTA)
        try:
            contenido = pyperclip.paste()
        except pyperclip.PyperclipException as e:
            return ToolExecutionResult(False, f"No se pudo leer el portapapeles: {e}")
        if not contenido:
            return ToolExecutionResult(True, "El portapapeles esta vacio.")
        return ToolExecutionResult(
            True, contenido, data={"texto": contenido}
        )


@register_tool
class EscribirPortapapelesTool(Tool):
    name = "escribir_portapapeles"
    description = (
        "Copia un texto al portapapeles del sistema para que el usuario pueda "
        "pegarlo donde quiera."
    )
    parameters = {
        "type": "object",
        "properties": {
            "texto": {
                "type": "string",
                "description": "Texto a copiar al portapapeles.",
            },
        },
        "required": ["texto"],
    }
    risk_level = RiskLevel.SAFE

    def execute(self, texto: str) -> ToolExecutionResult:
        # Privacidad: se loguea la accion y el tamano, NUNCA el texto.
        log.info(
            "[escribir_portapapeles] escribiendo %d caracteres (texto no logueado)",
            len(texto or ""),
        )
        try:
            import pyperclip
        except ImportError:
            return ToolExecutionResult(False, _PYPERCLIP_FALTA)
        try:
            pyperclip.copy(texto or "")
        except pyperclip.PyperclipException as e:
            return ToolExecutionResult(False, f"No se pudo escribir el portapapeles: {e}")
        return ToolExecutionResult(True, "Texto copiado al portapapeles.")
