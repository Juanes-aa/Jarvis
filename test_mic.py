"""
test_mic.py
Diagnóstico rápido de dispositivos de ENTRADA de audio.

Graba unos segundos de CADA dispositivo de entrada disponible (o solo de una
lista de candidatos) y muestra en consola su nivel RMS pico y promedio, para
poder comparar objetivamente cuál captura mejor la voz SIN tener que cambiar el
.env y reiniciar la app cada vez.

Uso:
    python test_mic.py                 # prueba los candidatos por defecto
    python test_mic.py --all           # prueba TODOS los dispositivos de entrada
    python test_mic.py --devices 1 5 9 11 20
    python test_mic.py --duration 5    # segundos por dispositivo (def. 3)

También se puede lanzar desde la app principal con:
    python main.py --test-mic
"""

from __future__ import annotations

import argparse

import numpy as np
import sounddevice as sd


# Candidatos identificados en el diagnóstico previo (micrófonos plausibles).
# El resto de dispositivos son mezclas, salidas o duplicados.
DEFAULT_CANDIDATES = [1, 5, 9, 11, 20]

# Colores ANSI (opcionales, degradan a texto plano si la terminal no los soporta)
_CYAN = "\033[96m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _input_device_indices() -> list[int]:
    """Devuelve los índices de todos los dispositivos con canales de entrada."""
    devices = sd.query_devices()
    return [i for i, d in enumerate(devices) if d.get("max_input_channels", 0) > 0]


def measure_device(index: int, duration: float, target_sr: int = 16000) -> dict:
    """
    Graba `duration` segundos del dispositivo `index` y devuelve sus métricas
    de nivel. Intenta abrir a `target_sr` mono; si el dispositivo no lo soporta,
    cae a su sample rate por defecto.

    Returns dict con: index, name, ok, error, sample_rate, peak, rms, rms_peak.
    """
    info = sd.query_devices(index)
    name = info.get("name", "?")
    result = {
        "index": index,
        "name": name,
        "ok": False,
        "error": None,
        "sample_rate": None,
        "peak": 0.0,      # muestra individual máxima (|amplitud|)
        "rms": 0.0,       # RMS global de toda la grabación
        "rms_peak": 0.0,  # RMS máximo entre bloques de 100 ms (pico de energía)
    }

    # Elegir sample rate: preferimos 16 kHz (lo que usa el wake word / STT);
    # si el dispositivo no lo permite, usamos su default.
    sample_rate = target_sr
    try:
        sd.check_input_settings(device=index, samplerate=target_sr, channels=1, dtype="float32")
    except Exception:
        sample_rate = int(info.get("default_samplerate") or 44100)

    result["sample_rate"] = sample_rate
    block = max(1, int(sample_rate * 0.1))  # bloques de ~100 ms

    try:
        chunks: list[np.ndarray] = []
        block_rms: list[float] = []
        with sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            blocksize=block,
            device=index,
        ) as stream:
            n_blocks = max(1, int(duration / 0.1))
            for _ in range(n_blocks):
                data, _overflow = stream.read(block)
                flat = data.flatten()
                chunks.append(flat.copy())
                block_rms.append(float(np.sqrt(np.mean(flat ** 2))) if flat.size else 0.0)

        audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
        result["peak"] = float(np.max(np.abs(audio)))
        result["rms"] = float(np.sqrt(np.mean(audio ** 2)))
        result["rms_peak"] = float(max(block_rms) if block_rms else 0.0)
        result["ok"] = True
    except Exception as e:
        result["error"] = str(e)

    return result


def _level_bar(value: float, scale: float = 0.2, width: int = 30) -> str:
    """Barra visual proporcional al nivel (0..scale -> 0..width)."""
    filled = min(width, int(value / (scale + 1e-9) * width))
    return "#" * filled + "-" * (width - filled)


def run(devices: list[int] | None, duration: float, test_all: bool) -> None:
    """Ejecuta el diagnóstico y lo imprime en consola."""
    available = _input_device_indices()

    if test_all:
        targets = available
    elif devices:
        targets = [d for d in devices if d in available]
        missing = [d for d in devices if d not in available]
        if missing:
            print(f"{_YELLOW}[Aviso] Índices sin entrada de audio, se omiten: {missing}{_RESET}")
    else:
        targets = [d for d in DEFAULT_CANDIDATES if d in available]

    if not targets:
        print(f"{_RED}No se encontraron dispositivos de entrada para probar.{_RESET}")
        return

    print(f"\n{_CYAN}{_BOLD}== Diagnóstico de micrófonos =={_RESET}")
    print(f"{_DIM}Se grabarán {duration:.0f}s por dispositivo. HABLA con normalidad "
          f"(di 'Hey Jarvis') durante cada grabación.{_RESET}\n")

    results: list[dict] = []
    for idx in targets:
        info = sd.query_devices(idx)
        name = info.get("name", "?")
        print(f"{_YELLOW}[{idx}]{_RESET} {name}")
        print(f"    {_DIM}Grabando {duration:.0f}s... habla ahora.{_RESET}")
        res = measure_device(idx, duration)
        results.append(res)

        if not res["ok"]:
            print(f"    {_RED}ERROR: {res['error']}{_RESET}\n")
            continue

        print(f"    sr={res['sample_rate']}Hz  "
              f"pico={res['peak']:.4f}  rms_prom={res['rms']:.4f}  "
              f"rms_pico={res['rms_peak']:.4f}")
        print(f"    nivel |{_GREEN}{_level_bar(res['rms_peak'])}{_RESET}|\n")

    # Ranking por rms_pico (el mejor indicador de captación de voz por encima del ruido)
    ok_results = [r for r in results if r["ok"]]
    if ok_results:
        ok_results.sort(key=lambda r: r["rms_peak"], reverse=True)
        print(f"{_CYAN}{_BOLD}== Ranking (por RMS pico, mayor = mejor captación) =={_RESET}")
        for pos, r in enumerate(ok_results, 1):
            marca = f"  {_GREEN}<-- mejor{_RESET}" if pos == 1 else ""
            print(f"  {pos}. [{r['index']}] rms_pico={r['rms_peak']:.4f} "
                  f"pico={r['peak']:.4f}  {r['name']}{marca}")
        best = ok_results[0]
        print(f"\n{_GREEN}Sugerencia:{_RESET} el índice {_BOLD}{best['index']}{_RESET} "
              f"captó el nivel más alto. Para fijarlo, pon en tu .env:")
        print(f"    {_BOLD}WAKE_WORD_DEVICE_INDEX={best['index']}{_RESET}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnóstico de micrófonos de entrada.")
    parser.add_argument("--all", action="store_true", help="Probar TODOS los dispositivos de entrada.")
    parser.add_argument("--devices", nargs="*", type=int, help="Índices concretos a probar.")
    parser.add_argument("--duration", type=float, default=3.0, help="Segundos por dispositivo (def. 3).")
    args = parser.parse_args()
    run(devices=args.devices, duration=args.duration, test_all=args.all)


if __name__ == "__main__":
    main()
