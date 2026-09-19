"""
tools/system/file_management.py
Tools de gestion de archivos CONFINADAS a un directorio raiz permitido.

Fase 1.2. Cuatro tools: crear_archivo, crear_carpeta, mover_archivo y
renombrar_archivo. NO se incluye borrado (queda pendiente, con su propio flujo
de confirmacion; el roadmap es explicito en ello).

SANDBOX (restriccion dura):
    Todas las rutas se resuelven y validan dentro de un directorio raiz
    permitido, configurable en tools_config.json (clave 'file_management_root').
    Por defecto es '~/Jarvis'. Cualquier ruta que intente salir del root
    (segmentos '..', rutas absolutas fuera del root, symlinks que escapen) se
    RECHAZA: se resuelve la ruta final con Path.resolve() (que colapsa '..' y
    resuelve symlinks) y se exige que quede dentro del root.

INVARIANTE DE SEGURIDAD (hard-crash):
    El root configurado DEBE estar dentro del perfil del usuario (Path.home()).
    Si no lo esta, validate_and_prepare_root() lanza FileManagementRootError en
    la fase de inicializacion (antes de arrancar el wake word/audio loop). Esto
    es una violacion de un invariante de seguridad, no una dependencia opcional
    ausente: Jarvis no debe arrancar con un sandbox de archivos roto.

Todas las tools son RiskLevel.REVERSIBLE (crear/mover/renombrar se puede
deshacer manualmente y no destruyen datos: crear_archivo ni siquiera sobrescribe
un archivo existente).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from tools.base import RiskLevel, Tool, ToolExecutionResult
from tools.registry import register_tool

log = logging.getLogger(__name__)

# Valor por defecto si la clave falta en tools_config.json. Restrictivo a
# proposito: una carpeta 'Jarvis' dedicada dentro del perfil del usuario.
_DEFAULT_ROOT = "~/Jarvis"

# Cache del root ya validado. Lo fija validate_and_prepare_root() al arrancar.
_root_cache: Path | None = None


class FileManagementRootError(RuntimeError):
    """El root configurado viola el invariante de seguridad (fuera del perfil)."""


# -- Resolucion y validacion del root -----------------------------------------

def _leer_root_configurado() -> str:
    """Lee la clave 'file_management_root' de tools_config.json (o el default)."""
    # Import diferido: mismo criterio que el registry (no acoplar orden de import).
    import config

    try:
        with open(config.TOOLS_ALLOWLIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        valor = data.get("file_management_root")
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    except (OSError, json.JSONDecodeError):
        # Si el archivo falta o esta corrupto usamos el default restrictivo; el
        # ToolRegistry ya loguea aparte el fallo de la allowlist.
        pass
    return _DEFAULT_ROOT


def validate_and_prepare_root() -> Path:
    """
    Resuelve, valida y crea (si falta) el root de gestion de archivos.

    Se llama UNA vez en la fase de inicializacion (desde tools/__init__.py).

    HARD-CRASH: si el root resuelto NO esta dentro del perfil del usuario
    (Path.home()), lanza FileManagementRootError con un mensaje claro y
    accionable (ruta configurada, ruta de perfil esperada y que corregir en
    tools_config.json). No se degrada a fail-closed: es un invariante de
    seguridad y Jarvis no debe arrancar con el sandbox roto.
    """
    global _root_cache

    configurado = _leer_root_configurado()
    expandido = os.path.expandvars(os.path.expanduser(configurado))
    root = Path(expandido).resolve()
    home = Path.home().resolve()

    if not root.is_relative_to(home):
        raise FileManagementRootError(
            "CONFIGURACION DE SEGURIDAD INVALIDA: el root de gestion de archivos "
            f"esta FUERA del perfil del usuario.\n"
            f"  - Ruta configurada (resuelta): {root}\n"
            f"  - Perfil del usuario esperado: {home}\n"
            f"  - Corrige la clave 'file_management_root' en tools_config.json "
            f"para que apunte a una carpeta DENTRO de tu perfil "
            f"(por defecto '{_DEFAULT_ROOT}').\n"
            "Jarvis no arranca con un sandbox de archivos roto."
        )

    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise FileManagementRootError(
            f"No se pudo preparar el root de gestion de archivos '{root}': {e}"
        ) from e

    _root_cache = root
    log.info("[file_management] Root de gestion de archivos validado: %s", root)
    return root


def _get_root() -> Path:
    """Devuelve el root ya validado; lo valida al vuelo si aun no se hizo."""
    if _root_cache is None:
        return validate_and_prepare_root()
    return _root_cache


def _resolver_dentro_de_root(ruta: str) -> Path:
    """
    Resuelve 'ruta' (relativa al root o absoluta) a una ruta final dentro del
    root permitido. Lanza ValueError si escapa del sandbox o esta vacia.
    """
    root = _get_root()
    arg = (ruta or "").strip().strip('"').strip("'")
    if not arg:
        raise ValueError("No se indico ninguna ruta.")

    arg = os.path.expandvars(os.path.expanduser(arg))
    p = Path(arg)
    destino = p if p.is_absolute() else root / p
    # resolve() colapsa '..' y resuelve symlinks (strict=False: aun puede no
    # existir). Asi un symlink que apunte fuera del root tambien queda detectado.
    destino = destino.resolve()

    if not destino.is_relative_to(root):
        raise ValueError(
            f"Ruta rechazada: '{destino}' esta fuera del directorio permitido "
            f"'{root}'."
        )
    return destino


# -- Tools ---------------------------------------------------------------------

@register_tool
class CrearArchivoTool(Tool):
    name = "crear_archivo"
    description = (
        "Crea un archivo de texto nuevo dentro de la carpeta de trabajo de "
        "Jarvis. La ruta es relativa a esa carpeta (por ejemplo "
        "'notas/lista.txt'). Si el archivo ya existe, NO lo sobrescribe. Usa "
        "esta herramienta cuando el usuario pida crear o guardar un archivo con "
        "cierto contenido."
    )
    parameters = {
        "type": "object",
        "properties": {
            "ruta": {
                "type": "string",
                "description": (
                    "Ruta del archivo a crear, relativa a la carpeta de trabajo "
                    "de Jarvis (ej. 'lista.txt', 'notas/ideas.txt')."
                ),
            },
            "contenido": {
                "type": "string",
                "description": "Contenido de texto inicial del archivo (opcional).",
            },
        },
        "required": ["ruta"],
    }
    risk_level = RiskLevel.REVERSIBLE

    def execute(self, ruta: str, contenido: str = "") -> ToolExecutionResult:
        try:
            destino = _resolver_dentro_de_root(ruta)
        except ValueError as e:
            return ToolExecutionResult(False, str(e))

        # Log SIN el contenido (privacidad): solo ruta y tamano.
        log.info(
            "[crear_archivo] destino=%s (contenido oculto, %d caracteres)",
            destino, len(contenido or ""),
        )

        if destino.exists():
            return ToolExecutionResult(
                False, f"El archivo '{destino.name}' ya existe; no se sobrescribe."
            )
        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(contenido or "", encoding="utf-8")
        except OSError as e:
            return ToolExecutionResult(False, f"No se pudo crear el archivo: {e}")
        # message: ruta completa para el log; spoken_message: frase corta (no se
        # lee la ruta resuelta letra por letra por voz).
        return ToolExecutionResult(
            True, f"Archivo creado: {destino}", spoken_message="Archivo creado."
        )


@register_tool
class CrearCarpetaTool(Tool):
    name = "crear_carpeta"
    description = (
        "Crea una carpeta nueva (y las intermedias necesarias) dentro de la "
        "carpeta de trabajo de Jarvis. La ruta es relativa a esa carpeta "
        "(ej. 'proyectos/2025')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "ruta": {
                "type": "string",
                "description": (
                    "Ruta de la carpeta a crear, relativa a la carpeta de "
                    "trabajo de Jarvis (ej. 'proyectos', 'fotos/viaje')."
                ),
            },
        },
        "required": ["ruta"],
    }
    risk_level = RiskLevel.REVERSIBLE

    def execute(self, ruta: str) -> ToolExecutionResult:
        try:
            destino = _resolver_dentro_de_root(ruta)
        except ValueError as e:
            return ToolExecutionResult(False, str(e))

        log.info("[crear_carpeta] destino=%s", destino)
        try:
            destino.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ToolExecutionResult(False, f"No se pudo crear la carpeta: {e}")
        return ToolExecutionResult(
            True, f"Carpeta creada: {destino}", spoken_message="Carpeta creada."
        )


@register_tool
class MoverArchivoTool(Tool):
    name = "mover_archivo"
    description = (
        "Mueve un archivo o carpeta de una ubicacion a otra, ambas dentro de la "
        "carpeta de trabajo de Jarvis. Rutas relativas a esa carpeta."
    )
    parameters = {
        "type": "object",
        "properties": {
            "origen": {
                "type": "string",
                "description": "Ruta actual del archivo/carpeta a mover (relativa).",
            },
            "destino": {
                "type": "string",
                "description": "Ruta destino (relativa) donde se moverá.",
            },
        },
        "required": ["origen", "destino"],
    }
    risk_level = RiskLevel.REVERSIBLE

    def execute(self, origen: str, destino: str) -> ToolExecutionResult:
        try:
            ruta_origen = _resolver_dentro_de_root(origen)
            ruta_destino = _resolver_dentro_de_root(destino)
        except ValueError as e:
            return ToolExecutionResult(False, str(e))

        log.info("[mover_archivo] origen=%s destino=%s", ruta_origen, ruta_destino)
        if not ruta_origen.exists():
            return ToolExecutionResult(
                False, f"El origen '{ruta_origen.name}' no existe."
            )
        try:
            ruta_destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(ruta_origen), str(ruta_destino))
        except (OSError, shutil.Error) as e:
            return ToolExecutionResult(False, f"No se pudo mover: {e}")
        return ToolExecutionResult(
            True, f"Movido a: {ruta_destino}", spoken_message="Archivo movido."
        )


@register_tool
class RenombrarArchivoTool(Tool):
    name = "renombrar_archivo"
    description = (
        "Renombra un archivo o carpeta dentro de la carpeta de trabajo de "
        "Jarvis, manteniendolo en la misma ubicacion. El nuevo nombre debe ser "
        "un nombre simple, sin barras ni rutas."
    )
    parameters = {
        "type": "object",
        "properties": {
            "ruta": {
                "type": "string",
                "description": "Ruta actual del archivo/carpeta (relativa).",
            },
            "nuevo_nombre": {
                "type": "string",
                "description": (
                    "Nuevo nombre (solo el nombre, sin barras ni rutas, "
                    "ej. 'informe_final.txt')."
                ),
            },
        },
        "required": ["ruta", "nuevo_nombre"],
    }
    risk_level = RiskLevel.REVERSIBLE

    def execute(self, ruta: str, nuevo_nombre: str) -> ToolExecutionResult:
        nombre = (nuevo_nombre or "").strip().strip('"').strip("'")
        # El nuevo nombre debe ser un nombre simple: sin separadores ni '..',
        # para que no pueda usarse como vector de escape del sandbox.
        if (
            not nombre
            or nombre in (".", "..")
            or "/" in nombre
            or "\\" in nombre
            or os.sep in nombre
            or (os.altsep and os.altsep in nombre)
        ):
            return ToolExecutionResult(
                False,
                "El nuevo nombre debe ser un nombre simple, sin barras ni rutas.",
            )
        try:
            ruta_origen = _resolver_dentro_de_root(ruta)
        except ValueError as e:
            return ToolExecutionResult(False, str(e))

        destino = ruta_origen.parent / nombre
        log.info("[renombrar_archivo] origen=%s -> %s", ruta_origen, destino)
        if not ruta_origen.exists():
            return ToolExecutionResult(
                False, f"El elemento '{ruta_origen.name}' no existe."
            )
        if destino.exists():
            return ToolExecutionResult(
                False, f"Ya existe un elemento llamado '{nombre}'."
            )
        try:
            ruta_origen.rename(destino)
        except OSError as e:
            return ToolExecutionResult(False, f"No se pudo renombrar: {e}")
        return ToolExecutionResult(
            True, f"Renombrado a: {destino}", spoken_message="Elemento renombrado."
        )
