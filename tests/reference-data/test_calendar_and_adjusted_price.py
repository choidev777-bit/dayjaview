from __future__ import annotations

from dataclasses import replace
from datetime import date, time
from decimal import Decimal
from typing import Any

from conftest import aware


def _metadata(
    models: Any,
    *,
    dataset: Any,
    source_key: str,
    collected_at: str,
    revision: int = 1,
):
    timestamp = aware(collected_at)
    return models.SourceMetadata(
        provider=models.SourceProvider.KRX_OPEN_API,
        dataset=dataset,
        endpoint="https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
        source_key=source_key,
        as_of=timestamp,
        collected_at=timestamp,
        parser_version="reference-source-2026.08.1",
        revision=revision,
        lineage=(f"calendar:{source_key}:r{revision}",),
    )


def _calendar_entries(modules: dict[str, Any]):
    models = modules["models"]
    trading_dates = {date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12), date(2026, 8, 13)}
    entries = []
    for day in range(8, 14):
        market_date = date(2026, 8, day)
        is_trading = market_date in trading_dates
        entries.append(
            models.TradingDayObservation(
                market_date=market_date,
                is_trading_day=is_trading,
                session_open=time(9, 0) if is_trading else None,
                session_close=time(15, 30) if is_trading else None,
                version="krx-calendar-2026.08.1",
                metadata=_metadata(
                    models,
                    dataset=models.SourceDataset.KRX_CALENDAR_DERIVED,
                    source_key=market_date.isoformat(),
                    collected_at="2026-08-01T09:00:00+09:00",
                ),
            )
        )
    return tuple(entries)


def test_previous_trading_day_uses_explicit_calendar(modules: dict[str, Any]) -> None:
    calendar = modules["calendar"].TradingCalendar(_calendar_entries(modules))
    previous, resolution = calendar.previous_trading_day(
        date(2026, 8, 14),
        decision_at=aware("2026-08-14T08:30:00+09:00"),
    )

    assert previous == date(2026, 8, 13)
    assert resolution.state.value == "VERIFIED"
    assert resolution.version == "krx-calendar-2026.08.1"


def test_calendar_missing_day_fails_closed(modules: dict[str, Any]) -> None:
    entries = tuple(
        item for item in _calendar_entries(modules) if item.market_date != date(2026, 8, 13)
    )
    calendar = modules["calendar"].TradingCalendar(entries)
    previous, resolution = calendar.previous_trading_day(
        date(2026, 8, 14),
        decision_at=aware("2026-08-14T08:30:00+09:00"),
    )

    assert previous is None
    assert resolution.state.value == "POINT_IN_TIME_UNAVAILABLE"
    assert "CALENDAR_UNAVAILABLE" in {flag.value for flag in resolution.quality_flags}


def test_calendar_revision_is_point_in_time(modules: dict[str, Any]) -> None:
    models = modules["models"]
    original = models.TradingDayObservation(
        market_date=date(2026, 8, 17),
        is_trading_day=False,
        session_open=None,
        session_close=None,
        version="krx-calendar-r1",
        metadata=_metadata(
            models,
            dataset=models.SourceDataset.KRX_CALENDAR_DERIVED,
            source_key="2026-08-17",
            collected_at="2026-08-01T09:00:00+09:00",
        ),
    )
    correction = models.TradingDayObservation(
        market_date=date(2026, 8, 17),
        is_trading_day=True,
        session_open=time(9, 0),
        session_close=time(15, 30),
        version="krx-calendar-r2",
        metadata=_metadata(
            models,
            dataset=models.SourceDataset.KRX_CALENDAR_DERIVED,
            source_key="2026-08-17",
            collected_at="2026-08-10T09:00:00+09:00",
            revision=2,
        ),
    )
    calendar = modules["calendar"].TradingCalendar((original, correction))

    before = calendar.resolve(
        date(2026, 8, 17), decision_at=aware("2026-08-05T10:00:00+09:00")
    )
    after = calendar.resolve(
        date(2026, 8, 17), decision_at=aware("2026-08-11T10:00:00+09:00")
    )

    assert before.is_trading_day is False
    assert before.version == "krx-calendar-r1"
    assert after.is_trading_day is True
    assert after.version == "krx-calendar-r2"


def _action(modules: dict[str, Any], *, status: Any, factor: Decimal | None, known: str):
    models = modules["models"]
    return models.CorporateActionReference(
        stock_code="A00001",
        effective_on=date(2026, 8, 14),
        status=status,
        adjustment_factor=factor,
        metadata=_metadata(
            models,
            dataset=models.SourceDataset.KRX_CORPORATE_ACTION_REFERENCE,
            source_key="A00001:2026-08-14",
            collected_at=known,
        ),
    )


def test_clear_corporate_action_uses_previous_close(
    modules: dict[str, Any], load_fixture
) -> None:
    models = modules["models"]
    previous = modules["parsers"].parse_krx_stock_daily(
        load_fixture("krx-stock-daily.json")
    )[0]
    result = modules["adjusted_price"].resolve_previous_adjusted_close(
        stock_code="A00001",
        market_date=date(2026, 8, 14),
        decision_at=aware("2026-08-14T09:00:00+09:00"),
        calendar=modules["calendar"].TradingCalendar(_calendar_entries(modules)),
        daily_prices=(previous,),
        corporate_actions=(
            _action(
                modules,
                status=models.CorporateActionStatus.CLEAR,
                factor=Decimal(1),
                known="2026-08-14T08:00:00+09:00",
            ),
        ),
        version="adjusted-price-2026.08.1",
    )

    assert result.available is True
    assert result.previous_trading_day == date(2026, 8, 13)
    assert result.previous_adjusted_close == Decimal(51000)


def test_verified_factor_adjusts_previous_close(
    modules: dict[str, Any], load_fixture
) -> None:
    models = modules["models"]
    previous = modules["parsers"].parse_krx_stock_daily(
        load_fixture("krx-stock-daily.json")
    )[0]
    result = modules["adjusted_price"].resolve_previous_adjusted_close(
        stock_code="A00001",
        market_date=date(2026, 8, 14),
        decision_at=aware("2026-08-14T09:00:00+09:00"),
        calendar=modules["calendar"].TradingCalendar(_calendar_entries(modules)),
        daily_prices=(previous,),
        corporate_actions=(
            _action(
                modules,
                status=models.CorporateActionStatus.ADJUSTED,
                factor=Decimal("0.5"),
                known="2026-08-14T08:00:00+09:00",
            ),
        ),
        version="adjusted-price-2026.08.1",
    )

    assert result.previous_adjusted_close == Decimal("25500.0")


def test_missing_or_unresolved_action_never_falls_back(
    modules: dict[str, Any], load_fixture
) -> None:
    models = modules["models"]
    previous = modules["parsers"].parse_krx_stock_daily(
        load_fixture("krx-stock-daily.json")
    )[0]
    common = {
        "stock_code": "A00001",
        "market_date": date(2026, 8, 14),
        "decision_at": aware("2026-08-14T09:00:00+09:00"),
        "calendar": modules["calendar"].TradingCalendar(_calendar_entries(modules)),
        "daily_prices": (previous,),
        "version": "adjusted-price-2026.08.1",
    }
    missing = modules["adjusted_price"].resolve_previous_adjusted_close(
        **common,
        corporate_actions=(),
    )
    unresolved = modules["adjusted_price"].resolve_previous_adjusted_close(
        **common,
        corporate_actions=(
            _action(
                modules,
                status=models.CorporateActionStatus.UNRESOLVED,
                factor=None,
                known="2026-08-14T08:00:00+09:00",
            ),
        ),
    )

    assert missing.previous_adjusted_close is None
    assert unresolved.previous_adjusted_close is None
    assert missing.state.value == "CORPORATE_ACTION_UNRESOLVED"
    assert unresolved.state.value == "CORPORATE_ACTION_UNRESOLVED"


def test_late_action_confirmation_is_not_backdated(
    modules: dict[str, Any], load_fixture
) -> None:
    models = modules["models"]
    previous = modules["parsers"].parse_krx_stock_daily(
        load_fixture("krx-stock-daily.json")
    )[0]
    action = _action(
        modules,
        status=models.CorporateActionStatus.CLEAR,
        factor=Decimal(1),
        known="2026-08-14T12:00:00+09:00",
    )
    result = modules["adjusted_price"].resolve_previous_adjusted_close(
        stock_code="A00001",
        market_date=date(2026, 8, 14),
        decision_at=aware("2026-08-14T09:00:00+09:00"),
        calendar=modules["calendar"].TradingCalendar(_calendar_entries(modules)),
        daily_prices=(previous,),
        corporate_actions=(action,),
        version="adjusted-price-2026.08.1",
    )

    assert result.previous_adjusted_close is None
    assert result.state.value == "CORPORATE_ACTION_UNRESOLVED"


def test_post_close_krx_implied_base_is_available_with_its_known_time(
    modules: dict[str, Any], load_fixture
) -> None:
    previous = modules["parsers"].parse_krx_stock_daily(
        load_fixture("krx-stock-daily.json")
    )[0]
    current_metadata = replace(
        previous.metadata,
        source_key="KOSPI:2026-08-14",
        as_of=aware("2026-08-14T15:30:00+09:00"),
        collected_at=aware("2026-08-14T18:00:00+09:00"),
    )
    current = replace(
        previous,
        market_date=date(2026, 8, 14),
        close=Decimal(26000),
        change_from_previous=Decimal(500),
        metadata=current_metadata,
    )
    result = modules["adjusted_price"].resolve_previous_adjusted_close(
        stock_code="A00001",
        market_date=date(2026, 8, 14),
        decision_at=aware("2026-08-14T18:01:00+09:00"),
        calendar=modules["calendar"].TradingCalendar(_calendar_entries(modules)),
        daily_prices=(previous, current),
        corporate_actions=(),
        version="adjusted-price-2026.08.1",
    )

    assert result.previous_adjusted_close == Decimal(25500)
    assert result.available is True
