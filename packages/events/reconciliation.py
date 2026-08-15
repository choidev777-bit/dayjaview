"""장후 정합(D-13): 장중 Event와 장후 인포스탁 확정 기사를 같은 eventId로 잇는다.

장중 INTRADAY_STRENGTH Event는 장 마감 뒤 인포스탁이 같은 날 같은 테마의
**상승**을 확정 기록했는지에 따라 같은 eventId 위에 MATCHED 또는 UNMATCHED
revision을 받는다(로드맵 S6-RECONCILE). 새 Event를 만들지 않으며, MATCHED에서만
분류가 CONFIRMED/INFOSTOCK으로 승격된다. 하락·혼조 기사는 상승 확정이 아니므로
매칭하지 않는다. UNMATCHED는 정식 상태이고, 기사가 늦게 도착한 날은
UNMATCHED → MATCHED 전이가 허용된다(packages/domain/state.py).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from packages.domain import ReconciliationStatus
from packages.infostock.models import ThemeDetail

from .models import (
    CanonicalEvent,
    EventInputMetadata,
    EventWriteResult,
    LineageRef,
    ReconcileEventCommand,
)
from .writer import EventWriter

# packages/pipeline/market.py의 INTRADAY_CATALYST_KEY와 같은 값이어야 한다.
INTRADAY_CATALYST_KEY = "INTRADAY_STRENGTH"
AFTER_CLOSE_RECONCILE_POLICY = "after-close-reconcile-2026.08.1"
RECONCILIATION_SOURCE = "after-close-reconciliation"
CONFIRMATION_LINEAGE_KIND = "INFOSTOCK_THEME_HISTORY"


@dataclass(frozen=True, slots=True)
class AfterCloseConfirmation:
    """장후 인포스탁 테마 history 한 건을 정합 입력으로 요약한 값."""

    canonical_theme_id: str
    event_date: date
    direction: str
    summary: str
    lineage: LineageRef


@dataclass(frozen=True, slots=True)
class AfterCloseReconciliationRun:
    """정합 1회 실행 결과.

    skipped_event_ids는 이미 종결된 Event(MATCHED이거나, 기사 없이 UNMATCHED
    유지)라 쓰지 않은 것. unmatched_confirmations는 상승 확정 기사는 있는데
    장중 Event가 없던 테마 — 탐지 누락 분석용으로 그대로 돌려준다.
    """

    market_date: date
    matched: tuple[EventWriteResult, ...]
    unmatched: tuple[EventWriteResult, ...]
    skipped_event_ids: tuple[str, ...]
    unmatched_confirmations: tuple[AfterCloseConfirmation, ...]


def confirmations_from_theme_details(
    details: Iterable[ThemeDetail],
) -> tuple[AfterCloseConfirmation, ...]:
    """인포스탁 theme detail의 history를 정합 입력으로 바꾼다.

    canonical theme id 규칙(`thm_{source_theme_id}`)은
    packages/pipeline/membership.py의 명단 로더와 같아야 한다. 날짜가 없는
    history는 어느 거래일과도 정합할 수 없으므로 제외한다.
    """

    confirmations: list[AfterCloseConfirmation] = []
    seen: set[str] = set()
    for detail in details:
        theme_id = f"thm_{detail.source_theme_id}"
        for entry in detail.history:
            if entry.event_date is None:
                continue
            identifier = f"{detail.source_theme_id}:{entry.source_history_key}"
            if identifier in seen:
                continue
            seen.add(identifier)
            confirmations.append(
                AfterCloseConfirmation(
                    canonical_theme_id=theme_id,
                    event_date=entry.event_date,
                    direction=entry.direction,
                    summary=entry.raw_text,
                    lineage=LineageRef(
                        kind=CONFIRMATION_LINEAGE_KIND,
                        identifier=identifier,
                        version=None,
                        content_hash=entry.content_hash,
                    ),
                )
            )
    return tuple(confirmations)


def reconcile_after_close(
    *,
    writer: EventWriter,
    events: Iterable[CanonicalEvent],
    confirmations: Iterable[AfterCloseConfirmation],
    market_date: date,
    now: datetime,
    catalyst_key: str = INTRADAY_CATALYST_KEY,
) -> AfterCloseReconciliationRun:
    """market_date의 장중 Event마다 상승 확정 기사 유무로 정합 revision을 쓴다.

    재실행 안전성: message_id가 (event, 결과, 기사 묶음)에 결정적이라 같은
    입력의 재실행은 이미 쓴 command의 receipt를 재생하고, 이미 종결된 Event는
    쓰지 않고 건너뛴다. source_sequence는 now에서 유도되므로 늦은 기사를
    반영하는 다음 실행은 이전 실행보다 뒤의 now로 호출해야 한다.
    """

    up_by_theme: dict[str, list[AfterCloseConfirmation]] = {}
    for confirmation in confirmations:
        if confirmation.event_date != market_date or confirmation.direction != "UP":
            continue
        up_by_theme.setdefault(confirmation.canonical_theme_id, []).append(
            confirmation
        )
    for articles in up_by_theme.values():
        articles.sort(key=lambda item: item.lineage.identifier)

    eligible = sorted(
        (
            event
            for event in events
            if event.market_date == market_date
            and event.catalyst_key == catalyst_key
        ),
        key=lambda event: event.event_id,
    )
    sequence = int(now.timestamp())
    correlation_id = f"reconcile:{market_date.isoformat()}"
    matched: list[EventWriteResult] = []
    unmatched: list[EventWriteResult] = []
    skipped: list[str] = []

    for event in eligible:
        articles = up_by_theme.get(event.canonical_theme_id, [])
        if event.reconciliation_status is ReconciliationStatus.MATCHED or (
            not articles
            and event.reconciliation_status is ReconciliationStatus.UNMATCHED
        ):
            skipped.append(event.event_id)
            continue
        if articles:
            digest = hashlib.sha256(
                "|".join(
                    f"{item.lineage.identifier}:{item.lineage.content_hash}"
                    for item in articles
                ).encode("utf-8")
            ).hexdigest()[:16]
            command = ReconcileEventCommand(
                metadata=EventInputMetadata(
                    message_id=f"reconcile:{event.event_id}:MATCHED:{digest}",
                    source=RECONCILIATION_SOURCE,
                    source_sequence=sequence,
                    occurred_at=now,
                    received_at=now,
                    correlation_id=correlation_id,
                    lineage=tuple(item.lineage for item in articles),
                ),
                event_id=event.event_id,
                target=ReconciliationStatus.MATCHED,
                expected_state_version=event.state_version,
                reason=f"장후 인포스탁 확정 기사 {len(articles)}건 정합",
                policy_version=AFTER_CLOSE_RECONCILE_POLICY,
            )
            matched.append(writer.write(command))
        else:
            command = ReconcileEventCommand(
                metadata=EventInputMetadata(
                    message_id=f"reconcile:{event.event_id}:UNMATCHED",
                    source=RECONCILIATION_SOURCE,
                    source_sequence=sequence,
                    occurred_at=now,
                    received_at=now,
                    correlation_id=correlation_id,
                ),
                event_id=event.event_id,
                target=ReconciliationStatus.UNMATCHED,
                expected_state_version=event.state_version,
                reason="장후 인포스탁 확정 기사 없음",
                policy_version=AFTER_CLOSE_RECONCILE_POLICY,
            )
            unmatched.append(writer.write(command))

    event_theme_ids = {event.canonical_theme_id for event in eligible}
    unmatched_confirmations = tuple(
        item
        for theme_id in sorted(up_by_theme)
        if theme_id not in event_theme_ids
        for item in up_by_theme[theme_id]
    )
    return AfterCloseReconciliationRun(
        market_date=market_date,
        matched=tuple(matched),
        unmatched=tuple(unmatched),
        skipped_event_ids=tuple(skipped),
        unmatched_confirmations=unmatched_confirmations,
    )
