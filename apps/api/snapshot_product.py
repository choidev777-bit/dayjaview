"""파이프라인 스냅샷을 REST 상품 문서로 그대로 노출하는 read repository.

REST 응답과 WebSocket 스냅샷이 같은 ReadSnapshot 하나에서 나오도록 만든다.
직접 값을 저장하지 않으며, 파이프라인이 마지막으로 발행한 스냅샷만 읽는다.
유사사례·과거 Event만 예외로, 스냅샷의 오늘 소재와 수집본의 과거 사건을
매칭 엔진에 넘겨 그 자리에서 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Protocol, cast

from packages.domain import DataStatus
from packages.historical_matching import (
    MATCH_MODEL_VERSION,
    ONTOLOGY_VERSION,
    HistoricalCaseIndex,
    OutcomeSource,
    TodayContext,
    historical_event_data,
    similar_events_data,
    today_context,
)
from packages.realtime import ReadSnapshot

from .app_types import JsonObject
from .product import EmptyProductReadRepository, ProductDocument

_KRX_OPEN_UTC = time(0, 0)
_KRX_CLOSE_UTC = time(6, 30)


class SnapshotSource(Protocol):
    """MarketDataPipeline이 이미 만족하는 읽기 전용 표면."""

    @property
    def latest_rankings(self) -> ReadSnapshot | None: ...

    @property
    def latest_treemap(self) -> ReadSnapshot | None: ...

    @property
    def last_data_status(self) -> DataStatus: ...

    @property
    def last_as_of(self) -> datetime | None: ...

    def theme_id_for_event(self, event_id: str) -> str | None: ...

    def theme_detail(self, event_id: str) -> dict[str, object] | None: ...

    def evidence_document(self, event_id: str) -> dict[str, object] | None: ...


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _snapshot_document(snapshot: ReadSnapshot) -> ProductDocument:
    data: JsonObject = {
        "snapshotId": snapshot.snapshot_id,
        "streamId": snapshot.stream_id,
        "sequence": snapshot.sequence,
        **cast(JsonObject, snapshot.payload),
    }
    market_context: JsonObject = {
        "market": "KRX",
        "timeZone": "Asia/Seoul",
        "marketDate": snapshot.market_date.isoformat(),
        "asOf": _utc_iso(snapshot.as_of),
        "dataStatus": snapshot.data_status.value,
        "lastHealthyAt": (
            _utc_iso(snapshot.as_of)
            if snapshot.data_status is DataStatus.LIVE
            else None
        ),
        "qualityFlags": list(snapshot.quality_flags),
    }
    versions: JsonObject = {
        "calculationVersion": snapshot.versions.calculation_version,
        "rankingModelVersion": snapshot.versions.ranking_model_version,
        "membershipVersion": snapshot.versions.membership_version,
    }
    return ProductDocument(data, market_context, versions)


@dataclass(frozen=True, slots=True)
class HistoricalMatching:
    """과거 사건 색인과 일봉 corpus. 둘 다 있어야 유사사례를 낼 수 있다."""

    index: HistoricalCaseIndex
    outcomes: OutcomeSource | None = None


class SnapshotProductReadRepository(EmptyProductReadRepository):
    """rankings·treemap·market session·근거·유사사례를 스냅샷 소스에서 읽는다.

    `historical`이 없으면 유사사례·과거 Event는 기본값(None)으로 남는다.
    """

    def __init__(
        self,
        source: SnapshotSource,
        historical: HistoricalMatching | None = None,
    ) -> None:
        self._source = source
        self._historical = historical

    def market_session(self) -> ProductDocument | None:
        snapshot = self._source.latest_rankings
        if snapshot is None:
            return None
        market_date = snapshot.market_date
        opened_at = datetime.combine(market_date, _KRX_OPEN_UTC, tzinfo=UTC)
        closes_at = datetime.combine(market_date, _KRX_CLOSE_UTC, tzinfo=UTC)
        as_of = self._source.last_as_of or snapshot.as_of
        if as_of < opened_at:
            phase = "PREOPEN"
            next_transition = opened_at
        elif as_of < closes_at:
            phase = "REGULAR"
            next_transition = closes_at
        else:
            phase = "CLOSED"
            next_transition = opened_at + timedelta(days=1)
        base = _snapshot_document(snapshot)
        data: JsonObject = {
            "market": "KRX",
            "timeZone": "Asia/Seoul",
            "marketDate": market_date.isoformat(),
            "sessionPhase": phase,
            "sessionOpenedAt": _utc_iso(opened_at),
            "sessionClosesAt": _utc_iso(closes_at),
            "nextTransitionAt": _utc_iso(next_transition),
        }
        return ProductDocument(data, base.copy_market_context(), None)

    def rankings(self, market_date: str | None) -> ProductDocument | None:
        snapshot = self._source.latest_rankings
        if snapshot is None:
            return None
        if (
            market_date is not None
            and market_date != snapshot.market_date.isoformat()
        ):
            return None
        return _snapshot_document(snapshot)

    def treemap(self) -> ProductDocument | None:
        snapshot = self._source.latest_treemap
        if snapshot is None:
            return None
        return _snapshot_document(snapshot)

    def theme_for_event(self, event_id: str) -> str | None:
        """공개 가능한 Event일 때만 테마를 알려 준다.

        아직 공개 상태가 아닌 Event까지 여기서 테마를 돌려주면 라우트가
        themeId 불일치(409)로 답하게 되므로, 상세 문서가 있는 Event만 센다.
        """

        if self._source.theme_detail(event_id) is None:
            return None
        return self._source.theme_id_for_event(event_id)

    def theme_event(
        self,
        theme_id: str,
        event_id: str,
    ) -> ProductDocument | None:
        snapshot = self._source.latest_rankings
        if snapshot is None:
            return None
        if self._source.theme_id_for_event(event_id) != theme_id:
            return None
        detail = self._source.theme_detail(event_id)
        if detail is None:
            return None
        base = _snapshot_document(snapshot)
        return ProductDocument(
            cast(JsonObject, detail),
            base.copy_market_context(),
            base.copy_versions(),
        )

    def evidence(
        self,
        event_id: str,
        cursor: str | None,
    ) -> ProductDocument | None:
        # 파이프라인 근거 문서는 단일 페이지로 발행되고 nextCursor를 만들지
        # 않으므로, 어떤 cursor 값도 유효한 다음 페이지가 아니다.
        if cursor is not None:
            return None
        snapshot = self._source.latest_rankings
        if snapshot is None:
            return None
        document = self._source.evidence_document(event_id)
        if document is None:
            return None
        base = _snapshot_document(snapshot)
        return ProductDocument(
            cast(JsonObject, document),
            base.copy_market_context(),
            base.copy_versions(),
        )

    def similar_events(
        self,
        event_id: str,
        cursor: str | None,
        limit: int = 20,
    ) -> ProductDocument | None:
        # 유사사례는 한 페이지로 발행하고 nextCursor를 만들지 않는다.
        if cursor is not None or self._historical is None:
            return None
        snapshot = self._source.latest_rankings
        theme_id = self._source.theme_id_for_event(event_id)
        detail = self._source.theme_detail(event_id)
        if snapshot is None or theme_id is None or detail is None:
            return None
        data = similar_events_data(
            event_id=event_id,
            theme_id=theme_id,
            today=_today_context(detail),
            index=self._historical.index,
            outcomes=self._historical.outcomes,
            decision_at=self._source.last_as_of or snapshot.as_of,
            market_date=snapshot.market_date,
            limit=limit,
        )
        base = _snapshot_document(snapshot)
        return ProductDocument(
            cast(JsonObject, data),
            base.copy_market_context(),
            _matching_versions(base),
        )

    def historical_event(
        self,
        event_id: str,
        context_event_id: str | None = None,
    ) -> ProductDocument | None:
        if self._historical is None:
            return None
        case = self._historical.index.case(event_id)
        snapshot = self._source.latest_rankings
        if case is None or snapshot is None:
            return None
        context = None
        if context_event_id is not None:
            detail = self._source.theme_detail(context_event_id)
            context = None if detail is None else _today_context(detail)
        data = historical_event_data(
            case=case,
            outcomes=self._historical.outcomes,
            today=context,
        )
        base = _snapshot_document(snapshot)
        return ProductDocument(
            cast(JsonObject, data),
            base.copy_market_context(),
            _matching_versions(base),
        )


def _today_context(detail: dict[str, object]) -> TodayContext | None:
    """오늘 상세의 장중 근거 요약과 주도주로 오늘 소재를 만든다."""

    evidence = detail.get("evidenceSummary")
    summary = evidence.get("summary") if isinstance(evidence, dict) else None
    leaders = detail.get("leaders")
    codes = (
        [
            leader["symbol"]
            for leader in leaders
            if isinstance(leader, dict) and isinstance(leader.get("symbol"), str)
        ]
        if isinstance(leaders, list)
        else []
    )
    return today_context(summary if isinstance(summary, str) else None, codes)


def _matching_versions(base: ProductDocument) -> JsonObject:
    versions = base.copy_versions() or {}
    versions["ontologyVersion"] = ONTOLOGY_VERSION
    versions["retrievalArtifactVersion"] = MATCH_MODEL_VERSION
    return versions
