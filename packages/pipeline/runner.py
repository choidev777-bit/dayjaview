"""파이프라인 상시 발행 루프: 새 관측 ingest → publish → 구독 경로 통지."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta

from packages.domain import DataStatus
from packages.realtime import StockRealtimeUpdate

from .market import MarketDataPipeline, PublishedView


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
        view = self._pipeline.publish(now=now, data_status=self._data_status())
        self._on_published(view)
        return view

    async def run(
        self,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        while True:
            self.tick()
            await sleep(self._interval.total_seconds())
