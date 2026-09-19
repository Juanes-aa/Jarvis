"""
tests/test_sanitize_for_speech.py
Pruebas de regresión para el saneamiento de texto antes de TTS.
Importa sólo la función pura; no inicializa audio ni edge-tts.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tts.speaker import sanitize_for_speech


def test_bold_italic_code_and_headers():
    assert sanitize_for_speech("**negrita**") == "negrita."
    assert sanitize_for_speech("*cursiva*") == "cursiva."
    assert sanitize_for_speech("`codigo`") == "codigo."
    assert sanitize_for_speech("# Encabezado") == "Encabezado."
    assert sanitize_for_speech("## Subtitulo") == "Subtitulo."


def test_unordered_and_ordered_lists():
    assert sanitize_for_speech("- item uno") == "item uno."
    assert sanitize_for_speech("* item dos") == "item dos."
    assert sanitize_for_speech("+ item tres") == "item tres."
    assert sanitize_for_speech("1. item uno") == "item uno."
    assert sanitize_for_speech("2) item dos") == "item dos."
    assert sanitize_for_speech("\u2022 viñeta unicode") == "viñeta unicode."


def test_literal_escapes_do_not_destroy_words_or_paths():
    assert sanitize_for_speech("Texto con \\n literal y \\t tab") == "Texto con literal y tab."
    assert sanitize_for_speech("archivo en C:\\Users\\notas") == "archivo en C:\\Users\\notas."


def test_tables_links_and_blockquotes():
    assert sanitize_for_speech("columna A | columna B") == "columna A columna B."
    assert sanitize_for_speech("|---|---|") == ""
    assert sanitize_for_speech("[Google](https://g.com)") == "Google."
    assert sanitize_for_speech("> cita famosa") == "cita famosa."


def test_stray_unpaired_markers_are_removed():
    assert sanitize_for_speech("importante *sin cierre") == "importante sin cierre."
    assert sanitize_for_speech("\\*escapado") == "escapado."
    assert sanitize_for_speech("___resaltado___") == "resaltado."


def test_legitimate_symbols_are_preserved():
    assert sanitize_for_speech("3 * 4 = 12") == "3 * 4 = 12."
    assert sanitize_for_speech("A*B sin espacio") == "A*B sin espacio."
    assert sanitize_for_speech("mi_variable importante") == "mi_variable importante."
    assert sanitize_for_speech("#1 en ventas") == "#1 en ventas."


def test_multiline_pauses():
    text = "Primera linea\nSegunda linea"
    assert sanitize_for_speech(text) == "Primera linea. Segunda linea."


def test_empty_and_whitespace():
    assert sanitize_for_speech("") == ""
    assert sanitize_for_speech("   ") == ""


if __name__ == "__main__":
    funcs = [v for k, v in globals().items() if k.startswith("test_")]
    passed = failed = 0
    for f in funcs:
        try:
            f()
            print(f"  OK {f.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {f.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
