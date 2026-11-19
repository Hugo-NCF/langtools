"""Export PR #17 spectrograms without editing the application's analysis code.

Run only from a checkout containing PR #17 or after its implementation merges.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import subprocess
import wave
from pathlib import Path

import numpy as np

from langtools_praat.analysis.contracts import (
    AnalysisRequest,
    ChannelPolicy,
    SpectrogramSettings,
    TimeRange,
)
from langtools_praat.analysis.spectrogram import DisplaySettings, display, to_spectrogram
from langtools_praat.model.sound import Sound
from langtools_praat.model.time import SampledAxis

HERE = Path(__file__).resolve().parent


# These are the imported Python modules that define the candidate analysis path.
_SOURCE_MODULES = (
    "langtools_praat.analysis.spectrogram",
    "langtools_praat.analysis.contracts",
    "langtools_praat.model.sound",
    "langtools_praat.model.time",
    "langtools_praat.errors",
)


def candidate_source_identity() -> dict:
    """Fingerprint imported implementation files at export time, not compare time.

    Git HEAD is reported only as repository context. A clean status requires
    every fingerprinted module to match the corresponding tracked HEAD blob.
    """
    module_hashes = {}
    roots = set()
    commits = set()
    modified = False
    unversioned = False

    for name in _SOURCE_MODULES:
        module = importlib.import_module(name)
        module_file = Path(module.__file__).resolve()
        digest = hashlib.sha256(module_file.read_bytes()).hexdigest()
        module_hashes[name] = digest

        try:
            root_result = subprocess.run(
                ["git", "-C", str(module_file.parent), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=False,
            )
            if root_result.returncode != 0:
                unversioned = True
                continue
            root = Path(root_result.stdout.strip()).resolve()
            relative = module_file.relative_to(root).as_posix()
            commit_result = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            if commit_result.returncode != 0:
                unversioned = True
                continue
            commit = commit_result.stdout.strip()
            roots.add(str(root))
            commits.add(commit)
            tracked = subprocess.run(
                ["git", "-C", str(root), "show", f"HEAD:{relative}"],
                capture_output=True,
                check=False,
            )
            if tracked.returncode != 0 or hashlib.sha256(tracked.stdout).hexdigest() != digest:
                modified = True
        except (OSError, ValueError):
            unversioned = True

    if len(roots) > 1 or len(commits) > 1 or (unversioned and roots):
        status = "mixed"
    elif unversioned:
        status = "unversioned"
    elif modified:
        status = "modified"
    else:
        status = "clean"

    return {
        "schema": "langtools-praat-candidate-source/v1",
        "git_commit": next(iter(commits)) if len(commits) == 1 else None,
        "source_status": status,
        "modules_sha256": module_hashes,
        "numpy_version": np.__version__,
    }


def sound_from_pcm16(path: Path) -> Sound:
    with wave.open(str(path), "rb") as wav:
        channels, width, rate, frames = (
            wav.getnchannels(),
            wav.getsampwidth(),
            wav.getframerate(),
            wav.getnframes(),
        )
        if width != 2:
            raise ValueError(f"Expected PCM16: {path}")
        samples = np.frombuffer(wav.readframes(frames), dtype="<i2").astype(np.float64)
    # PCM16 decoding matches the usual full-scale convention; see verification notes.
    samples = (samples.reshape(-1, channels) / 32768.0).T
    return Sound(path.stem, SampledAxis.recording(frames, rate), samples)


def axis_dict(axis) -> dict:
    return {"xmin": axis.xmin, "xmax": axis.xmax, "nx": axis.nx, "dx": axis.dx, "x1": axis.x1}


def export(inputs: Path, output: Path) -> None:
    manifest_bytes = (inputs / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    source_identity = candidate_source_identity()
    output.mkdir(parents=True, exist_ok=True)
    for item in manifest["files"]:
        path = inputs / item["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"Unexpected audio hash: {path}")
        selected_settings = {
            "window_length": item.get("settings_override", {}).get("window_length_s", 0.005),
            "maximum_frequency": 5000.0,
            "time_step": 0.002,
            "frequency_step": 20.0,
        }
        settings = SpectrogramSettings(**selected_settings)
        sound = sound_from_pcm16(path)
        for variant, policy in (
            [
                ("left", ChannelPolicy.single(0)),
                ("right", ChannelPolicy.single(1)),
                ("combined", ChannelPolicy.combined()),
            ]
            if sound.channels > 1
            else [("mono", ChannelPolicy.single(0))]
        ):
            name = item["name"] + ("_" + variant if sound.channels > 1 else "")
            identity = {
                "schema": "langtools-praat-candidate-spectrogram/v1",
                "fixture": item["name"],
                "channel_policy": variant,
                "input_sha256": item["sha256"],
                "manifest_sha256": manifest_sha256,
                "candidate_source": source_identity,
                "settings": selected_settings,
            }
            request = AnalysisRequest(
                source_id=sound.id,
                revision=sound.revision,
                settings=settings,
                time_range=TimeRange(sound.axis.xmin, sound.axis.xmax),
                channels=policy,
            )
            try:
                result = to_spectrogram(sound, request)
                image = display(result, DisplaySettings())
                data = {
                    **identity,
                    "time": axis_dict(result.time),
                    "frequency": axis_dict(result.frequency),
                    "power_frame_major": result.power.tolist(),
                    "units": "Pa^2/Hz (candidate label; reference parity not established)",
                    "display_candidate": {
                        "settings": vars(DisplaySettings()),
                        "white": image.white,
                        "black": image.black,
                    },
                }
            except Exception as exc:
                data = {
                    **identity,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            (output / f"{name}.json").write_text(
                json.dumps(data, indent=2) + "\n", encoding="utf-8"
            )
            print(name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=HERE / "generated")
    parser.add_argument("--output", type=Path, default=HERE / "candidate")
    args = parser.parse_args()
    export(args.inputs, args.output)


if __name__ == "__main__":
    main()
