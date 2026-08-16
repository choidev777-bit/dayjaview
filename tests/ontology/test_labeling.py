"""history 라벨링·커버리지 집계 (E-17)."""

from __future__ import annotations

from datetime import date

from packages.ontology import HistoryRecord, label_history_records


def _record(key: str, text: str, when: date | None = date(2024, 3, 5)) -> HistoryRecord:
    return HistoryRecord(
        theme_id="7",
        theme_name="원자력발전",
        source_history_key=key,
        event_date=when,
        raw_text=text,
    )


def test_rows_carry_labels_and_report_counts_match() -> None:
    records = (
        _record("source:1", "정부 원전 수출 지원 방안 발표 소식 등에 상승"),
        _record("source:2", "체코 원전 수주 기대감 지속 등에 상승"),
        _record("source:3", "개별 이슈 부담 속 하락", when=None),
    )
    rows, report = label_history_records(records)
    assert len(rows) == 3
    assert rows[0]["primaryTypeId"] == "POLICY_MEASURE"
    assert rows[1]["typeIds"][0] == "ORDER_CONTRACT"
    assert rows[1]["continuation"] is True
    assert rows[2]["typeIds"] == []
    assert report["totalRecords"] == 3
    assert report["unclassifiedCount"] == 1
    assert report["totalByYear"] == {"2024": 2, "unknown": 1}
    assert report["unclassifiedByYear"] == {"unknown": 1}
    assert report["typeCounts"]["ORDER_CONTRACT"] == 1
    assert report["vocabularyVersion"] == "1.1.0"


def test_gate_thresholds() -> None:
    classified = _record("source:1", "정부 지원 소식에 상승")
    unclassified = _record("source:2", "개별 이슈로 상승")
    _, go_report = label_history_records((classified,) * 9 + (unclassified,))
    assert go_report["gate"] == "GO"
    _, review_report = label_history_records((classified,) * 4 + (unclassified,))
    assert review_report["gate"] == "REVIEW"
    _, redesign_report = label_history_records((classified, unclassified))
    assert redesign_report["gate"] == "REDESIGN"
    _, empty_report = label_history_records(())
    assert empty_report["totalRecords"] == 0
    assert empty_report["gate"] == "GO"
