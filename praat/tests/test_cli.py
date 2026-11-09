import csv
import json
import os
import subprocess
import sys

from langtools_praat.cli import main
from langtools_praat.io.textgrid import read


def test_cli_audio_to_annotation_roundtrip(wav, tmp_path, capsys):
    assert main(["inspect", str(wav)]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["frames"] == 16000
    assert info["x1"] == 0.5 / 16000
    original = tmp_path / "labels.TextGrid"
    edited = tmp_path / "edited.TextGrid"
    short = tmp_path / "short.TextGrid"
    exported = tmp_path / "labels.csv"
    assert main(["new-textgrid", str(wav), str(original), "--point-tier", "events"]) == 0
    # With only --point-tier the CLI intentionally creates only that requested tier.
    assert main(["new-textgrid", str(wav), str(edited), "--interval-tier", "phones"]) == 0
    assert (
        main(
            [
                "set-label",
                str(edited),
                str(short),
                "--tier",
                "1",
                "--item",
                "1",
                "--label",
                'ʔa "quoted"\nline',
            ]
        )
        == 0
    )
    assert read(short).tiers[0].intervals[0].label == 'ʔa "quoted"\nline'
    assert main(["convert-textgrid", str(short), str(edited), "--short", "--overwrite"]) == 0
    assert main(["export-annotations", str(edited), str(exported)]) == 0
    with exported.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["label"] == 'ʔa "quoted"\nline'
    assert main(["inspect", str(edited)]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["tiers"][0]["name"] == "phones"
    assert info["xmin"] == 0
    assert info["xmax"] == 1


def test_cli_no_clobber_invalid_input_and_exit_codes(wav, tmp_path, capsys):
    grid = tmp_path / "labels.TextGrid"
    assert main(["new-textgrid", str(wav), str(grid)]) == 0
    before = grid.read_bytes()
    assert main(["new-textgrid", str(wav), str(grid)]) == 1
    assert grid.read_bytes() == before
    assert (
        main(
            [
                "set-label",
                str(grid),
                str(grid),
                "--tier",
                "0",
                "--item",
                "1",
                "--label",
                "bad",
                "--overwrite",
            ]
        )
        == 2
    )
    assert grid.read_bytes() == before
    assert main(["inspect", str(tmp_path / "absent.wav")]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_headless_imports_do_not_initialize_qt_or_devices():
    code = """
import sys
from langtools_praat.cli import main
from langtools_praat.application.workspace import Workspace
from langtools_praat.media.transport import Transport
assert not any(n.startswith(('PySide6', 'pyqtgraph', 'sounddevice')) for n in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "QT_QPA_PLATFORM": "not-a-platform"},
    )
    assert result.returncode == 0, result.stderr
