"""과거 전 종목 일봉 corpus의 도메인 모델 (E-16)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

STOCK_CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
KRX_MARKETS: tuple[str, ...] = ("KOSPI", "KOSDAQ", "KONEX")


class HistoricalDataError(ValueError):
    """원문·수집 상태가 계약과 다를 때. 값을 지어내는 대신 멈춘다."""

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"{path}: {detail}")
        self.path = path
        self.detail = detail


@dataclass(frozen=True, slots=True)
class KrxDailyRow:
    """KRX 일별매매 한 종목 row. 가격은 원 단위 정수다.

    무거래(기세) 날은 KRX가 시·고·저가를 0으로 보내므로 None으로 남긴다.
    종가는 그날의 기준가·기세를 포함해 항상 있다.
    """

    stock_code: str
    market: str
    trade_date: date
    open: int | None
    high: int | None
    low: int | None
    close: int
    change_from_previous: int | None
    volume: int
    trading_value: int

    def __post_init__(self) -> None:
        if not STOCK_CODE_RE.fullmatch(self.stock_code):
            raise ValueError("6자리 종목코드가 필요합니다.")
        if self.market not in KRX_MARKETS:
            raise ValueError("market은 KOSPI, KOSDAQ, KONEX 중 하나여야 합니다.")
        if self.close <= 0:
            raise ValueError("close는 0보다 커야 합니다.")
        for name in ("open", "high", "low"):
            value: int | None = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name}은 없거나 0보다 커야 합니다.")
        if self.volume < 0 or self.trading_value < 0:
            raise ValueError("volume·trading_value는 0 이상이어야 합니다.")

    @property
    def stock_id(self) -> str:
        return f"KRX:{self.stock_code}"


@dataclass(frozen=True, slots=True)
class ParsedKrxDaily:
    """봉투 하나(1시장·1거래일)의 해석 결과."""

    market: str
    trade_date: date
    rows: tuple[KrxDailyRow, ...]
    skipped_stock_codes: tuple[str, ...]
