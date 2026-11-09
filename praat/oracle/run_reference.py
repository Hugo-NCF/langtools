"""Run a pinned original Praat executable headlessly and record its raw evidence."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS = {"window_length_s": 0.005, "maximum_frequency_hz": 5000.0,
            "time_step_s": 0.002, "frequency_step_hz": 20.0, "window_shape": "Gaussian"}


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def read_version(praat: Path) -> str:
    try:
        proc = subprocess.run([str(praat), "--version"], capture_output=True, text=True,
                              timeout=15, check=False)
        return (proc.stdout + proc.stderr).strip()[:1000] or "UNAVAILABLE"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"UNAVAILABLE: {exc}"


def project_commit() -> str:
    try:
        root = HERE.parent.parent
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNAVAILABLE (run inside cloned repository)"


def pin(praat: Path, pin_path: Path) -> None:
    if not praat.is_file():
        raise FileNotFoundError(f"Praat binary not found: {praat}")
    data = {"schema": "langtools-praat-reference-pin/v1", "executable_path": str(praat.resolve()),
            "sha256": hash_file(praat), "version_reported": read_version(praat),
            "os": platform.platform(), "cpu": platform.machine(),
            "recorded_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source": "Official distribution; enter downloaded installer URL and checksum in docs/VERIFICATION.md"}
    pin_path.parent.mkdir(parents=True, exist_ok=True)
    pin_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Pinned local Praat binary in {pin_path} (do not commit machine-specific path)")
    print(f"SHA256: {data['sha256']}")
    print(f"Version: {data['version_reported']}")


def praat_literal(path: Path) -> str:
    # Double quotes are escaped by doubling in Praat string literals.
    return '"' + str(path.resolve()).replace('"', '""') + '"'


def script_for(inp: Path, out: Path, settings: dict) -> str:
    args = (settings["window_length_s"], settings["maximum_frequency_hz"],
            settings["time_step_s"], settings["frequency_step_hz"])
    # Don't interpolate untrusted script snippets; all arguments are paths or validated numbers.
    return (f"Read from file: {praat_literal(inp)}\n"
            f"To Spectrogram: {args[0]}, {args[1]}, {args[2]}, {args[3]}, \"Gaussian\"\n"
            f"Save as text file: {praat_literal(out)}\n")


def run(praat: Path, pin_path: Path, inputs: Path, output: Path, timeout: int) -> dict:
    if not praat.is_file():
        raise FileNotFoundError(f"Praat executable missing: {praat}")
    pinned = json.loads(pin_path.read_text(encoding="utf-8"))
    actual = hash_file(praat)
    if actual != pinned["sha256"]:
        raise RuntimeError("Praat binary SHA256 changed since pinning; revalidate or re-pin deliberately")
    manifest = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    temp = output / "scripts"
    temp.mkdir(exist_ok=True)
    evidence = {"schema": "langtools-praat-oracle-capture/v1", "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "reference_pin": pinned, "reference_current_sha256": actual,
                "source_commit": project_commit(),
                "pr17_head_when_scoped": "10d648d5ab183f3ca38e67558306878d7e98092f",
                "os": platform.platform(), "cpu": platform.machine(),
                "python": platform.python_version(), "settings": SETTINGS,
                "fixture_manifest_sha256": hash_file(inputs / "manifest.json"), "runs": []}
    for entry in manifest["files"]:
        inp = inputs / entry["file"]
        if hash_file(inp) != entry["sha256"]:
            raise RuntimeError(f"Fixture changed since generation: {inp}")
        settings = {**SETTINGS, **entry.get("settings_override", {})}
        dest = output / (entry["name"] + ".Spectrogram")
        script = temp / (entry["name"] + ".praat")
        script.write_text(script_for(inp, dest, settings), encoding="utf-8")
        if dest.exists():
            dest.unlink()
        cmd = [str(praat.resolve()), "--run", str(script.resolve())]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                    check=False, cwd=HERE)
            status, stdout, stderr, code = "ok" if result.returncode == 0 and dest.is_file() else "failed", result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as exc:
            status, stdout, stderr, code = "timeout", str(exc.stdout), str(exc.stderr), None
        record = {"fixture": entry["name"], "input_sha256": entry["sha256"],
                  "script_sha256": hash_file(script), "script_text": script.read_text(encoding="utf-8"),
                  "command": cmd, "returncode": code, "status": status,
                  "stdout": stdout[-20000:], "stderr": stderr[-20000:],
                  "output": dest.name if dest.is_file() else None,
                  "output_sha256": hash_file(dest) if dest.is_file() else None,
                  "expected_reference_status": entry["expected_reference_status"],
                  "settings": settings}
        evidence["runs"].append(record)
        print(f"{entry['name']}: {status} (exit={code})")
    (output / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Raw reference results and provenance: {output}")
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["pin", "capture"])
    parser.add_argument("--praat", type=Path, default=Path("/Applications/Praat.app/Contents/MacOS/Praat"))
    parser.add_argument("--pin", type=Path, default=HERE / "local_reference_pin.json")
    parser.add_argument("--inputs", type=Path, default=HERE / "generated")
    parser.add_argument("--output", type=Path, default=HERE / "captures")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    if args.action == "pin":
        pin(args.praat, args.pin)
    else:
        run(args.praat, args.pin, args.inputs, args.output, args.timeout)


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
