from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from packages.domain import LifecycleStatus
from packages.events import (
    CanonicalEventIdentity,
    CreateEventCommand,
    EventInputMetadata,
    EventVersions,
    LineageRef,
    TransitionLifecycleCommand,
)

MARKET_DATE = date(2026, 8, 14)
START = datetime(2026, 8, 14, 0, 10, tzinfo=UTC)


def metadata(
    *,
    message_id: str,
    sequence: int,
    seconds: int = 0,
    source: str = "activation-ranking",
    occurred_offset: int | None = None,
) -> EventInputMetadata:
    received_at = START + timedelta(seconds=seconds)
    occurred_at = START + timedelta(
        seconds=seconds if occurred_offset is None else occurred_offset
    )
    return EventInputMetadata(
        message_id=message_id,
        source=source,
        source_sequence=sequence,
        occurred_at=occurred_at,
        received_at=received_at,
        correlation_id="corr-market-20260814",
        causation_id="market-input-001",
        lineage=(
            LineageRef(
                kind="THEME_METRIC_SNAPSHOT",
                identifier=f"metric-{sequence}",
                version="theme-metrics-2026.08.1",
                content_hash="a" * 64,
            ),
        ),
    )


def create_command(
    *,
    message_id: str = "cmd-create-1",
    sequence: int = 1,
    display_name: str = "원전수출",
) -> CreateEventCommand:
    return CreateEventCommand(
        metadata=metadata(message_id=message_id, sequence=sequence),
        identity=CanonicalEventIdentity(
            market_date=MARKET_DATE,
            canonical_theme_id="thm_nuclear",
            catalyst_key="cluster_nuclear_export",
        ),
        display_name=display_name,
        versions=EventVersions(
            calculation_version="theme-metrics-2026.08.1",
            ranking_model_version="theme-rank-2026.08.1",
            membership_version="membership-2026-08-14T00:00:00Z",
            baseline_version="same-time-turnover-20d-2026.08.1",
        ),
    )


def transition_command(
    *,
    event_id: str,
    message_id: str,
    sequence: int,
    seconds: int,
    expected: int,
    target: LifecycleStatus,
    occurred_offset: int | None = None,
) -> TransitionLifecycleCommand:
    return TransitionLifecycleCommand(
        metadata=metadata(
            message_id=message_id,
            sequence=sequence,
            seconds=seconds,
            occurred_offset=occurred_offset,
        ),
        event_id=event_id,
        target=target,
        expected_state_version=expected,
        reason=f"{target.value} fixture 판정",
        policy_version="theme-hysteresis-2026.08.1",
    )
