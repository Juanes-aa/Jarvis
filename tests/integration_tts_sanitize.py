"""
Prueba real de la ruta TTS con texto con Markdown.
Inicializa JarvisSpeaker (sin tocar el micrófono ni el wake word),
manda un texto con Markdown a speak_stream y deja que el log capture
el "antes/después" del saneamiento.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from tts.speaker import JarvisSpeaker

handler = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
handler.setFormatter(logging.Formatter(config.LOG_FORMAT))
root = logging.getLogger()
root.setLevel(logging.INFO)
root.handlers = [handler]

# Ejemplo de respuesta de LLM con formato Markdown, como si pidieras
# "dame una lista de 3 cosas que puedes hacer".
markdown_input = (
    "**Claro** que sí. Aquí tienes *tres* cosas que puedo hacer:\n"
    "- Abrir aplicaciones del sistema.\n"
    "- `Buscar` información en tu computadora.\n"
    "- Ajustar el volumen o el brillo.\n"
    "\\n Espero que te sirva."
)

speaker = JarvisSpeaker()
speaker.speak_stream(markdown_input)
