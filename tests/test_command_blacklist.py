"""
tests/test_command_blacklist.py
Pruebas de la lista negra de comandos catastroficos (is_blacklisted) y de que
la tool 'ejecutar_comando' bloquea con WARNING sin ejecutar la accion real.

Importa solo funciones puras / la clase de la tool; no inicializa audio ni Groq.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.system.command_blacklist import is_blacklisted


# ---------------------------------------------------------------------------
# 1) Cada categoria de la lista negra debe bloquearse (al menos un ejemplo).
# ---------------------------------------------------------------------------

def test_disco_y_particiones():
    for cmd in [
        "format C:",
        "format /q /fs:ntfs D:",
        "diskpart",
        "Clear-Disk -Number 0",
        "Remove-Partition -DriveLetter D",
        "Initialize-Disk -Number 1",
        "Format-Volume -DriveLetter D",
        "fsutil file setzerodata offset=0 length=1000 archivo.txt",
        "fsutil volume dismount C:",
    ]:
        blocked, reason = is_blacklisted(cmd)
        assert blocked, f"deberia bloquearse: {cmd!r}"
        assert reason


def test_arranque_del_sistema():
    for cmd in ["bcdedit /set", "bootrec /fixmbr", "bootcfg /rebuild"]:
        blocked, _ = is_blacklisted(cmd)
        assert blocked, f"deberia bloquearse: {cmd!r}"


def test_registro_de_windows():
    for cmd in [
        r"reg delete HKLM\SOFTWARE\Foo /f",
        "reg import backup.reg",
        "regedit /s payload.reg",
    ]:
        blocked, _ = is_blacklisted(cmd)
        assert blocked, f"deberia bloquearse: {cmd!r}"


def test_shadow_copies_y_restauracion():
    for cmd in [
        "vssadmin delete shadows /all /quiet",
        "wmic shadowcopy delete",
        "Disable-ComputerRestore C:\\",
    ]:
        blocked, _ = is_blacklisted(cmd)
        assert blocked, f"deberia bloquearse: {cmd!r}"


def test_borrado_masivo_destinos_criticos():
    for cmd in [
        "del /s /q C:\\",
        "rd /s /q C:\\Windows",
        "rmdir /s C:\\Program Files",
        "rd /s /q C:\\Program Files (x86)",
        "del /s /q C:\\Users",
        "rd /s /q D:\\",
        "rd /s /q C:/Windows",  # con barras normales
        "Remove-Item -Recurse -Force C:\\Windows",
        "Remove-Item -Recurse C:\\",
        "robocopy C:\\datos D:\\backup /MIR",
        "robocopy C:\\datos D:\\backup /MIRROR",
    ]:
        blocked, _ = is_blacklisted(cmd)
        assert blocked, f"deberia bloquearse: {cmd!r}"


def test_cuentas_y_permisos():
    for cmd in [
        "net user juan /delete",
        "net localgroup administrators juan /delete",
        "net localgroup administradores juan /delete",
        "Remove-LocalUser -Name juan",
        "takeown /f C:\\Windows /r && icacls C:\\Windows /grant todos:F",
    ]:
        blocked, _ = is_blacklisted(cmd)
        assert blocked, f"deberia bloquearse: {cmd!r}"


def test_servicios_criticos():
    for cmd in ["sc delete WinDefend", "sc config wuauserv start= disabled"]:
        blocked, _ = is_blacklisted(cmd)
        assert blocked, f"deberia bloquearse: {cmd!r}"


def test_procesos_criticos():
    for cmd in [
        "taskkill /f /im lsass.exe",
        "taskkill /im winlogon.exe /f",
        "taskkill /f /im explorer.exe",
        "wmic process where name='lsass.exe' call terminate",
    ]:
        blocked, _ = is_blacklisted(cmd)
        assert blocked, f"deberia bloquearse: {cmd!r}"


# ---------------------------------------------------------------------------
# 2) Comandos explicitamente permitidos: NO deben bloquearse.
# ---------------------------------------------------------------------------

def test_apagar_reiniciar_hibernar_no_se_bloquean():
    for cmd in [
        "shutdown /s /t 0",
        "shutdown /r /t 0",
        "shutdown /h",
        "shutdown -s -t 60",
    ]:
        blocked, reason = is_blacklisted(cmd)
        assert not blocked, f"NO deberia bloquearse: {cmd!r} (razon: {reason})"


def test_suspender_y_bloquear_pantalla_no_se_bloquean():
    for cmd in [
        "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
        "rundll32.exe user32.dll,LockWorkStation",
    ]:
        blocked, reason = is_blacklisted(cmd)
        assert not blocked, f"NO deberia bloquearse: {cmd!r} (razon: {reason})"


# ---------------------------------------------------------------------------
# 3) Comandos normales no listados: NO se bloquean (siguen el flujo normal).
# ---------------------------------------------------------------------------

def test_comandos_normales_no_se_bloquean():
    for cmd in [
        "mkdir escritorio\\pruebas",
        "mkdir C:\\Users\\juan\\Proyectos",   # subcarpeta concreta, no la raiz
        "md documentos\\Fotos",
        "ping 8.8.8.8",
        "pip install requests",
        "winget install Notepad++",
        "del /s /q C:\\Users\\juan\\basura",  # subcarpeta concreta -> permitido
        "Remove-Item C:\\Users\\juan\\temp\\log.txt",  # sin -Recurse ni raiz
        "Get-Process | Format-Table",         # Format-Table no es Format-Volume
        "robocopy C:\\a D:\\b /E",            # copia sin espejo
        "taskkill /f /im notepad.exe",        # proceso no critico
        "net user juan",                      # listar, no /delete
        "regedit",                            # abrir GUI, sin /s
    ]:
        blocked, reason = is_blacklisted(cmd)
        assert not blocked, f"NO deberia bloquearse: {cmd!r} (razon: {reason})"


# ---------------------------------------------------------------------------
# 4) La tool 'ejecutar_comando' bloquea, loggea WARNING y NO ejecuta la accion.
# ---------------------------------------------------------------------------

def test_tool_bloquea_y_loggea_warning(caplog=None):
    from tools.system.ejecutar_comando import EjecutarComandoTool

    tool = EjecutarComandoTool()

    # Capturamos los logs a nivel WARNING manualmente (sin depender de pytest).
    records: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("tools.system.ejecutar_comando")
    handler = _Handler(level=logging.WARNING)
    logger.addHandler(handler)
    prev_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        result = tool.execute("format C:")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)

    assert result.success is False
    assert "bloqueado" in result.message.lower()
    warnings = [r for r in records if r.levelno == logging.WARNING]
    assert warnings, "deberia haberse emitido un log WARNING al bloquear"
    assert "format C:" in warnings[0].getMessage()


def test_tool_no_bloquea_comando_normal_llega_a_la_accion():
    # Un comando no listado en la lista negra NO se bloquea por seguridad: la
    # tool delega a la accion real. Usamos 'ping' (que la allowlist interna
    # rechaza) para comprobar, sin efectos secundarios en disco, que el comando
    # atraveso el gate de la lista negra y llego a la accion: el mensaje NO es el
    # de bloqueo de seguridad, sino el de "comandos arbitrarios no permitidos".
    from tools.system.ejecutar_comando import EjecutarComandoTool

    tool = EjecutarComandoTool()
    result = tool.execute("ping 8.8.8.8")
    assert result.success is False
    assert "bloqueado por seguridad" not in result.message.lower()
    assert "arbitrarios" in result.message.lower()


if __name__ == "__main__":
    funcs = [v for k, v in globals().items() if k.startswith("test_")]
    passed = failed = 0
    for f in funcs:
        try:
            f()
            print(f"  OK {f.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {f.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
