"""수집 → 양방향 매칭 → grounded 구조화 → 근거 revision을 잇는 단일 pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from catalyst import (
        CatalystEvidence,
        EvidenceRevision,
        EvidenceRevisionRepository,
        ExtractionMethod,
        MatchConfig,
        NewsThemeMatch,
        SupplementalSearchGate,
        SupplementalSearchRequest,
        ThemeContext,
        decide,
        evidence_list_data,
        evidence_summary,
        match_news_to_events,
        match_theme_to_news,
    )
    from llm import (
        GroundedCatalyst,
        GroundingArticle,
        GroundingRejection,
        GroundingRequest,
        GroundingService,
        LlmCallRecord,
    )
    from news import (
        IngestionReport,
        NewsIngestor,
        NewsItem,
        NewsStore,
        RawNewsItem,
        RightsScope,
        SourcePoller,
        is_featured_stock_title,
    )
else:
    _catalyst = import_module("packages." + "catalyst")
    _llm = import_module("packages." + "llm")
    _news = import_module("packages." + "news")
    CatalystEvidence = _catalyst.CatalystEvidence
    ExtractionMethod = _catalyst.ExtractionMethod
    MatchConfig = _catalyst.MatchConfig
    decide = _catalyst.decide
    evidence_list_data = _catalyst.evidence_list_data
    evidence_summary = _catalyst.evidence_summary
    match_news_to_events = _catalyst.match_news_to_events
    match_theme_to_news = _catalyst.match_theme_to_news
    GroundingArticle = _llm.GroundingArticle
    GroundingRequest = _llm.GroundingRequest
    RightsScope = _news.RightsScope
    is_featured_stock_title = _news.is_featured_stock_title


class SupplementalSearchSource(Protocol):
    def search(self, request: SupplementalSearchRequest) -> Sequence[RawNewsItem]:
        """서버가 만든 고정 검색어로 공급원을 호출한다."""


@dataclass(frozen=True, slots=True)
class CollectionResult:
    report: IngestionReport
    degraded_source_ids: tuple[str, ...]

    @property
    def sources_degraded(self) -> bool:
        return bool(self.degraded_source_ids)


@dataclass(frozen=True, slots=True)
class EvidenceOutcome:
    event_id: str
    revision: EvidenceRevision
    matches: tuple[NewsThemeMatch, ...]
    evidence: tuple[CatalystEvidence, ...]
    llm_called: bool
    llm_record: LlmCallRecord | None
    rejection: GroundingRejection | None
    supplemental: SupplementalSearchRequest | None

    def list_projection(self, *, limit: int = 20) -> dict[str, object]:
        return evidence_list_data(self.revision, self.evidence, limit=limit)

    def summary_projection(self) -> dict[str, object]:
        return evidence_summary(self.revision, self.evidence)


class EvidencePipeline:
    """Event 모듈에 제출할 근거만 만든다. Event 상태 자체는 바꾸지 않는다."""

    def __init__(
        self,
        *,
        store: NewsStore,
        ingestor: NewsIngestor,
        grounding: GroundingService,
        revisions: EvidenceRevisionRepository,
        poller: SourcePoller | None = None,
        supplemental_gate: SupplementalSearchGate | None = None,
        supplemental_source: SupplementalSearchSource | None = None,
        match_config: MatchConfig | None = None,
    ) -> None:
        self._store = store
        self._ingestor = ingestor
        self._grounding = grounding
        self._revisions = revisions
        self._poller = poller
        self._gate = supplemental_gate
        self._supplemental_source = supplemental_source
        self._match_config = match_config or MatchConfig()

    def collect(self, *, now: datetime, window_start: datetime) -> CollectionResult:
        """테마가 없어도 계속 도는 수집 단계."""

        if self._poller is None:
            raise ValueError("poller 없이 수집을 실행할 수 없습니다")
        cursors = {
            source_id: cursor
            for source_id in self._poller.source_ids
            if (cursor := self._store.get_cursor(source_id)) is not None
        }
        result = self._poller.poll(cursors, now=now)
        for cursor in result.cursors:
            self._store.put_cursor(cursor)
        report = self._ingestor.ingest(result.items, now=now, window_start=window_start)
        return CollectionResult(report=report, degraded_source_ids=result.degraded_source_ids)

    def refresh_event(
        self,
        context: ThemeContext,
        *,
        now: datetime,
        window_start: datetime,
        sources_degraded: bool = False,
        after_close_summary: str | None = None,
    ) -> EvidenceOutcome:
        """테마 → 저장된 뉴스 방향 조회."""

        matches = self._local_matches(context, now=now, window_start=window_start)
        supplemental: SupplementalSearchRequest | None = None
        if not matches and self._gate is not None:
            supplemental = self._gate.request(
                context, now=now, has_local_evidence=False
            ).request
            if supplemental is not None and self._supplemental_source is not None:
                self._ingestor.ingest(
                    self._supplemental_source.search(supplemental),
                    now=now,
                    window_start=window_start,
                )
                matches = self._local_matches(context, now=now, window_start=window_start)
        return self._submit(
            context,
            matches,
            now=now,
            sources_degraded=sources_degraded,
            after_close_summary=after_close_summary,
            supplemental=supplemental,
        )

    def on_news_created(
        self,
        items: Sequence[NewsItem],
        contexts: Sequence[ThemeContext],
        *,
        now: datetime,
        window_start: datetime,
        sources_degraded: bool = False,
    ) -> tuple[EvidenceOutcome, ...]:
        """새 기사 → 현재 활성 Event 역방향 조회."""

        affected = {
            match.event_id
            for item in items
            for match in match_news_to_events(
                item, contexts, decision_at=now, config=self._match_config
            )
        }
        return tuple(
            self.refresh_event(
                context,
                now=now,
                window_start=window_start,
                sources_degraded=sources_degraded,
            )
            for context in contexts
            if context.event_id in affected
        )

    def _local_matches(
        self,
        context: ThemeContext,
        *,
        now: datetime,
        window_start: datetime,
    ) -> tuple[NewsThemeMatch, ...]:
        # 수집 필터는 새로 들어오는 기사만 막는다. 이미 저장된 기사 중에는 규칙이
        # 생기기 전에 들어온 것과 보완 검색이 면제로 들여온 것이 남아 있어, 근거
        # 경로에서 한 번 더 건다. 제목이 `[특징주`로 여는 기사만 근거가 된다.
        candidates = [
            item
            for item in self._store.search(
                stock_ids=context.stock_ids,
                keywords=context.theme_keywords,
                since=window_start,
                until=now,
            )
            if is_featured_stock_title(item.title)
        ]
        return match_theme_to_news(
            context, candidates, decision_at=now, config=self._match_config
        )

    def _submit(
        self,
        context: ThemeContext,
        matches: Sequence[NewsThemeMatch],
        *,
        now: datetime,
        sources_degraded: bool,
        after_close_summary: str | None,
        supplemental: SupplementalSearchRequest | None,
    ) -> EvidenceOutcome:
        news_items = [
            item for match in matches if (item := self._store.get(match.news_id)) is not None
        ]
        outcome = self._grounding.structure(
            GroundingRequest(
                theme_id=context.theme_id,
                display_name=context.display_name,
                candidate_stock_ids=context.stock_ids,
                reaction_started_at=context.activated_at,
                articles=tuple(
                    GroundingArticle(
                        news_id=item.news_id,
                        publisher=item.publisher,
                        title=item.title,
                        text=item.groundable_text,
                        original_url=item.original_url,
                        published_at=item.published_at,
                    )
                    for item in news_items
                ),
            ),
            now=now,
        )
        evidence = (
            self._evidence(
                context,
                matches,
                news_items,
                outcome.catalyst,
                outcome.record,
                now=now,
            )
            if outcome.catalyst is not None and outcome.record is not None
            else ()
        )
        decision = decide(
            context,
            evidence,
            now=now,
            previous=self._revisions.current(context.event_id),
            sources_degraded=sources_degraded,
            after_close_summary=after_close_summary,
        )
        return EvidenceOutcome(
            event_id=context.event_id,
            revision=self._revisions.record(context.event_id, decision, now=now),
            matches=tuple(matches),
            evidence=evidence,
            llm_called=outcome.called,
            llm_record=outcome.record,
            rejection=outcome.rejection,
            supplemental=supplemental,
        )

    @staticmethod
    def _evidence(
        context: ThemeContext,
        matches: Sequence[NewsThemeMatch],
        news_items: Sequence[NewsItem],
        catalyst: GroundedCatalyst,
        record: LlmCallRecord,
        *,
        now: datetime,
    ) -> tuple[CatalystEvidence, ...]:
        by_news_id = {item.news_id: item for item in news_items}
        rows: list[CatalystEvidence] = []
        for match in matches:
            item = by_news_id.get(match.news_id)
            if item is None:
                continue
            # 한 번의 LLM 호출이 기사 여러 건을 묶어 요약 하나를 만든다. 그 요약이
            # 어느 기사에서 나왔는지는 응답에 없으므로, 묶음 요약을 매칭된 기사마다
            # 복제하면 무관한 기사에 남의 요약이 붙는다(2026-08-18 광통신 테마의
            # 서울바이오시스 특허 칼럼). `catalyst_evidence`는 "한 기사에서 확인된
            # 근거 한 건"이므로, 그 기사가 실제로 뒷받침하는 엔티티가 하나도 없으면
            # 근거로 남기지 않는다.
            supported = tuple(
                entity
                for entity in catalyst.event_entities
                if entity.strip() and entity in item.groundable_text
            )
            if not supported:
                continue
            rows.append(
                CatalystEvidence(
                    news_id=match.news_id,
                    event_id=context.event_id,
                    publisher=item.publisher,
                    title=item.title,
                    summary=catalyst.catalyst_summary,
                    match_basis=match.match_basis,
                    entities=supported,
                    published_at=item.published_at,
                    received_at=item.retrieved_at,
                    original_url=item.original_url,
                    quality_flags=_quality_flags(item),
                    extraction_method=ExtractionMethod.LLM_GROUNDED,
                    model_name=record.model_name,
                    prompt_version=record.prompt_version,
                    confidence=catalyst.confidence,
                    generated_at=now,
                )
            )
        return tuple(rows)


def _quality_flags(item: NewsItem) -> tuple[str, ...]:
    flags: list[str] = []
    if item.published_at is None:
        flags.append("PUBLISHED_AT_MISSING")
    if item.rights_scope is RightsScope.METADATA_ONLY:
        flags.append("RIGHTS_LIMITED")
    return tuple(flags)
