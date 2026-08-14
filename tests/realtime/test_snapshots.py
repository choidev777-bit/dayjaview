from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from packages.domain import DataStatus
from packages.realtime import (
    InMemorySnapshotRepository,
    SnapshotCommitFailure,
    SnapshotIdempotencyConflict,
    SnapshotPublication,
    SnapshotTopic,
    SnapshotVersions,
    StaleSnapshotPublication,
    VersionedSnapshotWriter,
)
from scripts.validate_contracts import validate_instance

from ._factories import MARKET_DATE, START

CONTRACT_FIXTURES = Path(__file__).resolve().parents[2] / "contracts/fixtures/realtime"


def versions() -> SnapshotVersions:
    return SnapshotVersions(
        schema_version="2026-08-14.1",
        calculation_version="theme-metrics-2026.08.1",
        ranking_model_version="theme-rank-2026.08.1",
        membership_version="membership-2026-08-14T00:00:00Z",
        baseline_version="same-time-turnover-20d-2026.08.1",
    )


def publication(
    publication_id: str,
    *,
    stream_id: str = "stream_market_20260814",
    topic: SnapshotTopic = SnapshotTopic.THEME_RANK,
    params: dict[str, object] | None = None,
    seconds: int = 0,
    payload: dict[str, object] | None = None,
) -> SnapshotPublication:
    return SnapshotPublication(
        publication_id=publication_id,
        stream_id=stream_id,
        topic=topic,
        params=params or {"limit": 10},
        market_date=MARKET_DATE,
        generated_at=START + timedelta(seconds=seconds, milliseconds=100),
        as_of=START + timedelta(seconds=seconds),
        data_status=DataStatus.LIVE,
        quality_flags=(),
        payload=payload or {"items": []},
        versions=versions(),
    )


def test_snapshot_publication_is_idempotent_and_sequence_is_monotonic() -> None:
    repository = InMemorySnapshotRepository()
    writer = VersionedSnapshotWriter(repository)
    first_request = publication("publish-1")

    first = writer.publish(first_request)
    replay = writer.publish(first_request)
    second = VersionedSnapshotWriter(repository).publish(
        publication("publish-2", seconds=3)
    )

    assert replay == first
    assert first.sequence == 1
    assert second.sequence == 2
    assert (
        repository.latest(
            stream_id=first.stream_id,
            topic=first.topic,
            params={"limit": 10},
        )
        == second
    )


def test_sequence_scope_is_stream_topic_and_normalized_params() -> None:
    repository = InMemorySnapshotRepository()
    writer = VersionedSnapshotWriter(repository)

    base = writer.publish(publication("rank-10"))
    other_params = writer.publish(publication("rank-20", params={"limit": 20}))
    other_topic = writer.publish(
        publication(
            "treemap-10",
            topic=SnapshotTopic.THEME_TREEMAP,
            params={"limit": 10},
        )
    )
    other_stream = writer.publish(
        publication("rank-new-stream", stream_id="stream_restarted_20260814")
    )

    assert base.sequence == other_params.sequence == other_topic.sequence == 1
    assert other_stream.sequence == 1


def test_same_publication_id_with_different_snapshot_is_rejected() -> None:
    repository = InMemorySnapshotRepository()
    original = publication("same-publication")
    repository.publish(original)

    with pytest.raises(SnapshotIdempotencyConflict):
        repository.publish(replace(original, payload={"items": [{"changed": True}]}))


def test_out_of_order_snapshot_does_not_advance_sequence_or_replace_latest() -> None:
    repository = InMemorySnapshotRepository()
    latest = repository.publish(publication("publish-current", seconds=10))
    stale = publication("publish-stale", seconds=5)

    with pytest.raises(StaleSnapshotPublication):
        repository.publish(stale)

    next_snapshot = repository.publish(publication("publish-next", seconds=11))
    assert next_snapshot.sequence == latest.sequence + 1
    assert (
        repository.latest(
            stream_id=latest.stream_id,
            topic=latest.topic,
            params={"limit": 10},
        )
        == next_snapshot
    )


def test_snapshot_commit_failure_rolls_back_sequence_and_retry_recovers() -> None:
    repository = InMemorySnapshotRepository()
    request = publication("publish-crash")
    repository.fail_next_commit()

    with pytest.raises(SnapshotCommitFailure):
        repository.publish(request)
    assert (
        repository.latest(
            stream_id=request.stream_id,
            topic=request.topic,
            params=request.params,
        )
        is None
    )

    recovered = VersionedSnapshotWriter(repository).publish(request)
    assert recovered.sequence == 1


def test_ranking_snapshot_wire_message_matches_contract() -> None:
    snapshot = InMemorySnapshotRepository().publish(publication("publish-contract"))
    message = snapshot.to_ws_message(subscription_id="sub_001")

    validate_instance(message, "WsRankingSnapshot")
    assert message["payload"] == {
        "snapshotId": snapshot.snapshot_id,
        "items": [],
    }


def test_event_state_snapshot_is_full_summary_without_nested_snapshot_id() -> None:
    fixture = json.loads(
        (CONTRACT_FIXTURES / "event-state-changed.json").read_text(encoding="utf-8")
    )
    request = publication(
        "publish-event-state",
        stream_id="stream_event_20260814",
        topic=SnapshotTopic.EVENT_STATE_CHANGED,
        params={"eventIds": ["evt_current"]},
        payload=fixture["payload"],
    )
    snapshot = InMemorySnapshotRepository().publish(request)
    message = snapshot.to_ws_message(subscription_id="sub_001")

    validate_instance(message, "WsEventStateChanged")
    assert "snapshotId" not in message["payload"]
    assert message["payload"]["eventId"] == "evt_current"


def test_snapshot_payload_preserves_null_zero_and_empty_array_distinctions() -> None:
    payload = {"missingMetric": None, "actualZero": 0, "items": []}
    snapshot = InMemorySnapshotRepository().publish(
        publication("publish-null-zero", payload=payload)
    )

    assert snapshot.payload["missingMetric"] is None
    assert snapshot.payload["actualZero"] == 0
    assert snapshot.payload["items"] == []


def test_snapshot_versions_round_trip_without_collapsing_version_axes() -> None:
    snapshot = InMemorySnapshotRepository().publish(publication("publish-versions"))
    restored = type(snapshot).from_dict(snapshot.to_dict())

    assert restored == snapshot
    assert restored.versions.calculation_version != (
        restored.versions.ranking_model_version
    )
    assert restored.versions.membership_version.startswith("membership-")


def test_snapshot_rejects_as_of_after_generated_at() -> None:
    request = publication("publish-bad-time")
    with pytest.raises(ValueError, match="as_of"):
        replace(request, as_of=request.generated_at + timedelta(seconds=1))
