"""
pipeline_timing.py
Instrumentación de latencia del pipeline de voz (tarea de DIAGNÓSTICO).

Mide tiempos con time.perf_counter() en cada frontera del pipeline:

    t0  wake word detectado
    t1  inicio de grabación (AudioRecorder)
    t2  fin de grabación (VAD corta por silencio / timeout)
    t3  inicio de transcripción (Whisper)
    t4  fin de transcripción (texto disponible)
    t5  inicio llamada a Groq (JarvisBrain.process)
    t6  fin llamada a Groq (respuesta parseada)
    t7  inicio de síntesis TTS de la respuesta
    t8  primer audio de la respuesta reproducido

Además de los t*, se registran marcas intermedias (ack del wake word, calibración
del VAD, ejecución de tools, síntesis/reproducción por frase) para desglosar los
tramos largos. Al final de cada interacción, summary() imprime una tabla con la
duración y el porcentaje de cada tramo.

Se activa con la variable de entorno JARVIS_TIMING (default "1"). Con "0" todas
las funciones quedan como no-op de costo despreciable.

Uso típico:
    import pipeline_timing as timing
    timing.reset()
    timing.mark("t1_grabacion_ini")
    timing.begin_response()          # arma t7/t8 antes de hablar la respuesta
    timing.summary()
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger("jarvis.timing")

ENABLED = os.getenv("JARVIS_TIMING", "1") == "1"

_lock = threading.Lock()
_marks: list[tuple[str, float, str]] = []   # (tag, perf_counter, nota)
_in_response_phase = False                  # True entre t6 y el fin de la respuesta


def reset() -> None:
    """Limpia las marcas y arranca una nueva interacción."""
    global _in_response_phase
    if not ENABLED:
        return
    with _lock:
        _marks.clear()
        _in_response_phase = False


def active() -> bool:
    """True si hay una interacción con marcas registradas en curso."""
    return ENABLED and bool(_marks)


def mark(tag: str, note: str = "") -> None:
    """
    Registra una frontera del pipeline. Loguea el timestamp, la duración del
    tramo anterior (desde la marca previa) y el total acumulado desde la primera
    marca de la interacción.
    """
    if not ENABLED:
        return
    now = time.perf_counter()
    with _lock:
        prev = _marks[-1] if _marks else None
        t0 = _marks[0][1] if _marks else now
        _marks.append((tag, now, note))
    if prev is None:
        logger.info("[TIMING] %-24s t=%.3fs (origen)  %s", tag, now, note)
    else:
        logger.info(
            "[TIMING] %-24s +%6.3fs (tramo %s->%s)  total=+%.3fs  %s",
            tag, now - prev[1], prev[0], tag, now - t0, note,
        )


def mark_once(tag: str, note: str = "") -> bool:
    """Como mark(), pero solo si ese tag aún no fue registrado en la interacción."""
    if not ENABLED:
        return False
    with _lock:
        if any(t == tag for t, _, _ in _marks):
            return False
    mark(tag, note)
    return True


def begin_response() -> None:
    """
    Marca el inicio de la fase de respuesta (post-LLM). A partir de aquí, la
    primera síntesis TTS se registra como t7 y el primer play() como t8.
    """
    global _in_response_phase
    if not ENABLED:
        return
    with _lock:
        _in_response_phase = True


def in_response() -> bool:
    """True si estamos en la fase de respuesta (entre t6 y fin de la interacción)."""
    return ENABLED and _in_response_phase


def tts_synth_start(note: str = "") -> None:
    """Llamar antes de sintetizar una frase con edge-tts."""
    if not ENABLED:
        return
    if in_response():
        # La primera síntesis de la respuesta es la frontera t7.
        if mark_once("t7_tts_ini", note):
            return
    mark("tts_synth_ini", note)


def tts_synth_end(note: str = "") -> None:
    """Llamar cuando la síntesis de una frase terminó (MP3 generado)."""
    mark("tts_synth_fin", note)


def audio_playing(note: str = "") -> None:
    """Llamar justo después de que empieza a sonar un fragmento de audio."""
    if not ENABLED:
        return
    if in_response():
        # El primer audio audible de la respuesta es la frontera t8.
        if mark_once("t8_primer_audio", note):
            return
    mark("tts_play_ini", note)


def audio_stopped(note: str = "") -> None:
    """Llamar cuando un fragmento de audio terminó de sonar (wait=True)."""
    mark("tts_play_fin", note)


def summary(title: str = "RESUMEN INTERACCIÓN") -> str:
    """
    Loguea y devuelve una tabla con la duración de cada tramo entre marcas
    consecutivas, su porcentaje del total y el tiempo total de la interacción.
    """
    if not ENABLED:
        return ""
    with _lock:
        marks = list(_marks)
    if len(marks) < 2:
        return ""

    total = marks[-1][1] - marks[0][1]
    lines = [f"[TIMING] ===== {title} ====="]
    for (tag_a, t_a, _), (tag_b, t_b, note_b) in zip(marks, marks[1:]):
        dt = t_b - t_a
        pct = (dt / total * 100.0) if total > 0 else 0.0
        extra = f"   ({note_b})" if note_b else ""
        lines.append(
            f"[TIMING]   {tag_a} -> {tag_b:<24s} {dt:7.3f}s  {pct:5.1f}%{extra}"
        )
    lines.append(f"[TIMING]   TOTAL {marks[0][0]} -> {marks[-1][0]}: {total:.3f}s")
    out = "\n".join(lines)
    for line in lines:
        logger.info("%s", line)
    return out
