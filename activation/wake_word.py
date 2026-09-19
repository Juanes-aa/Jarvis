"""
activation/wake_word.py
Detección de palabra clave (Wake Word) en segundo plano con openWakeWord y bajo consumo.
Soporta liberación limpia del dispositivo de audio al pausar.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable
import numpy as np
import sounddevice as sd

import config
import pipeline_timing as timing


class WakeWordDetector:
    """
    Escucha continuamente el micrófono en segundo plano utilizando openWakeWord
    para detectar 'Hey Jarvis' o palabras clave configuradas.
    """

    def __init__(
        self,
        on_detected: Callable[[], None],
        model_names: list[str] | None = None,
        threshold: float | None = None,
        sample_rate: int | None = None,
        chunk_size: int | None = None,
        device_index: int | None = None,
    ) -> None:
        self.on_detected = on_detected
        self.model_names = model_names or config.WAKE_WORD_MODELS
        self.threshold = threshold or config.WAKE_WORD_THRESHOLD
        self.sample_rate = sample_rate or config.WAKE_WORD_SAMPLE_RATE
        self.chunk_size = chunk_size or config.WAKE_WORD_CHUNK_SIZE
        # None = usa el dispositivo de entrada por defecto del sistema,
        # el MISMO que usa AudioRecorder (que tampoco especifica device).
        # Si no se pasa explícito, tomamos el configurado en config.
        self._device_index = (
            device_index if device_index is not None else config.WAKE_WORD_DEVICE_INDEX
        )

        self._model = None
        self._is_running = False
        self._is_paused = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._logger = logging.getLogger(__name__)
        # Se activa cuando el bucle cierra realmente el stream de audio,
        # permitiendo a pause() sincronizarse sin un sleep fijo.
        self._stream_closed = threading.Event()
        self._stream_closed.set()

    def _init_model(self) -> bool:
        """Carga el modelo openWakeWord."""
        if self._model is None:
            try:
                import openwakeword
                from openwakeword.model import Model

                openwakeword.utils.download_models(self.model_names)

                self._model = Model(
                    wakeword_models=self.model_names,
                    inference_framework="onnx",
                )
                return True
            except ImportError as e:
                self._logger.error(f"No se pudo importar openWakeWord: {e}")
                try:
                    from openwakeword.model import Model
                    self._model = Model(inference_framework="onnx")
                    return True
                except ImportError as e:
                    self._logger.error(f"No se pudo importar Model de openWakeWord: {e}")
                    return False
            except Exception as e:
                self._logger.error(f"Error al inicializar modelo openWakeWord: {e}", exc_info=True)
                return False
        return True

    def _detection_loop(self) -> None:
        """Bucle continuo de captura y predicción con apertura/cierre dinámico del stream."""
        if not self._init_model():
            return

        open_count = 0  # Cuántas veces se ha (re)abierto el stream

        while self._is_running:
            if self._is_paused:
                time.sleep(0.1)
                continue

            try:
                # Al abrir el stream marcamos que está en uso (no cerrado)
                self._stream_closed.clear()
                # Abrimos el stream solo mientras no esté pausado
                with sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=self.chunk_size,
                    device=self._device_index,
                ) as stream:
                    open_count += 1
                    try:
                        dev_used = sd.default.device[0] if self._device_index is None else self._device_index
                        dev_name = sd.query_devices(dev_used)["name"] if dev_used is not None else "default"
                    except Exception:
                        dev_name = "desconocido"
                    if open_count == 1:
                        self._logger.info(
                            "[WakeWord] Escuchando (inicio). Modelos=%s threshold=%.3f device=%s sr=%dHz chunk=%d",
                            self.model_names, self.threshold, dev_name,
                            self.sample_rate, self.chunk_size,
                        )
                    else:
                        self._logger.info(
                            "[WakeWord] Stream reanudado (apertura #%d). Escuchando de nuevo en device=%s",
                            open_count, dev_name,
                        )

                    while self._is_running and not self._is_paused:
                        audio_chunk, overflowed = stream.read(self.chunk_size)
                        audio_data = audio_chunk.flatten()

                        prediction = self._model.predict(audio_data)

                        detected = False
                        for model_name, score in prediction.items():
                            # Log del score en CADA frame (aunque no supere el umbral)
                            self._logger.debug(
                                "[WakeWord] %s score=%.3f threshold=%.3f",
                                model_name, float(score), self.threshold,
                            )
                            if score >= self.threshold:
                                detected = True
                                self._logger.info(
                                    "[WakeWord] ¡DETECTADO! %s score=%.3f >= threshold=%.3f",
                                    model_name, float(score), self.threshold,
                                )
                                break

                        if detected:
                            self._model.reset()
                            # Pausar inmediatamente antes de lanzar el callback
                            self._is_paused = True
                            # t0: instante real de la detección del wake word.
                            timing.reset()
                            timing.mark(
                                "t0_wake_detectado",
                                f"score={max(float(s) for s in prediction.values()):.3f}",
                            )
                            threading.Thread(target=self.on_detected, daemon=True).start()
                            break

            except Exception as e:
                self._logger.error(f"Error en bucle de detección: {e}", exc_info=True)
                time.sleep(0.5)
            finally:
                # El stream ya se cerró (salimos del bloque with): avisar a pause()
                self._stream_closed.set()

    def log_input_devices(self) -> None:
        """
        Lista TODOS los dispositivos de entrada disponibles con su índice, para
        que el usuario pueda elegir uno explícito (WAKE_WORD_DEVICE_INDEX) en
        vez del dispositivo por defecto del sistema.
        """
        try:
            devices = sd.query_devices()
            try:
                default_in = sd.default.device[0]
            except Exception:
                default_in = None

            self._logger.info("[Audio] Dispositivos de entrada disponibles:")
            for idx, dev in enumerate(devices):
                if dev.get("max_input_channels", 0) > 0:
                    marca = "  <-- DEFAULT" if idx == default_in else ""
                    marca_sel = "  <-- SELECCIONADO" if idx == self._device_index else ""
                    self._logger.info(
                        "[Audio]   [%d] %s (canales_in=%d, sr=%.0fHz)%s%s",
                        idx, dev.get("name", "?"),
                        dev.get("max_input_channels", 0),
                        dev.get("default_samplerate", 0),
                        marca, marca_sel,
                    )
            objetivo = self._device_index if self._device_index is not None else default_in
            self._logger.info(
                "[Audio] Wake word usará el dispositivo indice=%s. "
                "Para forzar otro, define WAKE_WORD_DEVICE_INDEX=<indice>.",
                objetivo,
            )
        except Exception as e:
            self._logger.error("[Audio] No se pudieron listar dispositivos: %s", e)

    def start(self) -> bool:
        """Inicia el detector de wake word en un hilo en segundo plano."""
        with self._lock:
            if self._is_running:
                return True

            # Listar dispositivos de entrada al inicio para diagnóstico.
            self.log_input_devices()

            self._is_running = True
            self._is_paused = False
            self._thread = threading.Thread(target=self._detection_loop, daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        """Detiene el detector por completo."""
        with self._lock:
            self._is_running = False
            self._is_paused = False
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=1.0)
            self._thread = None

    def pause(self, timeout: float = 2.0) -> None:
        """
        Pausa la detección y espera a que el stream de audio se cierre realmente
        (sincronización por evento en lugar de un sleep fijo) para liberar el micrófono.
        """
        self._is_paused = True
        # Esperar hasta que el bucle de detección confirme el cierre del stream.
        self._stream_closed.wait(timeout=timeout)

    def resume(self) -> None:
        """Reanuda la detección tras terminar la interacción."""
        if self._model:
            self._model.reset()
        self._is_paused = False
        self._logger.info("[WakeWord] resume() llamado: se reabrirá el stream para volver a escuchar.")

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def is_paused(self) -> bool:
        return self._is_paused
