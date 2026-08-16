"""기준셋 v1 무결성 (E-17 정확도 게이트의 입력)."""

from __future__ import annotations

import json
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


QUERY_GOLDSET_PATH = Path(__file__).with_name("query_goldset.tsv")
QUERY_IDS = {
    "DAY_MOVERS",
    "PERIOD_SUMMARY",
    "STOCK_DAY_REASON",
    "STOCK_TOP_MOVES",
    "STOCK_THEME_MEMBERSHIP",
    "STOCK_COOCCURRENCE",
    "THEME_MEMBERS",
    "THEME_HISTORY",
    "THEME_COMPARISON",
    "THEME_FREQUENCY",
    "CATALYST_THEME_REACTION",
    "CATALYST_FREQUENCY",
    "CATALYST_CERTAINTY",
    "CATALYST_CONTINUATION",
    "COMPANY_DIRECT_EVENT",
    "COMPANY_VALUE_SUMMARY",
    "COMPANY_HISTORICAL_OUTCOME",
}
REJECT_REASONS = {"OUT_OF_SCOPE", "NOT_INTERPRETABLE"}
DIRECTION_SPLIT_QUERIES = {
    "DAY_MOVERS",
    "PERIOD_SUMMARY",
    "STOCK_DAY_REASON",
    "STOCK_TOP_MOVES",
    "THEME_COMPARISON",
}


def test_query_goldset_covers_every_type_with_both_directions() -> None:
    """17종 전부와 상승·하락 대칭이 표본군으로 덮여 있어야 한다."""

    rows = _rows(QUERY_GOLDSET_PATH)
    answered = {row[3] for row in rows if row[1] not in {"REJECT", "HARD_SLOT"}}
    assert answered == QUERY_IDS

    groups: dict[str, int] = {}
    for row in rows:
        groups[row[1]] = groups.get(row[1], 0) + 1
    for query_id in DIRECTION_SPLIT_QUERIES:
        assert f"{query_id}_UP" in groups or any(
            name.endswith("_UP") and name.startswith(query_id) for name in groups
        )
        assert any(name.endswith("_DOWN") and name.startswith(query_id) for name in groups)
    # test 30 + dev 15. 이보다 적으면 그 표본군은 측정 불가로 남는다.
    for name, count in groups.items():
        if name in {"REJECT", "HARD_SLOT"}:
            continue
        assert count == 45, f"{name} 표본이 45건이 아니다: {count}"


def test_query_goldset_rows_are_well_formed_and_ai_draft() -> None:
    rows = _rows(QUERY_GOLDSET_PATH)

    assert len({row[0] for row in rows}) == len(rows)
    assert len({row[2] for row in rows}) == len(rows)
    for row in rows:
        question_id, group, question, gold, slots, review_status = row
        assert question.strip() == question and question
        assert review_status in REVIEW_STATUSES
        parsed = json.loads(slots)
        assert isinstance(parsed, dict)
        if group == "REJECT":
            assert gold in REJECT_REASONS
            assert parsed == {}
        else:
            assert gold in QUERY_IDS
            assert parsed
    assert {row[5] for row in rows} == {"AI_DRAFT"}
