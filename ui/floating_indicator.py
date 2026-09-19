"""
ui/floating_indicator.py
Indicador visual flotante tipo Siri (EXPLORATORIO) con PySide6.

Ventana pequeña, sin bordes, siempre encima, fondo transparente, en la esquina
inferior derecha. Dibuja un pulso/ondas animadas cuyo color depende del estado.

Diseño desacoplable a propósito: la interfaz pública es solo `show(state)` y
`hide()`. Se registra como observer del StatusController vía `on_status_change`.
Como Qt exige que la UI viva en el thread principal, los cambios que llegan
desde threads de voz se marshalean con una Signal (conexión en cola).

Si más adelante se descarta, basta con no crear este objeto en main.py y poner
`FLOATING_INDICATOR_ENABLED = False`.

Prueba aislada:
    python -m ui.floating_indicator
"""

from __future__ import annotations

import logging
import math

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QGuiApplication
from PySide6.QtWidgets import QWidget

import config
from ui.status_controller import AppState


# Estados en los que el indicador es visible. En IDLE (y pausado) se oculta.
_VISIBLE_STATES = {AppState.LISTENING, AppState.PROCESSING, AppState.SPEAKING}


class FloatingIndicator(QWidget):
    """Widget flotante con animación de pulso. Vive en el thread de Qt."""

    # Señal para actualizar estado desde cualquier thread de forma segura.
    _state_changed = Signal(str, bool)

    def __init__(self, width: int = 140, height: int = 140, margin: int = 40) -> None:
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self._w = width
        self._h = height
        self._margin = margin
        self._phase = 0.0
        self._current_key = "idle"

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.resize(self._w, self._h)
        self._position_bottom_right()

        # Timer de animación (~30 FPS).
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        # Conectar la señal (thread-safe) al slot que aplica el estado.
        self._state_changed.connect(self._apply_state)

    # -- Posicionamiento --------------------------------------------------------

    def _position_bottom_right(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.right() - self._w - self._margin
        y = geo.bottom() - self._h - self._margin
        self.move(x, y)

    # -- Animación --------------------------------------------------------------

    def _tick(self) -> None:
        self._phase += 0.12
        if self._phase > math.tau:
            self._phase -= math.tau
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001 (firma Qt)
        color = config.STATE_COLORS.get(self._current_key, config.STATE_COLORS["idle"])
        base = QColor(*color)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        cx, cy = self._w / 2, self._h / 2
        max_r = min(self._w, self._h) / 2 - 4

        # Ondas concéntricas que laten con desfase.
        rings = 3
        for i in range(rings):
            t = (self._phase / math.tau + i / rings) % 1.0
            radius = max_r * t
            alpha = int(160 * (1.0 - t))
            if alpha <= 0:
                continue
            ring = QColor(base.red(), base.green(), base.blue(), alpha)
            painter.setBrush(ring)
            painter.drawEllipse(
                int(cx - radius), int(cy - radius),
                int(radius * 2), int(radius * 2),
            )

        # Núcleo central pulsante.
        core_r = max_r * (0.28 + 0.06 * math.sin(self._phase))
        core = QColor(base.red(), base.green(), base.blue(), 235)
        painter.setBrush(core)
        painter.drawEllipse(
            int(cx - core_r), int(cy - core_r),
            int(core_r * 2), int(core_r * 2),
        )
        painter.end()

    # -- Interfaz pública (llamable desde el thread de Qt) ----------------------

    def show_state(self, state: AppState) -> None:
        """Muestra el indicador con el color del estado dado."""
        self._current_key = state.value
        if not self._timer.isActive():
            self._timer.start(33)
        if not self.isVisible():
            self.show()
        self.raise_()
        self.update()

    def hide_indicator(self) -> None:
        """Oculta el indicador y detiene la animación."""
        self._timer.stop()
        self.hide()

    # -- Slot interno + observer thread-safe ------------------------------------

    def _apply_state(self, state_value: str, paused: bool) -> None:
        """Slot ejecutado en el thread de Qt. Decide mostrar/ocultar."""
        try:
            state = AppState(state_value)
        except ValueError:
            state = AppState.IDLE

        if paused or state not in _VISIBLE_STATES:
            self.hide_indicator()
        else:
            self.show_state(state)

    def on_status_change(self, state: AppState, paused: bool) -> None:
        """
        Observer registrable en StatusController. Puede llamarse desde CUALQUIER
        thread: solo emite la señal, que Qt entrega en el thread de la GUI.
        """
        self._state_changed.emit(state.value, paused)


if __name__ == "__main__":
    # Prueba aislada: cicla estados para ver la animación sin levantar Jarvis.
    import sys
    from PySide6.QtWidgets import QApplication

    logging.basicConfig(level=logging.INFO, format=config.LOG_FORMAT)

    app = QApplication(sys.argv)
    indicator = FloatingIndicator()

    cycle = [
        (AppState.LISTENING, False),
        (AppState.PROCESSING, False),
        (AppState.SPEAKING, False),
        (AppState.IDLE, False),
    ]
    idx = {"i": 0}

    def _demo() -> None:
        state, paused = cycle[idx["i"] % len(cycle)]
        idx["i"] += 1
        indicator.on_status_change(state, paused)

    demo_timer = QTimer()
    demo_timer.timeout.connect(_demo)
    demo_timer.start(2000)
    _demo()

    print("Indicador de prueba activo. Ctrl+C para salir.")
    sys.exit(app.exec())
