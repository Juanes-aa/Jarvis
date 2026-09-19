"""
activation/hotkey.py
Escuchador global de teclado para activar la interacción por voz en Jarvis.
"""

from __future__ import annotations

import threading
from typing import Callable
from pynput import keyboard


class HotkeyListener:
    """
    Escucha globalmente una combinación de teclas (hotkey) en segundo plano
    y ejecuta un callback al ser presionada.
    """

    def __init__(
        self,
        on_trigger: Callable[[], None],
        hotkey_combo: str = "<ctrl>+<alt>+j",
    ) -> None:
        """
        Args:
            on_trigger: Función a ejecutar cuando se presiona la combinación.
            hotkey_combo: Combinación de teclas en formato pynput (ej. '<ctrl>+<alt>+j').
        """
        self.on_trigger = on_trigger
        self.hotkey_combo = hotkey_combo
        self._listener: keyboard.GlobalHotKeys | None = None
        self._is_running = False
        self._lock = threading.Lock()

    def _wrapped_callback(self) -> None:
        """Envuelve la ejecución del callback para evitar bloqueos del listener."""
        threading.Thread(target=self.on_trigger, daemon=True).start()

    def start(self) -> None:
        """Inicia el escuchador en un hilo en segundo plano."""
        with self._lock:
            if self._is_running:
                return

            self._listener = keyboard.GlobalHotKeys({
                self.hotkey_combo: self._wrapped_callback,
            })
            self._listener.start()
            self._is_running = True

    def stop(self) -> None:
        """Detiene el escuchador global."""
        with self._lock:
            if not self._is_running or self._listener is None:
                return

            self._listener.stop()
            self._listener = None
            self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running
