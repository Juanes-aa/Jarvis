"""
tools/subir_bajar_volumen.py
Tool migrada al nuevo Tool Registry: ajustar el volumen del sistema.

La logica real vive en actions/system_control.subir_bajar_volumen; esta clase
solo declara el contrato para el LLM y delega la ejecucion.
"""

from __future__ import annotations

from actions.system_control import subir_bajar_volumen as _subir_bajar_volumen_action
from tools.base import RiskLevel, Tool, ToolExecutionResult
from tools.registry import register_tool


@register_tool
class SubirBajarVolumenTool(Tool):
    name = "subir_bajar_volumen"
    description = (
        "Ajusta el volumen del sistema. Usa esta herramienta cuando el usuario "
        "pida cambiar, subir, bajar o ajustar el volumen. "
        "Usa 'nivel' cuando el usuario indica un valor absoluto "
        "(ej. 'sube el volumen al 50%' -> nivel=50). "
        "Usa 'delta' cuando el usuario pide un ajuste relativo sin valor concreto "
        "(ej. 'sube el volumen' -> delta=10, 'bájalo un poco' -> delta=-10). "
        "Especifica solo uno de los dos parámetros, nunca ambos."
    )
    parameters = {
        "type": "object",
        "properties": {
            "nivel": {
                "type": "integer",
                "description": (
                    "Nivel de volumen deseado en porcentaje (0-100). "
                    "0 = mínimo, 100 = máximo. "
                    "Úsalo cuando el usuario pide un valor absoluto."
                ),
            },
            "delta": {
                "type": "integer",
                "description": (
                    "Cantidad a sumar (positivo) o restar (negativo) al volumen actual. "
                    "Ejemplo: 10 para subir, -20 para bajar. "
                    "Úsalo para ajustes relativos."
                ),
            },
        },
    }
    # Cambia el estado del sistema pero es trivialmente reversible (basta con
    # volver a ajustar el volumen), por eso es REVERSIBLE y no DESTRUCTIVE.
    risk_level = RiskLevel.REVERSIBLE

    def execute(
        self,
        nivel: int | None = None,
        delta: int | None = None,
    ) -> ToolExecutionResult:
        if nivel is not None and delta is not None:
            return ToolExecutionResult(
                False, "Debes especificar nivel o delta, no ambos."
            )
        if nivel is None and delta is None:
            return ToolExecutionResult(False, "Debes especificar nivel o delta.")

        result = _subir_bajar_volumen_action(delta=delta, nivel=nivel)
        return ToolExecutionResult(result.success, result.message)
