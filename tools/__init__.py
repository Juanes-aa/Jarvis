# tools - Tool Registry de Jarvis (sub-fase 1.1a)
"""
Importar este paquete DESCUBRE y registra todas las tools disponibles.

El descubrimiento es explicito: cada modulo de tool se importa aqui abajo, lo
que ejecuta su decorador @register_tool y la agrega al registro global. Se
eligio import explicito en vez de escaneo de directorio (ver tools/registry.py
para el razonamiento: estilo del codigo + compatibilidad con empaquetado bajo
pythonw + facilidad de depuracion).

Las tools estan organizadas en subpaquetes por categoria: tools/system/* para
las que actuan sobre el sistema operativo y tools/conversation/* para las
puramente conversacionales. Al ser import explicito, el descubrimiento recorre
esas subcarpetas simplemente importando cada modulo por su ruta de subpaquete.

Para agregar una tool nueva en el futuro: crear tools/<categoria>/<mi_tool>.py
con una clase Tool decorada con @register_tool y anadir su import en la seccion
de DESCUBRIMIENTO de abajo.
"""

import logging
import shutil

from tools.base import RiskLevel, Tool, ToolExecutionResult
from tools.registry import ToolRegistry, get_registry, register_tool

logger = logging.getLogger(__name__)

# -- DESCUBRIMIENTO DE TOOLS ---------------------------------------------------
# Importar cada modulo ejecuta su @register_tool y lo agrega al registro global.
# El ORDEN de estos imports define el orden del schema que ve el LLM; se mantiene
# igual al conjunto historico (abrir_app, responder_conversacional,
# subir_bajar_volumen, ajustar_brillo, ejecutar_comando) para no alterar el
# comportamiento externo tras completar la migracion en 1.1b.
from tools.system import abrir_app as _abrir_app  # noqa: F401,E402
from tools.conversation import responder_conversacional as _responder_conversacional  # noqa: F401,E402
from tools.system import subir_bajar_volumen as _subir_bajar_volumen  # noqa: F401,E402
from tools.system import ajustar_brillo as _ajustar_brillo  # noqa: F401,E402
from tools.system import ejecutar_comando as _ejecutar_comando  # noqa: F401,E402

# Fase 1.2: 6 categorias nuevas (15 tools). Import explicito obligatorio para que
# cada @register_tool se ejecute (y para que PyInstaller las vea al empaquetar).
from tools.system import file_management as _file_management  # noqa: F401,E402
from tools.system import window_control as _window_control  # noqa: F401,E402
from tools.system import clipboard as _clipboard  # noqa: F401,E402
from tools.system import screenshot as _screenshot  # noqa: F401,E402
from tools.system import media_control as _media_control  # noqa: F401,E402
from tools.system import notifications as _notifications  # noqa: F401,E402


# -- HOOKS DE ARRANQUE (fase 1.2) ---------------------------------------------

def _preparar_sandbox_archivos() -> None:
    """
    Valida y prepara el root de gestion de archivos en la inicializacion.

    HARD-CRASH deliberado: si el root configurado viola el invariante de
    seguridad (fuera del perfil del usuario), validate_and_prepare_root() lanza
    FileManagementRootError y Jarvis NO arranca. Esto ocurre aqui, en tiempo de
    import del paquete tools -- antes de que main() levante el wake word/audio
    loop-- por lo que no hay riesgo de tumbar una sesion ya en curso.
    """
    root = _file_management.validate_and_prepare_root()
    get_registry()._record_startup(
        logging.INFO, f"[tools] Sandbox de archivos preparado en: {root}"
    )


def _activar_ocr_si_hay_tesseract() -> None:
    """
    Activa 'leer_pantalla' dinamicamente SOLO si el binario Tesseract esta
    instalado. leer_pantalla arranca inactiva en tools_config.json; si se
    detecta el binario se agrega al allowlist en memoria (sin tocar el JSON ni
    registry.py). Si no, se deja inactiva con instrucciones en el log.
    """
    reg = get_registry()
    # Forzar la carga perezosa de la allowlist antes de mutarla.
    reg.is_active("leer_pantalla")
    if shutil.which("tesseract"):
        if reg._allowlist is not None:
            reg._allowlist.add("leer_pantalla")
        msg = (
            "[tools] Tesseract detectado: 'leer_pantalla' (OCR) activada "
            "automaticamente."
        )
        logger.info(msg)
        reg._record_startup(logging.INFO, msg)
    else:
        msg = (
            "[tools] Tesseract NO detectado: 'leer_pantalla' (OCR) queda "
            "INACTIVA. Para habilitarla instala Tesseract en Windows desde "
            "https://github.com/UB-Mannheim/tesseract/wiki (o "
            "'choco install tesseract') y reinicia Jarvis."
        )
        logger.info(msg)
        reg._record_startup(logging.INFO, msg)


_preparar_sandbox_archivos()
_activar_ocr_si_hay_tesseract()

# Con todas las tools ya registradas, aplicar la allowlist configurable y dejar
# constancia en el log de que se registro, que quedo activo y que quedo oculto.
get_registry().log_activation_summary()

__all__ = [
    "RiskLevel",
    "Tool",
    "ToolExecutionResult",
    "ToolRegistry",
    "get_registry",
    "register_tool",
]
