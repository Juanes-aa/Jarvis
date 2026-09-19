"""
brain/tools_schema.py
Definicion de herramientas (tools) que Jarvis puede invocar a traves de Groq.
Cada herramienta sigue el formato de la API de OpenAI/Groq (function calling).

Sub-fase 1.1b: TODAS las tools ya fueron migradas al nuevo Tool Registry
(paquete tools/). Ya no queda nada literal aqui: el schema se genera 100% desde
el registry, respetando la allowlist configurable. Importar el paquete tools
dispara el descubrimiento (imports explicitos) que registra cada tool.

JARVIS_TOOLS (lo que consume brain/groq_client.py) es exactamente el schema de
las tools activas del registry, de modo que el LLM sigue viendo el mismo
conjunto de herramientas que antes de migrar.
"""

from __future__ import annotations

import logging

from tools import get_registry

logger = logging.getLogger(__name__)


def _build_tools() -> list[dict]:
    """Genera el schema de JARVIS_TOOLS desde el registry (tools activas)."""
    registry = get_registry()
    schemas = registry.get_schemas()
    names = [s["function"]["name"] for s in schemas]
    logger.info(
        "[tools_schema] JARVIS_TOOLS armado desde el registry: %d tool(s) activa(s) = %s",
        len(schemas),
        names,
    )
    return schemas


# Conjunto completo de tools que ve el LLM (todas migradas al registry).
JARVIS_TOOLS: list[dict] = _build_tools()
