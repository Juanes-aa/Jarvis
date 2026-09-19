"""
tools/system/command_blacklist.py
Lista negra de patrones catastroficos para la tool 'ejecutar_comando'.

Contexto de seguridad
----------------------
La tool 'ejecutar_comando' es DESTRUCTIVE y dispara confirmacion por voz. El
sandbox de rutas que protege a las tools de archivos (crear_archivo,
mover_archivo, ...) NO aplica a 'ejecutar_comando'. En vez de intentar
sandboxear rutas dentro de comandos de shell (fragil y con falsa sensacion de
seguridad), se opta por una LISTA NEGRA de patrones que pueden causar dano
irreversible al sistema operativo, el disco, el arranque, cuentas de usuario o
procesos criticos.

Contrato
--------
    is_blacklisted(comando) -> (True, razon)  si el comando coincide con un
                                              patron peligroso.
                            -> (False, None)  en caso contrario.

Todo lo que NO caiga en la lista negra sigue el flujo normal (DESTRUCTIVE +
confirmacion por voz), exactamente igual que antes.

Notas de diseno del matching
----------------------------
- Case-insensitive (el comando puede venir con mayusculas/minusculas mezcladas).
- Se aceptan rutas con '/' o '\\' indistintamente ([\\\\/] en los patrones).
- Se usan lookaheads para que el orden de flags/argumentos no importe.
- Los patrones de borrado masivo (del/rd/Remove-Item) SOLO se activan cuando el
  destino es una raiz de unidad, C:\\Windows, C:\\Program Files(( x86)) o
  C:\\Users "a secas" (sin subcarpeta); un subdirectorio concreto NO se bloquea.
- Comandos explicitamente permitidos (apagar/reiniciar/hibernar, suspender,
  bloquear pantalla) NO deben ser atrapados por ningun patron. Ver los tests en
  tests/test_command_blacklist.py.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Fragmento reutilizable: destino "catastrofico" para borrados masivos.
#
# Cubre:
#   - Raiz de una unidad:        C:  C:\  D:/
#   - Directorio de Windows:     C:\Windows (y cualquier subruta suya)
#   - Program Files / (x86):     C:\Program Files , C:\Program Files (x86)
#   - Carpeta de usuarios "a secas" (sin subcarpeta):  C:\Users , C:\Users\
#
# NOTA: un subdirectorio concreto como C:\Users\juan\basura NO coincide, porque
# tras 'users\' hay mas ruta (no fin/espacio/comilla).
# ---------------------------------------------------------------------------
_DESTINO_CATASTROFICO = (
    r"(?:"
    r"[a-z]:[\\/]?(?=[\"'\s]|$)"                              # raiz de unidad
    r"|[a-z]:[\\/]windows(?=[\\/\"'\s]|$)"                    # C:\Windows(...)
    r"|[a-z]:[\\/]program files(?:\s*\(x86\))?(?=[\\/\"'\s]|$)"  # Program Files
    r"|[a-z]:[\\/]users[\\/]?(?=[\"'\s]|$)"                   # C:\Users a secas
    r")"
)

# Lista de nombres de procesos criticos del sistema. Matar cualquiera de estos
# (con o sin .exe) puede colgar o reiniciar Windows. explorer.exe no "mata" el
# sistema, pero deja al usuario sin escritorio/barra de tareas: se incluye.
_PROCESOS_CRITICOS = (
    r"(?:winlogon|csrss|services|lsass|smss|wininit|explorer)(?:\.exe)?"
)


# ---------------------------------------------------------------------------
# Patrones de la lista negra. Cada entrada: (regex, razon legible para el log).
# El primer patron que coincida determina el bloqueo.
# ---------------------------------------------------------------------------
_PATRONES: list[tuple[re.Pattern[str], str]] = [
    # -- Disco y particiones -------------------------------------------------
    (re.compile(r"(?:^|[&|;]\s*)format(?:\.com|\.exe)?\b(?![-\w])", re.I),
     "formateo de disco (format)"),
    (re.compile(r"\bdiskpart\b", re.I),
     "gestion de particiones (diskpart)"),
    (re.compile(r"\bclear-disk\b", re.I),
     "borrado de disco (Clear-Disk)"),
    (re.compile(r"\bremove-partition\b", re.I),
     "borrado de particion (Remove-Partition)"),
    (re.compile(r"\binitialize-disk\b", re.I),
     "reinicializacion de disco (Initialize-Disk)"),
    (re.compile(r"\bformat-volume\b", re.I),
     "formateo de volumen (Format-Volume)"),
    (re.compile(r"\bfsutil\s+file\s+setzerodata\b", re.I),
     "escritura destructiva de archivo (fsutil file setzerodata)"),
    (re.compile(r"\bfsutil\s+volume\s+dismount\b", re.I),
     "modificacion de volumen (fsutil volume dismount)"),

    # -- Arranque del sistema ------------------------------------------------
    (re.compile(r"\bbcdedit\b", re.I),
     "modificacion del gestor de arranque (bcdedit)"),
    (re.compile(r"\bbootrec\b", re.I),
     "reparacion/modificacion de arranque (bootrec)"),
    (re.compile(r"\bbootcfg\b", re.I),
     "modificacion de configuracion de arranque (bootcfg)"),

    # -- Registro de Windows -------------------------------------------------
    (re.compile(r"\breg(?:\.exe)?\s+delete\b", re.I),
     "borrado en el registro (reg delete)"),
    (re.compile(r"\breg(?:\.exe)?\s+import\b", re.I),
     "importacion en el registro (reg import)"),
    (re.compile(r"\bregedit\b[^&|;]*/s\b", re.I),
     "importacion silenciosa al registro (regedit /s)"),

    # -- Puntos de restauracion / shadow copies ------------------------------
    (re.compile(r"\bvssadmin\b[^&|;]*\bdelete\b", re.I),
     "borrado de shadow copies (vssadmin delete)"),
    (re.compile(r"\bwmic\b[^&|;]*\bshadowcopy\b[^&|;]*\bdelete\b", re.I),
     "borrado de shadow copies (wmic shadowcopy delete)"),
    (re.compile(r"\bdisable-computerrestore\b", re.I),
     "desactivacion de restauracion del sistema (Disable-ComputerRestore)"),

    # -- Borrado masivo fuera del sandbox ------------------------------------
    # del/erase/rd/rmdir recursivo (/s) apuntando a un destino catastrofico.
    (re.compile(
        r"\b(?:del|erase|rd|rmdir)\b(?=[^&|;]*/s\b)(?=[^&|;]*" + _DESTINO_CATASTROFICO + r")",
        re.I),
     "borrado recursivo de una ubicacion critica del sistema (del/rd /s)"),
    # PowerShell Remove-Item -Recurse (con o sin -Force) a un destino catastrofico.
    (re.compile(
        r"\bremove-item\b(?=[^&|;]*(?:-recurse|-r\b))(?=[^&|;]*" + _DESTINO_CATASTROFICO + r")",
        re.I),
     "borrado recursivo de una ubicacion critica del sistema (Remove-Item -Recurse)"),
    # robocopy en modo espejo: /MIR o /MIRROR borra el destino para clonarlo.
    (re.compile(r"\brobocopy\b[^&|;]*\s/mir(?:ror)?\b", re.I),
     "clonado en modo espejo que borra el destino (robocopy /MIR)"),

    # -- Cuentas de usuario y permisos ---------------------------------------
    (re.compile(r"\bnet\s+user\b[^&|;]*/delete\b", re.I),
     "eliminacion de cuenta de usuario (net user /delete)"),
    (re.compile(
        r"\bnet\s+localgroup\s+admini(?:strators|stradores)\b[^&|;]*/delete\b",
        re.I),
     "eliminacion del grupo de administradores (net localgroup administrators /delete)"),
    (re.compile(r"\bremove-localuser\b", re.I),
     "eliminacion de usuario local (Remove-LocalUser)"),
    # takeown + icacls en la misma cadena de comando: toma de propiedad + cambio
    # masivo de permisos (patron clasico previo a un borrado o secuestro).
    (re.compile(r"(?=[\s\S]*\btakeown\b)(?=[\s\S]*\bicacls\b)", re.I),
     "toma de propiedad y cambio de permisos combinados (takeown + icacls)"),

    # -- Servicios criticos --------------------------------------------------
    (re.compile(r"\bsc(?:\.exe)?\s+delete\b", re.I),
     "eliminacion de servicio (sc delete)"),
    (re.compile(r"\bsc(?:\.exe)?\s+config\b", re.I),
     "reconfiguracion de servicio (sc config)"),

    # -- Procesos criticos ---------------------------------------------------
    (re.compile(
        r"\btaskkill\b(?=[^&|;]*/f\b)(?=[^&|;]*\b" + _PROCESOS_CRITICOS + r"\b)",
        re.I),
     "terminacion forzada de un proceso critico del sistema (taskkill /f)"),
    (re.compile(
        r"\bwmic\b(?=[^&|;]*\bprocess\b)(?=[^&|;]*\bcall\b)"
        r"(?=[^&|;]*\bterminate\b)(?=[^&|;]*\b" + _PROCESOS_CRITICOS + r"\b)",
        re.I),
     "terminacion de un proceso critico del sistema (wmic process call terminate)"),
]


def is_blacklisted(comando: str) -> tuple[bool, str | None]:
    """
    Determina si 'comando' coincide con algun patron catastrofico.

    Args:
        comando: Texto del comando de shell a evaluar.

    Returns:
        (True, razon) si el comando esta en la lista negra (razon describe el
        patron activado, util para logs de seguridad); (False, None) si no.
    """
    if not comando:
        return False, None

    texto = comando.strip()
    for patron, razon in _PATRONES:
        if patron.search(texto):
            return True, razon
    return False, None
