"""Canonical Market Gateway orchestration, ordering fence, and recovery state."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from enum import StrEnum

from .contract import (
    CandidateData,
    CanonicalMarketEvent,
    KiwoomConnection,
    KiwoomSourceEnvelope,
    MarketObservation,
    ObservationSource,
    ReadOnlyKiwoomPort,
    require_aware,
    require_stock_id,
)
from .fixture import KiwoomConnectionError, KiwoomConnectionLost
from .normalizer import KiwoomNormalizer
from .recovery import ReconnectController, ReconnectSchedule
from .subscriptions import SubscriptionDemand, SubscriptionManager, SubscriptionPlan


class IngestDisposition(StrEnum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    OLD_SESSION = "OLD_SESSION"
    OLD_OBSERVATION = "OLD_OBSERVATION"


class ConnectionPhase(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    RECONNECTING = "RECONNECTING"
    RECOVERING = "RECOVERING"
    CONNECTED = "CONNECTED"


class GatewayDataStatus(StrEnum):
    LIVE = "LIVE"
    STALE = "STALE"
    DEGRADED = "DEGRADED"


class StockFreshness(StrEnum):
    FRESH = "FRESH"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    STALE = "STALE"
    INCOMPLETE = "INCOMPLETE"
    MISSING = "MISSING"


class CoverageStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class SupplementReason(StrEnum):
    RECONNECT = "RECONNECT"
    GAP = "GAP"
    COVERAGE = "COVERAGE"
    UNSUBSCRIBED_RELATED = "UNSUBSCRIBED_RELATED"


@dataclass(frozen=True, slots=True)
class IngestResult:
    event: CanonicalMarketEvent
    disposition: IngestDisposition
    detail: str


@dataclass(frozen=True, slots=True)
class CoverageReport:
    status: CoverageStatus
    requested_count: int
    fresh_count: int
    low_confidence_count: int
    stale_count: int
    incomplete_count: int
    missing_count: int
    fresh_ratio: Decimal | None
    fresh_stock_ids: tuple[str, ...]
    low_confidence_stock_ids: tuple[str, ...]
    stale_stock_ids: tuple[str, ...]
    incomplete_stock_ids: tuple[str, ...]
    missing_stock_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        total = (
            self.fresh_count
            + self.low_confidence_count
            + self.stale_count
            + self.incomplete_count
            + self.missing_count
        )
        if total != self.requested_count:
            raise ValueError("Coverage 상세 count 합계가 requested_count와 다릅니다")
        if self.requested_count == 0 and self.fresh_ratio is not None:
            raise ValueError("대상 0건 Coverage ratio는 null이어야 합니다")


@dataclass(frozen=True, slots=True)
class GatewayHealth:
    data_status: GatewayDataStatus
    connection_phase: ConnectionPhase
    session_id: str | None
    last_heartbeat_at: datetime | None
    coverage: CoverageReport


@dataclass(frozen=True, slots=True)
class SupplementOutcome:
    reason: SupplementReason
    requested_stock_ids: tuple[str, ...]
    ingest_results: tuple[IngestResult, ...]
    coverage: CoverageReport


@dataclass(frozen=True, slots=True)
class ReconnectOutcome:
    connected: bool
    connection: KiwoomConnection | None
    subscription_plan: SubscriptionPlan | None
    supplement: SupplementOutcome | None
    next_schedule: ReconnectSchedule | None


@dataclass(frozen=True, slots=True)
class _OrderingCursor:
    source_sequence: int
    source_item_index: int
    source_timestamp: datetime
    received_at: datetime


class EventOrderFence:
    """Reject duplicates, old sessions, and stale cross-source observations."""

    def __init__(self) -> None:
        self._current_session_id: str | None = None
        self._seen_hashes: dict[str, str] = {}
        self._ordering: dict[tuple[str, str, str, str], _OrderingCursor] = {}
        self._latest_observation: dict[str, CanonicalMarketEvent] = {}

    @property
    def current_session_id(self) -> str | None:
        return self._current_session_id

    def begin_session(self, session_id: str) -> None:
        if not session_id:
            raise ValueError("session_id는 비어 있을 수 없습니다")
        self._current_session_id = session_id

    def evaluate(self, event: CanonicalMarketEvent) -> IngestResult:
        if event.lineage.session_id != self._current_session_id:
            return IngestResult(
                event,
                IngestDisposition.OLD_SESSION,
                "현재 session 이전 입력입니다",
            )

        seen_hash = self._seen_hashes.get(event.idempotency_key)
        if seen_hash is not None:
            if seen_hash == event.lineage.raw_payload_sha256:
                return IngestResult(
                    event,
                    IngestDisposition.DUPLICATE,
                    "동일 idempotency 입력입니다",
                )
            return IngestResult(
                event,
                IngestDisposition.CONFLICT,
                "동일 idempotency key의 source payload hash가 다릅니다",
            )

        ordering_key = (
            event.lineage.session_id,
            event.lineage.source_channel.value,
            event.stock_id,
            self._ordering_lane(event),
        )
        cursor = self._ordering.get(ordering_key)
        incoming_position = (
            event.source_sequence,
            event.lineage.source_item_index,
        )
        if cursor is not None:
            cursor_position = (cursor.source_sequence, cursor.source_item_index)
            if incoming_position <= cursor_position or event.received_at < cursor.received_at:
                return IngestResult(
                    event,
                    IngestDisposition.OUT_OF_ORDER,
                    "source sequence/item index 또는 receivedAt이 역행했습니다",
                )

        if isinstance(event.data, MarketObservation):
            latest = self._latest_observation.get(event.stock_id)
            if latest is not None and self._is_old_observation(event, latest):
                return IngestResult(
                    event,
                    IngestDisposition.OLD_OBSERVATION,
                    "더 최신인 종목 관측값을 덮을 수 없습니다",
                )

        self._seen_hashes[event.idempotency_key] = event.lineage.raw_payload_sha256
        self._ordering[ordering_key] = _OrderingCursor(
            event.source_sequence,
            event.lineage.source_item_index,
            event.source_timestamp,
            event.received_at,
        )
        if isinstance(event.data, MarketObservation):
            self._latest_observation[event.stock_id] = event
        return IngestResult(event, IngestDisposition.ACCEPTED, "canonical event 수용")

    @staticmethod
    def _ordering_lane(event: CanonicalMarketEvent) -> str:
        if isinstance(event.data, CandidateData):
            return f"candidate:{event.data.condition_id}"
        return "market-observation"

    @staticmethod
    def _is_old_observation(
        incoming: CanonicalMarketEvent,
        latest: CanonicalMarketEvent,
    ) -> bool:
        if incoming.source_timestamp < latest.source_timestamp:
            return True
        if incoming.source_timestamp > latest.source_timestamp:
            return False
        if not isinstance(incoming.data, MarketObservation):
            return False
        if not isinstance(latest.data, MarketObservation):
            return False
        if (
            incoming.data.observation_source is ObservationSource.REST_KA10095
            and latest.data.observation_source is ObservationSource.REALTIME_0B
        ):
            return True
        if (
            incoming.data.observation_source is ObservationSource.REALTIME_0B
            and latest.data.observation_source is ObservationSource.REST_KA10095
        ):
            return False
        return incoming.received_at < latest.received_at


class MarketState:
    def __init__(
        self,
        *,
        snapshot_fresh_for: timedelta = timedelta(seconds=30),
        snapshot_low_confidence_for: timedelta = timedelta(seconds=60),
    ) -> None:
        if snapshot_fresh_for.total_seconds() < 0:
            raise ValueError("snapshot freshness는 음수일 수 없습니다")
        if snapshot_low_confidence_for < snapshot_fresh_for:
            raise ValueError("low-confidence 구간은 fresh 구간 이상이어야 합니다")
        self.snapshot_fresh_for = snapshot_fresh_for
        self.snapshot_low_confidence_for = snapshot_low_confidence_for
        self._observations: dict[str, CanonicalMarketEvent] = {}

    def apply(self, event: CanonicalMarketEvent) -> None:
        if isinstance(event.data, MarketObservation):
            self._observations[event.stock_id] = event

    def event_for(self, stock_id: str) -> CanonicalMarketEvent | None:
        return self._observations.get(stock_id)

    def coverage(
        self,
        stock_ids: Iterable[str],
        *,
        now: datetime,
        phase: ConnectionPhase,
        current_session_id: str | None,
        heartbeat_fresh: bool,
        subscriptions: Sequence[str],
    ) -> CoverageReport:
        require_aware(now, "now")
        requested = tuple(sorted(set(stock_ids)))
        for stock_id in requested:
            require_stock_id(stock_id)
        subscribed = set(subscriptions)
        groups: dict[StockFreshness, list[str]] = {
            freshness: [] for freshness in StockFreshness
        }
        for stock_id in requested:
            event = self._observations.get(stock_id)
            freshness = self._freshness(
                event,
                stock_id=stock_id,
                now=now,
                phase=phase,
                current_session_id=current_session_id,
                heartbeat_fresh=heartbeat_fresh,
                subscribed=stock_id in subscribed,
            )
            groups[freshness].append(stock_id)

        total = len(requested)
        fresh_count = len(groups[StockFreshness.FRESH])
        if total == 0:
            ratio = None
        else:
            with localcontext() as context:
                context.prec = 50
                ratio = Decimal(fresh_count) / Decimal(total)
        if total > 0 and fresh_count == total:
            status = CoverageStatus.COMPLETE
        elif fresh_count > 0:
            status = CoverageStatus.PARTIAL
        else:
            status = CoverageStatus.INSUFFICIENT
        return CoverageReport(
            status=status,
            requested_count=total,
            fresh_count=fresh_count,
            low_confidence_count=len(groups[StockFreshness.LOW_CONFIDENCE]),
            stale_count=len(groups[StockFreshness.STALE]),
            incomplete_count=len(groups[StockFreshness.INCOMPLETE]),
            missing_count=len(groups[StockFreshness.MISSING]),
            fresh_ratio=ratio,
            fresh_stock_ids=tuple(groups[StockFreshness.FRESH]),
            low_confidence_stock_ids=tuple(groups[StockFreshness.LOW_CONFIDENCE]),
            stale_stock_ids=tuple(groups[StockFreshness.STALE]),
            incomplete_stock_ids=tuple(groups[StockFreshness.INCOMPLETE]),
            missing_stock_ids=tuple(groups[StockFreshness.MISSING]),
        )

    def _freshness(
        self,
        event: CanonicalMarketEvent | None,
        *,
        stock_id: str,
        now: datetime,
        phase: ConnectionPhase,
        current_session_id: str | None,
        heartbeat_fresh: bool,
        subscribed: bool,
    ) -> StockFreshness:
        if event is None or not isinstance(event.data, MarketObservation):
            return StockFreshness.MISSING
        if event.data.missing_fields:
            return StockFreshness.INCOMPLETE
        if event.data.observation_source is ObservationSource.REALTIME_0B:
            if (
                phase is ConnectionPhase.CONNECTED
                and heartbeat_fresh
                and subscribed
                and event.lineage.session_id == current_session_id
            ):
                return StockFreshness.FRESH
            return StockFreshness.STALE
        age = now - event.received_at
        if age < timedelta(0):
            return StockFreshness.STALE
        if age <= self.snapshot_fresh_for:
            return StockFreshness.FRESH
        if age <= self.snapshot_low_confidence_for:
            return StockFreshness.LOW_CONFIDENCE
        return StockFreshness.STALE


class ReconnectNotDue(RuntimeError):
    pass


class MarketGateway:
    """Orchestrate only the read-only port and emit accepted canonical events."""

    def __init__(
        self,
        port: ReadOnlyKiwoomPort,
        *,
        normalizer: KiwoomNormalizer | None = None,
        subscriptions: SubscriptionManager | None = None,
        reconnect: ReconnectController | None = None,
        heartbeat_timeout: timedelta = timedelta(seconds=10),
    ) -> None:
        capabilities = port.capabilities
        if not capabilities.read_only or capabilities.orders or capabilities.accounts:
            raise ValueError("Market Gateway port는 주문·계좌 없는 read-only여야 합니다")
        if heartbeat_timeout.total_seconds() <= 0:
            raise ValueError("heartbeat_timeout은 0보다 커야 합니다")
        self.port = port
        self.normalizer = normalizer or KiwoomNormalizer()
        self.subscriptions = subscriptions or SubscriptionManager()
        self.reconnect = reconnect or ReconnectController()
        self.heartbeat_timeout = heartbeat_timeout
        self.order_fence = EventOrderFence()
        self.state = MarketState()
        self.phase = ConnectionPhase.DISCONNECTED
        self.connection: KiwoomConnection | None = None
        self.last_heartbeat_at: datetime | None = None
        self._accepted_events: list[CanonicalMarketEvent] = []

    @property
    def accepted_events(self) -> tuple[CanonicalMarketEvent, ...]:
        return tuple(self._accepted_events)

    def connect(self, *, now: datetime) -> KiwoomConnection:
        require_aware(now, "now")
        connection = self.port.connect(now=now)
        self.connection = connection
        self.order_fence.begin_session(connection.session_id)
        self.phase = ConnectionPhase.CONNECTED
        self.last_heartbeat_at = connection.connected_at
        self.reconnect.mark_connected()
        return connection

    def ingest(self, envelope: KiwoomSourceEnvelope) -> tuple[IngestResult, ...]:
        events = self.normalizer.normalize(envelope)
        results: list[IngestResult] = []
        for event in events:
            result = self.order_fence.evaluate(event)
            results.append(result)
            if result.disposition is IngestDisposition.ACCEPTED:
                self._accepted_events.append(event)
                self.state.apply(event)
        if envelope.session_id == self.order_fence.current_session_id and (
            self.last_heartbeat_at is None
            or envelope.received_at > self.last_heartbeat_at
        ):
            self.last_heartbeat_at = envelope.received_at
        return tuple(results)

    def poll_once(self, *, now: datetime) -> tuple[IngestResult, ...]:
        require_aware(now, "now")
        if self.connection is None:
            raise KiwoomConnectionLost("현재 연결된 Kiwoom session이 없습니다")
        try:
            envelope = self.port.read(self.connection.session_id)
        except KiwoomConnectionLost as exc:
            self.mark_disconnected(now=now, reason=str(exc))
            return ()
        if envelope is None:
            return ()
        return self.ingest(envelope)

    def reconcile_subscriptions(
        self,
        demands: Iterable[SubscriptionDemand],
        *,
        now: datetime,
        force: bool = False,
    ) -> SubscriptionPlan:
        if self.connection is None:
            raise KiwoomConnectionLost("구독을 적용할 Kiwoom session이 없습니다")
        plan = self.subscriptions.reconcile(demands, now=now, force=force)
        if force or plan.added or plan.removed:
            self.port.replace_trade_subscriptions(
                self.connection.session_id,
                plan.subscriptions,
            )
        return plan

    def mark_heartbeat(self, *, at: datetime) -> None:
        require_aware(at, "at")
        if self.connection is None:
            raise KiwoomConnectionLost("heartbeat를 수용할 session이 없습니다")
        if self.last_heartbeat_at is None or at > self.last_heartbeat_at:
            self.last_heartbeat_at = at

    def mark_disconnected(
        self,
        *,
        now: datetime,
        reason: str,
    ) -> ReconnectSchedule:
        require_aware(now, "now")
        session_id = self.connection.session_id if self.connection else "initial"
        if self.connection is not None:
            self.port.close_session(self.connection.session_id)
        self.connection = None
        self.phase = ConnectionPhase.RECONNECTING
        return self.reconnect.schedule_failure(
            now=now,
            reason=reason,
            jitter_key=session_id,
        )

    def recover(
        self,
        demands: Iterable[SubscriptionDemand],
        *,
        supplement_stock_ids: Iterable[str],
        now: datetime,
    ) -> ReconnectOutcome:
        require_aware(now, "now")
        if not self.reconnect.is_due(now):
            raise ReconnectNotDue("아직 reconnect backoff 시간이 지나지 않았습니다")
        try:
            connection = self.port.connect(now=now)
        except KiwoomConnectionError as exc:
            schedule = self.reconnect.schedule_failure(
                now=now,
                reason=str(exc),
                jitter_key="kiwoom-reconnect",
            )
            return ReconnectOutcome(False, None, None, None, schedule)

        self.connection = connection
        self.order_fence.begin_session(connection.session_id)
        self.phase = ConnectionPhase.RECOVERING
        self.last_heartbeat_at = connection.connected_at
        self.reconnect.mark_connected()
        plan = self.reconcile_subscriptions(demands, now=now, force=True)
        requested = tuple(sorted(set(plan.subscriptions) | set(supplement_stock_ids)))
        supplement = self.supplement(
            requested,
            reason=SupplementReason.RECONNECT,
            now=now,
        )
        self.phase = ConnectionPhase.CONNECTED
        return ReconnectOutcome(True, connection, plan, supplement, None)

    def supplement(
        self,
        stock_ids: Iterable[str],
        *,
        reason: SupplementReason,
        now: datetime,
    ) -> SupplementOutcome:
        require_aware(now, "now")
        if self.connection is None:
            raise KiwoomConnectionLost("snapshot을 요청할 Kiwoom session이 없습니다")
        requested = tuple(sorted(set(stock_ids)))
        for stock_id in requested:
            require_stock_id(stock_id)
        responses = self.port.fetch_watchlist_snapshots(
            self.connection.session_id,
            requested,
            requested_at=now,
        )
        results = tuple(result for response in responses for result in self.ingest(response))
        coverage = self.coverage(requested, now=now)
        return SupplementOutcome(reason, requested, results, coverage)

    def coverage(
        self,
        stock_ids: Iterable[str],
        *,
        now: datetime,
    ) -> CoverageReport:
        heartbeat_fresh = self._heartbeat_fresh(now)
        return self.state.coverage(
            stock_ids,
            now=now,
            phase=self.phase,
            current_session_id=self.order_fence.current_session_id,
            heartbeat_fresh=heartbeat_fresh,
            subscriptions=self.subscriptions.current,
        )

    def health(
        self,
        stock_ids: Iterable[str],
        *,
        now: datetime,
    ) -> GatewayHealth:
        coverage = self.coverage(stock_ids, now=now)
        heartbeat_fresh = self._heartbeat_fresh(now)
        if self.phase is not ConnectionPhase.CONNECTED:
            data_status = GatewayDataStatus.DEGRADED
        elif not heartbeat_fresh:
            data_status = GatewayDataStatus.STALE
        elif coverage.status is not CoverageStatus.COMPLETE:
            data_status = GatewayDataStatus.DEGRADED
        else:
            data_status = GatewayDataStatus.LIVE
        return GatewayHealth(
            data_status=data_status,
            connection_phase=self.phase,
            session_id=self.connection.session_id if self.connection else None,
            last_heartbeat_at=self.last_heartbeat_at,
            coverage=coverage,
        )

    def _heartbeat_fresh(self, now: datetime) -> bool:
        require_aware(now, "now")
        if self.last_heartbeat_at is None:
            return False
        age = now - self.last_heartbeat_at
        return timedelta(0) <= age <= self.heartbeat_timeout
