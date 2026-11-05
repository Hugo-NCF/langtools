"""Round-trip checks on generated files (REIMPLEMENTATION.md §6.2, checks 1, 2 and 4).

The documents come from tests/data/generate.py and are built in code. Check 3 (schema
validity) is left to test_roundtrip.py, which already has the schema.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from generate import LARGE_TIERS, build_large, build_text_edge_cases

from elan_ng.core.model import Document
from elan_ng.io.eaf_reader import parse_eaf
from elan_ng.io.eaf_writer import write_eaf

from eafcompare import canonical, first_difference

BUILDERS = [
    pytest.param(lambda: build_large(5_000, LARGE_TIERS), id="large_5000"),
    pytest.param(
        lambda: build_large(50_000, LARGE_TIERS),
        id="large_50000",
        marks=pytest.mark.perf,
    ),
    pytest.param(build_text_edge_cases, id="text_edge_cases"),
]


def _assert_same(before: bytes, after: bytes) -> None:
    a, b = canonical(before), canonical(after)
    assert a == b, first_difference(a, b)


def _values(doc: Document) -> list[tuple[str, str, str]]:
    return [(tier.id, ann.id, ann.value) for tier in doc.tiers for ann in tier.annotations]


@pytest.mark.parametrize("build", BUILDERS)
def test_generated_file_loads(build: Callable[[], Document]) -> None:
    built = build()
    loaded = parse_eaf(write_eaf(built))
    assert len(loaded.annotations()) == len(built.annotations())


@pytest.mark.parametrize("build", BUILDERS)
def test_generated_round_trip_is_semantically_exact(build: Callable[[], Document]) -> None:
    first = write_eaf(build())
    _assert_same(first, write_eaf(parse_eaf(first)))


@pytest.mark.parametrize("build", BUILDERS)
def test_generated_writing_is_idempotent(build: Callable[[], Document]) -> None:
    once = write_eaf(parse_eaf(write_eaf(build())))
    assert write_eaf(parse_eaf(once)) == once


@pytest.mark.parametrize("build", BUILDERS)
def test_generated_text_values_survive_exactly(build: Callable[[], Document]) -> None:
    built = build()
    loaded = parse_eaf(write_eaf(built))
    assert _values(loaded) == _values(built)
