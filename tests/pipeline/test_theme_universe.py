from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from packages.domain import (
    DataStatus,
    MembershipRole,
    StockReference,
    ThemeMember,
)
from packages.events import InMemoryEventStore
from packages.infostock import CommittedFixturePolicy, load_committed_fixture
from packages.infostock.models import (
    RawSnapshot,
    ReferenceQualityStatus,
    ThemeDetail,
    ThemeMembership,
)
from packages.pipeline import (
    MarketDataPipeline,
    ThemeUniverse,
    build_theme_universe,
)
from packages.realtime import InMemorySnapshotRepository, StockRealtimeUpdate

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = (
    REPOSITORY_ROOT / "tests" / "infostock" / "fixtures" / "infostock-280.synthetic.json"
)
MARKET_DATE = date(2026, 8, 14)
KNOWN_AT = datetime(2026, 8, 13, 23, 0, tzinfo=UTC)
BASE = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
VERSION = "membership-infostock-test"


def _detail(
    source_theme_id: str,
    theme_name: str,
    members: tuple[tuple[str | None, str, ReferenceQualityStatus], ...],
) -> ThemeDetail:
    return ThemeDetail(
        source_theme_id=source_theme_id,
        theme_name=theme_name,
        description="",
        theme_revision_hash=f"revision-{source_theme_id}",
        history=(),
        memberships=tuple(
            ThemeMembership(
                source_order=order,
                stock_code=stock_code,
                stock_name=stock_name,
                rationale="",
                source_index=None,
                content_hash=f"membership-{source_theme_id}-{order}",
                quality_status=quality_status,
            )
            for order, (stock_code, stock_name, quality_status) in enumerate(members)
        ),
        snapshot=RawSnapshot(
            page_type="THEME_DETAIL",
            source_entity_id=source_theme_id,
            source_url=f"https://infostock.co.kr/Theme/ThemeDB/{source_theme_id}",
            collected_at=KNOWN_AT,
            as_of=KNOWN_AT,
            raw_hash="0" * 64,
            source_content_hash=None,
            raw_payload_text="{}",
            raw_format="JSON",
            is_complete=True,
        ),
    )


def _universe(details: list[ThemeDetail]) -> ThemeUniverse:
    return build_theme_universe(
        details,
        version=VERSION,
        effective_from=MARKET_DATE,
        known_at=KNOWN_AT,
    )


def _pipeline(universe: ThemeUniverse) -> MarketDataPipeline:
    return MarketDataPipeline(
        market_date=MARKET_DATE,
        stream_id="stream_membership_test",
        schema_version="2026-08-14.1",
        catalog=universe.catalog(),
        references=universe.references,
        membership_version=universe.version,
        theme_names=universe.theme_names,
        stock_names=universe.stock_names,
        event_store=InMemoryEventStore(),
        snapshot_repository=InMemorySnapshotRepository(),
    )


def _update(stock_id: str, *, price: str, seconds: int) -> StockRealtimeUpdate:
    at = BASE + timedelta(seconds=seconds)
    return StockRealtimeUpdate(
        message_id=f"msg_{stock_id}_{seconds}",
        stock_id=stock_id,
        market_date=MARKET_DATE,
        source="membership-test",
        source_sequence=seconds,
        occurred_at=at,
        received_at=at,
        current_price=Decimal(price),
        cumulative_trading_value=Decimal("1000000"),
    )


def test_infostock_details_become_core_membership_snapshots() -> None:
    bundle = load_committed_fixture(
        FIXTURE_PATH, CommittedFixturePolicy(REPOSITORY_ROOT)
    )

    universe = _universe(list(bundle.details))

    assert len(universe.snapshots) == 280
    assert universe.version == VERSION
    # 기준정보(A-2)는 이 로더의 책임이 아니다.
    assert universe.references == ()
    first = universe.snapshots[0]
    assert first.theme_id == "thm_1001"
    assert first.effective_from == MARKET_DATE
    assert first.known_at == KNOWN_AT
    assert first.members == (
        ThemeMember("KRX:100001", MembershipRole.CORE),
        ThemeMember("KRX:200001", MembershipRole.CORE),
    )
    assert universe.theme_names["thm_1001"] == "합성 테마 001"
    assert universe.theme_names["thm_1280"] == "합성 테마 280"
    assert universe.stock_names["KRX:100001"] == "현재 관련주 A-001"
    assert all(
        member.role is MembershipRole.CORE
        for snapshot in universe.snapshots
        for member in snapshot.members
    )
    # history의 당시 주도주(300001)는 현재 구성종목이 아니므로 섞이지 않는다
    assert "KRX:300001" not in universe.stock_names
    assert len(universe.catalog().theme_ids) == 280


def test_membership_without_usable_stock_code_is_excluded() -> None:
    universe = _universe(
        [
            _detail(
                "584",
                "코드 결측 테마",
                (
                    ("005930", "삼성전자", "OK"),
                    (None, "코드 없는 관련주", "SOURCE_CODE_MISSING"),
                    ("00593", "잘못된 코드", "CODE_INVALID"),
                ),
            )
        ]
    )

    assert universe.snapshots[0].members == (
        ThemeMember("KRX:005930", MembershipRole.CORE),
    )
    assert universe.stock_names == {"KRX:005930": "삼성전자"}


def test_infostock_universe_without_reference_data_publishes_no_rankings() -> None:
    bundle = load_committed_fixture(
        FIXTURE_PATH, CommittedFixturePolicy(REPOSITORY_ROOT)
    )
    universe = _universe(list(bundle.details))
    pipeline = _pipeline(universe)

    for index, stock_id in enumerate(("KRX:100001", "KRX:200001")):
        assert pipeline.apply_update(
            _update(stock_id, price="10300", seconds=index + 1)
        ).changed

    pipeline.publish(now=BASE + timedelta(seconds=7), data_status=DataStatus.LIVE)
    view = pipeline.publish(
        now=BASE + timedelta(seconds=20),
        data_status=DataStatus.LIVE,
    )

    # 기준정보가 없으면 Coverage가 채워지지 않아 승격도 순위도 없다.
    assert view.rankings.payload == {"items": []}
    assert view.treemap.payload == {"items": []}
    assert view.rankings.versions.membership_version == VERSION
    assert {
        event.canonical_theme_id: event.lifecycle_status.value for event in view.events
    } == {"thm_1001": "CANDIDATE"}
    assert view.events[0].classification.display_name == "합성 테마 001"


def test_infostock_membership_ranks_once_reference_data_exists() -> None:
    universe = _universe(
        [
            _detail(
                "584",
                "2차전지",
                (
                    ("005930", "삼성전자", "OK"),
                    ("000660", "SK하이닉스", "OK"),
                    ("035420", "NAVER", "OK"),
                ),
            )
        ]
    )
    prices = {
        "KRX:005930": ("72200", "73200"),
        "KRX:000660": ("189500", "194000"),
        "KRX:035420": ("203500", "207000"),
    }
    universe = ThemeUniverse(
        version=universe.version,
        snapshots=universe.snapshots,
        theme_names=universe.theme_names,
        stock_names=universe.stock_names,
        references=tuple(
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
            for stock_id, (previous_close, _) in prices.items()
        ),
    )
    pipeline = _pipeline(universe)

    for index, (stock_id, (_, current_price)) in enumerate(prices.items()):
        assert pipeline.apply_update(
            _update(stock_id, price=current_price, seconds=index + 1)
        ).changed

    pipeline.publish(now=BASE + timedelta(seconds=7), data_status=DataStatus.LIVE)
    view = pipeline.publish(
        now=BASE + timedelta(seconds=20),
        data_status=DataStatus.LIVE,
    )

    items = view.rankings.payload["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    item = items[0]
    assert item["rank"] == 1
    assert item["lifecycleStatus"] == "ACTIVE"
    assert item["classification"]["themeId"] == "thm_584"
    assert item["classification"]["displayName"] == "2차전지"
    assert item["coverage"]["status"] == "SUFFICIENT"
    assert item["validCount"] == 3
    # 수익률 최고 종목(000660: +2.37%)이 주도주
    assert item["leader"]["symbol"] == "000660"
    assert item["leader"]["name"] == "SK하이닉스"
