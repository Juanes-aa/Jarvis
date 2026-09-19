"""
ui/status_controller.py
Máquina de estados mínima y thread-safe para la UI de Jarvis.

Es el punto ÚNICO de verdad para el estado visual. La lógica de voz llama a
`set_state(...)` / `toggle_paused()` y los observers (ícono de bandeja e
indicador flotante) reaccionan. No contiene ninguna lógica de audio.
"""

from __future__ import annotations

import enum
import logging
import threading
from typing import Callable


class AppState(enum.Enum):
    """Estados de la interacción de voz (dentro del modo 'activo')."""
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"


# Un observer recibe (state, paused) en cada cambio y decide cómo reaccionar.
Observer = Callable[[AppState, bool], None]


class StatusController:
    """
    Mantiene el estado actual (`AppState`) y una capa de pausa (`paused`)
    independiente y con prioridad visual. Notifica a los observers registrados
    cada vez que algo cambia.
    """

    def __init__(self) -> None:
        self._state = AppState.IDLE
        self._paused = False
        self._observers: list[Observer] = []
        self._lock = threading.RLock()
        self._logger = logging.getLogger(__name__)

    # -- Registro de observers --------------------------------------------------

    def add_observer(self, observer: Observer) -> None:
        """Registra un callback que se llamará en cada cambio de estado/pausa."""
        with self._lock:
            self._observers.append(observer)
        # Notificar el estado actual al recién registrado para que se sincronice.
        self._notify_one(observer)

    def _notify_all(self) -> None:
        with self._lock:
            observers = list(self._observers)
            state, paused = self._state, self._paused
        for obs in observers:
            self._safe_call(obs, state, paused)

    def _notify_one(self, observer: Observer) -> None:
        with self._lock:
            state, paused = self._state, self._paused
        self._safe_call(observer, state, paused)

    def _safe_call(self, observer: Observer, state: AppState, paused: bool) -> None:
        try:
            observer(state, paused)
        except Exception as e:  # Un observer roto no debe tumbar la app
            self._logger.error("Observer de estado falló: %s", e, exc_info=True)

    # -- Mutadores de estado ----------------------------------------------------

    def set_state(self, state: AppState) -> None:
        """Cambia el estado de interacción. No afecta la pausa."""
        with self._lock:
            if self._state == state:
                return
            self._state = state
        self._notify_all()

    def set_paused(self, paused: bool) -> None:
        """Activa/desactiva la pausa (capa con prioridad visual)."""
        with self._lock:
            if self._paused == paused:
                return
            self._paused = paused
        self._logger.info("Estado de pausa -> %s", paused)
        self._notify_all()

    def toggle_paused(self) -> bool:
        """Invierte la pausa. Devuelve el nuevo valor de `paused`."""
        with self._lock:
            new_value = not self._paused
        self.set_paused(new_value)
        return new_value

    # -- Lectores ---------------------------------------------------------------

    @property
    def state(self) -> AppState:
        with self._lock:
            return self._state

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def visual_key(self) -> str:
        """
        Clave de color/ícono a mostrar: 'paused' tiene prioridad sobre el
        estado de interacción actual.
        """
        with self._lock:
            return "paused" if self._paused else self._state.value
