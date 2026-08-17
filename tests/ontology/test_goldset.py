"""기준셋 v1 무결성 (E-17 정확도 게이트의 입력)."""

from __future__ import annotations

import json
from pathlib import Path

from packages.ontology import VOCABULARY, QueryType

GOLDSET_PATH = Path(__file__).with_name("goldset_v1.tsv")
SUPPLEMENT_PATH = Path(__file__).with_name("goldset_supplement.tsv")
REVIEW_STATUSES = {"AI_DRAFT", "AI_CROSS_CHECKED", "HUMAN_CONFIRMED"}
# 사람 검수 전 상태. 이 둘만으로는 승격 게이트를 통과하지 못한다.
UNCONFIRMED_STATUSES = {"AI_DRAFT", "AI_CROSS_CHECKED"}
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
    # 사람이 확인하기 전에는 확인 표시가 하나도 없어야 한다. 섞이면 승격
    # 판정이 검증되지 않은 라벨을 통과시킨다. 교차확인은 사람 검수가 아니다.
    assert {row[5] for row in rows} <= UNCONFIRMED_STATUSES


QUERY_GOLDSET_PATH = Path(__file__).with_name("query_goldset.tsv")
QUERY_IDS = {item.value for item in QueryType}
REJECT_REASONS = {"OUT_OF_SCOPE", "NOT_INTERPRETABLE"}
DIRECTION_SPLIT_QUERIES = {
    "DAY_MOVERS",
    "PERIOD_SUMMARY",
    "STOCK_DAY_REASON",
    "STOCK_TOP_MOVES",
    "THEME_COMPARISON",
}
# 회귀 고정용 표본군. 규모가 계획서 11.1.1의 160문장 내역이다.
FIXTURE_GROUPS = {"REJECT": 60, "HARD_SLOT": 80, "TODAY_BEFORE_PUBLISH": 20}


def test_query_goldset_covers_every_type_with_both_directions() -> None:
    """17종 전부와 상승·하락 대칭이 표본군으로 덮여 있어야 한다."""

    rows = _rows(QUERY_GOLDSET_PATH)
    answered = {row[3] for row in rows if row[1] not in FIXTURE_GROUPS}
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
        if name in FIXTURE_GROUPS:
            continue
        assert count == 45, f"{name} 표본이 45건이 아니다: {count}"
    for name, wanted in FIXTURE_GROUPS.items():
        assert groups.get(name) == wanted, f"{name} 표본이 {wanted}건이 아니다"


def test_query_goldset_splits_dev_and_test_by_column() -> None:
    """dev/test는 행 번호가 아니라 split 열이 정한다(계획서 11.1.1).

    겹 C가 쓰는 짝/홀 규칙은 1:1만 표현할 수 있어 30:15에 못 쓴다. 그리고
    실패·난이도 표본을 dev로 흘리면 그것을 보며 규칙을 고치게 되어 회귀로서
    값어치가 없어진다.
    """

    rows = _rows(QUERY_GOLDSET_PATH)
    per_group: dict[str, dict[str, int]] = {}
    for row in rows:
        assert row[5] in {"dev", "test"}, f"split 값이 이상하다: {row[5]}"
        per_group.setdefault(row[1], {}).setdefault(row[5], 0)
        per_group[row[1]][row[5]] += 1

    for name, counts in per_group.items():
        if name in FIXTURE_GROUPS:
            assert counts.get("dev", 0) == 0, f"{name}은 전부 test여야 한다"
            continue
        assert counts.get("test") == 30, f"{name} test가 30건이 아니다"
        assert counts.get("dev") == 15, f"{name} dev가 15건이 아니다"


def test_query_goldset_rows_are_well_formed_and_ai_draft() -> None:
    rows = _rows(QUERY_GOLDSET_PATH)

    assert len({row[0] for row in rows}) == len(rows)
    assert len({row[2] for row in rows}) == len(rows)
    for row in rows:
        question_id, group, question, gold, slots, split, review_status = row
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
        # 발행 전 "오늘"은 직전 거래일로 답해야 하는 회귀다(계획서 4.0.1).
        if group == "TODAY_BEFORE_PUBLISH":
            assert parsed["date"] == "RELATIVE:TODAY"
            assert parsed["publicationState"] == "BEFORE_PUBLISH"
    assert {row[6] for row in rows} == {"AI_DRAFT"}
