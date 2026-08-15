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
    DEFAULT_PAGE_LIMIT,
    CatalystEvidence,
    EvidenceRevision,
    EvidenceStatus,
    ThemeContext,
    evidence_list_data,
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
        self._base_price_filled: set[str] = set()
        self._aggregator = DirtyThemeAggregator(
            catalog=catalog,
            references=references,
        )
        self._event_store = event_store
        self._writer = EventWriter(event_store)
        self._snapshots = snapshot_repository
        self._hysteresis: dict[str, HysteresisState] = {}
        self._event_ids: dict[str, str] = {}
        self._theme_by_event: dict[str, str] = {}
        self._activated_at: dict[str, datetime] = {}
        self._evidence: dict[str, dict[str, object]] = {}
        self._evidence_documents: dict[str, dict[str, object]] = {}
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
        """판정된 근거 상태를 다음 발행부터 rankings·근거 목록에 싣는다."""

        self._evidence[revision.event_id] = evidence_summary(revision, evidence)
        self._evidence_documents[revision.event_id] = evidence_list_data(
            revision, evidence
        )

    def evidence_document(self, event_id: str) -> dict[str, object] | None:
        """공개 Event의 근거 목록 문서를 만든다. 판정 전이면 SEARCHING 빈 목록.

        상세(theme_detail)가 공개하지 않는 Event는 근거도 공개하지 않는다.
        상승 이유는 저장된 기사 근거가 확인된 범위에서만 제시하므로, 여기서
        돌려주는 items가 그 확인된 전부다.
        """

        if self.theme_detail(event_id) is None:
            return None
        document = self._evidence_documents.get(event_id)
        if document is not None:
            return dict(document)
        return {
            "eventId": event_id,
            "evidenceStatus": EvidenceStatus.SEARCHING.value,
            "items": [],
            "page": {
                "nextCursor": None,
                "hasMore": False,
                "limit": DEFAULT_PAGE_LIMIT,
            },
        }

    def theme_id_for_event(self, event_id: str) -> str | None:
        """이 파이프라인이 만든 Event가 속한 테마."""

        return self._theme_by_event.get(event_id)

    def theme_detail(self, event_id: str) -> dict[str, object] | None:
        """활성화된 Event 하나의 테마 상세 문서를 만든다.

        아직 공개 상태가 아닌 CANDIDATE·DISCARDED Event는 None을 돌려준다.
        거래대금 배수·관심 공백은 기준선 이력이 쌓이기 전이라(A-6) null이다.
        """

        theme_id = self._theme_by_event.get(event_id)
        if theme_id is None:
            return None
        event = self._event_store.read_event(event_id)
        update = self._latest_metrics.get(theme_id)
        if event is None or update is None:
            return None
        if event.lifecycle_status not in (
            LifecycleStatus.ACTIVE,
            LifecycleStatus.WEAKENING,
            LifecycleStatus.CLOSED,
        ):
            return None
        metrics = update.metrics
        evidence = self._evidence.get(event_id)
        return {
            "eventId": event.event_id,
            "marketDate": self._market_date.isoformat(),
            "lifecycleStatus": event.lifecycle_status.value,
            "reconciliationStatus": event.reconciliation_status.value,
            "classification": event.classification.to_public_dict(),
            "currentReaction": metrics.to_public_current_reaction(
                turnover_multiple=None,
                attention_gap_trading_days=None,
            ),
            "coverage": metrics.coverage.to_public_dict(),
            "evidenceSummary": (
                dict(evidence)
                if evidence is not None
                else {
                    "evidenceStatus": EvidenceStatus.SEARCHING.value,
                    "summary": None,
                    "sourceCount": 0,
                    "latestPublishedAt": None,
                }
            ),
            "leaders": [
                {**leader, "role": "LEADER"}
                for leader in self._leaders(theme_id, limit=3)
            ],
            # 유사사례는 온톨로지 재검증(E-19) 통과 전까지 잠겨 있다.
            "historicalAccess": {
                "status": "GATED",
                "reason": "ONTOLOGY_VALIDATION_REQUIRED",
            },
            "canonicalPath": f"/v1/themes/{theme_id}/events/{event_id}",
            "qualityFlags": list(metrics.quality_flags),
        }

    def apply_update(self, update: StockRealtimeUpdate) -> HotApplyResult:
        """정규화된 시장 관측 1건을 hot state와 dirty 테마 집계에 반영한다."""

        if update.market_date != self._market_date:
            raise ValueError("pipeline의 market_date와 다른 관측은 적용할 수 없습니다")
        self._supplement_base_price(update)
        result = self._hot.apply(update)
        if result.changed:
            self._aggregator.mark_stock(
                stock_id=update.stock_id,
                market_date=update.market_date,
                decision_at=update.received_at,
            )
        return result

    def _supplement_base_price(self, update: StockRealtimeUpdate) -> None:
        """장중 기준가로 전일 종가가 빈 기준정보를 채운다.

        KRX 일별매매는 장 마감 후에야 나오므로 장중에는 전일 종가를 만들 수
        없고, 기업행위 원천도 없어 전 종목이 계산에서 빠진다. 시세 공급원이
        주는 기준가는 권리락·액면분할이 반영된 값이라 이 자리를 메운다.

        이미 전일 종가가 있으면 손대지 않는다. 기존 값을 덮지 않고 known_at이
        더 늦은 기준정보를 덧붙여, point-in-time 선택이 그대로 판단하게 한다.
        """

        if update.base_price is None or update.stock_id in self._base_price_filled:
            return
        for reference in self._references:
            if (
                reference.stock_id == update.stock_id
                and reference.effective_for == self._market_date
                and reference.previous_adjusted_close is not None
            ):
                self._base_price_filled.add(update.stock_id)
                return
        current = next(
            (
                reference
                for reference in self._references
                if reference.stock_id == update.stock_id
                and reference.effective_for == self._market_date
            ),
            None,
        )
        supplemented = StockReference(
            stock_id=update.stock_id,
            effective_for=self._market_date,
            known_at=update.received_at,
            previous_adjusted_close=update.base_price,
            listed_shares=current.listed_shares if current else None,
            free_float_ratio=current.free_float_ratio if current else None,
            free_float_validated=current.free_float_validated if current else False,
            version=f"{current.version if current else 'reference'}+base-price",
        )
        self._references = (*self._references, supplemented)
        self._aggregator.add_reference(supplemented)
        self._base_price_filled.add(update.stock_id)

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
            self._theme_by_event[event_id] = theme_id

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
                {
                    "evidenceStatus": evidence["evidenceStatus"],
                    "summary": evidence["summary"],
                    "publishedAt": evidence["latestPublishedAt"],
                }
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

    def _leaders(self, theme_id: str, *, limit: int) -> list[dict[str, object]]:
        """관측된 CORE 구성 종목을 당일 수익률 내림차순으로 상위 limit개."""

        update = self._latest_metrics.get(theme_id)
        if update is None:
            return []
        returns: list[tuple[Decimal, str]] = []
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
            returns.append(
                (state.current_price / reference - Decimal(1), weight.stock_id)
            )
        returns.sort(key=lambda entry: (-entry[0], entry[1]))
        return [
            {
                "stockId": stock_id,
                "symbol": stock_id.removeprefix("KRX:"),
                "name": self._stock_names.get(stock_id, stock_id),
                "return": float(stock_return),
            }
            for stock_return, stock_id in returns[:limit]
        ]

    def _leader(self, theme_id: str) -> dict[str, object] | None:
        """rankings에 싣는 단일 주도주 = 상세 화면 leaders의 첫 종목."""

        leaders = self._leaders(theme_id, limit=1)
        return leaders[0] if leaders else None

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
