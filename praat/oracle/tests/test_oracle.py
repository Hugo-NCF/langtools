"""Standard-library-only tests for the oracle tooling (no Praat binary needed)."""

import json
import tempfile
import unittest
from pathlib import Path

from oracle.compare import compare, compare_capture, parse_praat_text
from oracle.generate_fixtures import generate
from oracle.run_reference import script_for


class OracleTests(unittest.TestCase):
    def test_fixtures_are_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            first = generate(Path(temp) / "a")
            second = generate(Path(temp) / "b")
            self.assertEqual(first, second)
            self.assertEqual(len(first["files"]), 10)
            stereo = next(x for x in first["files"] if x["name"] == "stereo_different")
        self.assertEqual(stereo["channels"], 2)

    def test_parser_transposes_frequency_major_matrix(self):
        content = """File type = "ooTextFile"
Object class = "Spectrogram 2"

xmin = 0
xmax = 1
nx = 2
dx = 0.5
x1 = 0.25
ymin = 0
ymax = 1000
ny = 2
dy = 500
y1 = 250
z [] []:
    z [1]:
        z [1] [1] = 11
        z [1] [2] = 12
    z [2]:
        z [2] [1] = 21
        z [2] [2] = 22
"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.Spectrogram"
            path.write_text(content)
            result = parse_praat_text(path)
        self.assertEqual(result["power_frame_major"], [[11, 21], [12, 22]])
        self.assertEqual(result["frequency"]["nx"], 2)

    def test_comparator_detects_values_and_axes(self):
        axis = {"xmin": 0.0, "xmax": 1.0, "nx": 1, "dx": 0.1, "x1": 0.05}
        ref = {"time": axis, "frequency": axis, "power_frame_major": [[1.0]]}
        got = {"time": axis, "frequency": axis, "power_frame_major": [[2.0]]}
        self.assertEqual(compare(ref, got, atol=0, rtol=0)["mismatched_cells"], 1)

    def test_script_uses_explicit_window(self):
        s = script_for(
            Path("a.wav"),
            Path("b.Spectrogram"),
            {
                "window_length_s": 0.005,
                "maximum_frequency_hz": 5000,
                "time_step_s": 0.002,
                "frequency_step_hz": 20,
            },
        )
        self.assertIn('To Spectrogram: 0.005, 5000, 0.002, 20, "Gaussian"', s)

    def test_expected_short_signal_error_is_verified_by_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "short_invalid.json").write_text(
                json.dumps(
                    {
                        "error": (
                            "InvalidInput: The sound is too short: "
                            "it must be at least two window lengths."
                        )
                    }
                )
            )
            entry = {
                "fixture": "short_invalid",
                "expected_reference_status": "expected_error",
                "status": "failed",
                "returncode": 255,
                "stderr": (
                    "Error: Your sound is too short: "
                    "it should be at least as long as two window lengths."
                ),
                "stdout": "",
            }
            result = compare_capture(entry, folder, folder, atol=1e-9, rtol=1e-4)
            self.assertEqual(result["status"], "expected_error_match")
            entry["stderr"] = "Error: permission denied"
            result = compare_capture(entry, folder, folder, atol=1e-9, rtol=1e-4)
            self.assertEqual(result["status"], "error_behavior_discrepancy")

    def test_stereo_uses_combined_candidate_and_records_individual_differences(self):
        content = """File type = "ooTextFile"
Object class = "Spectrogram 2"
xmin = 0
xmax = 1
nx = 1
dx = 1
x1 = 0.5
ymin = 0
ymax = 1000
ny = 1
dy = 1000
y1 = 500
z [] []:
    z [1]:
        z [1] [1] = 2
"""
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "stereo_different.Spectrogram").write_text(content)
            reference = parse_praat_text(folder / "stereo_different.Spectrogram")
            for name, power in (("combined", 2), ("left", 1), ("right", 3)):
                candidate = {**reference, "power_frame_major": [[power]]}
                (folder / f"stereo_different_{name}.json").write_text(json.dumps(candidate))
            entry = {
                "fixture": "stereo_different",
                "expected_reference_status": "capture",
                "status": "ok",
                "output": "stereo_different.Spectrogram",
            }
            result = compare_capture(entry, folder, folder, atol=1e-9, rtol=1e-4)
            self.assertEqual(result["status"], "match")
            self.assertEqual(result["channel_policy"], "combined")
            self.assertEqual(result["individual_channel_checks"]["left"]["status"], "discrepancy")
            self.assertEqual(result["individual_channel_checks"]["right"]["status"], "discrepancy")

    def test_missing_candidate_is_reported_without_false_match(self):
        with tempfile.TemporaryDirectory() as temp:
            entry = {"fixture": "sine_1000", "expected_reference_status": "capture", "status": "ok"}
            result = compare_capture(entry, Path(temp), Path(temp), atol=1e-9, rtol=1e-4)
            self.assertEqual(result["status"], "missing_candidate")


if __name__ == "__main__":
    unittest.main()
