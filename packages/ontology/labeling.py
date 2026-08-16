"""테마 history 전수 라벨링과 커버리지 집계 (E-17).

transform을 history 기록 목록에 적용해 라벨 행과 커버리지 보고서를 만든다.
보고서의 기타(미분류) 비율이 게이트다 — remaining_work.md E-17 기준으로
10% 이하면 현행 어휘로 진행, 30% 이상이면 축을 다시 잡는다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from .transform import TRANSFORM_VERSION, classify_catalyst
from .vocabulary import VOCABULARY, VOCABULARY_VERSION, vocabulary_content_hash

if TYPE_CHECKING:
    from packages.infostock.models import ImportBundle

UNCLASSIFIED_GO_THRESHOLD = 0.10
UNCLASSIFIED_REDESIGN_THRESHOLD = 0.30


@dataclass(frozen=True, slots=True)
class HistoryRecord:
    """라벨링 입력 한 건. 인포스탁 import 번들에서 뽑거나 테스트에서 만든다."""

    theme_id: str
    theme_name: str
    source_history_key: str
    event_date: date | None
    raw_text: str


def records_from_bundle(bundle: ImportBundle) -> tuple[HistoryRecord, ...]:
    """import 번들의 모든 history를 (테마 번호, 원본 순서)로 정렬해 뽑는다."""

    records = [
        HistoryRecord(
            theme_id=detail.source_theme_id,
            theme_name=detail.theme_name,
            source_history_key=history.source_history_key,
            event_date=history.event_date,
            raw_text=history.raw_text,
        )
        for detail in sorted(bundle.details, key=lambda d: int(d.source_theme_id))
        for history in detail.history
    ]
    return tuple(records)


def label_history_records(
    records: Iterable[HistoryRecord],
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    """기록마다 분류를 붙인 라벨 행과 커버리지 보고서를 돌려준다."""

    rows: list[dict[str, Any]] = []
    type_counts: Counter[str] = Counter()
    primary_counts: Counter[str] = Counter()
    direction_counts: Counter[str] = Counter()
    certainty_counts: Counter[str] = Counter()
    continuation_count = 0
    unclassified_count = 0
    total_by_year: Counter[str] = Counter()
    unclassified_by_year: Counter[str] = Counter()
    for record in records:
        classification = classify_catalyst(record.raw_text)
        year = record.event_date.isoformat()[:4] if record.event_date else "unknown"
        total_by_year[year] += 1
        direction_counts[classification.direction] += 1
        certainty_counts[classification.certainty] += 1
        if classification.continuation:
            continuation_count += 1
        if classification.is_unclassified:
            unclassified_count += 1
            unclassified_by_year[year] += 1
        for type_id in classification.type_ids:
            type_counts[type_id] += 1
        if classification.primary_type_id is not None:
            primary_counts[classification.primary_type_id] += 1
        rows.append(
            {
                "themeId": record.theme_id,
                "themeName": record.theme_name,
                "sourceHistoryKey": record.source_history_key,
                "eventDate": (
                    record.event_date.isoformat() if record.event_date else None
                ),
                "rawText": record.raw_text,
                "typeIds": list(classification.type_ids),
                "primaryTypeId": classification.primary_type_id,
                "direction": classification.direction,
                "certainty": classification.certainty,
                "continuation": classification.continuation,
                "evidenceSpans": [
                    {
                        "field": span.field,
                        "value": span.value,
                        "keyword": span.keyword,
                        "start": span.start,
                        "end": span.end,
                    }
                    for span in classification.evidence_spans
                ],
            }
        )
    total = len(rows)
    unclassified_ratio = (unclassified_count / total) if total else 0.0
    if unclassified_ratio <= UNCLASSIFIED_GO_THRESHOLD:
        gate = "GO"
    elif unclassified_ratio >= UNCLASSIFIED_REDESIGN_THRESHOLD:
        gate = "REDESIGN"
    else:
        gate = "REVIEW"
    report: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "vocabularyVersion": VOCABULARY_VERSION,
        "vocabularyContentHash": vocabulary_content_hash(),
        "transformVersion": TRANSFORM_VERSION,
        "totalRecords": total,
        "unclassifiedCount": unclassified_count,
        "unclassifiedRatio": round(unclassified_ratio, 6),
        "gate": gate,
        "typeCounts": {
            definition.type_id: type_counts.get(definition.type_id, 0)
            for definition in VOCABULARY
        },
        "primaryTypeCounts": {
            definition.type_id: primary_counts.get(definition.type_id, 0)
            for definition in VOCABULARY
        },
        "directionCounts": dict(sorted(direction_counts.items())),
        "certaintyCounts": dict(sorted(certainty_counts.items())),
        "continuationCount": continuation_count,
        "totalByYear": dict(sorted(total_by_year.items())),
        "unclassifiedByYear": dict(sorted(unclassified_by_year.items())),
    }
    return tuple(rows), report
