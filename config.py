"""
config.py
Configuración centralizada para Jarvis.
Consolida variables de entorno, nombres de modelos, voces TTS, rutas y configuraciones generales.
"""

import os
from pathlib import Path

# -- Configuración de API Groq/OpenAI -------------------------------------------

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_TOKENS = 1024

# -- System Prompt para Jarvis -------------------------------------------------

SYSTEM_PROMPT = (
    "Eres Jarvis, un asistente de escritorio inteligente para Windows. "
    "Tu objetivo es ayudar al usuario controlando su computadora y respondiendo "
    "preguntas. Siempre responde en español.\n\n"
    "FORMATO DE RESPUESTA Y VOZ:\n"
    "- Todas tus respuestas serán leídas en voz alta mediante un sintetizador de voz (TTS).\n"
    "- Responde SIEMPRE en texto plano y conversacional, exactamente como si estuvieras hablando.\n"
    "- PROHIBIDO usar formato Markdown: NO uses negritas (**), cursivas (*), código entre comillas invertidas (`), "
    "encabezados (#), ni listas con viñetas (- o *).\n"
    "- Si necesitas enumerar puntos, hazlo de forma hablada y fluida con oraciones completas y pausas naturales "
    "(por ejemplo: 'Primero, puedo... Segundo, también puedo...').\n\n"
    "REGLAS:\n"
    "1. Si el usuario quiere realizar una ACCIÓN en su computadora (abrir apps, "
    "cambiar volumen, ajustar brillo, ejecutar comandos), usa la herramienta "
    "apropiada.\n"
    "2. Si el usuario hace una PREGUNTA o entabla conversación, usa la "
    "herramienta 'responder_conversacional'.\n"
    "3. Sé conciso y directo. No expliques qué herramienta vas a usar, "
    "simplemente úsala.\n"
    "4. 'ejecutar_comando' SOLO puede crear carpetas ('mkdir <ruta>'). No puede "
    "ejecutar PowerShell arbitrario. Si te piden borrar, mover, cerrar procesos "
    "o cualquier otra cosa, responde con 'responder_conversacional' diciendo que "
    "no puedes hacerlo por seguridad.\n"
    "5. Mantén la coherencia con el contexto previo de la conversación."
)

# -- Configuración de TTS (Text to Speech) -------------------------------------

# Voces recomendadas en español de Microsoft Edge:
# - 'es-MX-JorgeNeural' (México, asistente masculino)
# - 'es-ES-AlvaroNeural' (España, tono calmado)
# - 'es-US-AlonsoNeural' (Español neutro)
DEFAULT_TTS_VOICE = os.getenv("TTS_VOICE", "es-MX-JorgeNeural")
TTS_RATE = os.getenv("TTS_RATE", "+0%")
TTS_VOLUME = os.getenv("TTS_VOLUME", "+0%")
TTS_PITCH = os.getenv("TTS_PITCH", "+0Hz")

# -- Configuración de STT (Speech to Text) -------------------------------------

STT_SAMPLE_RATE = 16000
STT_DTYPE = "float32"
# 'small' ofrece el mejor equilibrio latencia/precisión para español en CPU (int8).
# 'base' es más rápido pero falla con acentos; 'medium' es más preciso pero lento en CPU.
STT_MODEL_SIZE = os.getenv("STT_MODEL_SIZE", "small")
STT_DEVICE = os.getenv("STT_DEVICE", "cpu")
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "int8")
STT_LANGUAGE = "es"

# Configuración de grabación de audio
AUDIO_MAX_DURATION = 12.0
# Umbral RMS de silencio de RESPALDO. Si la calibración automática de ruido
# ambiente está activada, este valor solo se usa como piso mínimo de seguridad.
AUDIO_SILENCE_THRESHOLD = 0.008
AUDIO_SILENCE_DURATION = 1.6
AUDIO_MIN_SPEECH_DURATION = 0.4

# Calibración automática del VAD (mide el ruido de fondo al inicio de cada grabación)
AUDIO_CALIBRATION_ENABLED = True
AUDIO_CALIBRATION_DURATION = 0.6   # Segundos de ruido de fondo a medir
AUDIO_NOISE_MULTIPLIER = 2.5       # threshold = ruido_ambiente * multiplicador
AUDIO_MIN_THRESHOLD = 0.004        # Piso mínimo para evitar umbrales demasiado bajos
AUDIO_MAX_THRESHOLD = 0.05         # Techo para evitar umbrales imposibles de superar
AUDIO_INITIAL_GRACE = 6.0          # Segundos de espera para que el usuario empiece a hablar

# Logs de depuración de audio (nivel RMS en tiempo real y texto crudo transcrito)
AUDIO_DEBUG = os.getenv("AUDIO_DEBUG", "1") == "1"

# -- Configuración de Wake Word ------------------------------------------------

# Umbral de confianza para disparar el wake word. Calibrado con logs reales:
# los picos al decir "Hey Jarvis" llegaban a ~0.342, por lo que 0.35 nunca
# disparaba. 0.25 deja margen suficiente por encima del ruido de fondo (~0.00-0.01).
WAKE_WORD_THRESHOLD = 0.25
WAKE_WORD_SAMPLE_RATE = 16000
WAKE_WORD_CHUNK_SIZE = 1280
# Solo 'hey_jarvis' activa a Jarvis. Se quitó 'alexa' para evitar activaciones falsas.
WAKE_WORD_MODELS = ["hey_jarvis"]
# Índice del dispositivo de ENTRADA para el wake word (sounddevice).
# None = usa el dispositivo por defecto del sistema. Fija un índice concreto
# (visible en el log "[Audio] Dispositivos de entrada") si el default es un
# dispositivo agregado/mixto que degrada la detección.
_wake_dev = os.getenv("WAKE_WORD_DEVICE_INDEX")
WAKE_WORD_DEVICE_INDEX = int(_wake_dev) if _wake_dev not in (None, "") else None

# -- Configuración de Hotkey ----------------------------------------------------

HOTKEY_COMBO = "<ctrl>+<alt>+j"

# -- Configuración de Memoria ---------------------------------------------------

MEMORY_DIR = Path(__file__).parent / "memory"
MEMORY_FILE = MEMORY_DIR / "history.json"
MAX_MEMORY_MESSAGES = 20  # Últimos 10 turnos de pregunta/respuesta

# -- Configuración de Tools (Tool Registry) ------------------------------------

# Allowlist configurable de tools activas (sub-fase 1.1b). El ToolRegistry lee
# este archivo al arrancar y SOLO expone al LLM / permite ejecutar las tools
# listadas en "active_tools", aunque haya mas registradas en el codigo. Esto
# permite desactivar una tool puntual sin tocar codigo (util para 1.1c y fases
# siguientes). Politica FAIL-CLOSED (sub-fase 1.1c): si el archivo no existe o
# es invalido, NO se activan todas las tools; se activan SOLO las tools SAFE y
# se deja un ERROR visible en el log.
TOOLS_ALLOWLIST_FILE = Path(__file__).parent / "tools_config.json"

# Confirmacion por voz para acciones DESTRUCTIVE (sub-fase 1.1c). Antes de
# ejecutar una tool con risk_level == DESTRUCTIVE, Jarvis pide confirmacion
# hablada y escucha UNA respuesta sin necesitar el wake word. Este es el grace
# period (en segundos) que espera a que el usuario empiece a responder; si no
# se detecta nada, se CANCELA por seguridad (nunca se ejecuta ante silencio).
TOOL_CONFIRMATION_TIMEOUT = 12.0

# -- Configuración de Logging ---------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Archivo de log. Al correr sin consola (pythonw) se pierde stdout, por lo que
# siempre escribimos a este archivo para conservar visibilidad de debug.
# La opción "Ver logs" del ícono de bandeja abre exactamente este archivo.
LOG_DIR = Path(__file__).parent / "logs"
LOG_FILE = LOG_DIR / "jarvis.log"
LOG_MAX_BYTES = 2_000_000  # ~2 MB por archivo antes de rotar
LOG_BACKUP_COUNT = 3       # Cantidad de archivos rotados a conservar

# -- Configuración de UI (bandeja del sistema + indicador flotante) -------------

# Indicador flotante tipo Siri. Exploratorio: si no convence, poner en False
# para quedarse solo con el cambio de estado/color del ícono de bandeja.
FLOATING_INDICATOR_ENABLED = os.getenv("FLOATING_INDICATOR_ENABLED", "1") == "1"

# Colores placeholder por estado (RGB). Ajustables después.
# Se usan tanto para el ícono de bandeja como para el indicador flotante.
STATE_COLORS = {
    "idle": (120, 120, 130),        # Gris apagado
    "listening": (0, 150, 255),     # Azul
    "processing": (0, 200, 120),    # Verde
    "speaking": (170, 90, 255),     # Morado
    "paused": (70, 70, 75),         # Oscuro (capa de pausa, prioridad visual)
}
