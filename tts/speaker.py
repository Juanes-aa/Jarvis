"""
tts/speaker.py
Síntesis de voz (Text to Speech) de alta calidad usando edge-tts con streaming en tiempo real.
Decodificación continua con PyAV y reproducción de baja latencia con sounddevice.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Callable

import av
import edge_tts
import numpy as np
import sounddevice as sd

import config
import pipeline_timing as timing

logger = logging.getLogger(__name__)


def split_into_sentences(text: str) -> list[str]:
    """
    Divide un texto en frases para procesar por separado si es necesario.
    """
    text = text.strip()
    if not text:
        return []
    # Cortar tras signos de puntuación de fin de frase, conservando el signo.
    parts = re.split(r"(?<=[\.\!\?\u2026])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def sanitize_for_speech(text: str) -> str:
    """
    Limpia y normaliza texto antes de enviarlo a síntesis de voz (TTS).
    Elimina artefactos de Markdown, secuencias de escape literales y formatea
    saltos de línea para pausas naturales sin nombrar símbolos.
    """
    if not text:
        return ""

    # 1. Secuencias de escape literales tipo '\n', '\r', '\t' (backslash + caracter como texto).
    # Se convierten a un espacio para evitar juntar palabras y para no leer el simbolo.
    # El lookahead (?![A-Za-z]) protege rutas de Windows ('C:\\Users\\notas') y palabras.
    text = re.sub(r"(?:\\r\\n|\\r|\\n)(?![A-Za-z])", " ", text)
    # Convertir '\\t' literal a espacio (misma protección)
    text = re.sub(r"\\t(?![A-Za-z])", " ", text)
    # Normalizar saltos de línea reales (caracter de control) a un solo '\n'
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 1b. Markdown escapado: '\\*', '\\_', '\\`', '\\#', '\\~', '\\-' -> el símbolo sin la barra.
    text = re.sub(r"\\([*_`#~\-])", r"\1", text)

    # 2. Bloques de código con triple backtick (```python ... ``` o ``` ... ```)
    # Conservar el contenido interior, quitando los triples backticks y el nombre de lenguaje
    text = re.sub(r"```(?:[a-zA-Z0-9_\-\+]+)?\n?([\s\S]*?)```", r"\1", text)
    text = text.replace("```", "")

    # 3. Código inline con backticks: `código` -> código
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = text.replace("`", "")

    # 4. Marcadores de imágenes y enlaces Markdown: ![alt](url) -> alt, [texto](url) -> texto
    text = re.sub(r"!\[([^\]]*)\]\([^\)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", text)

    # 5. Placeholders técnicos estilo <parametro> o <ruta>
    text = re.sub(r"<([a-zA-Z0-9_\-\s]+)>", r"\1", text)

    # 6. Encabezados Markdown (# Título, ## Subtítulo al inicio de línea)
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)

    # 7. Citas Markdown (> texto al inicio de línea)
    text = re.sub(r"(?m)^\s*>\s*", "", text)

    # 8. Separadores horizontales (---, ***, ___)
    text = re.sub(r"(?m)^[\*\-_]{3,}\s*$", "", text)

    # 8b. Separadores y filas de tablas Markdown que solo contienen | - :
    text = re.sub(r"(?m)^\s*[\|\-:\s]+\|\s*[\|\-:\s]*\s*$", "", text)

    # 8c. Pipes sueltos dentro del texto (tablas inline) -> reemplazar por pausa
    text = re.sub(r"\s*\|\s*", " ", text)

    # 9. Listas no ordenadas (- item, * item, + item, • item al inicio de línea)
    # Incluye viñetas Unicode comunes.
    text = re.sub(r"(?m)^\s*(?:[\*\-\+•▪▸▹‣⁃\-]|[\u2022-\u2027])\s+", "", text)
    # Listas ordenadas: "1. item" o "1) item" al inicio de línea
    text = re.sub(r"(?m)^\s*\d+[\.\)]\s+", "", text)

    # 10. Negrita y cursiva en Markdown
    # ***texto*** -> texto
    text = re.sub(r"\*{3}(?!\s)([^*\n]+?)(?<!\s)\*{3}", r"\1", text)
    # **texto** -> texto
    text = re.sub(r"\*{2}(?!\s)([^*\n]+?)(?<!\s)\*{2}", r"\1", text)
    # *texto* -> texto (conservador: no tocar multiplicaciones como '2 * 3 * 4' o comodines)
    text = re.sub(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)", r"\1", text)
    # __texto__ -> texto (evitar nombres de variables internas con doble guión bajo)
    text = re.sub(r"(?<!\w)__([^_]+?)__(?!\w)", r"\1", text)
    # _texto_ -> texto
    text = re.sub(r"(?<!\w)_([^_]+?)_(?!\w)", r"\1", text)
    # Tachado ~~texto~~ -> texto
    text = re.sub(r"~~([^~\n]+?)~~", r"\1", text)

    # 11. Convertir saltos de línea a pausas naturales
    # Si una línea no termina en puntuación, agregamos un punto para que el TTS haga pausa.
    lines = [line.strip() for line in text.split("\n")]
    processed_lines = []
    for line in lines:
        if not line:
            continue
        # Si la línea no termina en puntuación común, añadir un punto
        if line[-1] not in ".!?:;,":
            line += "."
        processed_lines.append(line)

    text = " ".join(processed_lines)

    # 12. Pasada final de seguridad: eliminar marcadores Markdown sueltos/no balanceados
    # (asterisco, backtick, subrayado) que quedaron solitarios.
    # - Marcadores al inicio de una palabra: '*palabra' o '**palabra' -> 'palabra'
    # - Marcadores al final de una palabra: 'palabra*' o 'palabra**' -> 'palabra'
    # No toca marcadores rodeados de espacios ('3 * 4') ni dentro de palabras ('A*B', 'mi_variable').
    text = re.sub(r"(?<![\w*_`])[*_`]+(?=\w)", "", text)
    text = re.sub(r"(?<=\w)[*_`]+(?![\w*_`])", "", text)

    # 13. Limpieza de espacios redundantes y puntuación duplicada
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.,!?:;])", r"\1", text)
    text = re.sub(r"\.{2,}", "...", text)  # Preservar puntos suspensivos

    return text.strip()


class JarvisSpeaker:
    """
    Gestiona la síntesis y reproducción de voz de Jarvis utilizando edge-tts con streaming.
    """

    def __init__(
        self,
        voice: str | None = None,
        rate: str | None = None,
        volume: str | None = None,
        pitch: str | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        """
        Args:
            voice: Nombre de la voz de Microsoft Edge TTS.
            rate: Modificador de velocidad (ej. '+10%', '-5%').
            volume: Modificador de volumen (ej. '+0%', '-20%').
            pitch: Modificador de tono (ej. '+0Hz', '-5Hz').
            should_stop: Predicado opcional consultado periódicamente DURANTE la
                reproducción; si devuelve True, se corta el audio en curso de
                inmediato (p. ej. para respetar la pausa Ctrl+Alt+J sin esperar a
                que termine la frase actual).
        """
        self.voice = voice or config.DEFAULT_TTS_VOICE
        self.rate = rate or config.TTS_RATE
        self.volume = volume or config.TTS_VOLUME
        self.pitch = pitch or config.TTS_PITCH
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[int, np.ndarray]] = {}
        self._current_stream: sd.OutputStream | None = None
        # Fuente de verdad externa de la pausa (status.is_paused). Si no se
        # provee, nunca interrumpe por sí solo.
        self._should_stop_external = should_stop or (lambda: False)
        # Señal de interrupción inmediata activada por interrupt() (hotkey).
        self._interrupt = threading.Event()

        # Precalentar frases fijas en segundo plano para latencia 0ms
        threading.Thread(
            target=self._prewarm_cache,
            args=(["¿Dime?", "Acción cancelada."],),
            daemon=True,
            name="tts_prewarm",
        ).start()

    @staticmethod
    def _sanitize_for_speech(text: str) -> str:
        """Sanitiza el texto eliminando Markdown y secuencias de escape."""
        return sanitize_for_speech(text)

    def _should_stop(self) -> bool:
        """
        True si la reproducción en curso debe cortarse ya: interrupción explícita
        (interrupt()) o el predicado externo de pausa. Se consulta entre chunks.
        """
        if self._interrupt.is_set():
            return True
        try:
            return bool(self._should_stop_external())
        except Exception:  # noqa: BLE001 - un predicado roto no debe tumbar el TTS
            return False

    def _abort_current_stream(self) -> None:
        """Aborta y cierra el stream de salida activo (sin drenar el buffer)."""
        stream = self._current_stream
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._current_stream = None

    def _prewarm_cache(self, phrases: list[str]) -> None:
        """Sintetiza y almacena en caché frases fijas frecuentes."""
        for phrase in phrases:
            try:
                self._synthesize_to_cache(phrase)
            except Exception as e:
                logger.debug("[TTS] Precalentamiento de caché para '%s' falló: %s", phrase, e)

    def _synthesize_to_cache(self, text: str) -> tuple[int, np.ndarray] | None:
        """Genera y almacena en memoria el PCM int16 de un texto."""
        clean = self._sanitize_for_speech(text)
        if not clean:
            return None
        with self._lock:
            if clean in self._cache:
                return self._cache[clean]

        comm = edge_tts.Communicate(
            text=clean,
            voice=self.voice,
            rate=self.rate,
            volume=self.volume,
            pitch=self.pitch,
        )
        codec = av.Codec("mp3", "r")
        ctx = av.CodecContext.create(codec)

        frames_list: list[np.ndarray] = []
        sample_rate = 24000

        for chunk in comm.stream_sync():
            if chunk["type"] == "audio":
                packets = ctx.parse(chunk["data"])
                for packet in packets:
                    for frame in ctx.decode(packet):
                        sample_rate = frame.rate
                        arr = frame.to_ndarray()
                        if arr.ndim == 2:
                            arr = arr.T
                        frames_list.append(arr)

        if frames_list:
            full_audio = np.concatenate(frames_list, axis=0)
            with self._lock:
                self._cache[clean] = (sample_rate, full_audio)
            return sample_rate, full_audio
        return None

    def _play_cached(self, text: str) -> bool:
        """Reproduce un audio presente en caché."""
        with self._lock:
            cached = self._cache.get(text)
        if cached is None:
            return False

        sample_rate, full_audio = cached
        timing.tts_synth_end("cache hit")

        try:
            out_stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
            )
            out_stream.start()
            self._current_stream = out_stream
            timing.audio_playing("cache hit")

            # Escribir en bloques de ~100ms para poder consultar la pausa entre
            # cada uno y cortar el audio de inmediato (no en un único write que
            # bloquearía hasta el final de la frase).
            block = max(1, int(sample_rate * 0.1))
            interrupted = False
            for i in range(0, len(full_audio), block):
                if self._should_stop():
                    interrupted = True
                    break
                out_stream.write(full_audio[i:i + block])

            if interrupted:
                logger.info("[TTS] Reproducción (caché) interrumpida por pausa.")
                self._abort_current_stream()
                timing.audio_stopped("interrumpido")
                return False

            out_stream.stop()
            out_stream.close()
            self._current_stream = None
            timing.audio_stopped()
            return True
        except Exception as e:
            logger.error("[TTS] Error reproduciendo desde caché: %s", e)
            self._abort_current_stream()
            return False

    def _stream_and_play(self, clean_text: str) -> bool:
        """
        Sintetiza con edge-tts mediante streaming continuo y reproduce con sounddevice.
        """
        comm = edge_tts.Communicate(
            text=clean_text,
            voice=self.voice,
            rate=self.rate,
            volume=self.volume,
            pitch=self.pitch,
        )
        codec = av.Codec("mp3", "r")
        ctx = av.CodecContext.create(codec)

        out_stream: sd.OutputStream | None = None
        initial_buffer: list[np.ndarray] = []
        initial_buffer_chunks = 2  # Micro-buffer para absorción de jitter de red (~60ms)
        chunk_count = 0
        sample_rate = 24000
        recorded_frames: list[np.ndarray] = []

        try:
            for chunk in comm.stream_sync():
                # Consultar la pausa entre chunks: corta tanto la descarga del
                # stream de síntesis como la reproducción ya iniciada.
                if self._should_stop():
                    logger.info("[TTS] Reproducción (streaming) interrumpida por pausa.")
                    self._abort_current_stream()
                    timing.audio_stopped("interrumpido")
                    return False

                if chunk["type"] == "audio":
                    chunk_count += 1
                    packets = ctx.parse(chunk["data"])
                    for packet in packets:
                        for frame in ctx.decode(packet):
                            sample_rate = frame.rate
                            arr = frame.to_ndarray()
                            if arr.ndim == 2:
                                arr = arr.T

                            if len(clean_text) <= 40:
                                recorded_frames.append(arr)

                            if out_stream is None:
                                initial_buffer.append(arr)
                                if chunk_count >= initial_buffer_chunks:
                                    out_stream = sd.OutputStream(
                                        samplerate=sample_rate,
                                        channels=1,
                                        dtype="int16",
                                    )
                                    out_stream.start()
                                    self._current_stream = out_stream
                                    timing.audio_playing()
                                    for b in initial_buffer:
                                        out_stream.write(b)
                                    initial_buffer.clear()
                            else:
                                if self._should_stop():
                                    logger.info(
                                        "[TTS] Reproducción (streaming) interrumpida por pausa."
                                    )
                                    self._abort_current_stream()
                                    timing.audio_stopped("interrumpido")
                                    return False
                                out_stream.write(arr)

            # Si el texto era muy breve y el buffer inicial no se había volcado
            if out_stream is None and initial_buffer:
                out_stream = sd.OutputStream(
                    samplerate=sample_rate,
                    channels=1,
                    dtype="int16",
                )
                out_stream.start()
                self._current_stream = out_stream
                timing.audio_playing()
                for b in initial_buffer:
                    out_stream.write(b)
                initial_buffer.clear()

            # La descarga del stream de edge-tts terminó (mientras el audio continúa sonando)
            timing.tts_synth_end("stream download complete")

            if out_stream is not None:
                # Drena las muestras restantes del buffer de la tarjeta de sonido
                out_stream.stop()
                out_stream.close()
                self._current_stream = None
                timing.audio_stopped()

            # Guardar en caché si es una frase corta
            if len(clean_text) <= 40 and recorded_frames:
                full = np.concatenate(recorded_frames, axis=0)
                with self._lock:
                    self._cache[clean_text] = (sample_rate, full)

            return True

        except Exception as e:
            logger.error("[TTS] Error en streaming: %s", e)
            self._abort_current_stream()
            return False

    def speak(self, text: str, wait: bool = True) -> bool:
        """
        Sintetiza y reproduce el texto en voz alta.

        Args:
            text: Frase o párrafo a pronunciar.
            wait: Si es True, bloquea la ejecución hasta terminar de hablar.

        Returns:
            True si la reproducción fue exitosa, False en caso de error.
        """
        clean_text = self._sanitize_for_speech(text)
        if not clean_text:
            return False

        if clean_text != text.strip():
            logger.info("[TTS] Texto sanitizado para voz:\n  Original: %r\n  Limpio:   %r", text, clean_text)

        if not wait:
            threading.Thread(
                target=self.speak,
                args=(text, True),
                daemon=True,
            ).start()
            return True

        # Nuevo turno de habla legítimo: limpiar cualquier interrupción previa.
        # La pausa externa (should_stop) sigue siendo la fuente de verdad y, si
        # está activa, abortará igualmente la reproducción entre chunks.
        self._interrupt.clear()

        timing.tts_synth_start(f"{len(clean_text)} chars")

        # 1. Si está en caché, reproducir inmediatamente
        if clean_text in self._cache:
            return self._play_cached(clean_text)

        # 2. Streaming en tiempo real
        with self._lock:
            # Doble verificación tras adquirir el lock
            if clean_text in self._cache:
                return self._play_cached(clean_text)
            return self._stream_and_play(clean_text)

    def speak_stream(self, text: str) -> bool:
        """
        Sintetiza y reproduce el texto con streaming en tiempo real.
        El audio comienza a sonar tras los primeros chunks de datos.
        """
        return self.speak(text, wait=True)

    def speak_async(self, text: str) -> threading.Thread:
        """Pronuncia el texto en un hilo en segundo plano."""
        thread = threading.Thread(target=self.speak, args=(text, True), daemon=True)
        thread.start()
        return thread

    def interrupt(self) -> None:
        """
        Corta INMEDIATAMENTE el audio en curso (p. ej. al pausar con Ctrl+Alt+J).

        Activa la señal de interrupción (para que los loops de reproducción salgan
        entre chunks) y aborta el stream de salida activo sin esperar a que drene
        su buffer. Es seguro llamarlo aunque no haya nada sonando.
        """
        self._interrupt.set()
        self._abort_current_stream()
        try:
            sd.stop()
        except Exception:
            pass

    def stop(self) -> None:
        """Detiene la reproducción activa de audio."""
        self._interrupt.set()
        self._abort_current_stream()

        try:
            sd.stop()
        except Exception:
            pass
