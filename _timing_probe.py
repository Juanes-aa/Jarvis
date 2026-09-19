"""
_timing_probe.py
Sonda de diagnóstico de latencia del pipeline de voz (tarea de medición).

Simula interacciones reales SIN necesidad de hablar al micrófono:

- Captura por el dispositivo de ENTRADA "Mezcla estéreo" (loopback digital de
  Realtek) usando un callback porque el host WDM-KS no soporta el modo
  bloqueante que usa AudioRecorder por defecto.
- La "voz del usuario" se genera con edge-tts (misma voz del pipeline) y se
  reproduce por los altavoces mientras el VAD escucha.
- El resto del pipeline es 100% real: WhisperTranscriber, JarvisBrain (Groq),
  execute_tool y JarvisSpeaker.

Uso:
    venv\\Scripts\\python.exe _timing_probe.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import sys
import tempfile
import threading
import time

from dotenv import load_dotenv
from scipy import signal

load_dotenv()

import config
import pipeline_timing as timing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("timing_probe")

import numpy as np
import sounddevice as sd
import edge_tts
import pygame

from stt.transcriber import WhisperTranscriber
from brain.groq_client import JarvisBrain
from tts.speaker import JarvisSpeaker
import main as jarvis_main


# Frases de prueba: 1) conversacional (tool 'responder_conversacional'),
# 2) acción con tool real, 3) frase un poco más larga (respuesta multi-frase -> speak_stream).
TEST_PHRASES = [
    "¿Qué hora es?",
    "Abre la calculadora",
    "Cuéntame en tres frases cortas qué puedes hacer por mí y después dame un dato curioso sobre el espacio.",
]

PROBE_VOICE = "es-MX-JorgeNeural"
LOOPBACK_SAMPLE_RATE = 48000


def find_loopback_device() -> int | None:
    """Índice del dispositivo de entrada 'Mezcla estéreo' / 'Stereo Mix'."""
    for i, d in enumerate(sd.query_devices()):
        name = d.get("name", "").lower()
        if d.get("max_input_channels", 0) > 0 and (
            "mezcla" in name or "stereo mix" in name or "what u hear" in name
        ):
            return i
    return None


def synth_user_voice(text: str, path: str) -> None:
    """Genera el MP3 de la frase del 'usuario' con edge-tts."""
    asyncio.run(edge_tts.Communicate(text=text, voice=PROBE_VOICE).save(path))


def play_mp3_blocking(path: str) -> None:
    """Reproduce un MP3 por los altavoces (lo captura la Mezcla estéreo)."""
    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        clock = pygame.time.Clock()
        while pygame.mixer.music.get_busy():
            clock.tick(20)
        pygame.mixer.music.unload()
    except Exception:
        pass


def _record_loopback(
    device: int,
    max_duration: float = 12.0,
    silence_duration: float = 1.6,
    min_speech_duration: float = 0.4,
    initial_grace: float = 6.0,
    threshold: float = 0.005,
) -> np.ndarray | None:
    """
    Graba audio del loopback a 48 kHz usando callback y lo resamplea a 16 kHz.

    El host WDM-KS de Stereo Mix no soporta el modo bloqueante de
    sounddevice, por lo que usamos un callback en un hilo separado y una cola.
    """
    chunk_samples = int(LOOPBACK_SAMPLE_RATE * 0.1)
    audio_queue: queue.Queue[np.ndarray] = queue.Queue()

    def _callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
        audio_queue.put(indata[:, 0].copy())

    timing.mark("t1_grabacion_ini")
    speech_started = False
    silence_start_time = None
    speech_start_time = None
    recorded_chunks: list[np.ndarray] = []

    with sd.InputStream(
        samplerate=LOOPBACK_SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=chunk_samples,
        device=device,
        callback=_callback,
    ):
        timing.mark("rec_stream_abierto")
        start = time.perf_counter()
        while True:
            now = time.perf_counter()
            elapsed = now - start
            try:
                chunk = audio_queue.get(timeout=0.1)
            except queue.Empty:
                if elapsed > initial_grace + max_duration + 3.0:
                    break
                continue

            recorded_chunks.append(chunk)
            rms = float(np.sqrt(np.mean(chunk**2)))

            if rms >= threshold:
                if not speech_started:
                    speech_started = True
                    speech_start_time = now
                    timing.mark("vad_voz_ini", f"rms={rms:.4f}")
                silence_start_time = None
            else:
                if speech_started:
                    if silence_start_time is None:
                        silence_start_time = now
                    elif (now - silence_start_time) >= silence_duration:
                        break

            if not speech_started and (now - start) >= initial_grace:
                break

            if speech_start_time and (now - speech_start_time) >= max_duration:
                break

    timing.mark("t2_grabacion_fin")

    if not recorded_chunks or not speech_started:
        return None

    audio_48k = np.concatenate(recorded_chunks).astype(np.float32)
    # Resample de 48 kHz a 16 kHz para Whisper.
    audio_16k = signal.resample_poly(audio_48k, up=1, down=3).astype(np.float32)

    spoken = (silence_start_time or time.perf_counter()) - speech_start_time
    if spoken < min_speech_duration:
        return None

    max_val = float(np.max(np.abs(audio_16k)))
    if max_val < 0.0005:
        return None

    audio_16k = audio_16k / (max_val + 1e-6) * 0.95
    timing.mark(
        "audio_listo",
        f"{len(audio_16k) / config.STT_SAMPLE_RATE:.2f}s capturados, pico={max_val:.4f}",
    )
    return audio_16k


def run_interaction(
    idx: int,
    phrase: str,
    loopback: int,
    transcriber: WhisperTranscriber,
    brain: JarvisBrain,
    speaker: JarvisSpeaker,
) -> None:
    log.info("=" * 70)
    log.info("PRUEBA %d: %r", idx, phrase)
    log.info("=" * 70)

    # 1. Preparar la voz del 'usuario' (fuera de la medición).
    fd, phrase_path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    synth_user_voice(phrase, phrase_path)

    # 2. t0 simulado + ack "¿Dime?" — idéntico a on_wake_word_triggered().
    timing.reset()
    timing.mark("t0_simulado", "sonda (sin wake word real)")
    timing.mark("ack_ini", "speak('¿Dime?')")
    speaker.speak("¿Dime?", wait=True)
    timing.mark("ack_fin")

    # 3. Reproducir la frase del usuario cuando el VAD ya esté escuchando
    #    (calibración ~0.6s + apertura de stream; 1.5s de margen).
    def _speak_phrase() -> None:
        time.sleep(1.5)
        play_mp3_blocking(phrase_path)

    threading.Thread(target=_speak_phrase, daemon=True).start()

    # 4. Grabación + transcripción reales del loopback.
    audio = _record_loopback(
        device=loopback,
        max_duration=config.AUDIO_MAX_DURATION,
        silence_duration=config.AUDIO_SILENCE_DURATION,
        min_speech_duration=config.AUDIO_MIN_SPEECH_DURATION,
        initial_grace=config.AUDIO_INITIAL_GRACE,
    )
    try:
        os.remove(phrase_path)
    except OSError:
        pass

    if audio is None or len(audio) == 0:
        log.warning("PRUEBA %d: el VAD no capturó audio (señal débil?)", idx)
        timing.summary("RESUMEN PARCIAL (sin audio)")
        return

    text = transcriber.transcribe(audio, language=config.STT_LANGUAGE)
    log.info("PRUEBA %d transcrito: %r", idx, text)
    if not text:
        timing.summary("RESUMEN PARCIAL (sin texto)")
        return

    # 5. LLM + tools + TTS reales (process_query imprime su propio summary).
    jarvis_main.process_query(brain, speaker, text, None, None, None)


def main() -> None:
    loopback = find_loopback_device()
    if loopback is None:
        log.error("No se encontró 'Mezcla estéreo'; no se puede inyectar audio.")
        sys.exit(1)
    dev_name = sd.query_devices(loopback)["name"]
    log.info("Usando dispositivo de entrada loopback: [%d] %s", loopback, dev_name)

    # El WDM-KS de Stereo Mix exige callback; AudioRecorder usa modo bloqueante,
    # así que este probe usa su propia captura (_record_loopback).
    sd.default.device = (loopback, sd.default.device[1])

    pygame.mixer.init()

    transcriber = WhisperTranscriber(
        model_size=config.STT_MODEL_SIZE,
        device=config.STT_DEVICE,
        compute_type=config.STT_COMPUTE_TYPE,
    )
    brain = JarvisBrain()
    speaker = JarvisSpeaker(voice=config.DEFAULT_TTS_VOICE)

    # Precalentar Whisper fuera de la medición (la carga perezosa del modelo
    # queda medida aparte vía stt_modelo_carga_* en la primera llamada real).
    log.info("Precargando modelo Whisper '%s'...", config.STT_MODEL_SIZE)
    transcriber.transcribe(np.zeros(config.STT_SAMPLE_RATE, dtype=np.float32))

    for i, phrase in enumerate(TEST_PHRASES, start=1):
        run_interaction(i, phrase, loopback, transcriber, brain, speaker)
        time.sleep(1.0)

    log.info("Sonda finalizada.")


if __name__ == "__main__":
    main()
