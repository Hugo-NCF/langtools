"""Parse Praat text Spectrogram objects and compare raw power with candidate JSON.

Records discrepancies; it does not assume or claim numerical parity.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
CELL = re.compile(rf"^\s*z\s*\[\s*(\d+)\s*\]\s*\[\s*(\d+)\s*\]\s*=\s*({NUMBER})\s*$")
AXIS = re.compile(rf"^\s*(xmin|xmax|nx|dx|x1|ymin|ymax|ny|dy|y1)\s*=\s*({NUMBER})\s*$")


def parse_praat_text(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    if '"Spectrogram' not in text:
        raise ValueError(f"Not a Praat Spectrogram text file: {path}")
    axes = {}
    cells = {}
    for line in text.splitlines():
        match = AXIS.fullmatch(line)
        if match:
            axes[match.group(1)] = float(match.group(2))
        match = CELL.fullmatch(line)
        if match:
            i, j, value = int(match.group(1)), int(match.group(2)), float(match.group(3))
            cells[(i, j)] = value
    for name in ("nx", "ny", "dx", "dy", "x1", "y1", "xmin", "xmax", "ymin", "ymax"):
        if name not in axes:
            raise ValueError(f"Missing axis field {name} in {path}")
    nx, ny = int(axes["nx"]), int(axes["ny"])
    if nx <= 0 or ny <= 0:
        raise ValueError(f"Invalid matrix shape ({ny}, {nx})")
    if len(cells) != nx * ny:
        raise ValueError(f"Expected {nx * ny} cells, found {len(cells)} in {path}")
    # Praat's z[frequency_row][time_column]; candidate stores [time][frequency].
    values = [[cells[(f + 1, t + 1)] for f in range(ny)] for t in range(nx)]
    return {
        "time": {
            "xmin": axes["xmin"],
            "xmax": axes["xmax"],
            "nx": nx,
            "dx": axes["dx"],
            "x1": axes["x1"],
        },
        "frequency": {
            "xmin": axes["ymin"],
            "xmax": axes["ymax"],
            "nx": ny,
            "dx": axes["dy"],
            "x1": axes["y1"],
        },
        "power_frame_major": values,
    }


def compare(reference: dict, candidate: dict, *, atol: float, rtol: float) -> dict:
    if "error" in candidate:
        return {"status": "candidate_error", "detail": candidate["error"]}
    disagreements = []
    for axis in ("time", "frequency"):
        for field in ("xmin", "xmax", "nx", "dx", "x1"):
            a, b = reference[axis][field], candidate[axis][field]
            if not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-10):
                disagreements.append({"axis": axis, "field": field, "praat": a, "candidate": b})
    ref, got = reference["power_frame_major"], candidate["power_frame_major"]
    if len(ref) != len(got) or (len(ref) and len(got) and len(ref[0]) != len(got[0])):
        return {
            "status": "shape_mismatch",
            "axis_discrepancies": disagreements,
            "reference_shape": [len(ref), len(ref[0]) if ref else 0],
            "candidate_shape": [len(got), len(got[0]) if got else 0],
        }
    mismatches = 0
    max_abs = 0.0
    max_rel = 0.0
    worst = None
    for t, (ref_row, got_row) in enumerate(zip(ref, got, strict=True)):
        for f, (a, b) in enumerate(zip(ref_row, got_row, strict=True)):
            delta = abs(a - b)
            rel = delta / max(abs(a), 1e-30)
            if delta > max_abs:
                max_abs = delta
                worst = {"frame": t, "frequency_band": f, "praat": a, "candidate": b}
            max_rel = max(max_rel, rel)
            if not math.isclose(a, b, abs_tol=atol, rel_tol=rtol):
                mismatches += 1
    return {
        "status": "match" if not disagreements and not mismatches else "discrepancy",
        "axis_discrepancies": disagreements,
        "mismatched_cells": mismatches,
        "total_cells": sum(map(len, ref)),
        "maximum_absolute_error": max_abs,
        "maximum_relative_error": max_rel,
        "worst_cell": worst,
        "tolerances": {"absolute": atol, "relative": rtol},
    }


def _candidate_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(HERE), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNAVAILABLE"


def compare_capture(
    entry: dict, oracle_dir: Path, candidate_dir: Path, *, atol: float, rtol: float
) -> dict:
    """Compare a capture, including expected failures and stereo channel policies."""
    name = entry["fixture"]
    is_stereo = name == "stereo_different"
    candidate_path = candidate_dir / (name + ("_combined" if is_stereo else "") + ".json")
    if not candidate_path.exists():
        return {"status": "missing_candidate", "candidate_file": candidate_path.name}

    candidate = _candidate_json(candidate_path)
    if entry["expected_reference_status"] == "expected_error":
        reference_error = (entry.get("stderr", "") + "\n" + entry.get("stdout", "")).lower()
        candidate_error = str(candidate.get("error", "")).lower()
        # Require the correct failure *reason*, not merely two nonzero exit codes.
        expected_reference = (
            entry["status"] == "failed"
            and entry.get("returncode") not in (None, 0)
            and "too short" in reference_error
            and "window lengths" in reference_error
        )
        expected_candidate = "too short" in candidate_error and "window lengths" in candidate_error
        return {
            "status": "expected_error_match"
            if expected_reference and expected_candidate
            else "error_behavior_discrepancy",
            "reference_returncode": entry.get("returncode"),
            "reference_error": reference_error.splitlines()[0:3],
            "candidate_error": candidate.get("error"),
        }

    if entry["status"] != "ok" or not entry.get("output"):
        return {"status": "oracle_failure", "oracle_run": entry["status"]}

    reference = parse_praat_text(oracle_dir / entry["output"])
    result = compare(reference, candidate, atol=atol, rtol=rtol)
    if is_stereo:
        result["channel_policy"] = "combined"
        checks = {}
        for policy in ("left", "right"):
            path = candidate_dir / f"{name}_{policy}.json"
            checks[policy] = (
                compare(reference, _candidate_json(path), atol=atol, rtol=rtol)
                if path.exists()
                else {"status": "missing_candidate"}
            )
        result["individual_channel_checks"] = checks
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, default=HERE / "captures")
    parser.add_argument("--candidate", type=Path, default=HERE / "candidate")
    parser.add_argument("--output", type=Path, default=HERE / "comparison.json")
    parser.add_argument("--atol", type=float, default=1e-9)
    parser.add_argument("--rtol", type=float, default=1e-4)
    args = parser.parse_args()
    captures = json.loads((args.oracle / "evidence.json").read_text(encoding="utf-8"))
    results = {
        "schema": "langtools-praat-comparison/v2",
        "oracle_evidence": str(args.oracle / "evidence.json"),
        "candidate_commit": _commit(),
        "tolerances": {"absolute": args.atol, "relative": args.rtol},
        "fixtures": [],
    }
    for entry in captures["runs"]:
        try:
            data = compare_capture(
                entry, args.oracle, args.candidate, atol=args.atol, rtol=args.rtol
            )
        except (ValueError, KeyError, UnicodeError, OSError) as exc:
            data = {"status": "parse_error", "detail": str(exc)}
        results["fixtures"].append({"fixture": entry["fixture"], **data})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    for item in results["fixtures"]:
        print(f"{item['fixture']}: {item['status']}")
        if item.get("individual_channel_checks"):
            for channel, data in item["individual_channel_checks"].items():
                print(f"  {channel}: {data['status']} ({data.get('mismatched_cells', '?')} cells)")
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
