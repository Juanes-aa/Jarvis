"""
tools/system/screenshot.py
Tools de captura de pantalla y OCR.

Fase 1.2. Dos tools, ambas RiskLevel.SAFE:
- tomar_captura(guardar_en=None)  -> captura de pantalla (PIL.ImageGrab).
- leer_pantalla()                 -> captura + OCR con pytesseract.

Sobre leer_pantalla y Tesseract:
    El OCR requiere el binario Tesseract instalado en el sistema (no basta el
    paquete de Python pytesseract). Por eso leer_pantalla arranca INACTIVA en
    tools_config.json y tools/__init__.py la activa dinamicamente al arrancar
    SOLO si detecta el binario (shutil.which("tesseract")). Como defensa en
    profundidad, execute() tambien captura TesseractNotFoundError por si el
    binario se desinstala o cambia el PATH a mitad de sesion, devolviendo un
    error descriptivo en espanol con instrucciones de instalacion.

tomar_captura NO depende de Tesseract y queda activa por defecto.
"""

from __future__ import annotations

import logging
from datetime import datetime

from tools.base import RiskLevel, Tool, ToolExecutionResult
from tools.registry import register_tool

log = logging.getLogger(__name__)

_PIL_FALTA = (
    "No se pudo importar PIL (Pillow). Instala con: pip install Pillow"
)
_PYTESSERACT_FALTA = (
    "No se pudo importar pytesseract. Instala con: pip install pytesseract"
)
_TESSERACT_BINARIO_FALTA = (
    "El motor de OCR Tesseract no esta instalado en el sistema. En Windows, "
    "instalalo desde https://github.com/UB-Mannheim/tesseract/wiki "
    "(o 'choco install tesseract' si usas Chocolatey) y asegurate de que "
    "'tesseract' este en el PATH."
)


@register_tool
class TomarCapturaTool(Tool):
    name = "tomar_captura"
    description = (
        "Toma una captura de la pantalla completa y la guarda como imagen PNG. "
        "Si no se indica donde guardarla, se guarda en la subcarpeta 'capturas' "
        "de la carpeta de trabajo de Jarvis."
    )
    parameters = {
        "type": "object",
        "properties": {
            "guardar_en": {
                "type": "string",
                "description": (
                    "Ruta (relativa a la carpeta de trabajo de Jarvis) donde "
                    "guardar la captura. Opcional."
                ),
            },
        },
    }
    risk_level = RiskLevel.SAFE

    def execute(self, guardar_en: str | None = None) -> ToolExecutionResult:
        log.info("[tomar_captura] guardar_en=%s", guardar_en)
        try:
            from PIL import ImageGrab
        except ImportError:
            return ToolExecutionResult(False, _PIL_FALTA)

        # Resolver destino dentro del sandbox de file_management.
        from tools.system.file_management import _get_root, _resolver_dentro_de_root

        try:
            if guardar_en and guardar_en.strip():
                destino = _resolver_dentro_de_root(guardar_en)
            else:
                nombre = f"screenshot_{datetime.now():%Y%m%d_%H%M%S}.png"
                destino = _get_root() / "capturas" / nombre
        except ValueError as e:
            return ToolExecutionResult(False, str(e))

        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            imagen = ImageGrab.grab()
            imagen.save(str(destino))
        except Exception as e:  # noqa: BLE001 - frontera de error de la tool
            return ToolExecutionResult(False, f"No se pudo tomar la captura: {e}")
        # message: ruta completa para el log (por si el usuario quiere ubicarla);
        # spoken_message: frase corta, sin leer la ruta ni el nombre de archivo
        # con timestamp letra por letra.
        return ToolExecutionResult(
            True,
            f"Captura guardada en: {destino}",
            spoken_message="Captura guardada.",
        )


@register_tool
class LeerPantallaTool(Tool):
    name = "leer_pantalla"
    description = (
        "Toma una captura de la pantalla y extrae el texto visible mediante OCR. "
        "Util para leer en voz alta lo que aparece en pantalla."
    )
    parameters = {"type": "object", "properties": {}}
    risk_level = RiskLevel.SAFE

    def execute(self) -> ToolExecutionResult:
        log.info("[leer_pantalla] ejecutando OCR de la pantalla")
        try:
            from PIL import ImageGrab
        except ImportError:
            return ToolExecutionResult(False, _PIL_FALTA)
        try:
            import pytesseract
        except ImportError:
            return ToolExecutionResult(False, _PYTESSERACT_FALTA)

        try:
            imagen = ImageGrab.grab()
            # Intentar espanol; si el paquete de idioma no esta, caer al default.
            try:
                texto = pytesseract.image_to_string(imagen, lang="spa")
            except pytesseract.TesseractError:
                texto = pytesseract.image_to_string(imagen)
        except pytesseract.TesseractNotFoundError:
            # Defensa en profundidad: binario ausente en tiempo de ejecucion.
            return ToolExecutionResult(False, _TESSERACT_BINARIO_FALTA)
        except Exception as e:  # noqa: BLE001 - frontera de error de la tool
            return ToolExecutionResult(False, f"No se pudo leer la pantalla: {e}")

        texto = (texto or "").strip()
        if not texto:
            return ToolExecutionResult(True, "No se detecto texto en la pantalla.")
        return ToolExecutionResult(True, texto, data={"texto": texto})
