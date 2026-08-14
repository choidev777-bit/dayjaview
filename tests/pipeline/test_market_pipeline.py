from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from packages.domain import (
    DataStatus,
    MembershipRole,
    StockReference,
    ThemeMember,
    ThemeMembershipSnapshot,
)
from packages.events import InMemoryEventStore
from packages.pipeline import MarketDataPipeline
from packages.realtime import (
    InMemorySnapshotRepository,
    StockRealtimeUpdate,
    VersionedThemeCatalog,
)

MARKET_DATE = date(2026, 8, 14)
KNOWN_AT = datetime(2026, 8, 13, 23, 0, tzinfo=UTC)
BASE = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
MEMBERSHIP_VERSION = "membership-test-1"

_PRICES: dict[str, tuple[str, str]] = {
    # stock_id: (previous_close, current_price)
    "KRX:000001": ("10000", "10300"),
    "KRX:000002": ("20000", "20200"),
    "KRX:000003": ("40000", "40100"),
    "KRX:000009": ("5000", "5100"),
}


def _catalog() -> VersionedThemeCatalog:
    return VersionedThemeCatalog(
        (
            ThemeMembershipSnapshot(
                theme_id="thm_full",
                version=MEMBERSHIP_VERSION,
                effective_from=MARKET_DATE,
                known_at=KNOWN_AT,
                members=(
                    ThemeMember("KRX:000001", MembershipRole.CORE),
                    ThemeMember("KRX:000002", MembershipRole.CORE),
                    ThemeMember("KRX:000003", MembershipRole.CORE),
                ),
            ),
            # 관측이 최소 기준(3)에 못 미쳐 rankings에서 제외되어야 하는 테마
            ThemeMembershipSnapshot(
                theme_id="thm_thin",
                version=MEMBERSHIP_VERSION,
                effective_from=MARKET_DATE,
                known_at=KNOWN_AT,
                members=(
                    ThemeMember("KRX:000009", MembershipRole.CORE),
                ),
            ),
        )
    )


def _references() -> tuple[StockReference, ...]:
    return tuple(
        StockReference(
            stock_id=stock_id,
            effective_for=MARKET_DATE,
            known_at=KNOWN_AT,
            previous_adjusted_close=Decimal(previous_close),
            listed_shares=1_000_000,
            free_float_ratio=Decimal("0.5"),
            free_float_validated=True,
            version="reference-test-1",
        )
        for stock_id, (previous_close, _) in _PRICES.items()
    )


def _pipeline() -> MarketDataPipeline:
    return MarketDataPipeline(
        market_date=MARKET_DATE,
        stream_id="stream_test",
        schema_version="2026-08-14.1",
        catalog=_catalog(),
        references=_references(),
        membership_version=MEMBERSHIP_VERSION,
        theme_names={"thm_full": "테스트 테마", "thm_thin": "빈약 테마"},
        stock_names={stock_id: stock_id for stock_id in _PRICES},
        event_store=InMemoryEventStore(),
        snapshot_repository=InMemorySnapshotRepository(),
    )


def _update(stock_id: str, *, seconds: int) -> StockRealtimeUpdate:
    _, current_price = _PRICES[stock_id]
    at = BASE + timedelta(seconds=seconds)
    return StockRealtimeUpdate(
        message_id=f"msg_{stock_id}_{seconds}",
        stock_id=stock_id,
        market_date=MARKET_DATE,
        source="test-session",
        source_sequence=seconds,
        occurred_at=at,
        received_at=at,
        current_price=Decimal(current_price),
        cumulative_trading_value=Decimal("1000000"),
    )


def _expected_weighted_return() -> float:
    # 3종목 유동시총이 모두 상한(1/3)에 걸려 동일가중 평균과 같다.
    returns = [
        Decimal(current) / Decimal(previous) - Decimal(1)
        for stock_id, (previous, current) in _PRICES.items()
        if stock_id != "KRX:000009"
    ]
    return float(sum(returns) / Decimal(3))


def test_market_updates_flow_into_active_event_and_ranked_snapshot() -> None:
    pipeline = _pipeline()
    for index, stock_id in enumerate(
        ("KRX:000001", "KRX:000002", "KRX:000003", "KRX:000009")
    ):
        result = pipeline.apply_update(_update(stock_id, seconds=index + 1))
        assert result.changed

    first = pipeline.publish(
        now=BASE + timedelta(seconds=7),
        data_status=DataStatus.LIVE,
    )
    # 첫 발행: hysteresis activate_after(10초) 이전이라 아직 후보 상태
    assert first.rankings.payload == {"items": []}
    assert {event.lifecycle_status.value for event in first.events} == {"CANDIDATE"}

    second = pipeline.publish(
        now=BASE + timedelta(seconds=20),
        data_status=DataStatus.LIVE,
    )
    items = second.rankings.payload["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    item = items[0]
    assert item["lifecycleStatus"] == "ACTIVE"
    assert item["rank"] == 1
    assert item["classification"]["themeId"] == "thm_full"
    assert item["classification"]["displayName"] == "테스트 테마"
    assert item["weightedReturn"] == _expected_weighted_return()
    assert item["advancingCount"] == 3
    assert item["validCount"] == 3
    assert item["coverage"]["status"] == "SUFFICIENT"
    assert item["evidence"] == {
        "evidenceStatus": "SEARCHING",
        "summary": None,
        "publishedAt": None,
    }
    # 수익률 최고 종목(000001: +3%)이 주도주
    assert item["leader"]["stockId"] == "KRX:000001"
    assert item["leader"]["symbol"] == "000001"

    assert second.rankings.sequence == 2
    assert second.rankings.stream_id == "stream_test"
    assert second.rankings.data_status is DataStatus.LIVE

    treemap_items = second.treemap.payload["items"]
    assert isinstance(treemap_items, list)
    assert treemap_items == [
        {
            "eventId": item["eventId"],
            "themeId": "thm_full",
            "displayName": "테스트 테마",
            "lifecycleStatus": "ACTIVE",
            "weightedReturn": item["weightedReturn"],
            "advancingCount": 3,
            "validCount": 3,
            "coverageStatus": "SUFFICIENT",
            "qualityFlags": [],
        }
    ]

    # 최소 관측 미달 테마는 후보로만 남고 rankings·treemap 어디에도 없다
    statuses = {
        event.canonical_theme_id: event.lifecycle_status.value
        for event in second.events
    }
    assert statuses["thm_thin"] == "CANDIDATE"
    assert statuses["thm_full"] == "ACTIVE"


def test_duplicate_updates_do_not_change_published_values() -> None:
    pipeline = _pipeline()
    update = _update("KRX:000001", seconds=1)
    assert pipeline.apply_update(update).changed
    duplicate = pipeline.apply_update(update)
    assert not duplicate.changed

    view = pipeline.publish(
        now=BASE + timedelta(seconds=7),
        data_status=DataStatus.LIVE,
    )
    assert view.rankings.sequence == 1
