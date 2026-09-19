"""
tools/system/media_control.py
Tools de control multimedia mediante teclas multimedia del sistema (pyautogui).

Fase 1.2. Tres tools, todas RiskLevel.SAFE:
- pausar_reproducir()
- siguiente_cancion()
- cancion_anterior()

Se simulan las teclas multimedia virtuales del sistema operativo ('playpause',
'nexttrack', 'prevtrack'), por lo que funcionan a nivel de Windows con CUALQUIER
reproductor activo (Spotify, YouTube en el navegador, VLC, etc.): no dependen de
una app de musica especifica.
"""

from __future__ import annotations

import logging

from tools.base import RiskLevel, Tool, ToolExecutionResult
from tools.registry import register_tool

log = logging.getLogger(__name__)

_PYAUTOGUI_FALTA = (
    "No se pudo importar pyautogui. Instala con: pip install pyautogui"
)


def _presionar_tecla_media(tecla: str, descripcion: str) -> ToolExecutionResult:
    """Presiona una tecla multimedia del sistema y devuelve el resultado."""
    log.info("[media_control] tecla=%s (%s)", tecla, descripcion)
    try:
        import pyautogui
    except ImportError:
        return ToolExecutionResult(False, _PYAUTOGUI_FALTA)
    try:
        pyautogui.press(tecla)
    except Exception as e:  # noqa: BLE001 - frontera de error de la tool
        return ToolExecutionResult(False, f"No se pudo {descripcion}: {e}")
    return ToolExecutionResult(True, f"{descripcion.capitalize()}.")


@register_tool
class PausarReproducirTool(Tool):
    name = "pausar_reproducir"
    description = (
        "Pausa o reanuda (play/pause) la reproduccion actual SIN cambiar de "
        "pista, en cualquier reproductor activo del sistema. Usala cuando el "
        "usuario diga 'pausa', 'pausa la musica', 'detente', 'para la musica', "
        "'reanuda', 'continua' o 'reproduce'. NO sirve para cambiar de cancion: "
        "si el usuario quiere avanzar usa 'siguiente_cancion' y si quiere "
        "retroceder usa 'cancion_anterior'."
    )
    parameters = {"type": "object", "properties": {}}
    risk_level = RiskLevel.SAFE

    def execute(self) -> ToolExecutionResult:
        return _presionar_tecla_media("playpause", "pausar o reanudar la reproduccion")


@register_tool
class SiguienteCancionTool(Tool):
    name = "siguiente_cancion"
    description = (
        "Avanza a la SIGUIENTE cancion o pista (salta hacia adelante) en el "
        "reproductor multimedia activo. Usala solo cuando el usuario diga "
        "'siguiente', 'siguiente cancion', 'pasa a la siguiente', 'salta esta' "
        "o 'la que sigue'. NO pausa ni reanuda (para eso usa "
        "'pausar_reproducir') y NO retrocede (para eso usa 'cancion_anterior')."
    )
    parameters = {"type": "object", "properties": {}}
    risk_level = RiskLevel.SAFE

    def execute(self) -> ToolExecutionResult:
        return _presionar_tecla_media("nexttrack", "pasar a la siguiente cancion")


@register_tool
class CancionAnteriorTool(Tool):
    name = "cancion_anterior"
    description = (
        "Retrocede a la cancion o pista ANTERIOR (salta hacia atras) en el "
        "reproductor multimedia activo. Usala solo cuando el usuario diga "
        "'anterior', 'cancion anterior', 'regresa', 'vuelve a la anterior' o "
        "'la de antes'. NO pausa ni reanuda (para eso usa 'pausar_reproducir') "
        "y NO avanza (para eso usa 'siguiente_cancion')."
    )
    parameters = {"type": "object", "properties": {}}
    risk_level = RiskLevel.SAFE

    def execute(self) -> ToolExecutionResult:
        return _presionar_tecla_media("prevtrack", "volver a la cancion anterior")
