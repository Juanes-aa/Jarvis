"""
ui/tray_icon.py
Ícono de bandeja del sistema (system tray) para Jarvis usando pystray + Pillow.

- Refleja el estado actual con un ícono de color distinto por estado
  (idle / escuchando / procesando / hablando) y una variante "pausado"
  (oscura + diagonal) que tiene prioridad visual.
- Menú de clic derecho dinámico: Pausar/Reanudar, Ver logs, Salir.
- Corre `icon.run()` en su propio thread para no bloquear el loop de voz.

Prueba aislada:
    python -m ui.tray_icon
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable

from PIL import Image, ImageDraw

import config
from ui.status_controller import AppState, StatusController


def _make_icon_image(color: tuple[int, int, int], paused: bool = False) -> Image.Image:
    """
    Genera un ícono placeholder: un círculo de color. Si `paused`, lo dibuja
    apagado y con una diagonal tipo símbolo de "muted".
    """
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = 6
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color + (255,),
        outline=(255, 255, 255, 60),
        width=2,
    )

    if paused:
        # Diagonal cruzando el círculo para indicar "silenciado / en pausa".
        draw.line(
            [margin + 4, size - margin - 4, size - margin - 4, margin + 4],
            fill=(230, 230, 230, 230),
            width=6,
        )

    return img


class TrayIcon:
    """
    Envuelve un `pystray.Icon`. Se registra como observer del StatusController
    para actualizar el ícono ante cambios de estado/pausa.
    """

    def __init__(
        self,
        status: StatusController,
        on_quit: Callable[[], None] | None = None,
        log_file: os.PathLike | str | None = None,
    ) -> None:
        self._status = status
        self._on_quit = on_quit
        self._log_file = str(log_file) if log_file is not None else str(config.LOG_FILE)
        self._logger = logging.getLogger(__name__)

        self._icon = None  # type: ignore[assignment]
        self._thread: threading.Thread | None = None
        self._images: dict[str, Image.Image] = self._build_images()

    def _build_images(self) -> dict[str, Image.Image]:
        images: dict[str, Image.Image] = {}
        for key, color in config.STATE_COLORS.items():
            images[key] = _make_icon_image(color, paused=(key == "paused"))
        return images

    # -- Callbacks del menú -----------------------------------------------------

    def _toggle_pause(self, icon, item) -> None:  # noqa: ANN001 (firma pystray)
        self._status.toggle_paused()
        # Refrescar el menú para que cambie el texto Pausar/Reanudar.
        icon.update_menu()

    def _view_logs(self, icon, item) -> None:  # noqa: ANN001
        try:
            os.startfile(self._log_file)  # type: ignore[attr-defined]  # Windows
        except Exception as e:
            self._logger.error("No se pudo abrir el log '%s': %s", self._log_file, e)

    def _quit(self, icon, item) -> None:  # noqa: ANN001
        self._logger.info("Salir solicitado desde el ícono de bandeja.")
        try:
            icon.stop()
        except Exception:
            pass
        if self._on_quit is not None:
            self._on_quit()

    def _pause_label(self, item) -> str:  # noqa: ANN001
        return "Reanudar" if self._status.is_paused() else "Pausar"

    def _build_menu(self):
        import pystray

        return pystray.Menu(
            pystray.MenuItem(self._pause_label, self._toggle_pause),
            pystray.MenuItem("Ver logs", self._view_logs),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Salir", self._quit),
        )

    # -- Observer del StatusController -----------------------------------------

    def on_status_change(self, state: AppState, paused: bool) -> None:
        """Actualiza el ícono según el estado visual efectivo."""
        if self._icon is None:
            return
        key = "paused" if paused else state.value
        self._icon.icon = self._images.get(key, self._images["idle"])
        try:
            self._icon.update_menu()
        except Exception:
            pass

    # -- Ciclo de vida ----------------------------------------------------------

    def start(self) -> None:
        """Crea el ícono y lo corre en un thread propio (no bloqueante)."""
        import pystray

        self._icon = pystray.Icon(
            "jarvis",
            icon=self._images["idle"],
            title="Jarvis",
            menu=self._build_menu(),
        )
        self._status.add_observer(self.on_status_change)

        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass


if __name__ == "__main__":
    # Prueba aislada: levanta solo el tray con un StatusController de juguete
    # y cicla estados para ver los cambios de ícono.
    import itertools
    import time

    logging.basicConfig(level=logging.INFO, format=config.LOG_FORMAT)

    status = StatusController()
    tray = TrayIcon(status, on_quit=lambda: os._exit(0))
    tray.start()

    print("Tray de prueba activo. Clic derecho para el menú. Ctrl+C para salir.")
    states = itertools.cycle(
        [AppState.IDLE, AppState.LISTENING, AppState.PROCESSING, AppState.SPEAKING]
    )
    try:
        while True:
            status.set_state(next(states))
            time.sleep(2.0)
    except KeyboardInterrupt:
        tray.stop()
