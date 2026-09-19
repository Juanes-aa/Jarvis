"""
brain/groq_client.py
Clase principal JarvisBrain que envía mensajes a la API de Groq
(compatible con OpenAI) con herramientas (tools), persistencia de memoria
y manejo robusto de errores.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from openai import (
    OpenAI,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
    APIError,
)

from brain.tools_schema import JARVIS_TOOLS
from memory.conversation_store import ConversationMemory
import config
import pipeline_timing as timing

logger = logging.getLogger(__name__)

# -- Configuración -------------------------------------------------------------

GROQ_BASE_URL = config.GROQ_BASE_URL
DEFAULT_MODEL = config.GROQ_MODEL
MAX_TOKENS = config.MAX_TOKENS
SYSTEM_PROMPT = config.SYSTEM_PROMPT


# -- Saneamiento de errores de la API (Bug 1) ---------------------------------
#
# La API de Groq puede devolver errores 4xx/5xx cuyo cuerpo incluye el JSON
# completo del error e incluso el 'failed_generation' con el razonamiento interno
# del modelo. Ese texto NUNCA debe llegar al TTS: en un caso real Jarvis leyó en
# voz alta ~48s de JSON crudo. Aquí clasificamos la excepción en una "familia" de
# error y devolvemos SOLO una frase corta y genérica para hablar. El error
# completo se loguea aparte a nivel ERROR (sin recortar) para diagnóstico.

# Mensaje hablado corto por familia de error. Máximo una frase.
_ERROR_SPEECH: dict[str, str] = {
    "parsing": "Tuve un problema procesando eso, ¿puedes repetirlo?",
    "network": "No pude conectarme con el servicio, intenta de nuevo.",
    "rate_limit": "Estoy recibiendo muchas peticiones, dame unos segundos.",
    "server": "El servicio de inteligencia falló un momento, intenta de nuevo.",
    "generic": "Tuve un problema procesando eso, ¿puedes intentarlo otra vez?",
}

# Sufijo breve cuando el mismo tipo de error se repite en la sesión. Sigue sin
# leer nada del JSON: solo sugiere revisar los logs.
_ERROR_REPEAT_SUFFIX = " Si sigue pasando, revisa los registros."

# Palabras clave (en minúsculas) que identifican un fallo de parsing/tool_use en
# el cuerpo del error 400. Se usan SOLO para clasificar, nunca para hablar.
_PARSING_MARKERS = (
    "tool_use_failed",
    "tool call validation",
    "parsing failed",
    "output_parse_failed",
    "failed_generation",
    "json_validate_failed",
)


def _classify_groq_error(exc: Exception) -> str:
    """
    Clasifica una excepción de la llamada a Groq en una familia de error, sin
    exponer nunca su contenido. Devuelve una de las claves de _ERROR_SPEECH.
    """
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return "network"
    if isinstance(exc, RateLimitError):
        return "rate_limit"
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None)
        if status is not None and 500 <= status < 600:
            return "server"
        # 4xx: intentar distinguir fallos de parsing / tool_use por marcadores.
        blob = f"{getattr(exc, 'code', '') or ''} {str(exc)}".lower()
        if any(marker in blob for marker in _PARSING_MARKERS):
            return "parsing"
        return "parsing"  # cualquier 4xx de la API se trata como "no te entendí"
    if isinstance(exc, APIError):
        return "generic"
    return "generic"


# -- Resultado estructurado ---------------------------------------------------

@dataclass
class ToolResult:
    """Resultado cuando el modelo decide invocar una herramienta."""
    tool_name: str
    tool_input: dict[str, Any]
    tool_use_id: str


@dataclass
class TextResult:
    """Resultado cuando el modelo responde con texto libre (sin tool_use)."""
    text: str


@dataclass
class JarvisResponse:
    """Respuesta completa de JarvisBrain.process()."""
    tool_results: list[ToolResult] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    @property
    def has_tool_use(self) -> bool:
        return len(self.tool_results) > 0

    @property
    def primary_tool(self) -> ToolResult | None:
        """Devuelve la primera herramienta invocada, si la hay."""
        return self.tool_results[0] if self.tool_results else None

    @property
    def combined_text(self) -> str:
        """Combina todos los fragmentos de texto en uno solo."""
        return "\n".join(self.text_parts)


# -- Clase principal -----------------------------------------------------------

class JarvisBrain:
    """
    Interfaz principal con la API de Groq (OpenAI-compatible) con memoria persistente.
    """

    def __init__(
        self,
        api_key: str | None = None,
        memory: ConversationMemory | None = None,
        model: str | None = None,
    ) -> None:
        key = api_key or config.GROQ_API_KEY
        if not key:
            raise ValueError(
                "Se requiere GROQ_API_KEY. "
                "Configúrala en el archivo .env o pásala al constructor."
            )
        self.model = model or config.GROQ_MODEL
        self.client = OpenAI(
            api_key=key,
            base_url=GROQ_BASE_URL,
            timeout=25.0,
            max_retries=2,
        )
        self.memory = memory or ConversationMemory()

        # Racha de errores para no leer JSON pero sí sugerir revisar logs si el
        # mismo tipo de error se repite (Bug 1). Se resetea ante cualquier éxito.
        self._last_error_kind: str | None = None
        self._error_streak: int = 0

    def _build_error_response(self, exc: Exception) -> JarvisResponse:
        """
        Convierte una excepción de la llamada a Groq en una respuesta hablada
        corta y genérica. Loguea el error COMPLETO (sin recortar) a nivel ERROR
        para diagnóstico, pero NUNCA pasa el texto crudo al TTS.
        """
        kind = _classify_groq_error(exc)

        # Log completo, sin recortar, con traceback (esto no cambia: sigue siendo
        # la fuente de verdad para depurar en el archivo de log).
        logger.error(
            "[Groq] Error en la llamada a la API (familia=%s): %s",
            kind, exc, exc_info=True,
        )

        # Actualizar racha del mismo tipo de error.
        if kind == self._last_error_kind:
            self._error_streak += 1
        else:
            self._last_error_kind = kind
            self._error_streak = 1

        speech = _ERROR_SPEECH.get(kind, _ERROR_SPEECH["generic"])
        if self._error_streak >= 2:
            speech += _ERROR_REPEAT_SUFFIX

        return JarvisResponse(
            tool_results=[
                ToolResult(
                    tool_name="responder_conversacional",
                    tool_input={"texto": speech},
                    tool_use_id=f"groq_error_{kind}",
                )
            ]
        )

    # -- API pública -----------------------------------------------------------

    def process(self, user_input: str) -> JarvisResponse:
        """
        Procesa una entrada de texto del usuario incluyendo el contexto histórico.

        Args:
            user_input: Texto escrito o transcrito del usuario.

        Returns:
            JarvisResponse con la(s) herramienta(s) invocada(s) y/o texto.
        """
        # 1. Registrar mensaje del usuario en la memoria persistente
        self.memory.add_user_message(user_input)

        # 2. Construir lista de mensajes con el system prompt al inicio
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *self.memory.get_messages_for_llm(),
        ]
        timing.mark("llm_msgs_listos", f"mensajes={len(messages)}")

        try:
            # 3. Llamar a la API de Groq con tools
            timing.mark("llm_http_ini", f"model={self.model}")
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                tools=JARVIS_TOOLS,
                messages=messages,
            )
            timing.mark("llm_http_fin")

            # 4. Parsear la respuesta
            message = response.choices[0].message
            jarvis_response = self._parse_response(message)

            # 5. Serializar y registrar respuesta del asistente en la memoria
            serialized_tool_calls = None
            if message.tool_calls:
                serialized_tool_calls = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ]

            self.memory.add_assistant_message(
                content=message.content or "",
                tool_calls=serialized_tool_calls,
            )

            # Éxito: se rompe cualquier racha de errores previa.
            self._last_error_kind = None
            self._error_streak = 0

            return jarvis_response

        except APIError as e:
            # Cubre APIConnectionError, APITimeoutError, RateLimitError,
            # APIStatusError (4xx/5xx incl. tool_use_failed / parsing failed) y
            # cualquier otro error de la API. Se sanea a una frase corta antes de
            # que pueda llegar al TTS; el error completo va al log a nivel ERROR.
            return self._build_error_response(e)
        except Exception as e:  # noqa: BLE001 - red de seguridad final
            # Cualquier fallo inesperado en la llamada/parseo tampoco debe
            # convertirse en JSON hablado: mensaje genérico + log completo.
            return self._build_error_response(e)

    def record_tool_result(self, tool_use_id: str, tool_name: str, result: str) -> None:
        """
        Registra en la memoria el resultado de una herramienta como mensaje
        role:"tool". Esto es obligatorio tras un mensaje assistant con
        tool_calls para que el historial sea válido en llamadas futuras a Groq.

        Args:
            tool_use_id: id del tool_call correspondiente (tool_call_id).
            tool_name: nombre de la herramienta ejecutada.
            result: resultado textual de la ejecución.
        """
        self.memory.add_tool_message(
            tool_call_id=tool_use_id,
            name=tool_name,
            content=result,
        )

    def reset_history(self) -> None:
        """Limpia todo el historial de conversación en memoria y en disco."""
        self.memory.clear()

    # -- Internos --------------------------------------------------------------

    @staticmethod
    def _parse_response(message) -> JarvisResponse:
        """Extrae tool_calls y texto de la respuesta de OpenAI/Groq."""
        result = JarvisResponse()

        # Texto libre del modelo
        if message.content and message.content.strip():
            result.text_parts.append(message.content.strip())

        # Llamadas a herramientas
        if message.tool_calls:
            for tool_call in message.tool_calls:
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    arguments = {}

                result.tool_results.append(
                    ToolResult(
                        tool_name=tool_call.function.name,
                        tool_input=arguments,
                        tool_use_id=tool_call.id,
                    )
                )

        return result
