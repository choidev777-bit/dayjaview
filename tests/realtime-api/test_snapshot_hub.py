from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from apps.api import (
    RealtimeSnapshotHub,
    SnapshotIngressDisposition,
    normalize_topic_request,
)
from packages.domain import DataStatus
from packages.realtime import (
    InMemorySnapshotRepository,
    SnapshotPublication,
    SnapshotTopic,
    SnapshotVersions,
)

_START = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
_MARKET_DATE = date(2026, 8, 14)
_PARAMS = {"limit": 10}


def _snapshot(
    publication_id: str,
    *,
    sequence: int,
    stream_id: str = "stream_fixture",
    seconds: int = 0,
):
    publication = SnapshotPublication(
        publication_id=publication_id,
        stream_id=stream_id,
        topic=SnapshotTopic.THEME_RANK,
        params=_PARAMS,
        market_date=_MARKET_DATE,
        generated_at=_START + timedelta(seconds=seconds, milliseconds=100),
        as_of=_START + timedelta(seconds=seconds),
        data_status=DataStatus.LIVE,
        quality_flags=(),
        payload={"items": []},
        versions=SnapshotVersions(
            schema_version="2026-08-14.1",
            calculation_version="theme-metrics-2026.08.1",
            ranking_model_version="theme-rank-2026.08.1",
            membership_version="membership-2026-08-14T00:00:00Z",
        ),
    )
    snapshot = InMemorySnapshotRepository().publish(publication)
    return replace(snapshot, sequence=sequence)


def test_hub_rejects_duplicate_out_of_order_and_retired_stream_snapshots() -> None:
    hub = RealtimeSnapshotHub()
    first = _snapshot("pub-first", sequence=10)
    older = _snapshot("pub-older", sequence=9, seconds=1)
    gap = _snapshot("pub-gap", sequence=12, seconds=2)
    restarted = _snapshot(
        "pub-restarted",
        sequence=1,
        stream_id="stream_restarted",
        seconds=3,
    )
    late_old_stream = _snapshot("pub-late-old", sequence=13, seconds=4)

    assert hub.publish(first, params=_PARAMS) is SnapshotIngressDisposition.APPLIED
    assert hub.publish(first, params=_PARAMS) is SnapshotIngressDisposition.DUPLICATE
    assert hub.publish(older, params=_PARAMS) is SnapshotIngressDisposition.OUT_OF_ORDER
    assert hub.publish(gap, params=_PARAMS) is SnapshotIngressDisposition.APPLIED
    assert hub.publish(restarted, params=_PARAMS) is SnapshotIngressDisposition.APPLIED
    assert (
        hub.publish(late_old_stream, params=_PARAMS)
        is SnapshotIngressDisposition.RETIRED_STREAM
    )

    topic = normalize_topic_request(
        {"name": "theme_rank_snapshot", "params": _PARAMS}
    )
    assert hub.latest(topic) == restarted
    assert hub.latest(topic).sequence == 1  # type: ignore[union-attr]
    assert hub.latest(topic).stream_id == "stream_restarted"  # type: ignore[union-attr]


def test_hub_coalesces_listener_notification_to_latest_full_snapshot() -> None:
    async def scenario() -> None:
        hub = RealtimeSnapshotHub()
        listener = hub.open_listener()
        topic = normalize_topic_request(
            {"name": "theme_rank_snapshot", "params": _PARAMS}
        )
        listener.add_subscription("sub_fixture", (topic,))
        second = _snapshot("pub-second", sequence=2, seconds=2)
        third = _snapshot("pub-third", sequence=3, seconds=3)

        hub.publish(second, params=_PARAMS)
        hub.publish(third, params=_PARAMS)
        await asyncio.wait_for(listener.wait(), timeout=0.2)
        assert hub.latest(topic) == third
        with_timeout = asyncio.create_task(listener.wait())
        try:
            await asyncio.wait_for(with_timeout, timeout=0.03)
        except TimeoutError:
            pass
        else:
            raise AssertionError("coalesced listener must not accumulate notifications")
        finally:
            listener.close()

    asyncio.run(scenario())
