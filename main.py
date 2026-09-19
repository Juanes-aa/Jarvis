"""
main.py
Jarvis en segundo plano -- Wake Word, Voz (STT/TTS), Memoria y Control del Sistema.

La app corre SIN ventana de consola (estilo pythonw). Toda interacción es por
voz, ícono de bandeja del sistema o hotkey (Ctrl+Alt+J = pausar/reanudar).
Los logs se escriben SIEMPRE a un archivo (config.LOG_FILE) para conservar
visibilidad de debug, y también a stdout si hay consola adjunta.

Ejecutar:
    pythonw main.py      # modo normal (sin consola)
    python main.py       # modo debug (logs en vivo por consola + archivo)
"""

import atexit
import json
import logging
import logging.handlers
import os
import re
import signal
import sys
import threading
import unicodedata

if sys.stdout is not None and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr is not None and sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()

import config
import pipeline_timing as timing

from brain.groq_client import JarvisBrain
from actions.system_control import execute_tool
from stt.transcriber import AudioRecorder, WhisperTranscriber
from activation.hotkey import HotkeyListener
from activation.wake_word import WakeWordDetector
from tools import RiskLevel, get_registry
from tts.speaker import JarvisSpeaker
from ui.status_controller import AppState, StatusController
from ui.tray_icon import TrayIcon


# -- Colores ANSI para la terminal ---------------------------------------------

class Colors:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    MAGENTA = "\033[95m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def print_banner() -> None:
    banner = f"""
{Colors.CYAN}{Colors.BOLD}
     +===========================================+
     |          J A R V I S  v0.6                |
     |   Asistente Autónomo (Wake Word & Voz)    |
     +===========================================+
{Colors.RESET}
{Colors.DIM}  - Di 'Hey Jarvis' o presiona Ctrl+Alt+J para hablar.
  - Escribe 'voz' o 'v' para grabar audio desde la consola.
  - Escribe 'reset' para limpiar el historial de conversación.
  - Escribe 'salir' para terminar.{Colors.RESET}
"""
    print(banner)


def format_tool_call(tool_name: str, tool_input: dict) -> str:
    """Formatea una llamada a herramienta para mostrar en consola."""
    params = json.dumps(tool_input, ensure_ascii=False, indent=2)
    return (
        f"\n{Colors.YELLOW}[Herramienta]{Colors.RESET} {Colors.BOLD}{tool_name}{Colors.RESET}\n"
        f"{Colors.YELLOW}[Parámetros]{Colors.RESET}\n{Colors.DIM}{params}{Colors.RESET}"
    )


def format_text_response(text: str) -> str:
    """Formatea una respuesta de texto para mostrar en consola."""
    return f"\n{Colors.GREEN}Jarvis:{Colors.RESET} {text}"


def format_action_success(message: str) -> str:
    """Formatea un resultado exitoso de acción."""
    return f"{Colors.GREEN}[OK]{Colors.RESET} {message}"


def format_action_error(message: str) -> str:
    """Formatea un resultado fallido de acción."""
    return f"{Colors.RED}[ERROR]{Colors.RESET} {message}"


# -- Red de seguridad: LLM que "aplana" un tool call en texto libre ------------

# Groq/gpt-oss ocasionalmente devuelve la respuesta de 'responder_conversacional'
# como TEXTO LIBRE con forma de JSON (p. ej. '{"texto":"..."}') en vez de un
# tool call estructurado. Cuando eso pasa, el JSON llega en message.content y cae
# por la rama de texto libre de process_query, que lo manda a hablar tal cual:
# Jarvis termina leyendo en voz alta las llaves, las comillas y la clave 'texto'.
# Esta red de seguridad extrae el valor real de 'texto' antes de sintetizar.

# Regex tolerante: el texto ENTERO debe ser un objeto con una unica clave 'texto'
# (con o sin comillas en la clave; comillas dobles o simples en el valor). Es
# deliberadamente estricto en los extremos (^\{ ... \}$) para NO tocar respuestas
# reales que solo empiecen con '{' o contengan comillas en medio.
_FLATTENED_TEXTO_RE = re.compile(
    r"""^\s*\{\s*['"]?texto['"]?\s*:\s*(?P<q>['"])(?P<val>.*)(?P=q)\s*\}\s*$""",
    re.DOTALL,
)


def _unwrap_flattened_texto(text: str) -> str:
    """
    Si 'text' es en realidad un tool call de responder_conversacional aplanado en
    texto libre (un objeto JSON con clave 'texto'), devuelve solo el valor de
    'texto'. En cualquier otro caso devuelve el texto original intacto.

    Conservador a propósito: solo actúa cuando el texto COMPLETO es ese objeto,
    para no romper respuestas reales que casualmente empiecen con '{' o incluyan
    comillas. No depende de que el JSON esté perfectamente bien formado: tolera
    comillas simples y la clave sin comillas.
    """
    if not text:
        return text
    stripped = text.strip()
    # Descarte rápido: si no tiene forma de objeto que mencione 'texto', no tocar.
    if not (stripped.startswith("{") and stripped.endswith("}") and "texto" in stripped):
        return text

    # 1) Intento estricto: JSON bien formado.
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict) and isinstance(parsed.get("texto"), str):
            return parsed["texto"]
    except (json.JSONDecodeError, TypeError):
        pass

    # 2) Fallback tolerante: comillas simples o clave sin comillas.
    m = _FLATTENED_TEXTO_RE.match(stripped)
    if m:
        val = m.group("val")
        # Desescapar (backslash primero) las secuencias más comunes del LLM.
        val = val.replace("\\\\", "\\")
        val = val.replace("\\n", "\n").replace("\\t", "\t")
        val = val.replace('\\"', '"').replace("\\'", "'")
        return val

    return text


# -- Pipeline central de procesamiento -----------------------------------------

processing_lock = threading.Lock()


def get_spoken_confirmation(tool_name: str, tool_input: dict, result) -> str:
    """Genera una respuesta corta y natural en voz para confirmar la ejecución de una acción.

    Para las tools con frase natural derivada de sus parámetros (abrir_app,
    volumen, brillo, comando) se construye aquí. Para el resto se usa la versión
    hablable que la tool declaró (result.spoken_message) y, si no la declaró, se
    cae al message técnico completo (result.speech encapsula ese fallback). Así
    nunca se lee por voz una ruta, un título de ventana ni un timestamp.
    """
    if not result.success:
        return "Hubo un problema al ejecutar la acción."

    if tool_name == "abrir_app":
        app = tool_input.get("nombre", "la aplicación")
        return f"Abriendo {app}."
    elif tool_name == "subir_bajar_volumen":
        delta = tool_input.get("delta", 0)
        return "Subiendo el volumen." if delta > 0 else "Bajando el volumen."
    elif tool_name == "ajustar_brillo":
        nivel = tool_input.get("nivel", "")
        return f"Brillo ajustado al {nivel} por ciento."
    elif tool_name == "ejecutar_comando":
        return "Comando ejecutado."
    return result.speech


# -- Confirmación por voz para acciones DESTRUCTIVE (sub-fase 1.1c) -------------

# Palabras (ya normalizadas: minúsculas y sin acentos) que interpretamos como
# confirmación afirmativa o negativa. Ante CUALQUIER cosa que no sea un "sí"
# claro sin negación, se cancela por seguridad (política fail-safe).
_AFFIRMATIVE_WORDS = {
    "si", "sip", "claro", "confirmo", "confirmar", "confirmado", "dale",
    "correcto", "afirmativo", "hazlo", "adelante", "ok", "okay", "vale",
    "seguro", "procede", "acepto", "aceptar", "hagamoslo",
}
_NEGATIVE_WORDS = {
    "no", "cancela", "cancelar", "cancelado", "negativo", "detente", "para",
    "olvidalo", "nada", "aborta", "abortar", "mejor", "espera", "detener",
}


def _normalize_text(text: str) -> str:
    """Minúsculas, sin acentos y sin puntuación, para comparar palabras sueltas."""
    text = text.lower().strip()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _interpret_confirmation(text: str) -> str:
    """
    Clasifica la respuesta del usuario a una confirmación.

    Devuelve "affirmative" SOLO ante un sí claro sin negación; "negative" ante
    una negación explícita; "ambiguous" en cualquier otro caso. El pipeline
    ejecuta la acción únicamente con "affirmative".
    """
    tokens = set(_normalize_text(text).split())
    has_negative = bool(tokens & _NEGATIVE_WORDS)
    has_affirmative = bool(tokens & _AFFIRMATIVE_WORDS)
    if has_affirmative and not has_negative:
        return "affirmative"
    if has_negative:
        return "negative"
    return "ambiguous"


def _build_confirmation_prompt(tool_name: str, tool_input: dict) -> str:
    """
    Arma la frase de confirmación, incluyendo la acción concreta y sus
    parámetros (no un mensaje genérico), para que el usuario sepa qué aprueba.
    """
    if tool_name == "ejecutar_comando":
        comando = tool_input.get("comando", "").strip()
        # Resolver la ruta destino real para que el usuario escuche EXACTAMENTE
        # dónde se creará la carpeta antes de confirmar (la ruta ahora puede ser
        # cualquiera, no solo el perfil de usuario).
        from actions.system_control import previsualizar_ruta_comando

        target = previsualizar_ruta_comando(comando)
        if target is not None:
            # Decimos EXPLÍCITAMENTE el comando y, además, la ruta resuelta
            # donde se creará la carpeta, para que el usuario sepa qué aprueba.
            return (
                f"Voy a ejecutar el comando: {comando}. Se creará la carpeta "
                f"'{target.name}' en {target.parent}. ¿Confirmas? Di sí o no."
            )
        return (
            f"Voy a ejecutar el comando: {comando}. ¿Confirmas? Di sí o no."
        )
    # Resto de tools que requieren confirmación: se usa la descripción legible
    # de la acción (confirmation_label) en vez del nombre crudo de la tool, que
    # se leería con guiones bajos ("cerrar guion bajo ventana guion bajo
    # activa"). Si la tool no la definió, se cae a un fallback genérico con el
    # nombre (mejor eso que no confirmar).
    registered = get_registry().get(tool_name)
    label = (getattr(registered, "confirmation_label", "") or "").strip() if registered else ""
    accion = label if label else f"ejecutar la acción {tool_name}"

    if tool_input:
        params = ", ".join(f"{k}: {v}" for k, v in tool_input.items())
        detalle = f" con {params}"
    else:
        detalle = ""
    return f"Vas a {accion}{detalle}. ¿Confirmas? Di sí o no."


def _run_voice_confirmation(
    recorder: AudioRecorder,
    transcriber: WhisperTranscriber,
    speaker: JarvisSpeaker,
    tool_name: str,
    tool_input: dict,
    status: StatusController | None = None,
) -> bool:
    """
    Pide confirmación hablada para una acción DESTRUCTIVE y escucha UNA respuesta
    sin wake word. Devuelve True solo si el usuario confirma claramente.

    Se ejecuta dentro de process_query (que ya sostiene processing_lock) mientras
    el WakeWordDetector está pausado por record_and_process_voice, por lo que el
    micrófono está libre y no hay reentrada del lock ni contención de audio.
    """
    logger = logging.getLogger(__name__)

    prompt = _build_confirmation_prompt(tool_name, tool_input)
    timing.mark("confirm_ini", tool_name)
    logger.info("[Confirmación] Tool DESTRUCTIVE '%s' requiere confirmación por voz.", tool_name)
    print(f"\n{Colors.YELLOW}{Colors.BOLD}[Confirmación requerida]{Colors.RESET} {prompt}")

    # speak() con wait=True bloquea hasta terminar de hablar, así no grabamos
    # nuestra propia voz al abrir el micrófono a continuación.
    speaker.speak(prompt, wait=True)

    if status is not None:
        status.set_state(AppState.LISTENING)
    print(f"{Colors.MAGENTA}{Colors.BOLD}🎤 [Esperando confirmación...]{Colors.RESET} Di sí o no...")

    try:
        audio = recorder.record(
            max_duration=config.AUDIO_MAX_DURATION,
            silence_threshold=config.AUDIO_SILENCE_THRESHOLD,
            silence_duration=config.AUDIO_SILENCE_DURATION,
            min_speech_duration=config.AUDIO_MIN_SPEECH_DURATION,
            initial_grace=config.TOOL_CONFIRMATION_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 - ante fallo de audio, cancelar por seguridad
        logger.error("[Confirmación] Error al grabar la respuesta: %s", e, exc_info=True)
        return False

    if audio is None or len(audio) == 0:
        logger.warning(
            "[Confirmación] Sin respuesta dentro del timeout (%.0fs); se CANCELA por seguridad.",
            config.TOOL_CONFIRMATION_TIMEOUT,
        )
        return False

    if status is not None:
        status.set_state(AppState.PROCESSING)
    answer = transcriber.transcribe(audio, language=config.STT_LANGUAGE)
    verdict = _interpret_confirmation(answer)
    logger.info(
        "[Confirmación] Respuesta='%s' -> interpretada como '%s'.",
        answer, verdict,
    )
    print(f"{Colors.GREEN}🗣️ [Respuesta]:{Colors.RESET} \"{answer}\" -> {verdict}")
    timing.mark("confirm_fin", f"veredicto={verdict}")
    return verdict == "affirmative"


def process_query(
    brain: JarvisBrain,
    speaker: JarvisSpeaker,
    user_input: str,
    status: StatusController | None = None,
    recorder: AudioRecorder | None = None,
    transcriber: WhisperTranscriber | None = None,
) -> None:
    """Procesa una consulta de voz, ejecuta acciones y responde con TTS."""
    logger = logging.getLogger(__name__)
    
    with processing_lock:
        if not user_input.strip():
            return

        print(f"\n{Colors.CYAN}{Colors.BOLD}[Procesando]:{Colors.RESET} {user_input}")

        try:
            # t5: inicio del procesamiento del LLM (incluye I/O de memoria).
            timing.mark("t5_llm_ini")
            response = brain.process(user_input)
            # t6: respuesta de Groq ya recibida y parseada.
            timing.mark(
                "t6_llm_fin",
                f"tools={[t.tool_name for t in response.tool_results]}" if response.has_tool_use else "texto libre",
            )
            # A partir de aquí, la 1ª síntesis/reproducción cuentan como t7/t8.
            timing.begin_response()

            # A partir de aquí Jarvis empieza a responder/hablar.
            if status is not None:
                status.set_state(AppState.SPEAKING)

            # 1. Mostrar y pronunciar texto libre adicional del modelo
            if response.text_parts:
                for raw_text in response.text_parts:
                    # Red de seguridad: si el LLM "aplanó" un tool call de
                    # responder_conversacional en texto libre ('{"texto":"..."}'),
                    # extraer el valor real antes de hablar el JSON crudo.
                    text = _unwrap_flattened_texto(raw_text)
                    if text != raw_text:
                        logger.warning(
                            "[FreeText] El LLM devolvió un tool call aplanado como "
                            "texto libre; se extrajo el valor de 'texto' antes de "
                            "hablar (evitado leer el JSON crudo)."
                        )
                    print(format_text_response(text))
                    speaker.speak_stream(text)

            # 2. Procesar y ejecutar herramientas invocadas
            if response.has_tool_use:
                for tool in response.tool_results:
                    print(format_tool_call(tool.tool_name, tool.tool_input))
                    timing.mark("tool_ini", tool.tool_name)

                    # Caso especial: respuesta puramente conversacional
                    if tool.tool_name == "responder_conversacional":
                        texto = tool.tool_input.get("texto", "")
                        print(format_text_response(texto))
                        speaker.speak_stream(texto)
                        # Registrar resultado role:"tool" para historial válido
                        brain.record_tool_result(
                            tool.tool_use_id, tool.tool_name, texto or "ok"
                        )
                        timing.mark("tool_fin", tool.tool_name)
                        continue

                    # Gate de seguridad (lista negra): para 'ejecutar_comando',
                    # se evalúa ANTES de la confirmación por voz. Si el comando
                    # coincide con un patrón catastrófico (formatear disco, borrar
                    # C:\Windows, matar procesos críticos, etc.), se RECHAZA de
                    # inmediato SIN pedir confirmación al usuario. Se loggea a
                    # WARNING (info de seguridad relevante).
                    if tool.tool_name == "ejecutar_comando":
                        from tools.system.command_blacklist import is_blacklisted

                        comando_solicitado = tool.tool_input.get("comando", "")
                        bloqueado, razon = is_blacklisted(comando_solicitado)
                        if bloqueado:
                            logger.warning(
                                "[Seguridad] Comando bloqueado por lista negra "
                                "(sin confirmación): %r (patrón: %s)",
                                comando_solicitado, razon,
                            )
                            msg = (
                                "Ese comando está bloqueado por seguridad porque "
                                "podría dañar el sistema o el disco de forma "
                                "irreversible."
                            )
                            print(format_action_error(msg))
                            if status is not None:
                                status.set_state(AppState.SPEAKING)
                            speaker.speak(msg, wait=True)
                            brain.record_tool_result(
                                tool.tool_use_id, tool.tool_name, msg
                            )
                            timing.mark("tool_fin", tool.tool_name)
                            continue

                    # Confirmación por voz (1.1c). Se dispara si la tool es
                    # DESTRUCTIVE O si pide confirmación explícita vía el flag
                    # requires_confirmation (independiente del risk_level; p. ej.
                    # cerrar_ventana_activa es REVERSIBLE pero pide confirmación).
                    # El risk_level/flag son la fuente de verdad y viven en la
                    # tool; los leemos del registry en vez de hardcodear nombres.
                    registered = get_registry().get(tool.tool_name)
                    if registered is not None and (
                        registered.risk_level == RiskLevel.DESTRUCTIVE
                        or registered.requires_confirmation
                    ):
                        if recorder is None or transcriber is None:
                            # Sin pipeline de audio no podemos confirmar; por
                            # seguridad, no ejecutar una acción que requiere
                            # confirmación.
                            logger.warning(
                                "[Confirmación] '%s' requiere confirmación pero no hay "
                                "pipeline de audio para confirmar; se CANCELA.", tool.tool_name,
                            )
                            confirmed = False
                        else:
                            confirmed = _run_voice_confirmation(
                                recorder, transcriber, speaker,
                                tool.tool_name, tool.tool_input, status,
                            )
                        if not confirmed:
                            print(format_action_error("Acción cancelada por el usuario."))
                            if status is not None:
                                status.set_state(AppState.SPEAKING)
                            speaker.speak("Acción cancelada.", wait=True)
                            brain.record_tool_result(
                                tool.tool_use_id, tool.tool_name,
                                "Acción cancelada por el usuario (no confirmada).",
                            )
                            logger.info(
                                "[Confirmación] '%s' NO ejecutada (cancelada).", tool.tool_name,
                            )
                            timing.mark("tool_fin", tool.tool_name)
                            continue
                        logger.info(
                            "[Confirmación] '%s' CONFIRMADA; procediendo a ejecutar.",
                            tool.tool_name,
                        )
                        if status is not None:
                            status.set_state(AppState.SPEAKING)

                    # Ejecutar acción real en el sistema operativo
                    result = execute_tool(tool.tool_name, tool.tool_input)
                    if result is not None:
                        if result.success:
                            print(format_action_success(result.message))
                        else:
                            print(format_action_error(result.message))

                        # Pronunciar confirmación hablada de la acción
                        confirmation = get_spoken_confirmation(
                            tool.tool_name,
                            tool.tool_input,
                            result,
                        )
                        speaker.speak(confirmation)

                    # Registrar resultado de la herramienta (role:"tool") en la
                    # memoria para que las llamadas futuras a Groq sean válidas.
                    tool_output = result.message if result is not None else "ok"
                    brain.record_tool_result(
                        tool.tool_use_id, tool.tool_name, tool_output
                    )
                    timing.mark("tool_fin", tool.tool_name)

            if not response.has_tool_use and not response.text_parts:
                print(f"{Colors.DIM}(Sin respuesta del modelo){Colors.RESET}")

        except Exception as e:
            logger.error(f"Error al procesar consulta: {e}", exc_info=True)
            print(f"\n{Colors.RED}Error al procesar: {e}{Colors.RESET}")

        # Tabla de tiempos de la interacción completa (t0 -> último audio).
        timing.summary()
        print()  # Línea en blanco


def record_and_process_voice(
    recorder: AudioRecorder,
    transcriber: WhisperTranscriber,
    brain: JarvisBrain,
    speaker: JarvisSpeaker,
    wake_detector: WakeWordDetector | None = None,
    status: StatusController | None = None,
) -> None:
    """Manejador de grabación y transcripción activado por voz/wake word."""
    logger = logging.getLogger(__name__)

    # Si Jarvis está pausado, ignorar cualquier disparo de interacción.
    if status is not None and status.is_paused():
        return

    if processing_lock.locked():
        return

    # Si la interacción no vino del wake word (p. ej. invocación manual), el
    # detector no registró t0: arrancamos la medición aquí.
    if not timing.active():
        timing.reset()
        timing.mark("t0_manual", "interacción sin wake word")

    # Pausar y liberar el micrófono del detector de wake word
    if wake_detector:
        wake_detector.pause()
        timing.mark("wake_pause_fin")

    responded = False
    try:
        if status is not None:
            status.set_state(AppState.LISTENING)
        print(f"\n{Colors.MAGENTA}{Colors.BOLD}🎤 [Escuchando...]{Colors.RESET} Habla ahora...")

        audio = recorder.record(
            max_duration=config.AUDIO_MAX_DURATION,
            silence_threshold=config.AUDIO_SILENCE_THRESHOLD,
            silence_duration=config.AUDIO_SILENCE_DURATION,
            min_speech_duration=config.AUDIO_MIN_SPEECH_DURATION,
        )
        if audio is None or len(audio) == 0:
            print(f"{Colors.DIM}[Voz] No se detectó audio suficiente. Acércate más al micrófono.{Colors.RESET}")
            return

        if status is not None:
            status.set_state(AppState.PROCESSING)
        print(f"{Colors.MAGENTA}⏳ [Transcribiendo...]{Colors.RESET}")
        text = transcriber.transcribe(audio, language=config.STT_LANGUAGE)

        if not text:
            print(f"{Colors.DIM}[Voz] No se reconoció ninguna palabra.{Colors.RESET}")
            return

        print(f"{Colors.GREEN}🗣️ [Voz detectada]:{Colors.RESET} \"{text}\"")
        responded = True
        process_query(brain, speaker, text, status, recorder, transcriber)

    except RuntimeError as e:
        logger.error(f"Error de audio: {e}", exc_info=True)
        print(f"\n{Colors.RED}[Error de Audio]{Colors.RESET} {e}")
    except Exception as e:
        logger.error(f"Error en STT: {e}", exc_info=True)
        print(f"\n{Colors.RED}[Error en STT]{Colors.RESET} {e}")
    finally:
        # Si no se llegó a responder (sin voz / sin texto), process_query no
        # imprimió su resumen: dejamos el desglose parcial de lo medido.
        if not responded:
            timing.summary("RESUMEN PARCIAL (sin respuesta hablada)")
        # Volver a idle en la UI.
        if status is not None:
            status.set_state(AppState.IDLE)
        # Reanudar la escucha del wake word solo si NO seguimos pausados.
        if wake_detector and (status is None or not status.is_paused()):
            wake_detector.resume()


def on_wake_word_triggered(
    recorder: AudioRecorder,
    transcriber: WhisperTranscriber,
    brain: JarvisBrain,
    speaker: JarvisSpeaker,
    wake_detector: WakeWordDetector,
    status: StatusController | None = None,
) -> None:
    """Callback disparado cuando se escucha 'Hey Jarvis'."""
    # Si Jarvis está pausado, el wake word no debe hacer nada.
    if status is not None and status.is_paused():
        return

    if processing_lock.locked():
        return

    print(f"\n{Colors.YELLOW}{Colors.BOLD}⚡ [Wake Word Detectado]: 'Hey Jarvis'{Colors.RESET}")
    # Pronunciar confirmación corta. Este tramo (síntesis edge-tts + reproducción
    # completa con wait=True) ocurre ENTRE t0 y t1: es tiempo muerto medible.
    timing.mark("ack_ini", "speak('¿Dime?')")
    speaker.speak("¿Dime?", wait=True)
    timing.mark("ack_fin")
    record_and_process_voice(recorder, transcriber, brain, speaker, wake_detector, status)


class _NullStream:
    """
    Stream inerte para reemplazar a sys.stdout/sys.stderr bajo pythonw.

    Bajo pythonw.exe ambos quedan en None (literal), y cualquier código -nuestro
    o de una librería como pystray/PIL/openwakeword- que intente escribir a
    stdout/stderr lanza AttributeError. Si eso ocurre dentro de un thread (p.ej.
    el del ícono de bandeja), el thread muere en silencio sin dejar rastro.

    Implementa la interfaz mínima de un stream de texto para ser un destino
    válido y no-operativo, en vez de dejar None.
    """

    def write(self, _data):  # noqa: ANN001
        return 0

    def flush(self):
        pass

    def isatty(self):
        return False

    def fileno(self):
        raise OSError("_NullStream no tiene descriptor de archivo")

    def writable(self):
        return True

    encoding = "utf-8"


def _guard_std_streams() -> None:
    """
    Reemplaza sys.stdout/sys.stderr por un objeto dummy cuando son None (pythonw),
    para que ningún write() -propio o de terceros, en cualquier thread- explote.
    El debug real se conserva vía logging a archivo.
    """
    if sys.stdout is None:
        sys.stdout = _NullStream()
    if sys.stderr is None:
        sys.stderr = _NullStream()


def _install_thread_excepthook(logger: logging.Logger) -> None:
    """
    Registra un threading.excepthook global que envía al log de archivo cualquier
    excepción no manejada en CUALQUIER thread. Sin esto, un fallo dentro del
    thread del tray (u otro) se pierde silenciosamente.
    """

    def _hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        thread_name = args.thread.name if args.thread else "desconocido"
        logger.error(
            "Excepción no manejada en el thread '%s':",
            thread_name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _hook


def _promote_tray_icon(logger: logging.Logger, tooltip: str = "Jarvis") -> None:
    """
    Fuerza a Windows 11 a mostrar SIEMPRE el ícono de bandeja de Jarvis.

    Windows guarda la preferencia "mostrar en la barra" por EJECUTABLE, en
    HKCU\\Control Panel\\NotifyIconSettings. Como python.exe y pythonw.exe son
    binarios distintos, activar el toggle para uno NO aplica al otro; por eso
    bajo pythonw el ícono queda oculto en el desbordamiento.

    Buscamos la entrada cuyo InitialTooltip sea 'Jarvis' (nuestro título) y le
    ponemos IsPromoted=1. Best-effort: si falla o aún no existe, no pasa nada
    (la próxima ejecución lo aplicará). Requiere que el ícono ya se haya
    registrado al menos una vez, por eso se llama con un pequeño retraso.
    """
    if os.name != "nt":
        return

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\NotifyIconSettings") as root:
            promoted = 0
            index = 0
            while True:
                try:
                    subname = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(
                        root, subname, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE
                    ) as sub:
                        try:
                            tip, _ = winreg.QueryValueEx(sub, "InitialTooltip")
                        except FileNotFoundError:
                            continue
                        if tip == tooltip:
                            winreg.SetValueEx(sub, "IsPromoted", 0, winreg.REG_DWORD, 1)
                            promoted += 1
                except OSError:
                    continue

            if promoted:
                logger.info(
                    "Tray: ícono '%s' promovido a visible en %d entrada(s) del registro.",
                    tooltip, promoted,
                )
            else:
                logger.debug(
                    "Tray: aún no existe entrada '%s' para promover (se aplicará luego).",
                    tooltip,
                )
    except OSError as e:
        logger.debug("Tray: no se pudo promover el ícono en el registro: %s", e)


def _setup_logging() -> logging.Logger:
    """
    Configura logging a archivo (siempre, con rotación) y a stdout si hay
    consola adjunta. El archivo es config.LOG_FILE y es lo que abre el tray.
    """
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    formatter = logging.Formatter(config.LOG_FORMAT)

    root = logging.getLogger()
    root.setLevel(level)
    # Evitar handlers duplicados si se reinvoca.
    for h in list(root.handlers):
        root.removeHandler(h)

    file_handler = logging.handlers.RotatingFileHandler(
        config.LOG_FILE,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Consola solo si realmente hay una (modo debug con `python main.py`).
    if sys.stdout is not None and getattr(sys.stdout, "isatty", lambda: False)():
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    return logging.getLogger(__name__)


def main() -> None:
    # Modo diagnóstico rápido de micrófonos: no arranca la app completa,
    # solo mide el nivel de cada dispositivo de entrada. Uso: python main.py --test-mic
    if "--test-mic" in sys.argv:
        import test_mic

        rest = [a for a in sys.argv[1:] if a != "--test-mic"]
        test_all = "--all" in rest
        duration = 3.0
        devices = None
        if "--duration" in rest:
            try:
                duration = float(rest[rest.index("--duration") + 1])
            except (ValueError, IndexError):
                pass
        if "--devices" in rest:
            di = rest.index("--devices") + 1
            devices = []
            while di < len(rest) and rest[di].isdigit():
                devices.append(int(rest[di]))
                di += 1
        test_mic.run(devices=devices, duration=duration, test_all=test_all)
        return

    # Bajo pythonw no hay stdout/stderr: protegerlos ANTES de nada.
    _guard_std_streams()

    # Configurar logging (archivo siempre + consola si la hay).
    logger = _setup_logging()
    # Capturar excepciones de cualquier thread (p.ej. el del tray) en el log.
    _install_thread_excepthook(logger)
    logger.info("=== Jarvis arrancando ===")

    # Los logs de arranque del ToolRegistry ('Allowlist cargada' / 'Resumen de
    # arranque') se generan en tiempo de import del paquete tools, ANTES de que
    # exista el handler de archivo, por lo que nunca llegaban a jarvis.log. El
    # registry los guardo; aqui, ya con el logging configurado, los re-emitimos.
    get_registry().replay_startup_log(logger)

    # Red de seguridad del log de cierre: '=== Jarvis cerrando ===' se emite
    # dentro de _shutdown(), que solo corre por 'Salir' del tray o por SIGINT.
    # Si el proceso termina por otro camino (excepcion no controlada, sys.exit
    # temprano, etc.), este hook deja igual constancia del cierre. NO cubre un
    # kill forzado (taskkill /F, apagado/cierre de sesion de Windows, cerrar la
    # ventana de consola), que mata al interprete sin ejecutar codigo Python.
    _shutdown_state = {"done": False}
    atexit.register(
        lambda: None
        if _shutdown_state["done"]
        else logger.info("=== Jarvis cerrando ===")
    )

    # Controlador de estado central para la UI (tray + indicador flotante).
    status = StatusController()

    # Inicializar el cerebro de Jarvis
    try:
        brain = JarvisBrain()
    except ValueError as e:
        logger.error("Error de configuración: %s. Copia .env.example a .env y configura tu API key.", e)
        sys.exit(1)

    logger.info("[OK] Cerebro Groq inicializado (Modelo: %s).", brain.model)

    # Inicializar módulo de voz de salida (TTS).
    # should_stop=status.is_paused permite que el TTS corte el audio en curso en
    # cuanto se activa la pausa (Ctrl+Alt+J), sin esperar a terminar la frase.
    speaker = JarvisSpeaker(
        voice=config.DEFAULT_TTS_VOICE,
        should_stop=status.is_paused,
    )
    logger.info("[OK] Sintetizador de voz (TTS) listo.")

    # Aviso fail-closed (1.1c): la allowlist se carga en tiempo de import del
    # paquete tools (al construir JARVIS_TOOLS), MUCHO antes de que exista el
    # JarvisSpeaker; por eso el aviso por voz no puede darse en el punto exacto
    # de la carga. Lo diferimos hasta aquí, apenas el speaker está disponible.
    if get_registry().allowlist_failed:
        logger.error(
            "[Arranque] La allowlist falló al cargar; Jarvis arrancó en MODO "
            "RESTRINGIDO (solo tools SAFE). Avisando por voz."
        )
        speaker.speak(
            "Aviso: no pude cargar la configuración de herramientas. "
            "Arrancando con permisos limitados.",
            wait=True,
        )

    # Inicializar componentes de entrada de voz (STT)
    recorder = AudioRecorder()
    transcriber = WhisperTranscriber(
        model_size=config.STT_MODEL_SIZE,
        device=config.STT_DEVICE,
        compute_type=config.STT_COMPUTE_TYPE
    )

    # Inicializar detector de Wake Word en segundo plano
    wake_detector = WakeWordDetector(
        on_detected=lambda: on_wake_word_triggered(recorder, transcriber, brain, speaker, wake_detector, status),
        model_names=config.WAKE_WORD_MODELS,
        threshold=config.WAKE_WORD_THRESHOLD,
    )

    try:
        wake_detector.start()
        logger.info("[OK] Wake Word activo: Di 'Hey Jarvis' para activar.")
    except Exception as e:
        logger.error("No se pudo activar el Wake Word: %s", e, exc_info=True)

    # Hotkey Ctrl+Alt+J: toggle global de pausa/reanudar (mismo estado que el tray).
    def _toggle_pause() -> None:
        paused = status.toggle_paused()
        if paused:
            logger.info("[Pausa] Jarvis PAUSADO (wake word y procesamiento desactivados).")
            # Cortar de inmediato cualquier audio que esté sonando, sin esperar a
            # que termine la frase en curso.
            speaker.interrupt()
            wake_detector.pause()
        else:
            logger.info("[Pausa] Jarvis REANUDADO.")
            if wake_detector.is_running:
                wake_detector.resume()

    hotkey = HotkeyListener(
        on_trigger=_toggle_pause,
        hotkey_combo=config.HOTKEY_COMBO,
    )

    try:
        hotkey.start()
        logger.info("[OK] Hotkey activo: Ctrl+Alt+J para pausar/reanudar.")
    except Exception as e:
        logger.error("No se pudo activar el hotkey global: %s", e, exc_info=True)

    # -- Arranque de la UI ------------------------------------------------------

    # Evento para mantener vivo el thread principal cuando NO hay loop Qt.
    stop_event = threading.Event()
    qt_app = {"app": None}  # Contenedor mutable para acceder desde el shutdown.

    def _shutdown() -> None:
        """Cierre limpio: detiene voz, hotkey y (si aplica) el loop Qt."""
        if _shutdown_state["done"]:
            return
        _shutdown_state["done"] = True
        logger.info("=== Jarvis cerrando ===")
        try:
            wake_detector.stop()
        except Exception:
            pass
        try:
            hotkey.stop()
        except Exception:
            pass
        try:
            speaker.stop()
        except Exception:
            pass
        stop_event.set()
        app = qt_app["app"]
        if app is not None:
            # Cerrar el loop Qt desde su propio thread de forma segura.
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, app.quit)

    # Ícono de bandeja (siempre) en su propio thread.
    tray = TrayIcon(status, on_quit=_shutdown, log_file=config.LOG_FILE)
    try:
        tray.start()
        logger.info("[OK] Ícono de bandeja activo.")
        # Asegurar que Windows muestre el ícono en la barra (no en el
        # desbordamiento), incluso bajo pythonw.exe. Se hace con un pequeño
        # retraso para que el ícono ya esté registrado en el sistema.
        threading.Timer(3.0, _promote_tray_icon, args=(logger,)).start()
    except Exception as e:
        logger.error("No se pudo iniciar el ícono de bandeja: %s", e, exc_info=True)

    # Ctrl+C debe cerrar Jarvis de forma limpia (misma ruta que 'Salir').
    def _handle_sigint(signum, frame) -> None:  # noqa: ANN001
        logger.info("SIGINT (Ctrl+C) recibido. Cerrando...")
        _shutdown()

    signal.signal(signal.SIGINT, _handle_sigint)

    # Indicador flotante (exploratorio) — solo si está habilitado en config.
    if config.FLOATING_INDICATOR_ENABLED:
        try:
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import QTimer
            from ui.floating_indicator import FloatingIndicator

            app = QApplication.instance() or QApplication(sys.argv)
            app.setQuitOnLastWindowClosed(False)
            qt_app["app"] = app

            indicator = FloatingIndicator()
            status.add_observer(indicator.on_status_change)

            # Timer "vacío" cada 200ms: devuelve el control al intérprete de
            # Python periódicamente para que pueda atender señales (SIGINT).
            # Sin esto, app.exec() bloquea el thread y Ctrl+C nunca se procesa.
            sigint_timer = QTimer()
            sigint_timer.start(200)
            sigint_timer.timeout.connect(lambda: None)

            logger.info("[OK] Indicador flotante activo. Corriendo loop Qt en el thread principal.")

            # El loop Qt bloquea el thread principal hasta app.quit()
            # (desde 'Salir' del tray o desde el manejador de Ctrl+C).
            app.exec()
        except Exception as e:
            logger.error("No se pudo iniciar el indicador flotante: %s", e, exc_info=True)
            # Fallback: mantener viva la app con el tray hasta 'Salir'/Ctrl+C.
            try:
                stop_event.wait()
            except KeyboardInterrupt:
                _shutdown()
    else:
        # Sin Qt: el thread principal solo espera hasta que 'Salir'/Ctrl+C lo libere.
        logger.info("Indicador flotante deshabilitado (solo tray). Esperando eventos.")
        try:
            stop_event.wait()
        except KeyboardInterrupt:
            _shutdown()

    # Garantizar limpieza si salimos del loop Qt sin pasar por _shutdown.
    try:
        wake_detector.stop()
        hotkey.stop()
    except Exception:
        pass


if __name__ == "__main__":
    main()
