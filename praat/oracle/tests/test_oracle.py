"""Standard-library-only tests for the oracle tooling (no Praat binary needed)."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oracle.compare import (
    compare,
    compare_capture,
    evidence_sha256,
    parse_praat_text,
    verified_candidate_source,
)
from oracle.compare import (
    main as compare_main,
)
from oracle.generate_fixtures import generate
from oracle.run_reference import script_for
from oracle.summarize import render


class OracleTests(unittest.TestCase):
    @staticmethod
    def _candidate_identity(fixture, channel="mono"):
        return {
            "schema": "langtools-praat-candidate-spectrogram/v1",
            "fixture": fixture,
            "channel_policy": channel,
            "input_sha256": "a" * 64,
            "manifest_sha256": "b" * 64,
            "candidate_source": {
                "schema": "langtools-praat-candidate-source/v1",
                "git_commit": "c" * 40,
                "source_status": "clean",
                "modules_sha256": {
                    "langtools_praat.analysis.spectrogram": "d" * 64,
                },
                "numpy_version": "test",
            },
            "settings": {
                "window_length": 0.005,
                "maximum_frequency": 5000.0,
                "time_step": 0.002,
                "frequency_step": 20.0,
            },
        }

    @staticmethod
    def _reference_identity():
        return {
            "input_sha256": "a" * 64,
            "settings": {
                "window_length_s": 0.005,
                "maximum_frequency_hz": 5000.0,
                "time_step_s": 0.002,
                "frequency_step_hz": 20.0,
                "window_shape": "Gaussian",
            },
        }

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
                        **self._candidate_identity("short_invalid"),
                        "error": (
                            "InvalidInput: The sound is too short: "
                            "it must be at least two window lengths."
                        ),
                    }
                )
            )
            entry = {
                "fixture": "short_invalid",
                **self._reference_identity(),
                "expected_reference_status": "expected_error",
                "status": "failed",
                "returncode": 255,
                "stderr": (
                    "Error: Your sound is too short: "
                    "it should be at least as long as two window lengths."
                ),
                "stdout": "",
            }
            result = compare_capture(
                entry, folder, folder, atol=1e-9, rtol=1e-4, manifest_sha256="b" * 64
            )
            self.assertEqual(result["status"], "expected_error_match")
            entry["stderr"] = "Error: permission denied"
            result = compare_capture(
                entry, folder, folder, atol=1e-9, rtol=1e-4, manifest_sha256="b" * 64
            )
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
                candidate = {
                    **reference,
                    **self._candidate_identity("stereo_different", name),
                    "power_frame_major": [[power]],
                }
                (folder / f"stereo_different_{name}.json").write_text(json.dumps(candidate))
            entry = {
                "fixture": "stereo_different",
                **self._reference_identity(),
                "expected_reference_status": "capture",
                "status": "ok",
                "output": "stereo_different.Spectrogram",
                "output_sha256": hashlib.sha256(
                    (folder / "stereo_different.Spectrogram").read_bytes()
                ).hexdigest(),
            }
            result = compare_capture(
                entry, folder, folder, atol=1e-9, rtol=1e-4, manifest_sha256="b" * 64
            )
            self.assertEqual(result["status"], "match")
            self.assertEqual(result["channel_policy"], "combined")
            self.assertEqual(result["individual_channel_checks"]["left"]["status"], "discrepancy")
            self.assertEqual(result["individual_channel_checks"]["right"]["status"], "discrepancy")
            left_path = folder / "stereo_different_left.json"
            left_candidate = json.loads(left_path.read_text())
            left_candidate["candidate_source"]["git_commit"] = "e" * 40
            left_path.write_text(json.dumps(left_candidate))
            result = compare_capture(
                entry,
                folder,
                folder,
                atol=1e-9,
                rtol=1e-4,
                manifest_sha256="b" * 64,
            )
            self.assertEqual(
                result["individual_channel_checks"]["left"]["status"],
                "candidate_provenance_mismatch",
            )

    def test_modified_reference_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = folder / "sine_1000.Spectrogram"
            original = """File type = "ooTextFile"
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
z [1] [1] = 1
"""
            path.write_text(original)
            original_hash = hashlib.sha256(path.read_bytes()).hexdigest()

            path.write_text(original.replace("z [1] [1] = 1", "z [1] [1] = 2"))
            candidate = {
                **parse_praat_text(path),
                **self._candidate_identity("sine_1000"),
            }
            (folder / "sine_1000.json").write_text(json.dumps(candidate))

            entry = {
                "fixture": "sine_1000",
                **self._reference_identity(),
                "expected_reference_status": "capture",
                "status": "ok",
                "output": path.name,
                "output_sha256": original_hash,
            }
            result = compare_capture(
                entry, folder, folder, atol=1e-9, rtol=1e-4, manifest_sha256="b" * 64
            )
            self.assertEqual(result["status"], "reference_integrity_failure")

    def test_candidate_identity_mismatches_are_rejected(self):
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
z [1] [1] = 1
"""
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            reference_path = folder / "sine_1000.Spectrogram"
            reference_path.write_text(content)
            reference = parse_praat_text(reference_path)
            entry = {
                "fixture": "sine_1000",
                **self._reference_identity(),
                "expected_reference_status": "capture",
                "status": "ok",
                "output": reference_path.name,
                "output_sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest(),
            }
            settings = self._candidate_identity("sine_1000")["settings"]
            invalid_fields = {
                "schema": "wrong-schema",
                "fixture": "different_fixture",
                "channel_policy": "right",
                "input_sha256": "c" * 64,
                "manifest_sha256": "d" * 64,
                "settings": {**settings, "window_length": 0.03},
            }
            for field, invalid_value in invalid_fields.items():
                with self.subTest(field=field):
                    candidate = {
                        **reference,
                        **self._candidate_identity("sine_1000"),
                        field: invalid_value,
                    }
                    (folder / "sine_1000.json").write_text(json.dumps(candidate))
                    result = compare_capture(
                        entry,
                        folder,
                        folder,
                        atol=1e-9,
                        rtol=1e-4,
                        manifest_sha256="b" * 64,
                    )
                    self.assertEqual(result["status"], "candidate_identity_mismatch")
                    self.assertEqual(result["field"], field)

    def test_candidate_provenance_is_required(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            candidate = self._candidate_identity("short_invalid")
            candidate.pop("candidate_source")
            candidate["error"] = "The sound is too short: two window lengths."
            (folder / "short_invalid.json").write_text(json.dumps(candidate))
            entry = {
                "fixture": "short_invalid",
                **self._reference_identity(),
                "expected_reference_status": "expected_error",
                "status": "failed",
                "returncode": 255,
                "stderr": "The sound is too short: two window lengths.",
                "stdout": "",
            }
            result = compare_capture(
                entry,
                folder,
                folder,
                atol=1e-9,
                rtol=1e-4,
                manifest_sha256="b" * 64,
            )
            self.assertEqual(result["status"], "candidate_provenance_missing")

    def test_saved_generation_provenance_not_comparator_checkout(self):
        source_a = self._candidate_identity("sine_1000")["candidate_source"]
        fixtures = [
            {"status": "match", "candidate_source": source_a},
            {"status": "expected_error_match", "candidate_source": source_a},
        ]
        self.assertEqual(verified_candidate_source(fixtures), source_a)
        # A second export from a different checkout must never be called A.
        source_b = {**source_a, "git_commit": "e" * 40}
        fixtures[1] = {"status": "match", "candidate_source": source_b}
        self.assertIsNone(verified_candidate_source(fixtures))
        fixtures[1] = {"status": "candidate_identity_mismatch", "candidate_source": source_a}
        self.assertIsNone(verified_candidate_source(fixtures))

    def test_missing_candidate_is_reported_without_false_match(self):
        with tempfile.TemporaryDirectory() as temp:
            entry = {"fixture": "sine_1000", "expected_reference_status": "capture", "status": "ok"}
            result = compare_capture(
                entry, Path(temp), Path(temp), atol=1e-9, rtol=1e-4, manifest_sha256="b" * 64
            )
            self.assertEqual(result["status"], "missing_candidate")

    def test_summary_renders_tolerances(self):
        evidence = {
            "reference_pin": {"version_reported": "Praat", "sha256": "abc"},
            "os": "macOS",
            "cpu": "arm64",
            "source_commit": "reference",
            "fixture_manifest_sha256": "manifest",
            "utc": "2026-11-16",
            "settings": {
                "window_length_s": 0.005,
                "maximum_frequency_hz": 5000,
                "time_step_s": 0.002,
                "frequency_step_hz": 20,
                "window_shape": "Gaussian",
            },
            "runs": [],
        }
        comparison = {
            "oracle_evidence_sha256": evidence_sha256(evidence),
            "candidate_commit": "candidate",
            "tolerances": {"absolute": 1e-9, "relative": 1e-4},
            "fixtures": [],
        }
        report = render(evidence, comparison)
        self.assertIn("`atol=1e-09`, `rtol=0.0001`", report)
        self.assertIn("UNKNOWN (legacy/unverified)", report)
        comparison["candidate_source"] = self._candidate_identity("sine_1000")["candidate_source"]
        report = render(evidence, comparison)
        self.assertIn("`" + "c" * 40 + "`", report)

    def test_summary_rejects_swapped_evidence_with_same_fixture_name(self):
        evidence_a = {
            "reference_pin": {"version_reported": "Praat", "sha256": "abc"},
            "os": "macOS",
            "cpu": "arm64",
            "source_commit": "reference",
            "fixture_manifest_sha256": "a" * 64,
            "utc": "2026-11-16",
            "settings": {
                "window_length_s": 0.005,
                "maximum_frequency_hz": 5000,
                "time_step_s": 0.002,
                "frequency_step_hz": 20,
                "window_shape": "Gaussian",
            },
            "runs": [
                {
                    "fixture": "sine_1000",
                    "status": "ok",
                    "input_sha256": "b" * 64,
                    "output_sha256": "c" * 64,
                    "settings": {"window_length_s": 0.005},
                }
            ],
        }
        comparison_a = {
            "oracle_evidence_sha256": evidence_sha256(evidence_a),
            "tolerances": {"absolute": 1e-9, "relative": 1e-4},
            "fixtures": [{"fixture": "sine_1000", "status": "match"}],
        }
        report = render(evidence_a, comparison_a)
        self.assertIn("`sine_1000` | `ok` | `match`", report)
        self.assertIn(comparison_a["oracle_evidence_sha256"], report)

        # The fixture name stays identical, but the evidence identity changes.
        evidence_b = json.loads(json.dumps(evidence_a))
        evidence_b["runs"][0]["input_sha256"] = "d" * 64
        with self.assertRaisesRegex(ValueError, "evidence SHA-256"):
            render(evidence_b, comparison_a)
        with self.assertRaisesRegex(ValueError, "evidence SHA-256"):
            render(evidence_a, {**comparison_a, "oracle_evidence_sha256": None})

    def test_comparison_cli_records_evidence_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            oracle_dir = root / "captures"
            oracle_dir.mkdir()
            evidence = {"fixture_manifest_sha256": "a" * 64, "runs": []}
            (oracle_dir / "evidence.json").write_text(json.dumps(evidence))
            output = root / "comparison.json"
            with patch(
                "sys.argv",
                [
                    "oracle.compare",
                    "--oracle",
                    str(oracle_dir),
                    "--candidate",
                    str(root / "candidate"),
                    "--output",
                    str(output),
                ],
            ):
                compare_main()
            result = json.loads(output.read_text())
            self.assertEqual(result["oracle_evidence_sha256"], evidence_sha256(evidence))


if __name__ == "__main__":
    unittest.main()
