"""키움 canonical 이벤트 → hot state → 테마 계산 → Event → 실시간 스냅샷.

이 모듈은 이미 검증된 부품(HotStateStore, DirtyThemeAggregator, hysteresis,
EventWriter, SnapshotRepository)을 하나의 실행 경로로 조립만 한다. 계산·상태
전이 규칙은 각 부품이 소유하고, 여기서는 순서와 배선만 정한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from packages.calculations import THEME_CALCULATION_POLICY_V1, ThemeMetrics
from packages.catalyst import (
    CatalystEvidence,
    EvidenceRevision,
    EvidenceStatus,
    ThemeContext,
    evidence_summary,
)
from packages.domain import (
    DataStatus,
    LifecycleStatus,
    ReconciliationStatus,
    StockReference,
)
from packages.events import (
    CanonicalEvent,
    CanonicalEventIdentity,
    CreateEventCommand,
    EventInputMetadata,
    EventStore,
    EventVersions,
    EventWriter,
    TransitionLifecycleCommand,
)
from packages.realtime import (
    HYSTERESIS_POLICY_V1,
    ActivationEvaluation,
    DirtyThemeAggregator,
    HotApplyResult,
    HotStateStore,
    HysteresisPolicy,
    HysteresisState,
    ReadSnapshot,
    SnapshotPublication,
    SnapshotRepository,
    SnapshotTopic,
    SnapshotVersions,
    StockRealtimeUpdate,
    ThemeMetricUpdate,
    VersionedThemeCatalog,
    evaluate_hysteresis,
)

RANKING_MODEL_VERSION = "theme-rank-2026.08.1"
INTRADAY_CATALYST_KEY = "INTRADAY_STRENGTH"
RANKINGS_PARAMS: dict[str, object] = {"limit": 10}
TREEMAP_PARAMS: dict[str, object] = {"limit": 12}


@dataclass(frozen=True, slots=True)
class PublishedView:
    """한 publish 주기의 결과: REST와 WSS가 같은 스냅샷을 읽는다."""

    rankings: ReadSnapshot
    treemap: ReadSnapshot
    events: tuple[CanonicalEvent, ...]


class MarketDataPipeline:
    """단일 시장일 기준으로 시장 이벤트를 스냅샷까지 밀어 보내는 조립 경로."""

    def __init__(
        self,
        *,
        market_date: date,
        stream_id: str,
        schema_version: str,
        catalog: VersionedThemeCatalog,
        references: tuple[StockReference, ...],
        membership_version: str,
        theme_names: dict[str, str],
        stock_names: dict[str, str],
        event_store: EventStore,
        snapshot_repository: SnapshotRepository,
        hysteresis_policy: HysteresisPolicy = HYSTERESIS_POLICY_V1,
    ) -> None:
        if not stream_id.strip():
            raise ValueError("stream_id는 비어 있을 수 없습니다")
        self._market_date = market_date
        self._stream_id = stream_id
        self._schema_version = schema_version
        self._catalog = catalog
        self._membership_version = membership_version
        self._theme_names = dict(theme_names)
        self._stock_names = dict(stock_names)
        self._hysteresis_policy = hysteresis_policy
        self._hot = HotStateStore()
        self._references = references
        self._aggregator = DirtyThemeAggregator(
            catalog=catalog,
            references=references,
        )
        self._event_store = event_store
        self._writer = EventWriter(event_store)
        self._snapshots = snapshot_repository
        self._hysteresis: dict[str, HysteresisState] = {}
        self._event_ids: dict[str, str] = {}
        self._activated_at: dict[str, datetime] = {}
        self._evidence: dict[str, dict[str, object]] = {}
        self._latest_metrics: dict[str, ThemeMetricUpdate] = {}
        self._input_sequence = 0
        self._command_sequence = 0
        self._publication_index = 0
        self._latest_rankings: ReadSnapshot | None = None
        self._latest_treemap: ReadSnapshot | None = None
        self._last_as_of: datetime | None = None
        self._last_data_status: DataStatus = DataStatus.PREOPEN

    @property
    def market_date(self) -> date:
        return self._market_date

    @property
    def stream_id(self) -> str:
        return self._stream_id

    @property
    def latest_rankings(self) -> ReadSnapshot | None:
        return self._latest_rankings

    @property
    def latest_treemap(self) -> ReadSnapshot | None:
        return self._latest_treemap

    @property
    def last_as_of(self) -> datetime | None:
        return self._last_as_of

    @property
    def last_data_status(self) -> DataStatus:
        return self._last_data_status

    def current_events(self) -> tuple[CanonicalEvent, ...]:
        events = [
            event
            for event_id in sorted(self._event_ids.values())
            if (event := self._event_store.read_event(event_id)) is not None
        ]
        return tuple(events)

    def active_theme_contexts(self) -> tuple[ThemeContext, ...]:
        """근거 매칭이 조회할 활성 Event를 ThemeContext로 넘긴다.

        아직 활성화되지 않은 후보 테마는 근거를 찾지 않는다.
        """

        contexts: list[ThemeContext] = []
        for theme_id in sorted(self._latest_metrics):
            event_id = self._event_ids.get(theme_id)
            event = None if event_id is None else self._event_store.read_event(event_id)
            activated_at = self._activated_at.get(theme_id)
            if event is None or activated_at is None:
                continue
            if event.lifecycle_status not in (
                LifecycleStatus.ACTIVE,
                LifecycleStatus.WEAKENING,
            ):
                continue
            leader = self._leader(theme_id)
            leader_stock_ids = () if leader is None else (str(leader["stockId"]),)
            contexts.append(
                ThemeContext(
                    event_id=event.event_id,
                    theme_id=theme_id,
                    display_name=event.classification.display_name,
                    market_date=self._market_date,
                    activated_at=activated_at,
                    leader_names=() if leader is None else (str(leader["name"]),),
                    leader_stock_ids=leader_stock_ids,
                    related_stock_ids=tuple(
                        stock_id
                        for stock_id in self._member_stock_ids(theme_id)
                        if stock_id not in leader_stock_ids
                    ),
                )
            )
        return tuple(contexts)

    def record_evidence(
        self,
        revision: EvidenceRevision,
        evidence: Sequence[CatalystEvidence] = (),
    ) -> None:
        """판정된 근거 상태를 다음 발행부터 rankings에 싣는다."""

        summary = evidence_summary(revision, evidence)
        self._evidence[revision.event_id] = {
            "evidenceStatus": summary["evidenceStatus"],
            "summary": summary["summary"],
            "publishedAt": summary["latestPublishedAt"],
        }

    def apply_update(self, update: StockRealtimeUpdate) -> HotApplyResult:
        """정규화된 시장 관측 1건을 hot state와 dirty 테마 집계에 반영한다."""

        if update.market_date != self._market_date:
            raise ValueError("pipeline의 market_date와 다른 관측은 적용할 수 없습니다")
        result = self._hot.apply(update)
        if result.changed:
            self._aggregator.mark_stock(
                stock_id=update.stock_id,
                market_date=update.market_date,
                decision_at=update.received_at,
            )
        return result

    def publish(
        self,
        *,
        now: datetime,
        data_status: DataStatus,
    ) -> PublishedView:
        """dirty 테마를 재계산하고 Event·rankings·treemap 스냅샷을 발행한다.

        새 시장 관측이 없어도 호출할 수 있다. 시간 경과에 따른 lifecycle
        전이(ACTIVE 승격·약화·종료)는 이 호출 시점에만 평가된다.
        """

        for update in self._aggregator.drain(self._hot):
            self._latest_metrics[update.theme_id] = update
        for theme_id in sorted(self._latest_metrics):
            self._evaluate_theme(self._latest_metrics[theme_id], now=now)

        self._last_data_status = data_status
        as_of = self._last_as_of or now
        items = self._ranking_items()
        rankings = self._publish_snapshot(
            topic=SnapshotTopic.THEME_RANK,
            params=RANKINGS_PARAMS,
            payload={"items": items},
            now=now,
            as_of=as_of,
            data_status=data_status,
        )
        treemap_items = [self._treemap_item(item) for item in items[:12]]
        treemap = self._publish_snapshot(
            topic=SnapshotTopic.THEME_TREEMAP,
            params=TREEMAP_PARAMS,
            payload={"items": treemap_items},
            now=now,
            as_of=as_of,
            data_status=data_status,
        )
        self._latest_rankings = rankings
        self._latest_treemap = treemap
        return PublishedView(
            rankings=rankings,
            treemap=treemap,
            events=self.current_events(),
        )

    def close_market(self, *, now: datetime) -> None:
        """장 마감 시점에 남은 테마를 hysteresis market_closed 규칙으로 닫는다.

        CANDIDATE는 DISCARDED, ACTIVE·WEAKENING은 CLOSED로 전이되고 같은
        전이가 Event 저장소에도 기록된다. 이미 종결된 테마는 건너뛴다.
        """

        for theme_id in sorted(self._latest_metrics):
            state = self._hysteresis.get(theme_id)
            if state is None or state.lifecycle_status in (
                LifecycleStatus.CLOSED,
                LifecycleStatus.DISCARDED,
            ):
                continue
            update = self._latest_metrics[theme_id]
            self._input_sequence += 1
            evaluation = ActivationEvaluation(
                input_id=f"{self._stream_id}:{theme_id}:{self._input_sequence}",
                sequence=self._input_sequence,
                evaluated_at=now,
                policy_version=self._hysteresis_policy.version,
                coverage_status=update.metrics.coverage.status,
                qualifies=None,
                market_closed=True,
            )
            decision = evaluate_hysteresis(
                state,
                evaluation,
                policy=self._hysteresis_policy,
            )
            self._hysteresis[theme_id] = decision.state
            if decision.transition is None:
                continue
            event_id = self._event_ids.get(theme_id)
            if event_id is None:
                continue
            self._transition_event(
                event_id,
                target=decision.transition.to_status,
                reason=decision.transition.reason,
                update=update,
                now=now,
            )

    def _evaluate_theme(self, update: ThemeMetricUpdate, *, now: datetime) -> None:
        theme_id = update.theme_id
        if self._last_as_of is None or update.as_of > self._last_as_of:
            self._last_as_of = update.as_of
        event_id = self._event_ids.get(theme_id)
        if event_id is None:
            event_id = self._create_event(update, now=now)
            self._event_ids[theme_id] = event_id

        state = self._hysteresis.get(theme_id) or HysteresisState.candidate(
            theme_id=theme_id,
            policy=self._hysteresis_policy,
        )
        metrics = update.metrics
        qualifies = (
            None
            if metrics.weighted_return is None
            else metrics.weighted_return > Decimal(0)
        )
        self._input_sequence += 1
        evaluation = ActivationEvaluation(
            input_id=f"{self._stream_id}:{theme_id}:{self._input_sequence}",
            sequence=self._input_sequence,
            evaluated_at=now,
            policy_version=self._hysteresis_policy.version,
            coverage_status=metrics.coverage.status,
            qualifies=qualifies,
        )
        decision = evaluate_hysteresis(
            state,
            evaluation,
            policy=self._hysteresis_policy,
        )
        self._hysteresis[theme_id] = decision.state
        if decision.transition is not None:
            if decision.transition.to_status is LifecycleStatus.ACTIVE:
                self._activated_at.setdefault(theme_id, now)
            self._transition_event(
                event_id,
                target=decision.transition.to_status,
                reason=decision.transition.reason,
                update=update,
                now=now,
            )

    def _create_event(self, update: ThemeMetricUpdate, *, now: datetime) -> str:
        identity = CanonicalEventIdentity(
            market_date=self._market_date,
            canonical_theme_id=update.theme_id,
            catalyst_key=INTRADAY_CATALYST_KEY,
        )
        command = CreateEventCommand(
            metadata=self._command_metadata(update=update, now=now),
            identity=identity,
            display_name=self._theme_names.get(update.theme_id, update.theme_id),
            versions=EventVersions(
                calculation_version=update.metrics.calculation_version,
                ranking_model_version=RANKING_MODEL_VERSION,
                membership_version=update.membership_version,
                baseline_version=None,
            ),
        )
        return self._writer.write(command).event.event_id

    def _transition_event(
        self,
        event_id: str,
        *,
        target: LifecycleStatus,
        reason: str,
        update: ThemeMetricUpdate,
        now: datetime,
    ) -> None:
        event = self._event_store.read_event(event_id)
        if event is None:
            raise RuntimeError(f"등록된 Event를 찾을 수 없습니다: {event_id}")
        command = TransitionLifecycleCommand(
            metadata=self._command_metadata(update=update, now=now),
            event_id=event_id,
            target=target,
            expected_state_version=event.state_version,
            reason=reason,
            policy_version=self._hysteresis_policy.version,
        )
        self._writer.write(command)

    def _command_metadata(
        self,
        *,
        update: ThemeMetricUpdate,
        now: datetime,
    ) -> EventInputMetadata:
        self._command_sequence += 1
        occurred_at = min(update.as_of, now)
        return EventInputMetadata(
            message_id=f"{self._stream_id}:cmd:{self._command_sequence}",
            source=self._stream_id,
            source_sequence=self._command_sequence,
            occurred_at=occurred_at,
            received_at=now,
            correlation_id=self._stream_id,
        )

    def _ranking_items(self) -> list[dict[str, object]]:
        candidates: list[tuple[Decimal, str, CanonicalEvent, ThemeMetrics]] = []
        for theme_id, update in self._latest_metrics.items():
            event_id = self._event_ids.get(theme_id)
            event = (
                None if event_id is None else self._event_store.read_event(event_id)
            )
            if event is None:
                continue
            if event.lifecycle_status not in (
                LifecycleStatus.ACTIVE,
                LifecycleStatus.WEAKENING,
            ):
                continue
            metrics = update.metrics
            if not metrics.rank_eligible:
                continue
            if metrics.weighted_return is None or metrics.weighted_return <= 0:
                continue
            candidates.append(
                (metrics.weighted_return, theme_id, event, metrics)
            )
        candidates.sort(key=lambda entry: (-entry[0], entry[1]))
        items: list[dict[str, object]] = []
        for rank, (_, theme_id, event, metrics) in enumerate(candidates, start=1):
            item: dict[str, object] = {
                "eventId": event.event_id,
                "lifecycleStatus": event.lifecycle_status.value,
                "reconciliationStatus": ReconciliationStatus.PENDING.value,
                "classification": event.classification.to_public_dict(),
                "rank": rank,
                "rankChange60s": None,
                "badges": [],
            }
            item.update(metrics.to_public_ranking_fields())
            item["leader"] = self._leader(theme_id)
            evidence = self._evidence.get(event.event_id)
            item["evidence"] = (
                dict(evidence)
                if evidence is not None
                else {
                    "evidenceStatus": EvidenceStatus.SEARCHING.value,
                    "summary": None,
                    "publishedAt": None,
                }
            )
            items.append(item)
        return items

    def _member_stock_ids(self, theme_id: str) -> tuple[str, ...]:
        update = self._latest_metrics.get(theme_id)
        if update is None:
            return ()
        membership = self._catalog.select(
            theme_id=theme_id,
            market_date=update.market_date,
            decision_at=update.as_of,
        )
        if membership is None:
            return ()
        return tuple(member.stock_id for member in membership.members)

    def _leader(self, theme_id: str) -> dict[str, object] | None:
        """관측된 CORE 구성 종목 중 당일 수익률 최고 종목."""

        update = self._latest_metrics.get(theme_id)
        if update is None:
            return None
        membership = self._catalog.select(
            theme_id=theme_id,
            market_date=update.market_date,
            decision_at=update.as_of,
        )
        if membership is None:
            return None
        best: tuple[Decimal, str] | None = None
        for weight in update.metrics.capped_weights:
            state = self._hot.get(
                market_date=update.market_date,
                stock_id=weight.stock_id,
            )
            if state is None or state.current_price is None:
                continue
            reference = self._reference_close(weight.stock_id, update)
            if reference is None:
                continue
            stock_return = state.current_price / reference - Decimal(1)
            if best is None or stock_return > best[0]:
                best = (stock_return, weight.stock_id)
        if best is None:
            return None
        stock_return, stock_id = best
        return {
            "stockId": stock_id,
            "symbol": stock_id.removeprefix("KRX:"),
            "name": self._stock_names.get(stock_id, stock_id),
            "return": float(stock_return),
        }

    def _reference_close(
        self,
        stock_id: str,
        update: ThemeMetricUpdate,
    ) -> Decimal | None:
        for reference in self._references:
            if reference.stock_id != stock_id:
                continue
            close = reference.adjusted_return_reference(
                market_date=update.market_date,
                as_of=update.as_of,
            )
            if close is not None:
                return close
        return None

    def _treemap_item(self, item: dict[str, object]) -> dict[str, object]:
        classification = item["classification"]
        assert isinstance(classification, dict)
        coverage = item["coverage"]
        assert isinstance(coverage, dict)
        return {
            "eventId": item["eventId"],
            "themeId": classification["themeId"],
            "displayName": classification["displayName"],
            "lifecycleStatus": item["lifecycleStatus"],
            "weightedReturn": item["weightedReturn"],
            "advancingCount": item["advancingCount"],
            "validCount": item["validCount"],
            "coverageStatus": coverage["status"],
            "qualityFlags": item["qualityFlags"],
        }

    def _publish_snapshot(
        self,
        *,
        topic: SnapshotTopic,
        params: dict[str, object],
        payload: dict[str, object],
        now: datetime,
        as_of: datetime,
        data_status: DataStatus,
    ) -> ReadSnapshot:
        self._publication_index += 1
        publication = SnapshotPublication(
            publication_id=f"{self._stream_id}:pub:{self._publication_index}",
            stream_id=self._stream_id,
            topic=topic,
            params=params,
            market_date=self._market_date,
            generated_at=max(now, as_of),
            as_of=as_of,
            data_status=data_status,
            quality_flags=(),
            payload=payload,
            versions=SnapshotVersions(
                schema_version=self._schema_version,
                calculation_version=THEME_CALCULATION_POLICY_V1.version,
                ranking_model_version=RANKING_MODEL_VERSION,
                membership_version=self._membership_version,
            ),
        )
        return self._snapshots.publish(publication)
