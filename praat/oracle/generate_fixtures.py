"""Produce original, deterministic PCM16 WAV test fixtures without third-party modules."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import struct
import wave
from pathlib import Path

RATE = 22050
DURATION = 0.12
SEED = 1701


def _sine(t: float, hz: float) -> float:
    return math.sin(2 * math.pi * hz * t)


def signals() -> dict[str, list[tuple[float, ...]]]:
    n = round(RATE * DURATION)
    t = [(i + 0.5) / RATE for i in range(n)]
    rng = random.Random(SEED)
    noise = [0.25 * rng.uniform(-1, 1) for _ in t]
    return {
        "silence": [(0.0,) for _ in t],
        "impulse": [(0.75 if i == n // 2 else 0.0,) for i in range(n)],
        "sine_1000": [(0.5 * _sine(v, 1000),) for v in t],
        "dual_1000_1200": [(0.25 * (_sine(v, 1000) + _sine(v, 1200)),) for v in t],
        "dual_long_30ms": [(0.25 * (_sine(v, 1000) + _sine(v, 1200)),) for v in t],
        "chirp_300_3500": [
            (0.5 * math.sin(2 * math.pi * (300 * v + 0.5 * (3200 / DURATION) * v * v)),) for v in t
        ],
        "seeded_noise": [(v,) for v in noise],
        "short_valid": [(0.5 * _sine(v, 1000),) for v in t[: round(RATE * 0.012)]],
        "short_invalid": [(0.5 * _sine(v, 1000),) for v in t[: round(RATE * 0.008)]],
        "stereo_different": [(0.5 * _sine(v, 1000), 0.5 * _sine(v, 2000)) for v in t],
    }


def pcm16(value: float) -> int:
    return max(-32768, min(32767, round(value * 32767)))


def generate(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    meta = {
        "schema": "langtools-praat-original-wav-fixtures/v1",
        "sample_rate_hz": RATE,
        "encoding": "WAV PCM16 little-endian",
        "seed": SEED,
        "sample_times": "(sample_index + 0.5) / sample_rate",
        "provenance": (
            "Generated from original Python formulas in "
            "oracle/generate_fixtures.py; no external recordings"
        ),
        "files": [],
    }
    for name, samples in signals().items():
        path = output / f"{name}.wav"
        channels = len(samples[0])
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(
                b"".join(
                    struct.pack("<" + "h" * channels, *(pcm16(v) for v in frame))
                    for frame in samples
                )
            )
        meta["files"].append(
            {
                "name": name,
                "file": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "frames": len(samples),
                "channels": channels,
                "expected_reference_status": "expected_error"
                if name == "short_invalid"
                else "capture",
                "settings_override": {"window_length_s": 0.03} if name == "dual_long_30ms" else {},
            }
        )
    (output / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).resolve().parent / "generated"
    )
    args = parser.parse_args()
    meta = generate(args.output)
    print(f"Generated {len(meta['files'])} original fixtures in {args.output}")


if __name__ == "__main__":
    main()
