"""
tools/ajustar_brillo.py
Tool migrada al nuevo Tool Registry: ajustar el brillo de la pantalla.

La logica real vive en actions/system_control.ajustar_brillo; esta clase solo
declara el contrato para el LLM y delega la ejecucion.
"""

from __future__ import annotations

from actions.system_control import ajustar_brillo as _ajustar_brillo_action
from tools.base import RiskLevel, Tool, ToolExecutionResult
from tools.registry import register_tool


@register_tool
class AjustarBrilloTool(Tool):
    name = "ajustar_brillo"
    description = (
        "Establece el nivel de brillo de la pantalla a un valor absoluto. "
        "Usa esta herramienta cuando el usuario pida cambiar, subir, bajar "
        "o ajustar el brillo de la pantalla."
    )
    parameters = {
        "type": "object",
        "properties": {
            "nivel": {
                "type": "integer",
                "description": (
                    "Nivel de brillo deseado en porcentaje (0-100). "
                    "0 = mínimo, 100 = máximo."
                ),
            },
        },
        "required": ["nivel"],
    }
    # Cambia el estado del sistema pero es trivialmente reversible (basta con
    # volver a ajustar el brillo), por eso es REVERSIBLE y no DESTRUCTIVE.
    risk_level = RiskLevel.REVERSIBLE

    def execute(self, nivel: int) -> ToolExecutionResult:
        result = _ajustar_brillo_action(nivel)
        return ToolExecutionResult(result.success, result.message)
