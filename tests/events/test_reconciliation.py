"""D-13 장후 정합: 같은 eventId revision, UNMATCHED 허용, 재실행 안전."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from importlib import import_module

import pytest

from packages.domain import (
    InvalidStateTransition,
    ReconciliationStatus,
    StateAxis,
)
from packages.events import (
    CanonicalEvent,
    CanonicalEventIdentity,
    ClassificationCertainty,
    ClassificationSource,
    CreateEventCommand,
    EventInputMetadata,
    EventVersions,
    EventWriteDisposition,
    EventWriter,
    InMemoryEventStore,
    LineageRef,
    ReconcileEventCommand,
    SimulatedCommitFailure,
)
from packages.events.reconciliation import (
    AFTER_CLOSE_RECONCILE_POLICY,
    CONFIRMATION_LINEAGE_KIND,
    INTRADAY_CATALYST_KEY,
    RECONCILIATION_SOURCE,
    AfterCloseConfirmation,
    confirmations_from_theme_details,
    reconcile_after_close,
)
from packages.infostock.models import RawSnapshot, ThemeDetail, ThemeHistory

MARKET_DATE = date(2026, 8, 14)
INTRADAY = datetime(2026, 8, 14, 0, 10, tzinfo=UTC)  # 09:10 KST 장중
AFTER_CLOSE = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)  # 18:00 KST 장후
NEXT_RUN = AFTER_CLOSE + timedelta(hours=3)
_daily_worker = import_module(
    "apps." + "worker-batch.infostock.reconcile_after_close"
)


def _create_event(
    writer: EventWriter,
    *,
    theme_id: str,
    sequence: int,
    market_date: date = MARKET_DATE,
    catalyst_key: str = INTRADAY_CATALYST_KEY,
) -> CanonicalEvent:
    command = CreateEventCommand(
        metadata=EventInputMetadata(
            message_id=f"cmd-create-{theme_id}-{catalyst_key}",
            source="activation-ranking",
            source_sequence=sequence,
            occurred_at=INTRADAY,
            received_at=INTRADAY,
            correlation_id="corr-market-20260814",
        ),
        identity=CanonicalEventIdentity(
            market_date=market_date,
            canonical_theme_id=theme_id,
            catalyst_key=catalyst_key,
        ),
        display_name=f"테마 {theme_id}",
        versions=EventVersions(
            calculation_version="theme-metrics-2026.08.1",
            ranking_model_version="theme-rank-2026.08.1",
            membership_version="membership-test-1",
            baseline_version=None,
        ),
    )
    return writer.write(command).event


def _article_ref(key: str) -> LineageRef:
    return LineageRef(
        kind=CONFIRMATION_LINEAGE_KIND,
        identifier=f"src:{key}",
        version=None,
        content_hash="a" * 64,
    )


def _confirmation(
    theme_id: str,
    *,
    key: str,
    direction: str = "UP",
    event_date: date = MARKET_DATE,
) -> AfterCloseConfirmation:
    return AfterCloseConfirmation(
        canonical_theme_id=theme_id,
        event_date=event_date,
        direction=direction,
        summary=f"{key} 사유에 상승",
        lineage=_article_ref(key),
    )


def _reconcile_command(
    event: CanonicalEvent,
    *,
    target: ReconciliationStatus,
    message_id: str = "cmd-reconcile-1",
    sequence: int = 1000,
    lineage: tuple[LineageRef, ...] = (),
) -> ReconcileEventCommand:
    return ReconcileEventCommand(
        metadata=EventInputMetadata(
            message_id=message_id,
            source=RECONCILIATION_SOURCE,
            source_sequence=sequence,
            occurred_at=AFTER_CLOSE,
            received_at=AFTER_CLOSE,
            correlation_id="reconcile:2026-08-14",
            lineage=lineage,
        ),
        event_id=event.event_id,
        target=target,
        expected_state_version=event.state_version,
        reason="테스트 정합",
        policy_version=AFTER_CLOSE_RECONCILE_POLICY,
    )


def test_matched_revision_confirms_classification_on_same_event() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    event = _create_event(writer, theme_id="thm_1", sequence=1)
    ref = _article_ref("h1")

    result = writer.write(
        _reconcile_command(
            event, target=ReconciliationStatus.MATCHED, lineage=(ref,)
        )
    )

    assert result.disposition is EventWriteDisposition.APPLIED
    updated = result.event
    assert updated.event_id == event.event_id
    assert updated.state_version == 2
    assert updated.reconciliation_status is ReconciliationStatus.MATCHED
    assert updated.lifecycle_status is event.lifecycle_status
    assert updated.classification.classification_version == 2
    assert updated.classification.certainty is ClassificationCertainty.CONFIRMED
    assert updated.classification.source is ClassificationSource.INFOSTOCK
    assert updated.classification.theme_id == "thm_1"
    assert updated.lineage == (ref,)

    logs = store.state_logs(event.event_id)
    assert len(logs) == 2
    assert logs[-1].axis is StateAxis.RECONCILIATION
    assert logs[-1].from_status is ReconciliationStatus.PENDING
    assert logs[-1].to_status is ReconciliationStatus.MATCHED
    assert logs[-1].lineage == (ref,)
    outbox = store.read_outbox(result.outbox_message_id or "")
    assert outbox is not None
    assert outbox.event_type == "canonical_event.reconciled"
    assert outbox.payload["fromReconciliationStatus"] == "PENDING"
    assert outbox.payload["classificationVersion"] == 2


def test_unmatched_keeps_provisional_classification() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    event = _create_event(writer, theme_id="thm_1", sequence=1)

    result = writer.write(
        _reconcile_command(event, target=ReconciliationStatus.UNMATCHED)
    )

    updated = result.event
    assert updated.state_version == 2
    assert updated.reconciliation_status is ReconciliationStatus.UNMATCHED
    assert updated.classification == event.classification
    assert (
        updated.classification.certainty is ClassificationCertainty.PROVISIONAL
    )


def test_unmatched_can_match_later_but_matched_is_terminal() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    event = _create_event(writer, theme_id="thm_1", sequence=1)

    first = writer.write(
        _reconcile_command(event, target=ReconciliationStatus.UNMATCHED)
    ).event
    second = writer.write(
        _reconcile_command(
            first,
            target=ReconciliationStatus.MATCHED,
            message_id="cmd-reconcile-2",
            sequence=1001,
            lineage=(_article_ref("late"),),
        )
    ).event
    assert second.state_version == 3
    assert second.reconciliation_status is ReconciliationStatus.MATCHED
    assert second.classification.classification_version == 2

    with pytest.raises(InvalidStateTransition):
        writer.write(
            _reconcile_command(
                second,
                target=ReconciliationStatus.UNMATCHED,
                message_id="cmd-reconcile-3",
                sequence=1002,
            )
        )
    with pytest.raises(ValueError):
        _reconcile_command(second, target=ReconciliationStatus.PENDING)


def test_reconcile_commit_failure_rolls_back_atomically() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    event = _create_event(writer, theme_id="thm_1", sequence=1)

    store.fail_next_commit()
    with pytest.raises(SimulatedCommitFailure):
        writer.write(
            _reconcile_command(event, target=ReconciliationStatus.MATCHED)
        )

    stored = store.read_event(event.event_id)
    assert stored is not None
    assert stored.state_version == 1
    assert stored.reconciliation_status is ReconciliationStatus.PENDING
    assert len(store.state_logs(event.event_id)) == 1
    assert len(store.outbox_records()) == 1


def test_reconcile_replay_is_idempotent() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    event = _create_event(writer, theme_id="thm_1", sequence=1)
    command = _reconcile_command(event, target=ReconciliationStatus.MATCHED)

    first = writer.write(command)
    replay = writer.write(command)

    assert replay == first
    assert len(store.state_logs(event.event_id)) == 2
    assert len(store.outbox_records()) == 2


def test_engine_matches_up_articles_and_marks_rest() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    detected = _create_event(writer, theme_id="thm_1", sequence=1)
    undetected_theme = _create_event(writer, theme_id="thm_2", sequence=2)

    run = reconcile_after_close(
        writer=writer,
        events=(detected, undetected_theme),
        confirmations=(
            _confirmation("thm_1", key="h2"),
            _confirmation("thm_1", key="h1"),
            _confirmation("thm_1", key="wrong-date", event_date=date(2026, 8, 13)),
            _confirmation("thm_2", key="down", direction="DOWN"),
            _confirmation("thm_3", key="missed"),
        ),
        market_date=MARKET_DATE,
        now=AFTER_CLOSE,
    )

    assert len(run.matched) == 1
    matched = run.matched[0].event
    assert matched.event_id == detected.event_id
    assert matched.reconciliation_status is ReconciliationStatus.MATCHED
    assert matched.classification.certainty is ClassificationCertainty.CONFIRMED
    # 같은 날 UP 기사 2건만, identifier 순서로 lineage에 남는다.
    assert [ref.identifier for ref in matched.lineage] == ["src:h1", "src:h2"]

    assert len(run.unmatched) == 1
    unmatched = run.unmatched[0].event
    assert unmatched.event_id == undetected_theme.event_id
    assert unmatched.reconciliation_status is ReconciliationStatus.UNMATCHED

    assert run.skipped_event_ids == ()
    assert [item.lineage.identifier for item in run.unmatched_confirmations] == [
        "src:missed"
    ]


def test_engine_reruns_skip_and_promote_late_article() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    event = _create_event(writer, theme_id="thm_1", sequence=1)

    first = reconcile_after_close(
        writer=writer,
        events=(event,),
        confirmations=(),
        market_date=MARKET_DATE,
        now=AFTER_CLOSE,
    )
    assert len(first.unmatched) == 1
    current = first.unmatched[0].event
    assert current.reconciliation_status is ReconciliationStatus.UNMATCHED

    replay = reconcile_after_close(
        writer=writer,
        events=(current,),
        confirmations=(),
        market_date=MARKET_DATE,
        now=AFTER_CLOSE,
    )
    assert replay.matched == () and replay.unmatched == ()
    assert replay.skipped_event_ids == (event.event_id,)
    assert len(store.state_logs(event.event_id)) == 2

    late = reconcile_after_close(
        writer=writer,
        events=(current,),
        confirmations=(_confirmation("thm_1", key="late"),),
        market_date=MARKET_DATE,
        now=NEXT_RUN,
    )
    assert len(late.matched) == 1
    promoted = late.matched[0].event
    assert promoted.state_version == 3
    assert promoted.reconciliation_status is ReconciliationStatus.MATCHED

    final = reconcile_after_close(
        writer=writer,
        events=(promoted,),
        confirmations=(_confirmation("thm_1", key="late"),),
        market_date=MARKET_DATE,
        now=NEXT_RUN + timedelta(hours=1),
    )
    assert final.skipped_event_ids == (event.event_id,)
    assert len(store.state_logs(event.event_id)) == 3


def test_engine_ignores_other_catalyst_and_other_market_date() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    other_catalyst = _create_event(
        writer, theme_id="thm_1", sequence=1, catalyst_key="cluster_manual"
    )
    other_date = _create_event(
        writer, theme_id="thm_2", sequence=2, market_date=date(2026, 8, 13)
    )

    run = reconcile_after_close(
        writer=writer,
        events=(other_catalyst, other_date),
        confirmations=(_confirmation("thm_1", key="h1"),),
        market_date=MARKET_DATE,
        now=AFTER_CLOSE,
    )

    assert run.matched == () and run.unmatched == ()
    assert run.skipped_event_ids == ()
    for event in (other_catalyst, other_date):
        stored = store.read_event(event.event_id)
        assert stored is not None
        assert stored.reconciliation_status is ReconciliationStatus.PENDING
    # 대상 Event가 없는 UP 기사는 탐지 누락으로 보고된다.
    assert [item.lineage.identifier for item in run.unmatched_confirmations] == [
        "src:h1"
    ]


def _history(
    *,
    key: str,
    event_date: date | None,
    direction: str,
    raw_text: str,
) -> ThemeHistory:
    return ThemeHistory(
        source_order=1,
        source_history_id=None,
        source_history_key=key,
        event_date=event_date,
        source_date=None if event_date is None else event_date.isoformat(),
        source_created_at=None,
        source_updated_at=None,
        raw_text=raw_text,
        direction=direction,  # type: ignore[arg-type]
        leaders=(),
        member_stocks=(),
        author=None,
        chart_flag=None,
        source_fingerprint=f"fp-{key}",
        quality_status="OK",
        content_hash=f"hash-{key}",
    )


def test_confirmations_from_theme_details_keeps_dated_entries_once() -> None:
    snapshot = RawSnapshot(
        page_type="THEME_DETAIL",
        source_entity_id="55",
        source_url="https://example.test/theme/55",
        collected_at=AFTER_CLOSE,
        as_of=AFTER_CLOSE,
        raw_hash="raw-55",
        source_content_hash=None,
        raw_payload_text="",
        raw_format="HTML",
        is_complete=True,
    )
    detail = ThemeDetail(
        source_theme_id="55",
        theme_name="원전",
        description="",
        theme_revision_hash="rev-1",
        history=(
            _history(
                key="h1",
                event_date=MARKET_DATE,
                direction="UP",
                raw_text="원전 수출 기대감에 상승",
            ),
            _history(
                key="h1",
                event_date=MARKET_DATE,
                direction="UP",
                raw_text="중복 항목",
            ),
            _history(
                key="h2",
                event_date=None,
                direction="UP",
                raw_text="날짜 없는 항목",
            ),
            _history(
                key="h3",
                event_date=MARKET_DATE,
                direction="DOWN",
                raw_text="차익 매물에 하락",
            ),
        ),
        memberships=(),
        snapshot=snapshot,
    )

    confirmations = confirmations_from_theme_details((detail,))

    assert [item.lineage.identifier for item in confirmations] == [
        "55:h1",
        "55:h3",
    ]
    first = confirmations[0]
    assert first.canonical_theme_id == "thm_55"
    assert first.event_date == MARKET_DATE
    assert first.direction == "UP"
    assert first.summary == "원전 수출 기대감에 상승"
    assert first.lineage.kind == CONFIRMATION_LINEAGE_KIND
    assert first.lineage.content_hash == "hash-h1"
    assert confirmations[1].direction == "DOWN"


def test_daily_confirmation_requires_an_explicit_positive_theme_move() -> None:
    assert _daily_worker._is_up(
        "원전",
        "",
        ("원전\t+3.25%\t한국원전\t+5.0%",),
    )
    assert not _daily_worker._is_up(
        "원전",
        "",
        ("원전\t-1.20%\t한국원전\t+5.0%",),
    )
    assert _daily_worker._is_up("원전", "수주 기대감으로 강세", ())
