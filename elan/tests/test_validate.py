"""Document checks (REIMPLEMENTATION.md §2.1.2, §2.1.4)."""

from __future__ import annotations

import pytest

from elan_ng.core.validate import Severity, validate
from elan_ng.io.eaf_reader import read_eaf

from support import DATA, external


@pytest.mark.parametrize(
    "name",
    [
        "minimal.eaf",
        "edge_cases.eaf",
        "old_cv_2_7.eaf",
        "nested_subdivision_4.eaf",
        "format_2_8.eaf",
    ],
)
def test_clean_files_have_no_problems(name: str) -> None:
    assert validate(read_eaf(DATA / name)) == []


@pytest.mark.parametrize("name", ["pympi_sample_2.7.eaf", "pympi_sample_3.0.eaf"])
def test_elan_samples_have_no_problems(name: str) -> None:
    assert validate(read_eaf(external(name))) == []


def test_problem_file_reports_every_problem() -> None:
    problems = validate(read_eaf(DATA / "problems.eaf"))
    found = {(p.code, p.ids) for p in problems}
    assert ("overlap", ("a1", "a2")) in found
    assert ("empty-interval", ("a3",)) in found
    assert ("time-order", ("ts3",)) in found
    assert ("unused-slot", ("ts7",)) in found
    assert ("association", ("a1",)) in found
    assert ("unknown-annotation", ("a6",)) in found
    assert ("no-parent", ("a6",)) in found
    assert ("unknown-type", ("bad type",)) in found
    severities = {p.code: p.severity for p in problems}
    assert severities["unknown-type"] is Severity.ERROR
    assert severities["overlap"] is Severity.WARNING
