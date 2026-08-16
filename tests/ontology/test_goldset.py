"""기준셋 v1 무결성 (E-17 정확도 게이트의 입력)."""

from __future__ import annotations

from pathlib import Path

from packages.ontology import VOCABULARY

GOLDSET_PATH = Path(__file__).with_name("goldset_v1.tsv")
VALID_TYPES = {definition.type_id for definition in VOCABULARY} | {"OTHER"}
VALID_DIRECTIONS = {"UP", "DOWN", "MIXED", "UNKNOWN"}
VALID_CERTAINTIES = {"CONFIRMED", "ANTICIPATION", "UNSPECIFIED"}


def test_goldset_rows_are_valid() -> None:
    rows = [
        line.split("\t")
        for line in GOLDSET_PATH.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert len(rows) == 1000
    keys = [row[0] for row in rows]
    assert len(set(keys)) == 1000
    for key, primary, alt, direction, certainty in rows:
        assert "/" in key
        assert primary in VALID_TYPES
        assert alt == "" or (alt in VALID_TYPES and alt != primary)
        assert direction in VALID_DIRECTIONS
        assert certainty in VALID_CERTAINTIES
