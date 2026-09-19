"""
tools/system/notifications.py
Tool de notificaciones nativas de Windows mediante winotify.

Fase 1.2. Una tool, RiskLevel.SAFE:
- mostrar_notificacion(titulo, mensaje)

Se usa winotify (en vez de win10toast) por su mejor soporte de las
notificaciones toast modernas de Windows 10/11.
"""

from __future__ import annotations

import logging

from tools.base import RiskLevel, Tool, ToolExecutionResult
from tools.registry import register_tool

log = logging.getLogger(__name__)

_WINOTIFY_FALTA = (
    "No se pudo importar winotify. Instala con: pip install winotify"
)


@register_tool
class MostrarNotificacionTool(Tool):
    name = "mostrar_notificacion"
    description = (
        "Muestra una notificacion nativa de Windows (toast) con un titulo y un "
        "mensaje. Util para recordatorios o avisos visuales."
    )
    parameters = {
        "type": "object",
        "properties": {
            "titulo": {
                "type": "string",
                "description": "Titulo de la notificacion.",
            },
            "mensaje": {
                "type": "string",
                "description": "Cuerpo/mensaje de la notificacion.",
            },
        },
        "required": ["titulo", "mensaje"],
    }
    risk_level = RiskLevel.SAFE

    def execute(self, titulo: str, mensaje: str) -> ToolExecutionResult:
        log.info("[mostrar_notificacion] titulo=%r", titulo)
        try:
            from winotify import Notification
        except ImportError:
            return ToolExecutionResult(False, _WINOTIFY_FALTA)
        try:
            toast = Notification(
                app_id="Jarvis",
                title=titulo or "",
                msg=mensaje or "",
            )
            toast.show()
        except Exception as e:  # noqa: BLE001 - frontera de error de la tool
            return ToolExecutionResult(False, f"No se pudo mostrar la notificacion: {e}")
        return ToolExecutionResult(True, "Notificacion mostrada.")
