"""
stt/transcriber.py
Grabación de audio robusta desde el micrófono y transcripción a texto con faster-whisper.
Optimizado para micrófonos de Windows con baja ganancia y habla en español.
"""

from __future__ import annotations

import logging
import time
import numpy as np
import sounddevice as sd

import config
import pipeline_timing as timing

logger = logging.getLogger(__name__)


# -- Grabador de Audio ---------------------------------------------------------

class AudioRecorder:
    """
    Graba audio desde el micrófono con calibración de ganancia,
    detección de silencio y normalización para Whisper.
    """

    def __init__(
        self,
        sample_rate: int | None = None,
        dtype: str | None = None,
    ) -> None:
        self.sample_rate = sample_rate or config.STT_SAMPLE_RATE
        self.dtype = dtype or config.STT_DTYPE

    @staticmethod
    def is_microphone_available() -> bool:
        """Verifica si existe al menos un dispositivo de entrada de audio funcional."""
        try:
            devices = sd.query_devices()
            input_devices = [d for d in devices if d.get("max_input_channels", 0) > 0]
            return len(input_devices) > 0
        except Exception:
            return False

    def _calibrate_noise(self, stream, chunk_samples: int) -> float:
        """
        Mide el ruido de fondo durante unos segundos y devuelve un umbral RMS
        adaptado al ambiente. Se acota entre AUDIO_MIN_THRESHOLD y AUDIO_MAX_THRESHOLD.
        """
        n_chunks = max(1, int(config.AUDIO_CALIBRATION_DURATION / 0.1))
        noise_samples: list[float] = []
        for _ in range(n_chunks):
            chunk, _ = stream.read(chunk_samples)
            rms = float(np.sqrt(np.mean(chunk.flatten() ** 2)))
            noise_samples.append(rms)

        noise_floor = float(np.mean(noise_samples)) if noise_samples else 0.0
        threshold = noise_floor * config.AUDIO_NOISE_MULTIPLIER
        threshold = max(config.AUDIO_MIN_THRESHOLD, min(config.AUDIO_MAX_THRESHOLD, threshold))

        if config.AUDIO_DEBUG:
            logger.info(
                "[VAD] Ruido de fondo=%.5f -> umbral adaptativo=%.5f",
                noise_floor, threshold,
            )
        return threshold

    def record(
        self,
        max_duration: float = 8.0,
        silence_threshold: float = 0.003,
        silence_duration: float = 1.2,
        min_speech_duration: float = 0.4,
        initial_grace: float | None = None,
    ) -> np.ndarray | None:
        """
        Graba audio desde el micrófono hasta detectar silencio tras el habla,
        o hasta alcanzar max_duration.

        El umbral de silencio se calibra automáticamente midiendo el ruido de
        fondo al inicio (si AUDIO_CALIBRATION_ENABLED). El argumento
        silence_threshold se usa como piso mínimo de seguridad.

        Args:
            max_duration: Tiempo máximo de grabación en segundos.
            silence_threshold: Nivel RMS umbral de respaldo (piso mínimo).
            silence_duration: Segundos de pausa para considerar finalizada la frase.
            min_speech_duration: Duración mínima del habla para procesar.
            initial_grace: Segundos de espera para que el usuario empiece a
                hablar antes de abortar. None usa config.AUDIO_INITIAL_GRACE.
                Se expone para que el flujo de confirmación por voz (1.1c) pueda
                dar una ventana más larga que la de una interacción normal.

        Returns:
            np.ndarray en float32 a 16kHz mono normalizado, o None si no hubo audio.
        """
        grace = initial_grace if initial_grace is not None else config.AUDIO_INITIAL_GRACE
        if not self.is_microphone_available():
            raise RuntimeError(
                "No se encontró ningún micrófono disponible en el sistema."
            )

        chunk_duration = 0.1  # Bloques de 100ms
        chunk_samples = int(self.sample_rate * chunk_duration)
        recorded_chunks: list[np.ndarray] = []

        speech_started = False
        silence_start_time = None
        speech_start_time = None

        # t1: inicio de la grabación (el VAD empieza a escuchar).
        timing.mark("t1_grabacion_ini")
        try:
            # Abrir stream de grabación mono a 16kHz (formato que espera faster-whisper)
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype=self.dtype,
                blocksize=chunk_samples,
            ) as stream:
                timing.mark("rec_stream_abierto")
                # 1. Calibración de ruido ambiente -> umbral adaptativo
                if config.AUDIO_CALIBRATION_ENABLED:
                    threshold = self._calibrate_noise(stream, chunk_samples)
                    threshold = max(threshold, silence_threshold)
                else:
                    threshold = silence_threshold
                timing.mark("vad_calibrado", f"threshold={threshold:.4f}")

                # 2. Bucle de captura. start_time se fija DESPUÉS de calibrar
                #    para no descontar la calibración del tiempo del usuario.
                start_time = time.time()
                while True:
                    chunk, overflowed = stream.read(chunk_samples)
                    chunk_flat = chunk.flatten()
                    recorded_chunks.append(chunk_flat.copy())

                    # Calcular energía RMS del bloque
                    rms = float(np.sqrt(np.mean(chunk_flat**2)))
                    now = time.time()

                    if config.AUDIO_DEBUG:
                        bar = "#" * min(40, int(rms / (threshold + 1e-9) * 10))
                        logger.debug("[VAD] rms=%.5f thr=%.5f %s", rms, threshold, bar)

                    if rms >= threshold:
                        if not speech_started:
                            speech_started = True
                            speech_start_time = now
                            timing.mark("vad_voz_ini", f"rms={rms:.4f}")
                            if config.AUDIO_DEBUG:
                                logger.info("[VAD] Inicio de voz detectado (rms=%.5f)", rms)
                        silence_start_time = None
                    else:
                        if speech_started:
                            if silence_start_time is None:
                                silence_start_time = now
                            elif (now - silence_start_time) >= silence_duration:
                                # Silencio sostenido tras haber hablado -> fin de frase
                                if config.AUDIO_DEBUG:
                                    logger.info("[VAD] Fin de frase por silencio")
                                break

                    # Si el usuario aún no empieza a hablar, esperar el grace period.
                    if not speech_started:
                        if (now - start_time) >= grace:
                            if config.AUDIO_DEBUG:
                                logger.info("[VAD] Sin voz tras grace period, abortando")
                            break
                        continue

                    # Tiempo máximo alcanzado (solo cuenta desde que empezó a hablar)
                    if speech_start_time and (now - speech_start_time) >= max_duration:
                        if config.AUDIO_DEBUG:
                            logger.info("[VAD] max_duration alcanzado")
                        break

            # t2: fin de la captura (silencio sostenido, grace o max_duration).
            timing.mark("t2_grabacion_fin")

            if not recorded_chunks or not speech_started:
                return None

            audio = np.concatenate(recorded_chunks).astype(np.float32)

            # Descartar frases demasiado cortas para ser útiles
            if speech_start_time is not None:
                spoken = (silence_start_time or time.time()) - speech_start_time
                if spoken < min_speech_duration:
                    if config.AUDIO_DEBUG:
                        logger.info("[VAD] Habla demasiado corta (%.2fs), descartada", spoken)
                    return None

            # Si el nivel es muy bajo en todo el buffer (micrófono mudo o apagado)
            max_val = float(np.max(np.abs(audio)))
            if max_val < 0.0005:
                return None

            # Normalizar ganancia para que Whisper siempre reciba una señal clara
            audio = audio / (max_val + 1e-6) * 0.95

            timing.mark(
                "audio_listo",
                f"{len(audio) / self.sample_rate:.2f}s capturados, pico={max_val:.4f}",
            )

            if config.AUDIO_DEBUG:
                logger.info(
                    "[VAD] Audio capturado: %.2fs, pico=%.4f, sr=%dHz",
                    len(audio) / self.sample_rate, max_val, self.sample_rate,
                )

            return audio

        except Exception as e:
            raise RuntimeError(f"Error al grabar audio del micrófono: {e}")

    def record_fixed(self, duration: float = 4.0) -> np.ndarray:
        """Graba audio por una duración fija en segundos con normalización."""
        if not self.is_microphone_available():
            raise RuntimeError(
                "No se encontró ningún micrófono disponible en el sistema."
            )

        try:
            audio = sd.rec(
                int(duration * self.sample_rate),
                samplerate=self.sample_rate,
                channels=1,
                dtype=self.dtype,
            )
            sd.wait()
            audio_flat = audio.flatten().astype(np.float32)
            max_val = float(np.max(np.abs(audio_flat)))
            if max_val > 0.001:
                audio_flat = audio_flat / (max_val + 1e-6) * 0.95
            return audio_flat
        except Exception as e:
            raise RuntimeError(f"Error al grabar audio: {e}")


# -- Transcriptor Whisper ------------------------------------------------------

class WhisperTranscriber:
    """
    Transcribe audio a texto utilizando faster-whisper optimizado para español.
    """

    def __init__(
        self,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
    ) -> None:
        self.model_size = model_size or config.STT_MODEL_SIZE
        self.device = device or config.STT_DEVICE
        self.compute_type = compute_type or config.STT_COMPUTE_TYPE
        self._model = None

    def _get_model(self):
        """Carga perezosa del modelo Whisper."""
        if self._model is None:
            timing.mark("stt_modelo_carga_ini", f"{self.model_size}/{self.device}/{self.compute_type}")
            from faster_whisper import WhisperModel

            comp_type = self.compute_type
            if comp_type == "default":
                comp_type = "int8" if self.device == "cpu" else "float16"

            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=comp_type,
            )
            timing.mark("stt_modelo_carga_fin")
        return self._model

    def transcribe(
        self,
        audio: np.ndarray | str,
        language: str = "es",
    ) -> str:
        """
        Transcribe audio a texto en español sin descartar frases con VAD agresivo.
        """
        # t3: inicio de la transcripción.
        timing.mark(
            "t3_stt_ini",
            f"{len(audio) / config.STT_SAMPLE_RATE:.2f}s de audio" if isinstance(audio, np.ndarray) else "archivo",
        )
        model = self._get_model()

        # Forzamos siempre el idioma para que Whisper no auto-detecte y se
        # confunda a mitad de frase. Por defecto usamos config.STT_LANGUAGE.
        lang = language or config.STT_LANGUAGE or "es"

        # Desactivamos vad_filter agresivo para que Whisper transcriba todo el audio recibido
        segments, info = model.transcribe(
            audio,
            language=lang,
            beam_size=5,
            temperature=0.0,
            vad_filter=False,  # Audio ya recortado por AudioRecorder
            initial_prompt="Comandos de asistente: abre programas, sube baja volumen, ajusta brillo, ejecuta comandos, responde preguntas.",
        )

        transcription_parts = [segment.text.strip() for segment in segments]
        raw_text = " ".join(transcription_parts).strip()

        # t4: fin de la transcripción (texto disponible).
        timing.mark("t4_stt_fin", f"segmentos={len(transcription_parts)}")

        if config.AUDIO_DEBUG:
            logger.info("[STT] Texto crudo transcrito (lang=%s): %r", lang, raw_text)

        return raw_text
