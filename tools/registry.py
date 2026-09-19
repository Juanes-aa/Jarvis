"""
tools/registry.py
ToolRegistry: descubrimiento, generacion de schema y ejecucion de tools.

Mecanismo de descubrimiento elegido: DECORADOR @register_tool + import explicito
(en tools/__init__.py), en vez de escaneo del directorio con importlib/pkgutil.

Por que decorador + import explicito y NO escaneo de directorio:
  1. Encaja con el estilo del codigo actual, que es explicito y estatico
     (JARVIS_TOOLS es una lista literal, TOOL_DISPATCHER es un dict literal).
  2. Jarvis corre bajo pythonw y esta pensado para empaquetarse; el escaneo de
     directorio con importlib/pkgutil se rompe con frecuencia al congelar el
     binario (PyInstaller no ve modulos que nunca se importan explicitamente).
     Los imports explicitos siempre son visibles para el empaquetador.
  3. Es trivial de depurar: se sabe exactamente que se registra y en que orden,
     y cada registro deja un log claro al arrancar.

Sub-fase 1.1b: se agrega una ALLOWLIST configurable (archivo JSON externo). Una
tool puede estar registrada en el codigo pero quedar oculta/inejecutable si no
esta en la allowlist.

Sub-fase 1.1c: la politica de la allowlist pasa a ser FAIL-CLOSED. Si el archivo
falta o esta corrupto, ya NO se activan todas las tools (fail-open): se activan
solo las tools SAFE y se deja un ERROR visible. Ademas, la confirmacion por voz
para tools DESTRUCTIVE NO vive aqui: el registry sigue siendo un despachador puro
y sincrono, sin dependencias de audio/TTS. La confirmacion se orquesta en main.py
(pipeline de voz), que consulta el risk_level de la tool antes de ejecutarla.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tools.base import RiskLevel, Tool, ToolExecutionResult

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registro central de tools disponibles para Jarvis."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        # Allowlist de nombres de tools activas. None = sin restriccion (todas
        # las registradas quedan activas). Se carga perezosamente la primera vez
        # que se consulta, para no depender del orden de import con config.
        self._allowlist: set[str] | None = None
        self._allowlist_loaded: bool = False
        # True si la allowlist NO se pudo cargar (archivo ausente/corrupto) y se
        # arranco en modo restringido fail-closed (solo tools SAFE activas).
        # main.py lo consulta tras crear el JarvisSpeaker para avisar por voz.
        self.allowlist_failed: bool = False
        # Registros de arranque (nivel, mensaje) que se generan en tiempo de
        # import del paquete tools -- ANTES de que main() configure el logging a
        # archivo-- y que por eso nunca llegaban a jarvis.log. Se guardan aqui y
        # main() los re-emite con replay_startup_log() apenas el logging esta
        # listo, para conservarlos como diagnostico.
        self.startup_log_records: list[tuple[int, str]] = []

    def _record_startup(self, level: int, message: str) -> None:
        """Guarda un mensaje de arranque para re-emitirlo tras configurar logging."""
        self.startup_log_records.append((level, message))

    def replay_startup_log(self, target_logger: logging.Logger | None = None) -> None:
        """
        Re-emite los logs de arranque capturados en tiempo de import.

        main() lo llama justo despues de _setup_logging() para que los mensajes de
        'Allowlist cargada' / 'Resumen de arranque' (que se generan al importar el
        paquete tools, antes de existir el handler de archivo) queden tambien en
        jarvis.log.
        """
        log = target_logger or logger
        for level, message in self.startup_log_records:
            log.log(level, message)

    # -- Registro --------------------------------------------------------------

    def register(self, tool: Tool) -> None:
        """Registra una instancia de Tool. Advierte si se sobrescribe una existente."""
        if not tool.name:
            raise ValueError(f"La tool {tool!r} no define 'name'; no se puede registrar.")
        if tool.name in self._tools:
            logger.warning("[ToolRegistry] Sobrescribiendo tool ya registrada: '%s'", tool.name)
        self._tools[tool.name] = tool
        logger.info(
            "[ToolRegistry] Registrada tool '%s' (risk=%s)",
            tool.name,
            tool.risk_level.value,
        )

    # -- Allowlist -------------------------------------------------------------

    def _safe_tool_names(self) -> set[str]:
        """Nombres de las tools registradas con risk_level == SAFE."""
        return {
            name for name, tool in self._tools.items()
            if tool.risk_level == RiskLevel.SAFE
        }

    def _apply_fail_closed(self, path: object, reason: str) -> None:
        """
        Aplica la politica FAIL-CLOSED cuando la allowlist no se pudo cargar.

        En vez de activar todas las tools (lo cual seria un escalado silencioso
        de permisos ante un archivo corrupto), se activan SOLO las tools SAFE.
        Se deja un log de ERROR bien visible y se marca allowlist_failed para
        que main.py pueda avisar por voz tras crear el JarvisSpeaker.
        """
        self.allowlist_failed = True
        self._allowlist = self._safe_tool_names()
        message = (
            f"[ToolRegistry] !!! FALLO AL CARGAR LA ALLOWLIST ({path}): {reason}. "
            f"Arrancando en MODO RESTRINGIDO (fail-closed): solo se activan las "
            f"tools SAFE = {sorted(self._allowlist)}. Revisa el archivo de configuracion."
        )
        logger.error(message)
        self._record_startup(logging.ERROR, message)

    def _load_allowlist(self) -> None:
        """
        Carga la allowlist desde el archivo JSON configurado (config.TOOLS_ALLOWLIST_FILE).

        Formato esperado: {"active_tools": ["nombre1", "nombre2", ...]}.

        Politica FAIL-CLOSED (sub-fase 1.1c): si el archivo no existe, no se
        puede leer o es invalido, NO se activan todas las tools. En su lugar se
        activan SOLO las tools con risk_level == SAFE y se registra un ERROR
        visible. Un archivo de configuracion roto nunca debe traducirse en mas
        permisos de los declarados.
        """
        self._allowlist_loaded = True

        # Import diferido para no acoplar el orden de import del paquete tools
        # con config (y para que sea trivial de mockear en tests).
        import config

        path = config.TOOLS_ALLOWLIST_FILE
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            active = data["active_tools"]
            if not isinstance(active, list) or not all(isinstance(n, str) for n in active):
                raise ValueError("'active_tools' debe ser una lista de strings")
            self._allowlist = set(active)
            message = (
                f"[ToolRegistry] Allowlist cargada desde {path}: {sorted(self._allowlist)}"
            )
            logger.info(message)
            self._record_startup(logging.INFO, message)
        except FileNotFoundError:
            self._apply_fail_closed(path, "archivo no encontrado")
        except (json.JSONDecodeError, KeyError, ValueError, OSError) as e:
            self._apply_fail_closed(path, f"archivo invalido ({e})")

    def is_active(self, name: str) -> bool:
        """
        True si la tool esta registrada Y activa segun la allowlist.

        Sin allowlist (None) toda tool registrada se considera activa.
        """
        if not self._allowlist_loaded:
            self._load_allowlist()
        if name not in self._tools:
            return False
        if self._allowlist is None:
            return True
        return name in self._allowlist

    # -- Consultas -------------------------------------------------------------

    def has(self, name: str) -> bool:
        """True si existe una tool registrada con ese nombre."""
        return name in self._tools

    def get(self, name: str) -> Tool | None:
        """Devuelve la tool registrada o None."""
        return self._tools.get(name)

    def names(self) -> list[str]:
        """Nombres de todas las tools registradas (activas o no)."""
        return list(self._tools.keys())

    def active_names(self) -> list[str]:
        """Nombres de las tools registradas Y activas segun la allowlist."""
        return [name for name in self._tools if self.is_active(name)]

    def log_activation_summary(self) -> None:
        """
        Deja constancia en el log de que se registro, que quedo activo y que
        quedo oculto por la allowlist. Se llama una vez tras el descubrimiento.
        """
        if not self._allowlist_loaded:
            self._load_allowlist()
        active = self.active_names()
        inactive = [name for name in self._tools if name not in active]
        message = (
            f"[ToolRegistry] Resumen de arranque: {len(self._tools)} registrada(s)="
            f"{self.names()} | {len(active)} activa(s)={active} | "
            f"{len(inactive)} oculta(s) por allowlist={inactive}"
        )
        logger.info(message)
        self._record_startup(logging.INFO, message)

    # -- Schema para Groq ------------------------------------------------------

    def get_schemas(self) -> list[dict[str, Any]]:
        """
        Genera la lista de schemas (function calling) para enviar a Groq.

        Solo incluye tools ACTIVAS segun la allowlist; una tool registrada pero
        no activa queda invisible para el LLM (aunque siga en el codigo).
        """
        active = self.active_names()
        schemas = [self._tools[name].to_schema() for name in active]
        logger.info(
            "[ToolRegistry] Schema generado para %d tool(s) activa(s): %s",
            len(schemas),
            active,
        )
        return schemas

    # -- Ejecucion -------------------------------------------------------------

    def execute(self, name: str, params: dict[str, Any] | None = None) -> ToolExecutionResult:
        """
        Ejecuta una tool por nombre con los parametros dados.

        Respeta la allowlist: una tool registrada pero no activa NO se ejecuta.
        La ejecucion aqui es directa: la confirmacion por voz para tools
        DESTRUCTIVE se orquesta en main.py (ver _run_voice_confirmation) antes de
        llegar a este metodo, no dentro del registry.
        Nunca lanza; cualquier fallo se envuelve en un ToolExecutionResult.
        """
        params = params or {}
        tool = self._tools.get(name)
        if tool is None:
            logger.error("[ToolRegistry] Tool no registrada: '%s'", name)
            return ToolExecutionResult(False, f"La herramienta '{name}' no esta registrada.")

        if not self.is_active(name):
            logger.warning(
                "[ToolRegistry] Tool '%s' registrada pero DESACTIVADA por la allowlist; "
                "no se ejecuta.",
                name,
            )
            return ToolExecutionResult(
                False,
                f"La herramienta '{name}' esta desactivada por configuracion.",
            )

        logger.info("[ToolRegistry] Ejecutando '%s' con params=%s", name, params)
        try:
            result = tool.execute(**params)
        except TypeError as e:
            logger.error("[ToolRegistry] Parametros invalidos para '%s': %s", name, e)
            return ToolExecutionResult(False, f"Parametros invalidos para {name}: {e}")
        except Exception as e:  # noqa: BLE001 - frontera de error deliberada
            logger.error("[ToolRegistry] Error ejecutando '%s': %s", name, e, exc_info=True)
            return ToolExecutionResult(False, f"Error inesperado ejecutando {name}: {e}")

        logger.info(
            "[ToolRegistry] '%s' -> success=%s msg=%s",
            name,
            result.success,
            result.message,
        )
        return result


# -- Singleton global + decorador ---------------------------------------------

_REGISTRY = ToolRegistry()


def get_registry() -> ToolRegistry:
    """Devuelve el registro global de tools."""
    return _REGISTRY


def register_tool(cls: type[Tool]) -> type[Tool]:
    """
    Decorador de clase: instancia la Tool y la registra en el singleton global.

    Uso:
        @register_tool
        class AbrirAppTool(Tool):
            ...
    """
    _REGISTRY.register(cls())
    return cls
