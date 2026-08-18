"""live Market Gateway를 상시 발행 루프에 잇는 실행기.

``MarketPublishLoop``의 ``poll_updates`` 자리에서 tick마다 호출되어:

1. 연결을 보장한다 (최초 접속, 끊기면 backoff 후 재접속·구독 복원·보완).
2. 게이트웨이를 드레인해 accepted canonical 이벤트를 모은다.
3. 조건검색 후보(ENTER/EXIT)를 구독 수요로 바꿔 reconcile하고, 구독 상한
   밖 수요는 주기적으로 ka10095 스냅샷으로 보완한다.
4. 시장 관측(체결·스냅샷)을 ``StockRealtimeUpdate``로 돌려준다.

우선순위 규칙은 수집기의 검증된 동작을 따른다: 조건검색에 직접 잡힌 종목이
후보(신호 2개↑면 MULTI), 후보가 속한 테마의 나머지 구성종목이 ACTIVE_RELATED다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from packages.adapters.kiwoom import (
    CandidateAction,
    CandidateData,
    CanonicalMarketEvent,
    ConnectionPhase,
    DemandPriority,
    GatewayDataStatus,
    IngestDisposition,
    KiwoomConnectionError,
    KiwoomConnectionLost,
    MarketGateway,
    MarketObservation,
    NormalizationError,
    ReconnectNotDue,
    SubscriptionDemand,
    SubscriptionPlan,
    SupplementReason,
)
from packages.domain import DataStatus
from packages.events import LineageRef
from packages.realtime import StockRealtimeUpdate

LOG = logging.getLogger("dayjaview.live_market_runner")

# 게이트웨이 STALE(heartbeat 끊김)은 도메인에 같은 이름이 없다. 이름으로 변환하면
# ValueError로 떨어져 수신 지연이 전부 DEGRADED("일부 데이터 지연")로 뭉개졌다.
GATEWAY_DATA_STATUS = {
    GatewayDataStatus.LIVE: DataStatus.LIVE,
    GatewayDataStatus.STALE: DataStatus.DELAYED,
    GatewayDataStatus.DEGRADED: DataStatus.DEGRADED,
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class _CandidateState:
    condition_ids: set[str] = field(default_factory=set)
    last_observed_at: datetime = field(default_factory=_utc_now)


class LiveMarketRunner:
    """게이트웨이 폴링·구독 수요·보완·관측 변환을 tick 단위로 조율한다."""

    def __init__(
        self,
        *,
        gateway: MarketGateway,
        market_date: date,
        theme_members: Mapping[str, Sequence[str]],
        clock: Callable[[], datetime] = _utc_now,
        candidate_ttl: timedelta = timedelta(minutes=30),
        supplement_interval: timedelta = timedelta(seconds=30),
        max_poll_per_tick: int = 50_000,
    ) -> None:
        if candidate_ttl <= timedelta(0):
            raise ValueError("candidate_ttl은 0보다 커야 합니다")
        if supplement_interval < timedelta(0):
            raise ValueError("supplement_interval은 음수일 수 없습니다")
        if max_poll_per_tick < 1:
            raise ValueError("max_poll_per_tick은 1 이상이어야 합니다")
        self._gateway = gateway
        self._market_date = market_date
        self._theme_members: dict[str, tuple[str, ...]] = {
            theme_id: tuple(members) for theme_id, members in theme_members.items()
        }
        self._stock_to_themes: dict[str, tuple[str, ...]] = {}
        stock_themes: dict[str, set[str]] = {}
        for theme_id, members in self._theme_members.items():
            for stock_id in members:
                stock_themes.setdefault(stock_id, set()).add(theme_id)
        self._stock_to_themes = {
            stock_id: tuple(sorted(theme_ids))
            for stock_id, theme_ids in stock_themes.items()
        }
        self._clock = clock
        self._candidate_ttl = candidate_ttl
        self._supplement_interval = supplement_interval
        self._max_poll_per_tick = max_poll_per_tick
        self._candidates: dict[str, _CandidateState] = {}
        self._last_supplement_at: datetime | None = None
        self._normalization_errors = 0

    @property
    def normalization_errors(self) -> int:
        return self._normalization_errors

    def poll_updates(self) -> list[StockRealtimeUpdate]:
        """MarketPublishLoop.tick이 부르는 한 주기: 연결→드레인→구독→보완."""

        now = self._clock()
        updates = self._ensure_connection(now)
        if self._gateway.connection is None:
            return updates

        accepted: list[CanonicalMarketEvent] = []
        for _ in range(self._max_poll_per_tick):
            try:
                results = self._gateway.poll_once(now=self._clock())
            except NormalizationError as exc:
                self._normalization_errors += 1
                LOG.warning("normalize 불가 envelope을 버립니다: %s", exc)
                continue
            if not results:
                break
            accepted.extend(
                result.event
                for result in results
                if result.disposition is IngestDisposition.ACCEPTED
            )
        self._mark_heartbeat()

        for event in accepted:
            if isinstance(event.data, CandidateData):
                self._apply_candidate(event)
            elif isinstance(event.data, MarketObservation):
                updates.append(self._to_update(event))
        self._prune_candidates(self._clock())

        if self._gateway.connection is None:
            return updates
        demands = self._build_demands()
        if demands:
            try:
                plan = self._gateway.reconcile_subscriptions(
                    demands, now=self._clock()
                )
                updates.extend(self._maybe_supplement(plan))
            except KiwoomConnectionLost as exc:
                self._gateway.mark_disconnected(now=self._clock(), reason=str(exc))
        return updates

    def data_status(self) -> DataStatus:
        """MarketPublishLoop의 data_status 자리: 게이트웨이 health를 옮긴다."""

        health = self._gateway.health(
            self._gateway.subscriptions.current, now=self._clock()
        )
        return GATEWAY_DATA_STATUS[health.data_status]

    def _ensure_connection(self, now: datetime) -> list[StockRealtimeUpdate]:
        gateway = self._gateway
        if gateway.connection is not None:
            return []
        if gateway.reconnect.schedule is None:
            try:
                gateway.connect(now=now)
            except KiwoomConnectionError as exc:
                gateway.phase = ConnectionPhase.RECONNECTING
                gateway.reconnect.schedule_failure(
                    now=now, reason=str(exc), jitter_key="kiwoom-live-initial"
                )
                LOG.warning("kiwoom live 최초 접속 실패, 재시도 예약: %s", exc)
            return []
        if not gateway.reconnect.is_due(now):
            return []
        try:
            outcome = gateway.recover(
                self._build_demands(),
                supplement_stock_ids=tuple(sorted(self._demand_stock_ids())),
                now=now,
            )
        except ReconnectNotDue:
            return []
        if not outcome.connected or outcome.supplement is None:
            return []
        self._last_supplement_at = now
        return [
            self._to_update(result.event)
            for result in outcome.supplement.ingest_results
            if result.disposition is IngestDisposition.ACCEPTED
            and isinstance(result.event.data, MarketObservation)
        ]

    def _mark_heartbeat(self) -> None:
        at = getattr(self._gateway.port, "last_message_at", None)
        if isinstance(at, datetime) and self._gateway.connection is not None:
            try:
                self._gateway.mark_heartbeat(at=at)
            except KiwoomConnectionLost:
                pass

    def _apply_candidate(self, event: CanonicalMarketEvent) -> None:
        assert isinstance(event.data, CandidateData)
        if event.data.action is CandidateAction.ENTER:
            state = self._candidates.setdefault(event.stock_id, _CandidateState())
            state.condition_ids.add(event.data.condition_id)
            state.last_observed_at = event.received_at
            return
        existing = self._candidates.get(event.stock_id)
        if existing is None:
            return
        existing.condition_ids.discard(event.data.condition_id)
        if not existing.condition_ids:
            del self._candidates[event.stock_id]

    def _prune_candidates(self, now: datetime) -> None:
        expired = [
            stock_id
            for stock_id, state in self._candidates.items()
            if now - state.last_observed_at > self._candidate_ttl
        ]
        for stock_id in expired:
            del self._candidates[stock_id]

    def _build_demands(self) -> tuple[SubscriptionDemand, ...]:
        demands: list[SubscriptionDemand] = []
        active_themes: dict[str, datetime] = {}
        for stock_id, state in sorted(self._candidates.items()):
            theme_ids = self._stock_to_themes.get(stock_id, ())
            priority = (
                DemandPriority.MULTI_SIGNAL_CANDIDATE
                if len(state.condition_ids) >= 2
                else DemandPriority.SINGLE_SIGNAL_CANDIDATE
            )
            demands.append(
                SubscriptionDemand(
                    stock_id=stock_id,
                    priority=priority,
                    observed_at=state.last_observed_at,
                    signal_count=max(1, len(state.condition_ids)),
                    theme_ids=theme_ids,
                )
            )
            for theme_id in theme_ids:
                seen = active_themes.get(theme_id)
                if seen is None or state.last_observed_at > seen:
                    active_themes[theme_id] = state.last_observed_at
        for theme_id, observed_at in sorted(active_themes.items()):
            for stock_id in self._theme_members.get(theme_id, ()):
                if stock_id in self._candidates:
                    continue
                demands.append(
                    SubscriptionDemand(
                        stock_id=stock_id,
                        priority=DemandPriority.ACTIVE_RELATED,
                        observed_at=observed_at,
                        theme_ids=(theme_id,),
                    )
                )
        return tuple(demands)

    def _demand_stock_ids(self) -> set[str]:
        stock_ids = set(self._candidates)
        for stock_id in self._candidates:
            for theme_id in self._stock_to_themes.get(stock_id, ()):
                stock_ids.update(self._theme_members.get(theme_id, ()))
        return stock_ids

    def _maybe_supplement(self, plan: SubscriptionPlan) -> list[StockRealtimeUpdate]:
        if not plan.snapshot_supplement:
            return []
        now = self._clock()
        if (
            self._last_supplement_at is not None
            and now - self._last_supplement_at < self._supplement_interval
        ):
            return []
        self._last_supplement_at = now
        outcome = self._gateway.supplement(
            plan.snapshot_supplement,
            reason=SupplementReason.UNSUBSCRIBED_RELATED,
            now=now,
        )
        return [
            self._to_update(result.event)
            for result in outcome.ingest_results
            if result.disposition is IngestDisposition.ACCEPTED
            and isinstance(result.event.data, MarketObservation)
        ]

    def _to_update(self, event: CanonicalMarketEvent) -> StockRealtimeUpdate:
        assert isinstance(event.data, MarketObservation)
        return StockRealtimeUpdate(
            message_id=event.event_id,
            stock_id=event.stock_id,
            market_date=self._market_date,
            source=f"kiwoom:{event.lineage.session_id}",
            source_sequence=event.source_sequence,
            # 거래소 시계가 수신 시계보다 앞서면 검증 규칙에 맞게 잘라 낸다.
            occurred_at=min(event.source_timestamp, event.received_at),
            received_at=event.received_at,
            current_price=event.data.current_price,
            cumulative_trading_value=event.data.cumulative_trading_value,
            base_price=event.data.base_price,
            lineage=(
                LineageRef(
                    kind="market-event",
                    identifier=event.event_id,
                    version=event.schema_version,
                ),
            ),
        )
