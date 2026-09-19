"""
tools/system/window_control.py
Tools de control de ventanas de Windows mediante pywin32 (win32gui).

Fase 1.2. Tres tools:
- minimizar_ventana_activa()        -> SAFE
- cambiar_ventana(direccion=...)    -> SAFE
- cerrar_ventana_activa()           -> REVERSIBLE + requires_confirmation

Se usa pywin32 (no pygetwindow) por dar control nativo mas directo de Windows
(HWND, mensajes de ventana, foco). Cerrar la ventana activa se marca como
REVERSIBLE (no destruye datos a nivel de sistema) pero con requires_confirmation
= True, porque puede perder trabajo no guardado en la app cerrada: main.py pide
confirmacion por voz aunque no sea DESTRUCTIVE (ver Tool.requires_confirmation).
"""

from __future__ import annotations

import logging

from tools.base import RiskLevel, Tool, ToolExecutionResult
from tools.registry import register_tool

log = logging.getLogger(__name__)

_PYWIN32_FALTA = (
    "No se pudo importar pywin32 (win32gui). Instala con: pip install pywin32"
)


def _ventanas_visibles(win32gui) -> list[int]:
    """Lista de HWND de ventanas de nivel superior visibles y con titulo."""
    ventanas: list[int] = []

    def _callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).strip():
            ventanas.append(hwnd)
        return True

    win32gui.EnumWindows(_callback, None)
    return ventanas


def _foreground_alt(hwnd, win32api, win32con, win32gui) -> None:
    """
    Truco del Alt simulado: Windows relaja el foreground lock si detecta una
    interaccion de teclado justo antes de SetForegroundWindow. Enviamos un
    Alt down/up sinteticos rodeando la llamada.
    """
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)  # Alt down
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    finally:
        win32api.keybd_event(
            win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0
        )  # Alt up


def _foreground_attach(hwnd, win32api, win32con, win32gui, win32process) -> None:
    """
    Fallback mas robusto: adjunta temporalmente el input thread de Jarvis al de
    la ventana en foco actual (AttachThreadInput), fuerza el foco y siempre
    desadjunta en el finally, incluso si SetForegroundWindow falla.
    """
    fg = win32gui.GetForegroundWindow()
    remoto, _ = win32process.GetWindowThreadProcessId(fg)
    actual = win32api.GetCurrentThreadId()

    # AttachThreadInput da error 87 si los threads coinciden o el remoto es 0;
    # en ese caso no hay nada que adjuntar, vamos directos.
    if not remoto or remoto == actual:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return

    win32process.AttachThreadInput(remoto, actual, True)
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    finally:
        win32process.AttachThreadInput(remoto, actual, False)


def _forzar_foreground(hwnd) -> None:
    """
    Trae una ventana al frente sorteando el foreground lock de Windows: un
    proceso en segundo plano no puede robar el foco con SetForegroundWindow a
    secas. Intenta primero el truco del Alt simulado (mas simple) y, si falla,
    el workaround AttachThreadInput. Si ambos fallan, propaga la ultima
    excepcion para que la tool devuelva un error claro.
    """
    import win32api
    import win32con
    import win32gui
    import win32process

    if win32gui.GetForegroundWindow() == hwnd:
        return

    try:
        _foreground_alt(hwnd, win32api, win32con, win32gui)
        if win32gui.GetForegroundWindow() == hwnd:
            return
    except Exception:  # noqa: BLE001 - probamos el siguiente metodo
        pass

    # Fallback: AttachThreadInput. Si esto tambien lanza, dejamos que suba.
    _foreground_attach(hwnd, win32api, win32con, win32gui, win32process)


@register_tool
class MinimizarVentanaActivaTool(Tool):
    name = "minimizar_ventana_activa"
    description = (
        "Minimiza la ventana que esta actualmente en primer plano (la ventana "
        "activa)."
    )
    parameters = {"type": "object", "properties": {}}
    risk_level = RiskLevel.SAFE

    def execute(self) -> ToolExecutionResult:
        log.info("[minimizar_ventana_activa] ejecutando")
        try:
            import win32con
            import win32gui
        except ImportError:
            return ToolExecutionResult(False, _PYWIN32_FALTA)
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return ToolExecutionResult(False, "No hay ninguna ventana activa.")
            titulo = win32gui.GetWindowText(hwnd)
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        except Exception as e:  # noqa: BLE001 - frontera de error de la tool
            return ToolExecutionResult(False, f"No se pudo minimizar la ventana: {e}")
        # message: detalle tecnico con el titulo completo (util para debug/log).
        # spoken_message: frase corta y segura; NO repite el titulo, que suele
        # traer basura no hablable (IDs de perfil, "N paginas mas", etc.).
        return ToolExecutionResult(
            True,
            f"Ventana minimizada: {titulo or 'activa'}",
            spoken_message="Ventana minimizada.",
        )


@register_tool
class CambiarVentanaTool(Tool):
    name = "cambiar_ventana"
    description = (
        "Cambia el foco a otra ventana abierta, en orden. Usa 'siguiente' para "
        "ir a la proxima ventana o 'anterior' para volver a la previa."
    )
    parameters = {
        "type": "object",
        "properties": {
            "direccion": {
                "type": "string",
                "enum": ["siguiente", "anterior"],
                "description": "Direccion del cambio de ventana.",
            },
        },
    }
    risk_level = RiskLevel.SAFE

    def execute(self, direccion: str = "siguiente") -> ToolExecutionResult:
        log.info("[cambiar_ventana] direccion=%s", direccion)
        dir_norm = (direccion or "siguiente").strip().lower()
        if dir_norm not in ("siguiente", "anterior"):
            return ToolExecutionResult(
                False, "La direccion debe ser 'siguiente' o 'anterior'."
            )
        try:
            import win32gui
        except ImportError:
            return ToolExecutionResult(False, _PYWIN32_FALTA)
        try:
            ventanas = _ventanas_visibles(win32gui)
            if len(ventanas) < 2:
                return ToolExecutionResult(
                    False, "No hay suficientes ventanas abiertas para cambiar."
                )
            actual = win32gui.GetForegroundWindow()
            try:
                idx = ventanas.index(actual)
            except ValueError:
                idx = 0
            paso = 1 if dir_norm == "siguiente" else -1
            objetivo = ventanas[(idx + paso) % len(ventanas)]
            titulo = win32gui.GetWindowText(objetivo)
            _forzar_foreground(objetivo)
        except Exception as e:  # noqa: BLE001 - frontera de error de la tool
            return ToolExecutionResult(False, f"No se pudo cambiar de ventana: {e}")
        return ToolExecutionResult(True, f"Ventana activa: {titulo or 'sin titulo'}")


@register_tool
class CerrarVentanaActivaTool(Tool):
    name = "cerrar_ventana_activa"
    description = (
        "Cierra la ventana que esta actualmente en primer plano (la ventana "
        "activa). Puede pedir confirmacion antes de cerrar, ya que la app podria "
        "tener trabajo sin guardar."
    )
    parameters = {"type": "object", "properties": {}}
    # No destruye datos a nivel de sistema (REVERSIBLE), pero puede perder
    # trabajo no guardado, por eso pide confirmacion por voz explicitamente.
    risk_level = RiskLevel.REVERSIBLE
    requires_confirmation = True
    # Frase legible para el prompt de confirmacion (evita leer el nombre crudo
    # 'cerrar_ventana_activa' con guiones bajos).
    confirmation_label = "cerrar la ventana activa"

    def execute(self) -> ToolExecutionResult:
        log.info("[cerrar_ventana_activa] ejecutando")
        try:
            import win32con
            import win32gui
        except ImportError:
            return ToolExecutionResult(False, _PYWIN32_FALTA)
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return ToolExecutionResult(False, "No hay ninguna ventana activa.")
            titulo = win32gui.GetWindowText(hwnd)
            # WM_CLOSE respeta los dialogos de guardado de la app (no la mata).
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception as e:  # noqa: BLE001 - frontera de error de la tool
            return ToolExecutionResult(False, f"No se pudo cerrar la ventana: {e}")
        # message: titulo completo para el log; spoken_message: frase corta.
        return ToolExecutionResult(
            True,
            f"Ventana cerrada: {titulo or 'activa'}",
            spoken_message="Ventana cerrada.",
        )
