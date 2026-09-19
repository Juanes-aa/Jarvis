"""
tools/responder_conversacional.py
Tool migrada al nuevo Tool Registry: responder al usuario con texto libre.

No tiene efectos sobre el sistema operativo (es 'solo lectura' en la practica),
por eso su risk_level es SAFE. En el flujo actual, main.py intercepta esta tool
antes de ejecutarla para pronunciar el texto; execute() se provee para que el
registro sea completo y coherente (schema + ejecucion) de cara a 1.1b.
"""

from __future__ import annotations

from tools.base import RiskLevel, Tool, ToolExecutionResult
from tools.registry import register_tool


@register_tool
class ResponderConversacionalTool(Tool):
    name = "responder_conversacional"
    description = (
        "Responde al usuario con texto libre cuando la solicitud NO requiere "
        "ejecutar ninguna accion en el sistema. Usa esta herramienta para "
        "preguntas generales, chistes, saludos, conversacion casual, "
        "explicaciones, o cualquier cosa que sea puramente informativa."
    )
    parameters = {
        "type": "object",
        "properties": {
            "texto": {
                "type": "string",
                "description": (
                    "Texto de respuesta en lenguaje natural y plano para ser leído en voz alta: "
                    "sin formato Markdown, sin asteriscos, sin viñetas ni encabezados."
                ),
            },
        },
        "required": ["texto"],
    }
    # Solo devuelve texto; no toca el sistema.
    risk_level = RiskLevel.SAFE

    def execute(self, texto: str) -> ToolExecutionResult:
        return ToolExecutionResult(True, texto)
