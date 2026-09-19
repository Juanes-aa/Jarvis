"""
actions/system_control.py
Ejecutores reales de acciones del sistema operativo Windows.

Cada funcion corresponde a una herramienta (tool) definida en
brain/tools_schema.py y realiza la accion sobre el sistema.
"""

from __future__ import annotations

import logging
import os
import subprocess
import shutil
import winreg
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


# -- Resultado de accion ------------------------------------------------------

@dataclass
class ActionResult:
    """Resultado de la ejecucion de una accion del sistema.

    'message' es el detalle tecnico completo (para log/historial) y
    'spoken_message' la version corta y natural para decir por voz. Si
    'spoken_message' es None, el pipeline usa 'message' (ver propiedad 'speech').
    """
    success: bool
    message: str
    spoken_message: str | None = None

    @property
    def speech(self) -> str:
        """Texto para TTS: 'spoken_message' o, si falta, 'message'."""
        return self.spoken_message if self.spoken_message else self.message


# -- Mapa de aplicaciones conocidas -------------------------------------------

# Mapeo de nombres coloquiales a comandos/rutas de ejecucion en Windows.
# Las claves estan en minusculas para busqueda case-insensitive.
APP_MAP: dict[str, str | list[str]] = {
    # Utilidades del sistema
    "notepad":       "notepad.exe",
    "bloc de notas": "notepad.exe",
    "calculadora":   "calc.exe",
    "calculator":    "calc.exe",
    "calc":          "calc.exe",
    "cmd":           "cmd.exe",
    "terminal":      "wt.exe",
    "powershell":    "powershell.exe",
    "paint":         "mspaint.exe",
    "explorador":    "explorer.exe",
    "explorer":      "explorer.exe",
    "snipping tool": "SnippingTool.exe",
    "recortes":      "SnippingTool.exe",
    "task manager":  "taskmgr.exe",
    "administrador de tareas": "taskmgr.exe",

    # Configuracion
    "configuracion": "ms-settings:",
    "settings":      "ms-settings:",
    "wifi":          "ms-settings:network-wifi",
    "bluetooth":     "ms-settings:bluetooth",
    "pantalla":      "ms-settings:display",
    "sonido":        "ms-settings:sound",

    # Microsoft Office
    "word":          "winword.exe",
    "excel":         "excel.exe",
    "powerpoint":    "powerpnt.exe",
    "outlook":       "outlook.exe",
    "onenote":       "onenote.exe",

    # Navegadores
    "chrome":        "chrome.exe",
    "google chrome": "chrome.exe",
    "firefox":       "firefox.exe",
    "edge":          "msedge.exe",
    "microsoft edge": "msedge.exe",
    "navegador":     "msedge.exe",
    "brave":         "brave.exe",
    "opera":         "opera.exe",

    # Comunicacion
    "discord":       "discord.exe",
    "teams":         "ms-teams.exe",
    "microsoft teams": "ms-teams.exe",
    "zoom":          "zoom.exe",
    "slack":         "slack.exe",
    "whatsapp":      "whatsapp.exe",
    "telegram":      "telegram.exe",

    # Multimedia y entretenimiento
    "spotify":       "spotify.exe",
    "vlc":           "vlc.exe",
    "steam":         "steam.exe",

    # Desarrollo
    "vscode":        "code.exe",
    "visual studio code": "code.exe",
    "code":          "code.exe",
    "git bash":      "git-bash.exe",
}


# -- Helpers internos ---------------------------------------------------------

def _resolve_from_app_paths(exe_name: str) -> str | None:
    """
    Busca un ejecutable en el registro de Windows (App Paths).

    Muchas apps (Edge, Chrome, etc.) no estan en PATH pero se registran en
    HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\<exe>.
    Este es el mismo mecanismo que usa Win+R / ShellExecute.
    """
    if not exe_name.endswith(".exe"):
        exe_name = f"{exe_name}.exe"

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, None)  # valor (Default)
                if value and os.path.isfile(value):
                    return value
        except OSError:
            continue
    return None


# -- Implementaciones de acciones --------------------------------------------

def abrir_app(nombre: str) -> ActionResult:
    """
    Abre una aplicacion en Windows.

    Estrategia de busqueda:
    1. Busca en APP_MAP por nombre coloquial.
    2. Intenta localizar el ejecutable con shutil.which().
    3. Busca en el registro de Windows (App Paths).
    4. Intenta os.startfile() como ultimo recurso (abre URIs, archivos, etc.).
    """
    nombre_lower = nombre.strip().lower()

    # 1. Buscar en el mapa de apps conocidas
    cmd = APP_MAP.get(nombre_lower)

    if cmd:
        result = _try_launch(cmd, nombre)
        if result:
            return result

    # 2. Buscar ejecutable en PATH
    exe_name = nombre_lower if nombre_lower.endswith(".exe") else f"{nombre_lower}.exe"
    found = shutil.which(exe_name)
    if found:
        result = _try_launch(found, nombre)
        if result:
            return result

    # Si cmd venia del mapa, tambien buscar ese exe en PATH
    if cmd and isinstance(cmd, str) and not cmd.startswith("ms-"):
        found = shutil.which(cmd)
        if found:
            result = _try_launch(found, nombre)
            if result:
                return result

    # 3. Buscar en el registro de Windows (App Paths)
    # Intentar con el exe del mapa primero, luego con el nombre crudo.
    for candidate in dict.fromkeys(filter(None, [cmd if isinstance(cmd, str) else None, exe_name])):
        if candidate.startswith("ms-"):
            continue
        app_path = _resolve_from_app_paths(candidate)
        if app_path:
            log.info("[abrir_app] Encontrado via App Paths: %s -> %s", candidate, app_path)
            result = _try_launch(app_path, nombre)
            if result:
                return result

    # 4. Ultimo recurso: os.startfile (soporta URIs, accesos directos, etc.)
    try:
        os.startfile(nombre)
        return ActionResult(True, f"Abriendo {nombre}...")
    except OSError:
        pass

    return ActionResult(
        False,
        f"No se encontro la aplicacion '{nombre}'. "
        f"Verifica que este instalada y accesible en el sistema.",
    )


def _try_launch(cmd: str | list[str], display_name: str) -> ActionResult | None:
    """
    Intenta lanzar un ejecutable o URI. Retorna ActionResult en exito o error
    fatal, None si solo fue FileNotFoundError (para que el caller siga buscando).
    """
    try:
        # URIs de settings de Windows (ms-settings:, etc.)
        if isinstance(cmd, str) and cmd.startswith("ms-"):
            os.startfile(cmd)
            return ActionResult(True, f"Abriendo {display_name}...")
        # Ejecutable normal
        subprocess.Popen(
            cmd if isinstance(cmd, list) else [cmd],
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return ActionResult(True, f"Abriendo {display_name}...")
    except FileNotFoundError:
        # El ejecutable no se encontro en esta ruta, el caller seguira buscando
        return None
    except OSError as e:
        return ActionResult(False, f"Error al abrir {display_name}: {e}")


def subir_bajar_volumen(
    delta: int | None = None,
    nivel: int | None = None,
) -> ActionResult:
    """
    Ajusta el volumen master del sistema usando pycaw (Core Audio API).

    Soporta dos modos mutuamente excluyentes:
        - nivel: establece el volumen a un valor absoluto (0-100).
        - delta: suma/resta un porcentaje al volumen actual (-100 a 100).

    Args:
        delta: Porcentaje a sumar/restar (-100 a 100).
        nivel: Porcentaje de volumen deseado (0-100).
    """
    if nivel is not None and delta is not None:
        return ActionResult(
            False,
            "Debes especificar nivel o delta, no ambos.",
        )
    if nivel is None and delta is None:
        return ActionResult(
            False,
            "Debes especificar nivel o delta.",
        )

    try:
        from pycaw.pycaw import AudioUtilities

        # pycaw >= 20220416: GetSpeakers() retorna un AudioDevice cuyo
        # .EndpointVolume ya es la interfaz IAudioEndpointVolume activada.
        device = AudioUtilities.GetSpeakers()
        volume = device.EndpointVolume

        # Obtener volumen actual (escala 0.0 - 1.0)
        current = volume.GetMasterVolumeLevelScalar()
        current_pct = round(current * 100)

        # Calcular nuevo volumen (clamped a [0, 100] igual que ajustar_brillo)
        if nivel is not None:
            new_pct = max(0, min(100, nivel))
        else:
            new_pct = max(0, min(100, current_pct + delta))
        volume.SetMasterVolumeLevelScalar(new_pct / 100, None)

        direction = "subido" if new_pct >= current_pct else "bajado"
        return ActionResult(
            True,
            f"Volumen {direction}: {current_pct}% -> {new_pct}%",
        )

    except ImportError:
        return ActionResult(
            False,
            "No se pudo importar pycaw. "
            "Instala con: pip install pycaw",
        )
    except Exception as e:
        return ActionResult(False, f"Error al ajustar el volumen: {e}")


def ajustar_brillo(nivel: int) -> ActionResult:
    """
    Establece el brillo de la pantalla a un nivel absoluto (0-100).

    Args:
        nivel: Porcentaje de brillo deseado (0-100).
    """
    nivel = max(0, min(100, nivel))

    try:
        import screen_brightness_control as sbc

        sbc.set_brightness(nivel)
        return ActionResult(True, f"Brillo ajustado a {nivel}%")

    except ImportError:
        return ActionResult(
            False,
            "No se pudo importar screen_brightness_control. "
            "Instala con: pip install screen-brightness-control",
        )
    except Exception as e:
        # Comun en PCs de escritorio sin soporte DDC/CI o WMI
        return ActionResult(
            False,
            f"No se pudo ajustar el brillo: {e}. "
            f"Tu monitor puede no soportar ajuste de brillo por software.",
        )


# -- Resolucion de carpetas conocidas de Windows -----------------------------

# GUIDs (FOLDERID_*) de las carpetas estandar de Windows. Se resuelven en
# tiempo de ejecucion via SHGetKnownFolderPath, de modo que funcionan aunque el
# usuario haya movido/renombrado la carpeta o tenga Windows en otro idioma. No
# hardcodeamos rutas como "C:\Users\...\Desktop".
_KNOWN_FOLDER_GUIDS: dict[str, str] = {
    "escritorio":  "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",  # Desktop
    "desktop":     "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documentos":  "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",  # Documents
    "documents":   "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "descargas":   "{374DE290-123F-4565-9164-39C4925E467B}",  # Downloads
    "downloads":   "{374DE290-123F-4565-9164-39C4925E467B}",
    "imagenes":    "{33E28130-4E1E-4676-835A-98395C3BC9FF}",  # Pictures
    "imágenes":    "{33E28130-4E1E-4676-835A-98395C3BC9FF}",
    "pictures":    "{33E28130-4E1E-4676-835A-98395C3BC9FF}",
    "fotos":       "{33E28130-4E1E-4676-835A-98395C3BC9FF}",
    "musica":      "{4BD8D571-6D19-48D3-BE97-422220080E43}",  # Music
    "música":      "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "music":       "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "videos":      "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",  # Videos
    "vídeos":      "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}

# Fallbacks (relativos a la carpeta home) por si SHGetKnownFolderPath falla.
_KNOWN_FOLDER_FALLBACKS: dict[str, str] = {
    "escritorio": "Desktop", "desktop": "Desktop",
    "documentos": "Documents", "documents": "Documents",
    "descargas": "Downloads", "downloads": "Downloads",
    "imagenes": "Pictures", "imágenes": "Pictures", "pictures": "Pictures", "fotos": "Pictures",
    "musica": "Music", "música": "Music", "music": "Music",
    "videos": "Videos", "vídeos": "Videos",
}


def _get_known_folder_path(guid_str: str) -> Path | None:
    """Devuelve la ruta real de una carpeta conocida via SHGetKnownFolderPath."""
    try:
        import ctypes
        from ctypes import wintypes
        from uuid import UUID

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

            def __init__(self, uuid_str: str):
                super().__init__()
                u = UUID(uuid_str)
                self.Data1, self.Data2, self.Data3 = (
                    u.time_low, u.time_mid, u.time_hi_version,
                )
                rest = u.bytes[8:]
                self.Data4 = (ctypes.c_ubyte * 8)(*rest)

        SHGetKnownFolderPath = ctypes.windll.shell32.SHGetKnownFolderPath
        SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        ptr = ctypes.c_wchar_p()
        hr = SHGetKnownFolderPath(ctypes.byref(GUID(guid_str)), 0, None, ctypes.byref(ptr))
        if hr != 0 or not ptr.value:
            return None
        path = Path(ptr.value)
        ctypes.windll.ole32.CoTaskMemFree(ptr)
        return path
    except Exception as e:  # noqa: BLE001 - cualquier fallo -> usar fallback
        log.debug("SHGetKnownFolderPath fallo para %s: %s", guid_str, e)
        return None


def _resolver_carpeta_conocida(alias: str) -> Path | None:
    """Traduce un alias en espanol/ingles (ej. 'escritorio') a su ruta real."""
    guid = _KNOWN_FOLDER_GUIDS.get(alias)
    if guid is None:
        return None
    path = _get_known_folder_path(guid)
    if path is not None:
        return path
    # Fallback: carpeta relativa al perfil del usuario.
    return Path.home() / _KNOWN_FOLDER_FALLBACKS.get(alias, alias)


def resolver_ruta_destino(ruta: str) -> tuple[Path | None, str]:
    """
    Resuelve el argumento de creacion de carpeta a una ruta absoluta destino.

    Soporta:
      - Alias de carpetas conocidas como primer segmento:
        'escritorio\\Proyectos' -> <Desktop real>\\Proyectos
      - Rutas absolutas literales: 'C:\\Users\\...\\X' -> tal cual.
      - Nombres/rutas relativas sin alias: 'prueba' -> <perfil de usuario>\\prueba
        (comportamiento por defecto historico, para no romper lo que ya funciona).

    Devuelve (Path destino, "") si se pudo resolver, o (None, mensaje_error).
    """
    arg = (ruta or "").strip().strip('"').strip("'")
    if not arg:
        return None, "No se indicó ninguna carpeta a crear."

    arg = os.path.expandvars(os.path.expanduser(arg))
    arg_norm = arg.replace("/", "\\")

    partes = arg_norm.split("\\", 1)
    primer_segmento = partes[0].strip().lower()
    base_conocida = _resolver_carpeta_conocida(primer_segmento)

    if base_conocida is not None:
        resto = partes[1].strip() if len(partes) > 1 else ""
        if not resto:
            return None, (
                "Debes indicar el nombre de la carpeta a crear dentro de "
                f"'{partes[0].strip()}'."
            )
        target = base_conocida / resto
    else:
        p = Path(arg)
        # Ruta absoluta literal (cualquier ubicacion) o relativa al perfil.
        target = p if p.is_absolute() else Path.home() / p

    try:
        target = target.resolve()
    except Exception:  # noqa: BLE001 - resolve puede fallar en rutas raras
        pass
    return target, ""


def crear_carpeta(ruta: str) -> ActionResult:
    """
    Crea una carpeta (y las intermedias necesarias) de forma segura.

    Ya no se restringe al perfil del usuario: se admite cualquier ruta (alias de
    carpeta conocida, ruta absoluta literal o relativa). La seguridad se apoya en
    (1) que esto SOLO hace mkdir, nunca borra/sobrescribe, y (2) la confirmacion
    por voz DESTRUCTIVE que muestra la ruta resuelta antes de ejecutar.
    """
    target, err = resolver_ruta_destino(ruta)
    if target is None:
        return ActionResult(False, err)
    try:
        target.mkdir(parents=True, exist_ok=True)
        return ActionResult(True, f"Carpeta creada: {target}")
    except Exception as e:
        return ActionResult(False, f"No se pudo crear la carpeta en {target}: {e}")


# -- Allowlist de comandos permitidos ----------------------------------------

# Comandos "libres" de PowerShell estan DESHABILITADOS por seguridad.
# En su lugar, solo se permiten acciones concretas y parametrizadas mapeadas
# a funciones seguras de Python. Todo lo que no este aqui se rechaza.
def _accion_no_permitida(comando: str) -> ActionResult:
    return ActionResult(
        False,
        "Por seguridad no puedo ejecutar comandos arbitrarios de PowerShell. "
        "Solo puedo realizar acciones concretas como abrir apps, ajustar "
        "volumen o brillo, o crear carpetas.",
    )


# Prefijos aceptados para la unica accion permitida: crear carpetas.
_MKDIR_PREFIJOS = ("mkdir ", "md ", "new-item -itemtype directory ")


def _extraer_ruta_mkdir(comando: str) -> str | None:
    """Si 'comando' es un mkdir permitido, devuelve la ruta cruda; si no, None."""
    cmd = (comando or "").strip()
    cmd_lower = cmd.lower()
    for prefijo in _MKDIR_PREFIJOS:
        if cmd_lower.startswith(prefijo):
            ruta = cmd[len(prefijo):].strip().strip('"').strip("'")
            return ruta or None
    return None


def previsualizar_ruta_comando(comando: str) -> Path | None:
    """
    Resuelve (sin crear nada) la ruta destino de un comando mkdir, para mostrarla
    en la confirmacion por voz. Devuelve None si el comando no es un mkdir valido
    o si la ruta no se pudo resolver.
    """
    ruta = _extraer_ruta_mkdir(comando)
    if not ruta:
        return None
    target, _err = resolver_ruta_destino(ruta)
    return target


def ejecutar_comando(comando: str) -> ActionResult:
    """
    Punto de entrada para 'ejecutar_comando'. Ya NO ejecuta PowerShell arbitrario.

    Se intenta mapear la intencion a una accion segura conocida (allowlist).
    Si el comando no corresponde a ninguna accion permitida, se rechaza.

    Args:
        comando: Texto del comando solicitado por el LLM.
    """
    # Allowlist: unica accion permitida -> crear carpetas ('mkdir <ruta>').
    ruta = _extraer_ruta_mkdir(comando)
    if ruta:
        return crear_carpeta(ruta)

    # Cualquier otra cosa se rechaza explicitamente.
    return _accion_no_permitida(comando.strip())


# -- Dispatcher ---------------------------------------------------------------


def execute_tool(tool_name: str, tool_input: dict) -> ActionResult | None:
    """
    Punto de entrada central: dado un nombre de tool y sus parametros, delega
    su ejecucion al Tool Registry.

    Sub-fase 1.1b: TODAS las tools estan migradas al registry, por lo que ya no
    existe el camino legacy (TOOL_DISPATCHER eliminado). Se delega 100% al
    registry (que respeta la allowlist) y se adapta el ToolExecutionResult al
    ActionResult que espera el pipeline.

    Retorna None solo si la tool no esta registrada en absoluto (caso al que el
    pipeline reacciona como "sin ejecucion real"; p.ej. nombres invalidos).
    """
    # Import diferido: evita un ciclo de importacion (tools -> system_control).
    from tools import get_registry

    registry = get_registry()
    if not registry.has(tool_name):
        # No es una herramienta ejecutable (nombre desconocido).
        return None

    result = registry.execute(tool_name, tool_input or {})
    # Se propaga spoken_message para que main.py pueda decir por voz la version
    # corta/natural en vez del message tecnico (rutas, titulos, timestamps...).
    return ActionResult(result.success, result.message, result.spoken_message)
