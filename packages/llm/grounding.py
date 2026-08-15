"""선택된 기사 안에서만 구조화한다. 근거가 없으면 호출하지 않는다."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from .models import (
    GROUNDING_PROMPT_VERSION,
    MIN_CONFIDENCE,
    VERBATIM_WINDOW,
    GroundedCatalyst,
    GroundingOutcome,
    GroundingRejection,
    GroundingRequest,
    LlmCallRecord,
)


class LlmClient(Protocol):
    model_name: str

    def structure(
        self,
        *,
        prompt_version: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        """전달된 기사만으로 구조화한 결과를 돌려준다. 웹 검색은 하지 않는다."""


class GroundingService:
    def __init__(
        self,
        client: LlmClient,
        *,
        prompt_version: str = GROUNDING_PROMPT_VERSION,
        min_confidence: float = MIN_CONFIDENCE,
    ) -> None:
        self._client = client
        self._prompt_version = prompt_version
        self._min_confidence = min_confidence

    def structure(self, request: GroundingRequest, *, now: datetime) -> GroundingOutcome:
        if not request.articles:
            return GroundingOutcome(
                called=False,
                catalyst=None,
                record=None,
                rejection=GroundingRejection.NO_EVIDENCE,
            )
        try:
            raw = self._client.structure(
                prompt_version=self._prompt_version,
                payload=request.to_payload(),
            )
        except Exception:
            return GroundingOutcome(
                called=True,
                catalyst=None,
                record=self._record(request, None, GroundingRejection.PROVIDER_ERROR, now),
                rejection=GroundingRejection.PROVIDER_ERROR,
            )

        serialized = _serialize(raw)
        catalyst, rejection = _validate(raw, request, min_confidence=self._min_confidence)
        record = self._record(request, serialized, rejection, now)
        if catalyst is None:
            return GroundingOutcome(True, None, record, rejection)
        return GroundingOutcome(True, catalyst, record, None)

    def _record(
        self,
        request: GroundingRequest,
        raw_output: str | None,
        rejection: GroundingRejection | None,
        now: datetime,
    ) -> LlmCallRecord:
        return LlmCallRecord(
            model_name=self._client.model_name,
            prompt_version=self._prompt_version,
            news_ids=request.news_ids,
            request_fingerprint=request.fingerprint(),
            raw_output=raw_output,
            accepted=rejection is None,
            rejection=rejection,
            called_at=now,
        )


def _serialize(raw: Mapping[str, object]) -> str:
    try:
        return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return repr(raw)


def _string_tuple(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or any(not isinstance(entry, str) for entry in value):
        return None
    return tuple(str(entry) for entry in value)


def _validate(
    raw: Mapping[str, object],
    request: GroundingRequest,
    *,
    min_confidence: float,
) -> tuple[GroundedCatalyst | None, GroundingRejection | None]:
    stock_ids = _string_tuple(raw.get("stockIds"))
    theme_ids = _string_tuple(raw.get("candidateThemeIds"))
    entities = _string_tuple(raw.get("eventEntities"))
    summary = raw.get("catalystSummary")
    confidence = raw.get("confidence")
    grounded = raw.get("grounded")
    if (
        stock_ids is None
        or theme_ids is None
        or entities is None
        or not isinstance(summary, str)
        or not summary.strip()
        or not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not isinstance(grounded, bool)
        or not 0.0 <= float(confidence) <= 1.0
    ):
        return None, GroundingRejection.SCHEMA_INVALID
    if not grounded:
        return None, GroundingRejection.NOT_GROUNDED
    if not frozenset(stock_ids) <= frozenset(request.candidate_stock_ids):
        return None, GroundingRejection.UNKNOWN_STOCK
    if not frozenset(theme_ids) <= frozenset({request.theme_id}):
        return None, GroundingRejection.UNKNOWN_THEME
    if float(confidence) < min_confidence:
        return None, GroundingRejection.LOW_CONFIDENCE
    source_text = request.source_text
    if any(entity.strip() and entity not in source_text for entity in entities):
        return None, GroundingRejection.UNSUPPORTED_ENTITY
    if _has_verbatim_run(summary, source_text):
        return None, GroundingRejection.VERBATIM_QUOTE
    return (
        GroundedCatalyst(
            stock_ids=stock_ids,
            candidate_theme_ids=theme_ids,
            catalyst_summary=summary.strip(),
            event_entities=entities,
            confidence=float(confidence),
            grounded=True,
        ),
        None,
    )


def _has_verbatim_run(summary: str, source_text: str) -> bool:
    """기사 문장을 길게 복제한 요약은 재배포로 보고 폐기한다."""

    compact = " ".join(summary.split())
    haystack = " ".join(source_text.split())
    return any(
        compact[start : start + VERBATIM_WINDOW] in haystack
        for start in range(max(len(compact) - VERBATIM_WINDOW + 1, 0))
    )
