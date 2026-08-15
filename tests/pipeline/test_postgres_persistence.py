from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from packages.domain import (
    DataStatus,
    MembershipRole,
    StockReference,
    ThemeMember,
    ThemeMembershipSnapshot,
)
from packages.events import PostgresEventStore
from packages.pipeline import MarketDataPipeline
from packages.pipeline.market import RANKINGS_PARAMS
from packages.realtime import (
    PostgresSnapshotRepository,
    SnapshotTopic,
    StockRealtimeUpdate,
    VersionedThemeCatalog,
)

TEST_DSN = os.environ.get("PIPELINE_TEST_DSN")
MIGRATION = (
    Path(__file__).resolve().parents[2] / "infra/migrations/0002_event_realtime.sql"
)
MARKET_DATE = date(2026, 8, 14)
KNOWN_AT = datetime(2026, 8, 13, 23, 0, tzinfo=UTC)
BASE = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
MEMBERSHIP_VERSION = "membership-test-1"
STOCK_IDS = ("KRX:000001", "KRX:000002", "KRX:000003")

pytestmark = pytest.mark.skipif(
    TEST_DSN is None,
    reason="PIPELINE_TEST_DSN의 disposable PostgreSQL 16이 필요합니다.",
)


@pytest.fixture()
def connection():
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(TEST_DSN, autocommit=True) as admin:
        admin.execute("DROP SCHEMA IF EXISTS event CASCADE")
        admin.execute("DROP SCHEMA IF EXISTS realtime CASCADE")
        admin.execute("DROP SCHEMA IF EXISTS serving CASCADE")
        admin.execute(MIGRATION.read_text(encoding="utf-8"))
    connection = psycopg.connect(TEST_DSN)
    try:
        yield connection
    finally:
        connection.close()


def _pipeline(connection) -> MarketDataPipeline:
    catalog = VersionedThemeCatalog(
        (
            ThemeMembershipSnapshot(
                theme_id="thm_full",
                version=MEMBERSHIP_VERSION,
                effective_from=MARKET_DATE,
                known_at=KNOWN_AT,
                members=tuple(
                    ThemeMember(stock_id, MembershipRole.CORE)
                    for stock_id in STOCK_IDS
                ),
            ),
        )
    )
    references = tuple(
        StockReference(
            stock_id=stock_id,
            effective_for=MARKET_DATE,
            known_at=KNOWN_AT,
            previous_adjusted_close=Decimal("10000"),
            listed_shares=1_000_000,
            free_float_ratio=Decimal("0.5"),
            free_float_validated=True,
            version="reference-test-1",
        )
        for stock_id in STOCK_IDS
    )
    return MarketDataPipeline(
        market_date=MARKET_DATE,
        stream_id="stream_pg_test",
        schema_version="2026-08-14.1",
        catalog=catalog,
        references=references,
        membership_version=MEMBERSHIP_VERSION,
        theme_names={"thm_full": "테스트 테마"},
        stock_names={stock_id: stock_id for stock_id in STOCK_IDS},
        event_store=PostgresEventStore(connection),
        snapshot_repository=PostgresSnapshotRepository(connection),
    )


def _update(stock_id: str, *, seconds: int) -> StockRealtimeUpdate:
    at = BASE + timedelta(seconds=seconds)
    return StockRealtimeUpdate(
        message_id=f"msg_{stock_id}_{seconds}",
        stock_id=stock_id,
        market_date=MARKET_DATE,
        source="test-session",
        source_sequence=seconds,
        occurred_at=at,
        received_at=at,
        current_price=Decimal("10300"),
        cumulative_trading_value=Decimal("1000000"),
    )


def _count(connection, query: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(query)
        return int(cursor.fetchone()[0])


def test_snapshots_and_events_accumulate_in_postgres(connection) -> None:
    pipeline = _pipeline(connection)
    for index, stock_id in enumerate(STOCK_IDS):
        pipeline.apply_update(_update(stock_id, seconds=index + 1))

    pipeline.publish(now=BASE + timedelta(seconds=7), data_status=DataStatus.LIVE)
    active = pipeline.publish(
        now=BASE + timedelta(seconds=20), data_status=DataStatus.LIVE
    )
    items = active.rankings.payload["items"]
    assert isinstance(items, list) and len(items) == 1
    assert items[0]["lifecycleStatus"] == "ACTIVE"

    pipeline.close_market(now=BASE + timedelta(hours=7))
    closed = pipeline.publish(
        now=BASE + timedelta(hours=7, seconds=1),
        data_status=DataStatus.CLOSED,
    )
    assert closed.rankings.payload == {"items": []}

    # Event가 테이블에 영속화되고 상태 전이 이력이 남는다.
    assert _count(connection, "SELECT count(*) FROM event.events") == 1
    with connection.cursor() as cursor:
        cursor.execute("SELECT lifecycle_status FROM event.events")
        assert cursor.fetchone()[0] == "CLOSED"
    # CANDIDATE 생성 → ACTIVE 승격 → 장 마감 CLOSED = state log 3건
    assert _count(connection, "SELECT count(*) FROM event.state_logs") == 3
    assert _count(connection, "SELECT count(*) FROM event.outbox") >= 1

    # 발행 3회 × topic 2개 = 스냅샷 6건이 쌓인다.
    assert (
        _count(connection, "SELECT count(*) FROM serving.realtime_snapshots") == 6
    )

    # 저장소 latest()가 마지막 rankings 스냅샷을 그대로 돌려준다.
    repository = PostgresSnapshotRepository(connection)
    latest = repository.latest(
        stream_id="stream_pg_test",
        topic=SnapshotTopic.THEME_RANK,
        params=dict(RANKINGS_PARAMS),
    )
    assert latest is not None
    assert latest.sequence == 3
    assert latest.snapshot_id == closed.rankings.snapshot_id
