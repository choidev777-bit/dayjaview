"""KST 거래일 판정.

KRX Open API에는 거래일 달력 endpoint가 없다. 일별매매정보는 그날 장이 끝난
뒤에 나오므로 **오늘이 거래일인지는 장 시작 전에 원천으로 확인할 수 없다.**
그래서 확실히 아는 것만 확정하고 나머지는 거래일로 가정한다.

- 주말은 비거래일로 확정한다.
- 과거 날짜는 수집된 KRX 응답으로 만든 달력이 있으면 그 판정을 따른다.
- 그 밖의 평일은 거래일로 가정한다. 공휴일 원천이 없어 임시휴장·대체공휴일을
  미리 알 수 없다. 실제로 휴장이면 장중 이벤트가 오지 않아 게이트웨이 health가
  그대로 드러낸다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9))
SATURDAY = 5


def market_date_for(now: datetime) -> date:
    """KST 기준 그 시각이 속한 날짜."""

    return now.astimezone(KST).date()


def is_weekend(market_date: date) -> bool:
    return market_date.weekday() >= SATURDAY


def is_trading_day(
    market_date: date,
    *,
    known_trading_days: Callable[[date], bool | None] | None = None,
) -> bool:
    """주말이면 False, 달력이 아는 날이면 그 값, 나머지 평일은 True."""

    if is_weekend(market_date):
        return False
    if known_trading_days is not None:
        known = known_trading_days(market_date)
        if known is not None:
            return known
    return True


def next_trading_day(
    market_date: date,
    *,
    known_trading_days: Callable[[date], bool | None] | None = None,
    max_lookahead_days: int = 14,
) -> date:
    """market_date 다음의 첫 거래일."""

    if max_lookahead_days <= 0:
        raise ValueError("max_lookahead_days는 1 이상이어야 합니다")
    for offset in range(1, max_lookahead_days + 1):
        candidate = market_date + timedelta(days=offset)
        if is_trading_day(candidate, known_trading_days=known_trading_days):
            return candidate
    raise ValueError(f"{max_lookahead_days}일 안에 거래일이 없습니다: {market_date}")


def session_open_at(market_date: date, *, open_time: time) -> datetime:
    """그 거래일의 장 시작 시각을 UTC aware datetime으로."""

    return datetime.combine(market_date, open_time, tzinfo=KST).astimezone(UTC)


def session_close_at(market_date: date, *, close_time: time) -> datetime:
    """그 거래일의 장 마감 시각을 UTC aware datetime으로."""

    return datetime.combine(market_date, close_time, tzinfo=KST).astimezone(UTC)
