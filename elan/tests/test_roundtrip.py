"""Round-trip tests (REIMPLEMENTATION.md §6.2, checks 1-4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from elan_ng.io.eaf_reader import parse_eaf
from elan_ng.io.eaf_writer import write_eaf

from eafcompare import canonical, first_difference
from support import DATA, external

# Files whose round trip must be semantically exact. 2.7 files are checked separately,
# because converting their controlled vocabularies to the 3.0 form is a real change.
EXACT = ["minimal.eaf", "edge_cases.eaf", "nested_subdivision_4.eaf", "format_2_8.eaf"]
EXTERNAL_EXACT = ["pympi_sample_2.8.eaf", "pympi_sample_3.0.eaf"]
CV_ELEMENTS = frozenset({"CONTROLLED_VOCABULARY", "LANGUAGE"})


def _source(name: str) -> Path:
    return DATA / name if (DATA / name).exists() else external(name)


def _assert_same(before: bytes, after: bytes, skip: frozenset[str] = frozenset()) -> None:
    a, b = canonical(before, skip=skip), canonical(after, skip=skip)
    assert a == b, first_difference(a, b)


@pytest.mark.parametrize("name", EXACT + EXTERNAL_EXACT)
def test_round_trip_is_semantically_exact(name: str) -> None:
    data = _source(name).read_bytes()
    _assert_same(data, write_eaf(parse_eaf(data)))


@pytest.mark.parametrize("name", ["old_cv_2_7.eaf", "pympi_sample_2.7.eaf"])
def test_2_7_round_trip_is_exact_apart_from_cvs(name: str) -> None:
    data = _source(name).read_bytes()
    _assert_same(data, write_eaf(parse_eaf(data)), skip=CV_ELEMENTS)


@pytest.mark.parametrize(
    "name", EXACT + EXTERNAL_EXACT + ["old_cv_2_7.eaf", "pympi_sample_2.7.eaf", "problems.eaf"]
)
def test_writing_is_idempotent(name: str) -> None:
    once = write_eaf(parse_eaf(_source(name).read_bytes()))
    assert write_eaf(parse_eaf(once)) == once


# edge_cases.eaf is left out: it contains made-up elements on purpose, which we keep.
@pytest.mark.parametrize(
    "name",
    [
        "minimal.eaf",
        "old_cv_2_7.eaf",
        "nested_subdivision_4.eaf",
        "format_2_8.eaf",
        *EXTERNAL_EXACT,
        "pympi_sample_2.7.eaf",
    ],
)
def test_output_is_schema_valid(name: str) -> None:
    schema = etree.XMLSchema(etree.parse(str(external("EAFv3.0.xsd"))))
    output = etree.fromstring(write_eaf(parse_eaf(_source(name).read_bytes())))
    assert schema.validate(output), schema.error_log.last_error


def test_elan_3_0_file_is_reproduced_byte_for_byte() -> None:
    """Our writer's formatting matches ELAN's own output on an ELAN-saved file."""
    data = external("pympi_sample_3.0.eaf").read_bytes()
    assert write_eaf(parse_eaf(data)) == data
