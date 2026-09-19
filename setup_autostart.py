"""
setup_autostart.py
Script de configuración para autoarranque de Jarvis al iniciar Windows.

Uso:
    python setup_autostart.py --enable     # Activa el inicio automático
    python setup_autostart.py --disable    # Desactiva el inicio automático
    python setup_autostart.py --status     # Consulta el estado actual
"""

import argparse
import os
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).parent.resolve()
MAIN_SCRIPT = PROJECT_DIR / "main.py"
STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
LAUNCHER_VBS = STARTUP_DIR / "Jarvis_AutoStart.vbs"


def get_pythonw_executable() -> Path:
    """Obtiene la ruta de pythonw.exe (interprete sin ventana de consola)."""
    python_exe = Path(sys.executable)
    pythonw_exe = python_exe.parent / "pythonw.exe"
    if not pythonw_exe.exists():
        # Fallback: si no existe pythonw.exe usamos python.exe
        pythonw_exe = python_exe
    return pythonw_exe


def enable_autostart() -> None:
    """Configura el inicio automático creando un script VBS en la carpeta de Inicio."""
    if not STARTUP_DIR.exists():
        print(f"[-] Error: No se encontró la carpeta de inicio de Windows: {STARTUP_DIR}")
        return

    pythonw_exe = get_pythonw_executable()

    # VBScript que arranca pythonw.exe en segundo plano totalmente silencioso.
    # Estilo de ventana 0 = oculta; False = no esperar a que termine.
    # En VBScript las comillas literales se duplican ("").
    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.CurrentDirectory = "{PROJECT_DIR}"\n'
        f'WshShell.Run """{pythonw_exe}"" ""{MAIN_SCRIPT}""", 0, False\n'
    )
    try:
        with open(LAUNCHER_VBS, "w", encoding="utf-8") as f:
            f.write(vbs_content)
        print("[+] Inicio automático ACTIVADO con éxito.")
        print(f"    Archivo creado en: {LAUNCHER_VBS}")
        print(f"    Jarvis se iniciará automáticamente cada vez que inicies sesión en Windows.")
    except Exception as e:
        print(f"[-] Error al configurar inicio automático: {e}")


def disable_autostart() -> None:
    """Elimina el acceso directo de inicio automático."""
    if LAUNCHER_VBS.exists():
        try:
            LAUNCHER_VBS.unlink()
            print("[+] Inicio automático DESACTIVADO.")
        except Exception as e:
            print(f"[-] Error al eliminar {LAUNCHER_VBS}: {e}")
    else:
        print("[*] El inicio automático ya estaba desactivado.")

    # Limpieza de un posible run_jarvis.bat de versiones anteriores
    legacy_bat = PROJECT_DIR / "run_jarvis.bat"
    if legacy_bat.exists():
        try:
            legacy_bat.unlink()
        except Exception:
            pass


def check_status() -> None:
    """Comprueba si el inicio automático está activo."""
    if LAUNCHER_VBS.exists():
        print("[+] Estado: Inicio automático ACTIVO")
        print(f"    Ubicación: {LAUNCHER_VBS}")
    else:
        print("[-] Estado: Inicio automático INACTIVO")


def main() -> None:
    parser = argparse.ArgumentParser(description="Configuración de Autoarranque para Jarvis en Windows.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--enable", action="store_true", help="Activar inicio automático al encender Windows")
    group.add_argument("--disable", action="store_true", help="Desactivar inicio automático")
    group.add_argument("--status", action="store_true", help="Ver estado actual del inicio automático")

    args = parser.parse_args()

    if args.enable:
        enable_autostart()
    elif args.disable:
        disable_autostart()
    elif args.status:
        check_status()


if __name__ == "__main__":
    main()
