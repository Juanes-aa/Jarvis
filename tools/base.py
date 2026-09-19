"""
tools/base.py
Bloques base del Tool Registry de Jarvis (sub-fase 1.1a).

Contiene:
- RiskLevel:            nivel de riesgo de una tool (solo metadata por ahora).
- ToolExecutionResult:  resultado de EJECUTAR una tool.
- Tool:                 clase base abstracta que toda tool debe implementar.

NOTA sobre el nombre 'ToolExecutionResult':
    En brain/groq_client.py ya existe una dataclass llamada 'ToolResult', pero
    ahi significa "el LLM SOLICITO llamar a una tool" (name + input + id), NO el
    resultado de ejecutarla. Para evitar cualquier ambiguedad (incluso visual)
    esta clase se llama ToolExecutionResult. El nombre confuso de groq_client se
    deja intacto en esta entrega; queda como deuda tecnica para la 1.1b.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any


class RiskLevel(Enum):
    """
    Nivel de riesgo de una tool.

    El flujo de confirmacion por voz para las tools DESTRUCTIVE ya esta
    implementado y en uso: no vive aqui (el registry sigue siendo un despachador
    puro y sincrono), sino que main.py consulta el risk_level antes de ejecutar
    y, si es DESTRUCTIVE, pide confirmacion hablada (ver _run_voice_confirmation
    en main.py). Este enum sigue siendo la fuente de verdad de ese nivel.
    """
    SAFE = "safe"                # Sin efectos secundarios reales (p. ej. solo texto).
    REVERSIBLE = "reversible"    # Cambia el estado del sistema, pero es facil de deshacer.
    DESTRUCTIVE = "destructive"  # Cambios dificiles o imposibles de revertir.


@dataclass
class ToolExecutionResult:
    """
    Resultado de ejecutar una tool.

    Es el equivalente conceptual del ActionResult que ya existe en
    actions/system_control.py; se mantiene compatible en atributos
    (success, message) para que el resto del pipeline no cambie.

    Separacion mensaje tecnico / mensaje hablado:
        'message' es el detalle tecnico COMPLETO, pensado para el log y el
        historial (puede incluir rutas, timestamps, titulos de ventana, etc.).
        'spoken_message' es la version CORTA y natural que se dice en voz alta
        por TTS. Muchos mensajes tecnicos no fueron escritos para leerse en voz
        alta (una ruta completa o un titulo de pestana de navegador se leen
        letra por letra y suenan fatal). Cuando una tool no define
        'spoken_message', el pipeline usa 'message' como fallback (ver la
        propiedad 'speech'), para no romper las tools cuyo 'message' ya es corto
        y hablable (p. ej. pausar_reproducir).
    """
    success: bool
    message: str
    data: dict[str, Any] | None = None
    #: Version corta y natural para decir por voz. Si es None, se usa 'message'.
    spoken_message: str | None = None

    @property
    def speech(self) -> str:
        """Texto que debe decirse por voz: 'spoken_message' o, si falta, 'message'."""
        return self.spoken_message if self.spoken_message else self.message


class Tool(ABC):
    """
    Clase base para todas las tools de Jarvis.

    Cada tool declara su contrato (name/description/parameters) en formato
    compatible con el function calling de Groq/OpenAI, su nivel de riesgo, y
    la logica de ejecucion en execute().
    """

    #: Nombre unico de la tool (el que ve y llama el LLM).
    name: str = ""
    #: Descripcion en lenguaje natural para el LLM.
    description: str = ""
    #: JSON schema de los parametros (formato function calling).
    parameters: dict[str, Any] = {}
    #: Nivel de riesgo. Las tools DESTRUCTIVE disparan confirmacion por voz en
    #: main.py (_run_voice_confirmation) antes de ejecutarse.
    risk_level: RiskLevel = RiskLevel.SAFE
    #: Fuerza la confirmacion por voz INDEPENDIENTEMENTE del risk_level.
    #:
    #: risk_level y requires_confirmation son ejes distintos: risk_level describe
    #: la reversibilidad/peligrosidad intrinseca de la accion, mientras que
    #: requires_confirmation es una decision de diseno explicita de "pedir
    #: confirmacion hablada antes de ejecutar", aunque la accion no sea
    #: DESTRUCTIVE. Ejemplo: cerrar_ventana_activa es REVERSIBLE (no destruye
    #: nada a nivel de sistema) pero podria perder trabajo no guardado, asi que
    #: pide confirmacion. main.py confirma si risk_level == DESTRUCTIVE O si
    #: requires_confirmation es True. Utl tambien para casos futuros (p. ej.
    #: "enviar correo" en 1.3: tecnicamente REVERSIBLE pero deberia confirmar).
    requires_confirmation: bool = False
    #: Frase legible de la accion para el prompt de confirmacion por voz.
    #:
    #: El mensaje de confirmacion NO debe usar el nombre crudo de la tool (p. ej.
    #: 'cerrar_ventana_activa' se leeria como "cerrar guion bajo ventana guion
    #: bajo activa"). Cada tool que pida confirmacion define aqui una descripcion
    #: natural de la accion (p. ej. "cerrar la ventana activa"), que main.py usa
    #: para construir "Vas a <confirmation_label>. Confirmas?". Si queda vacio, se
    #: cae a un fallback generico basado en el nombre.
    confirmation_label: str = ""

    def to_schema(self) -> dict[str, Any]:
        """Genera la entrada de schema para la API de Groq (function calling)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """
        Ejecuta la accion de la tool.

        Los kwargs son los parametros que el LLM decidio pasar (ya parseados
        desde JSON). Debe devolver SIEMPRE un ToolExecutionResult.
        """
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuracion
        return f"<Tool name={self.name!r} risk={self.risk_level.value}>"
