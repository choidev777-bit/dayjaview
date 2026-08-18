"""파이프라인 상시 발행 루프: 새 관측 ingest → publish → 구독 경로 통지."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, date, datetime, timedelta

from packages.domain import DataStatus
from packages.realtime import StockRealtimeUpdate

from .market import MarketDataPipeline, PublishedView
from .trading_day import is_trading_day, market_date_for

LOG = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MarketPublishLoop:
    """수 초 간격으로 파이프라인을 publish하고 결과를 구독 경로에 넘긴다.

    ``tick``이 한 주기(새 관측 ingest → 장 마감 평가 → publish →
    on_published)이고, ``run``은 취소될 때까지 tick과 interval 대기를
    반복한다. 장 마감 평가는 clock이 ``market_close_at``을 지난 첫 tick에서
    한 번만 수행한다.
    """

    def __init__(
        self,
        *,
        pipeline: MarketDataPipeline,
        on_published: Callable[[PublishedView], None],
        data_status: Callable[[], DataStatus],
        interval: timedelta,
        poll_updates: Callable[[], Iterable[StockRealtimeUpdate]] | None = None,
        before_publish: Callable[[], None] | None = None,
        market_close_at: datetime | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("publish interval은 0보다 커야 합니다")
        self._pipeline = pipeline
        self._on_published = on_published
        self._data_status = data_status
        self._interval = interval
        self._poll_updates = poll_updates
        self._before_publish = before_publish
        self._market_close_at = market_close_at
        self._clock = clock
        self._market_close_applied = False

    @property
    def market_close_applied(self) -> bool:
        return self._market_close_applied

    def tick(self) -> PublishedView:
        now = self._clock()
        if self._poll_updates is not None:
            for update in self._poll_updates():
                self._pipeline.apply_update(update)
        if (
            not self._market_close_applied
            and self._market_close_at is not None
            and now >= self._market_close_at
        ):
            self._pipeline.close_market(now=now)
            self._market_close_applied = True
        if self._before_publish is not None:
            self._before_publish()
        view = self._pipeline.publish(now=now, data_status=self._data_status())
        self._on_published(view)
        return view

    def close(self) -> None:
        """장 마감을 아직 적용하지 않았으면 마감 시각으로 지금 적용한다."""

        if self._market_close_applied or self._market_close_at is None:
            return
        self._pipeline.close_market(now=self._market_close_at)
        self._market_close_applied = True

    async def run(
        self,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        while True:
            self.tick()
            await sleep(self._interval.total_seconds())


class TradingDayLoop:
    """거래일이 바뀌면 이전 장을 닫고 그날 파이프라인으로 갈아끼운다.

    ``MarketPublishLoop``은 하루 안에서만 돈다. 이 루프가 그 위에 날짜 축을
    얹어, 프로세스를 다시 띄우지 않아도 다음 거래일로 넘어가게 한다. 날짜를
    갈지 않으면 다음 날 어제 전일종가로 계산해 모든 수익률이 조용히 틀린다.

    ``build_session``은 그 거래일의 파이프라인과 발행 콜백을 만든다. 그날
    기준정보를 확보하지 못하면 ``None``을 돌려 그날 계산을 시작하지 않는다
    (product_decisions.md PD-001 10항 — 조용한 대체 금지).
    """

    def __init__(
        self,
        *,
        build_session: Callable[[date], MarketPublishLoop | None],
        interval: timedelta,
        clock: Callable[[], datetime] = _utc_now,
        known_trading_days: Callable[[date], bool | None] | None = None,
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("publish interval은 0보다 커야 합니다")
        self._build_session = build_session
        self._interval = interval
        self._clock = clock
        self._known_trading_days = known_trading_days
        self._market_date: date | None = None
        self._session: MarketPublishLoop | None = None

    @property
    def market_date(self) -> date | None:
        """지금 세워져 있는 거래일. 비거래일이면 None."""

        return self._market_date

    @property
    def session(self) -> MarketPublishLoop | None:
        return self._session

    def tick(self) -> PublishedView | None:
        today = market_date_for(self._clock())
        if today != self._market_date:
            self._roll_over(today)
        if self._session is None:
            return None
        return self._session.tick()

    def _roll_over(self, today: date) -> None:
        # 넘어가기 전에 이전 거래일 장 마감을 반드시 적용한다. 마감 시각과
        # 날짜 전환 사이에 tick이 한 번도 없었으면 아직 안 닫혀 있다.
        if self._session is not None:
            self._session.close()
        self._market_date = today
        if not is_trading_day(today, known_trading_days=self._known_trading_days):
            self._session = None
            return
        self._session = self._build_session(today)

    async def run(
        self,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        while True:
            try:
                self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # 이 루프는 lifespan에서 create_task로 떠 있고 아무도 await하지
                # 않는다. tick 하나가 올린 예외를 그대로 두면 task가 조용히 끝나
                # 장중 내내 순위가 멎는다(2026-08-18 두 차례, 로그에도 안 남았다).
                # 한 주기 실패는 기록만 하고 다음 주기에 다시 시도한다.
                LOG.exception(
                    "%s 장중 tick이 실패했습니다. 다음 주기에 다시 시도합니다",
                    self._market_date,
                )
            await sleep(self._interval.total_seconds())
