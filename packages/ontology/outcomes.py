"""사건 이후 실제 주가 연결 (E-22 단계 6).

E-16이 만든 일봉 corpus(`research/data/daily_prices.sqlite`)를 읽어 사건일
종가 대비 T+n 실제 수익률을 계산한다. 만들지 않는 것이 셋이다 — 미래 가격,
없는 값의 0 대체, corpus 범위 밖 구간의 추정치.

기업행위가 반영된 `adjusted_close`를 먼저 쓰고, 없으면 원종가로 내려간다.
두 값을 한 사건 안에서 섞지 않는다.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Sequence

# corpus에 이 날짜 이전 구간이 없다(E-16 실측: KRX Open API는 2010-01-04부터).
CORPUS_RANGE_FROM = date(2010, 1, 1)

MissingReason = str


class SqliteOutcomeReader:
    """E-16 일봉 corpus 읽기 전용 어댑터."""

    def __init__(self, corpus_path: Path | str) -> None:
        self._path = Path(corpus_path)
        if not self._path.is_file():
            raise FileNotFoundError(f"가격 corpus를 찾지 못했습니다: {self._path}")
        self._connection = sqlite3.connect(
            f"file:{self._path.as_posix()}?mode=ro", uri=True
        )
        self._range_from: date | None = None
        self._days: list[str] | None = None
        self._day_index: dict[str, int] | None = None

    def close(self) -> None:
        self._connection.close()

    def price_range_from(self) -> date:
        if self._range_from is None:
            row = self._connection.execute(
                "SELECT min(trade_date) FROM daily_prices"
            ).fetchone()
            self._range_from = (
                date.fromisoformat(row[0]) if row and row[0] else CORPUS_RANGE_FROM
            )
        return self._range_from

    @staticmethod
    def _stock_id(stock_code: str) -> str:
        """E-16 corpus는 종목을 ``KRX:005930``으로 적는다.

        온톨로지는 같은 종목을 ``005930``으로 들고 있어서, 붙이지 않으면 모든
        조회가 빈손이 된다(2026-08-19 운영 실측: outcome 10/10 미연결).
        """

        code = stock_code.strip()
        return code if ":" in code else f"KRX:{code}"

    @staticmethod
    def _close(row: Sequence[object]) -> Decimal | None:
        adjusted, raw = row[1], row[2]
        if adjusted is not None:
            return Decimal(str(adjusted))
        return None if raw is None else Decimal(str(raw))

    def returns(
        self, stock_code: str, occurred_on: date, horizons: Sequence[int]
    ) -> tuple[date | None, Decimal | None, Mapping[int, Decimal | None], MissingReason | None]:
        """사건일 기준 T+n 실제 수익률(%)을 돌려준다. 없는 값은 None이다."""

        empty: dict[int, Decimal | None] = {horizon: None for horizon in horizons}
        if occurred_on < self.price_range_from():
            return None, None, empty, "BEFORE_CORPUS_RANGE"
        wanted = max(horizons) if horizons else 0
        stock_id = self._stock_id(stock_code)
        rows = self._connection.execute(
            "SELECT trade_date, adjusted_close, close FROM daily_prices"
            " WHERE stock_id = ? AND trade_date <= ?"
            " ORDER BY trade_date DESC LIMIT 1",
            (stock_id, occurred_on.isoformat()),
        ).fetchall()
        if not rows:
            return None, None, empty, "NO_PRICE_ON_OR_BEFORE_EVENT"
        base_date = date.fromisoformat(str(rows[0][0]))
        base_close = self._close(rows[0])
        if base_close is None or base_close == 0:
            return base_date, None, empty, "BASE_CLOSE_MISSING"
        forward = self._connection.execute(
            "SELECT trade_date, adjusted_close, close FROM daily_prices"
            " WHERE stock_id = ? AND trade_date > ?"
            " ORDER BY trade_date ASC LIMIT ?",
            (stock_id, base_date.isoformat(), wanted),
        ).fetchall()
        computed: dict[int, Decimal | None] = {}
        for horizon in horizons:
            if horizon <= 0 or horizon > len(forward):
                computed[horizon] = None
                continue
            target = self._close(forward[horizon - 1])
            computed[horizon] = (
                None
                if target is None
                else ((target / base_close) - Decimal(1)) * Decimal(100)
            )
        missing = (
            "HORIZON_NOT_REACHED"
            if any(value is None for value in computed.values())
            else None
        )
        return base_date, base_close, computed, missing

    def _trading_days(self) -> list[str]:
        if self._days is None:
            self._days = [
                str(row[0])
                for row in self._connection.execute(
                    "SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date"
                )
            ]
            self._day_index = {day: order for order, day in enumerate(self._days)}
        return self._days

    def basket_daily_return(
        self, stock_codes: Sequence[str], on: date
    ) -> Decimal | None:
        """그날 그 종목들의 동일가중 당일 등락(%). 직전 거래일 종가 대비다.

        종목마다 따로 조회하면 사건 하나에 수십 번을 묻게 되므로 한 번에 읽는다.
        가격이 없는 종목은 바스켓에서 빠지고, 하나도 없으면 None이다.
        """

        self._trading_days()
        assert self._day_index is not None
        order = self._day_index.get(on.isoformat())
        if order is None or order == 0 or not stock_codes:
            return None
        previous = self._days[order - 1] if self._days else None
        if previous is None:
            return None
        stock_ids = [self._stock_id(code) for code in stock_codes if code]
        if not stock_ids:
            return None
        placeholders = ",".join("?" * len(stock_ids))
        rows = self._connection.execute(
            "SELECT stock_id, trade_date, adjusted_close, close FROM daily_prices"
            f" WHERE stock_id IN ({placeholders}) AND trade_date IN (?, ?)",
            (*stock_ids, previous, on.isoformat()),
        ).fetchall()
        closes: dict[str, dict[str, Decimal | None]] = {}
        for stock_id, trade_date, adjusted, raw in rows:
            closes.setdefault(str(stock_id), {})[str(trade_date)] = self._close(
                (trade_date, adjusted, raw)
            )
        returns: list[Decimal] = []
        for prices in closes.values():
            before, after = prices.get(previous), prices.get(on.isoformat())
            if before is None or after is None or before == 0:
                continue
            returns.append(((after / before) - Decimal(1)) * Decimal(100))
        if not returns:
            return None
        return sum(returns, Decimal(0)) / Decimal(len(returns))

    def daily_return(self, stock_code: str, on: date) -> Decimal | None:
        """사건일 등락률(%). 직전 거래일 종가 대비 사건일 종가다."""

        rows = self._connection.execute(
            "SELECT trade_date, adjusted_close, close FROM daily_prices"
            " WHERE stock_id = ? AND trade_date <= ?"
            " ORDER BY trade_date DESC LIMIT 2",
            (self._stock_id(stock_code), on.isoformat()),
        ).fetchall()
        if len(rows) < 2:
            return None
        close, previous = self._close(rows[0]), self._close(rows[1])
        if close is None or previous is None or previous == 0:
            return None
        return ((close / previous) - Decimal(1)) * Decimal(100)


__all__ = ["CORPUS_RANGE_FROM", "SqliteOutcomeReader"]
