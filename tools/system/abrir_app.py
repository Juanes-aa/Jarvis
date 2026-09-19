"""
tools/abrir_app.py
Tool migrada al nuevo Tool Registry: abrir una aplicacion instalada.

La logica real de apertura sigue viviendo en actions/system_control.abrir_app;
esta clase solo declara el contrato para el LLM y delega la ejecucion.
"""

from __future__ import annotations

from actions.system_control import abrir_app as _abrir_app_action
from tools.base import RiskLevel, Tool, ToolExecutionResult
from tools.registry import register_tool


@register_tool
class AbrirAppTool(Tool):
    name = "abrir_app"
    description = (
        "Abre una aplicacion instalada en el sistema operativo del usuario. "
        "Usa esta herramienta cuando el usuario pida abrir, iniciar o lanzar "
        "un programa o aplicacion."
    )
    parameters = {
        "type": "object",
        "properties": {
            "nombre": {
                "type": "string",
                "description": (
                    "Nombre de la aplicacion a abrir. Ejemplos: "
                    "'chrome', 'spotify', 'notepad', 'word', 'excel', 'calculadora'."
                ),
            },
        },
        "required": ["nombre"],
    }
    # Abrir una app cambia el estado del sistema pero es trivial de deshacer
    # (basta con cerrar la app), por eso es REVERSIBLE y no DESTRUCTIVE.
    risk_level = RiskLevel.REVERSIBLE

    def execute(self, nombre: str) -> ToolExecutionResult:
        result = _abrir_app_action(nombre)
        return ToolExecutionResult(result.success, result.message)
