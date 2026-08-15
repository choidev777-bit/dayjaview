from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any

from packages.domain import (
    DataStatus,
    MembershipRole,
    ThemeMember,
    ThemeMembershipSnapshot,
)
from packages.events import InMemoryEventStore
from packages.pipeline import (
    MarketDataPipeline,
    load_collected_references,
    resolve_stock_references,
)
from packages.realtime import (
    InMemorySnapshotRepository,
    StockRealtimeUpdate,
    VersionedThemeCatalog,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "reference-data" / "fixtures"
MARKET_DATE = date(2026, 8, 14)
DECISION_AT = datetime.fromisoformat("2026-08-14T08:30:00+09:00")
BASE = datetime.fromisoformat("2026-08-14T09:00:00+09:00")
STOCK_CODES = ("A00001", "A00002", "A00003")

_PACKAGE = "packages." + "reference-data.reference_data"


def _reference_module(name: str) -> Any:
    return import_module(f"{_PACKAGE}.{name}")


def _fixture(name: str) -> Any:
    return _reference_module("parsers").load_source_fixture(
        FIXTURE_ROOT / name,
        repository_root=REPOSITORY_ROOT,
    )


def _calendar() -> Any:
    models = _reference_module("models")
    krx = _fixture("krx-stock-daily.json").metadata
    trading_days = {date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12), date(2026, 8, 13)}
    observations = [
        models.TradingDayObservation(
            market_date=date(2026, 8, day),
            is_trading_day=date(2026, 8, day) in trading_days,
            session_open=time(9, 0) if date(2026, 8, day) in trading_days else None,
            session_close=time(15, 30) if date(2026, 8, day) in trading_days else None,
            version="krx-calendar-2026.08.1",
            metadata=replace(
                krx,
                dataset=models.SourceDataset.KRX_CALENDAR_DERIVED,
                source_key=date(2026, 8, day).isoformat(),
                lineage=(f"krx-calendar:{date(2026, 8, day).isoformat()}",),
            ),
        )
        for day in range(8, 14)
    ]
    return _reference_module("calendar").TradingCalendar(tuple(observations))


def _inputs(stock_codes: tuple[str, ...] = STOCK_CODES) -> dict[str, tuple[Any, ...]]:
    """committed KRX·OpenDART fixture를 여러 종목으로 늘려 실원천 모양을 유지한다."""

    models = _reference_module("models")
    parsers = _reference_module("parsers")
    krx_row = parsers.parse_krx_stock_daily(_fixture("krx-stock-daily.json"))[0]
    prices: list[Any] = []
    shares: list[Any] = []
    holdings: list[Any] = []
    declarations: list[Any] = []
    actions: list[Any] = []
    for stock_code in stock_codes:
        price = replace(krx_row, stock_code=stock_code)
        prices.append(price)
        shares.append(price.listed_share_observation())
        # 장중에는 당일 KRX 일별매매 row가 아직 없으므로 전일 종가를 쓰려면
        # 그 거래일의 기업행위 상태가 명시돼 있어야 한다.
        actions.append(
            models.CorporateActionReference(
                stock_code=stock_code,
                effective_on=MARKET_DATE,
                status=models.CorporateActionStatus.CLEAR,
                adjustment_factor=Decimal(1),
                metadata=replace(
                    krx_row.metadata,
                    dataset=models.SourceDataset.KRX_CORPORATE_ACTION_REFERENCE,
                    source_key=f"{stock_code}:{MARKET_DATE.isoformat()}",
                    lineage=(f"krx-corporate-action:{stock_code}",),
                ),
            )
        )
        for name in (
            "opendart-stock-total.json",
            "opendart-largest-shareholder.json",
            "opendart-treasury.json",
        ):
            normalized = parsers.parse_open_dart(_fixture(name), stock_code=stock_code)
            shares.extend(normalized.issued_share_observations)
            holdings.extend(normalized.non_float_holdings)
            declarations.extend(normalized.coverage_declarations)
    return {
        "daily_prices": tuple(prices),
        "share_observations": tuple(shares),
        "holdings": tuple(holdings),
        "coverage_declarations": tuple(declarations),
        "corporate_actions": tuple(actions),
    }


def _resolve(stock_ids: tuple[str, ...], **overrides: Any) -> tuple[Any, ...]:
    arguments: dict[str, Any] = {
        "market_date": MARKET_DATE,
        "decision_at": DECISION_AT,
        "calendar": _calendar(),
        **_inputs(),
    }
    arguments.update(overrides)
    return resolve_stock_references(stock_ids, **arguments)


def _write_collected_bundle(directory: Path) -> None:
    """수집 워커가 적재한 것과 같은 원문 봉투를 만든다."""

    models = _reference_module("models")
    parsers = _reference_module("parsers")
    krx = _fixture("krx-stock-daily.json")
    payload = json.loads(krx.raw_payload_text)
    snapshots = [krx, _fixture("opendart-stock-total.json")]
    snapshots.append(_fixture("opendart-largest-shareholder.json"))
    snapshots.append(_fixture("opendart-treasury.json"))

    previous_payload = {
        "OutBlock_1": [
            {**payload["OutBlock_1"][0], "BAS_DD": "20260812", "TDD_CLSPRC": "50,000"}
        ]
    }
    previous_text = _reference_module("hashing").canonical_json(previous_payload)
    snapshots.append(
        models.SourceSnapshot(
            metadata=replace(
                krx.metadata,
                source_key="KOSPI:2026-08-12",
                as_of=datetime.fromisoformat("2026-08-12T15:30:00+09:00"),
                collected_at=datetime.fromisoformat("2026-08-12T18:00:00+09:00"),
                lineage=("krx-open-api:KOSPI:2026-08-12",),
            ),
            raw_payload_text=previous_text,
            raw_hash=_reference_module("hashing").sha256_text(previous_text),
        )
    )
    corp_code_xml = (
        '<?xml version="1.0" encoding="UTF-8"?><result><list>'
        "<corp_code>00000001</corp_code><corp_name>예시전자</corp_name>"
        "<stock_code>A00001</stock_code><modify_date>20260801</modify_date>"
        "</list></result>"
    )
    snapshots.append(
        models.SourceSnapshot(
            metadata=replace(
                _fixture("opendart-stock-total.json").metadata,
                dataset=models.SourceDataset.OPENDART_CORP_CODE,
                endpoint="https://opendart.fss.or.kr/api/corpCode.xml",
                source_key="corp-code:2026-08-14",
                source_document_ids=(),
                lineage=("opendart:corp-code:2026-08-14",),
            ),
            raw_payload_text=corp_code_xml,
            raw_hash=_reference_module("hashing").sha256_text(corp_code_xml),
        )
    )
    for index, snapshot in enumerate(snapshots):
        (directory / f"{index}.json").write_text(
            json.dumps(
                parsers.dump_collected_snapshot(snapshot),
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


def test_collected_bundle_resolves_references_through_the_corp_code_index(
    tmp_path: Path,
) -> None:
    _write_collected_bundle(tmp_path)

    references = load_collected_references(
        tmp_path,
        market_date=date(2026, 8, 13),
        decision_at=datetime.fromisoformat("2026-08-14T09:00:00+09:00"),
        stock_ids=("KRX:A00001",),
    )

    reference = references[0]
    # 08-13 KRX row의 전일대비(+1,000)로 직전 거래일(08-12) 종가 50,000이 나온다.
    assert reference.previous_adjusted_close == Decimal(50_000)
    assert reference.listed_shares == 100_000_000
    assert reference.free_float_ratio == Decimal("0.55")
    assert reference.free_float_validated is True


def test_unparseable_opendart_snapshot_only_drops_that_stock(tmp_path: Path) -> None:
    """정기보고서 표 모양은 회사마다 다르다. 한 종목 때문에 전체가 죽으면 안 된다."""

    _write_collected_bundle(tmp_path)
    models = _reference_module("models")
    hashing = _reference_module("hashing")
    parsers = _reference_module("parsers")
    # 보고서를 내지 않은 회사에 OpenDART가 주는 응답.
    no_data = '{"status":"013","message":"조회된 데이타가 없습니다."}'
    snapshot = models.SourceSnapshot(
        metadata=replace(
            _fixture("opendart-stock-total.json").metadata,
            source_key="00000002:2026:11012",
            source_document_ids=(),
            lineage=("opendart:stock-total:00000002:2026:11012",),
        ),
        raw_payload_text=no_data,
        raw_hash=hashing.sha256_text(no_data),
    )
    (tmp_path / "no-data.json").write_text(
        json.dumps(
            parsers.dump_collected_snapshot(snapshot), ensure_ascii=False, sort_keys=True
        ),
        encoding="utf-8",
    )

    references = load_collected_references(
        tmp_path,
        market_date=date(2026, 8, 13),
        decision_at=datetime.fromisoformat("2026-08-14T09:00:00+09:00"),
        stock_ids=("KRX:A00001",),
    )

    assert references[0].free_float_ratio == Decimal("0.55")


def test_official_sources_resolve_a_usable_stock_reference() -> None:
    references = _resolve(("KRX:A00001",))

    assert len(references) == 1
    reference = references[0]
    assert reference.stock_id == "KRX:A00001"
    assert reference.effective_for == MARKET_DATE
    assert reference.known_at == DECISION_AT
    # KRX 2026-08-13 종가 51,000 (직전 거래일 = calendar가 정한 08-13)
    assert reference.previous_adjusted_close == Decimal(51_000)
    # 발행 1억 - (자기주식 1,000만 + 최대주주 3,000만 + 보호예수 500만) = 5,500만
    assert reference.listed_shares == 100_000_000
    assert reference.free_float_ratio == Decimal("0.55")
    assert reference.free_float_validated is True
    assert reference.corporate_action_resolved is True
    assert (
        reference.free_float_market_cap(market_date=MARKET_DATE, as_of=BASE)
        == Decimal(51_000) * 100_000_000 * Decimal("0.55")
    )


def test_missing_opendart_source_leaves_free_float_unvalidated() -> None:
    krx_only = _inputs()
    references = _resolve(
        ("KRX:A00001",),
        share_observations=tuple(
            value
            for value in krx_only["share_observations"]
            if value.metadata.dataset.value == "KRX_STOCK_DAILY"
        ),
        holdings=(),
        coverage_declarations=(),
    )

    reference = references[0]
    # 전일 종가는 KRX만으로 나오지만 유동주식비율은 OpenDART 없이 만들지 않는다.
    assert reference.previous_adjusted_close == Decimal(51_000)
    assert reference.free_float_ratio is None
    assert reference.listed_shares is None
    assert reference.free_float_validated is False
    assert reference.free_float_market_cap(market_date=MARKET_DATE, as_of=BASE) is None


def test_resolved_references_make_the_pipeline_rank_the_theme() -> None:
    stock_ids = tuple(f"KRX:{code}" for code in STOCK_CODES)
    references = _resolve(stock_ids)
    catalog = VersionedThemeCatalog(
        (
            ThemeMembershipSnapshot(
                theme_id="thm_584",
                version="membership-infostock-test",
                effective_from=MARKET_DATE,
                known_at=DECISION_AT,
                members=tuple(
                    ThemeMember(stock_id, MembershipRole.CORE) for stock_id in stock_ids
                ),
            ),
        )
    )
    pipeline = MarketDataPipeline(
        market_date=MARKET_DATE,
        stream_id="stream_reference_test",
        schema_version="2026-08-14.1",
        catalog=catalog,
        references=references,
        membership_version="membership-infostock-test",
        theme_names={"thm_584": "2차전지"},
        stock_names=dict.fromkeys(stock_ids, "예시전자"),
        event_store=InMemoryEventStore(),
        snapshot_repository=InMemorySnapshotRepository(),
    )
    for index, stock_id in enumerate(stock_ids):
        at = BASE + timedelta(seconds=index + 1)
        assert pipeline.apply_update(
            StockRealtimeUpdate(
                message_id=f"msg_{stock_id}",
                stock_id=stock_id,
                market_date=MARKET_DATE,
                source="reference-test",
                source_sequence=index + 1,
                occurred_at=at,
                received_at=at,
                current_price=Decimal(51_510 + index * 10),
                cumulative_trading_value=Decimal("1000000"),
            )
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
    assert item["classification"]["themeId"] == "thm_584"
    assert item["lifecycleStatus"] == "ACTIVE"
    assert item["coverage"]["status"] == "SUFFICIENT"
    assert item["validCount"] == 3
    assert item["advancingCount"] == 3
    assert item["qualityFlags"] == []
