"""기준셋 v1 무결성 (E-17 정확도 게이트의 입력)."""

from __future__ import annotations

from pathlib import Path

from packages.ontology import VOCABULARY

GOLDSET_PATH = Path(__file__).with_name("goldset_v1.tsv")
SUPPLEMENT_PATH = Path(__file__).with_name("goldset_supplement.tsv")
REVIEW_STATUSES = {"AI_DRAFT", "HUMAN_CONFIRMED"}
VALID_TYPES = {definition.type_id for definition in VOCABULARY} | {"OTHER"}
VALID_DIRECTIONS = {"UP", "DOWN", "MIXED", "UNKNOWN"}
VALID_CERTAINTIES = {"CONFIRMED", "ANTICIPATION", "UNSPECIFIED"}


def _rows(path: Path) -> list[list[str]]:
    return [
        line.split("\t")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]


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


def test_supplement_fills_sparse_types_and_is_marked_ai_draft() -> None:
    """보강 표본은 기존 key와 겹치지 않고, 검수 전에는 승격 판정에서 빠진다."""

    base = _rows(GOLDSET_PATH)
    rows = _rows(SUPPLEMENT_PATH)

    assert rows
    keys = [row[0] for row in rows]
    assert len(set(keys)) == len(keys)
    assert not set(keys) & {row[0] for row in base}
    for key, primary, alt, direction, certainty, review_status in rows:
        assert "/" in key
        assert primary in VALID_TYPES
        assert alt == "" or (alt in VALID_TYPES and alt != primary)
        assert direction in VALID_DIRECTIONS
        assert certainty in VALID_CERTAINTIES
        assert review_status in REVIEW_STATUSES
    # 사람이 확인하기 전에는 전부 초안이어야 한다. 하나라도 확인 표시가 섞이면
    # 승격 판정이 검증되지 않은 라벨을 통과시킨다.
    assert {row[5] for row in rows} == {"AI_DRAFT"}
