"""
memory/conversation_store.py
Persistencia y gestión de la memoria de conversación para Jarvis.
Almacena el historial reciente en formato JSON para mantener el contexto entre sesiones.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import config


class ConversationMemory:
    """
    Gestiona el almacenamiento persistente del historial de conversación
    con rotación automática de los mensajes más antiguos.
    """

    def __init__(
        self,
        file_path: Path | str | None = None,
        max_messages: int | None = None,
    ) -> None:
        self.file_path = Path(file_path or config.MEMORY_FILE)
        self.max_messages = max_messages or config.MAX_MEMORY_MESSAGES
        self.messages: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        """Carga el historial desde el archivo JSON si existe."""
        if not self.file_path.exists():
            self.messages = []
            return

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    self.messages = data[-self.max_messages:]
                else:
                    self.messages = []
        except Exception:
            self.messages = []

    def _save(self) -> None:
        """Guarda el historial en el archivo JSON local de forma segura."""
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.messages, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def add_user_message(self, content: str) -> None:
        """Registra un mensaje enviado por el usuario."""
        message = {
            "role": "user",
            "content": content,
            "timestamp": time.time(),
        }
        self.messages.append(message)
        self._trim_and_save()

    def add_assistant_message(
        self,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        """Registra una respuesta generada por Jarvis."""
        message: dict[str, Any] = {
            "role": "assistant",
            "content": content or "",
            "timestamp": time.time(),
        }
        if tool_calls:
            message["tool_calls"] = tool_calls

        self.messages.append(message)
        self._trim_and_save()

    def add_tool_message(
        self,
        tool_call_id: str,
        name: str,
        content: str,
    ) -> None:
        """
        Registra el resultado de una herramienta como mensaje role:"tool".
        Debe ir después de un mensaje assistant con el tool_call correspondiente
        para que el historial sea válido para Groq/OpenAI.
        """
        message = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content or "",
            "timestamp": time.time(),
        }
        self.messages.append(message)
        self._trim_and_save()

    def get_messages_for_llm(self) -> list[dict[str, Any]]:
        """
        Retorna la lista de mensajes formateada para la API de Groq/OpenAI,
        omitiendo campos internos como 'timestamp'.

        Sanea el historial para garantizar que sea válido:
        - Cada tool_call de un assistant SOLO se incluye si tiene su respuesta
          role:"tool" correspondiente (evita error 400 de Groq).
        - Cada mensaje role:"tool" SOLO se incluye si su tool_call_id fue
          declarado por un assistant previo (evita huérfanos por recorte).
        """
        # Paso 1: ids de tool_calls que SÍ tienen respuesta 'tool'.
        answered_ids = {
            msg["tool_call_id"]
            for msg in self.messages
            if msg.get("role") == "tool" and msg.get("tool_call_id")
        }

        formatted: list[dict[str, Any]] = []
        valid_ids: set[str] = set()

        for msg in self.messages:
            role = msg.get("role")

            if role == "assistant":
                entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.get("content", ""),
                }
                tool_calls = msg.get("tool_calls") or []
                kept = [tc for tc in tool_calls if tc.get("id") in answered_ids]
                if kept:
                    entry["tool_calls"] = kept
                    for tc in kept:
                        valid_ids.add(tc["id"])
                formatted.append(entry)

            elif role == "tool":
                tcid = msg.get("tool_call_id")
                if tcid in valid_ids:
                    formatted.append({
                        "role": "tool",
                        "tool_call_id": tcid,
                        "name": msg.get("name", ""),
                        "content": msg.get("content", ""),
                    })

            else:  # user / system
                formatted.append({
                    "role": role,
                    "content": msg.get("content", ""),
                })

        return formatted

    def _trim_and_save(self) -> None:
        """Mantiene únicamente los últimos N mensajes y persiste los cambios."""
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]
        self._save()

    def clear(self) -> None:
        """Limpia todo el historial de conversación y el archivo persistente."""
        self.messages.clear()
        try:
            if self.file_path.exists():
                self.file_path.unlink()
        except OSError:
            pass

    def __len__(self) -> int:
        return len(self.messages)
