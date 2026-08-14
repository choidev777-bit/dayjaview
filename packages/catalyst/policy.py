"""근거 상태 판정. 근거 없이 상태를 강화하지 않는다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import (
    CATALYST_POLICY_VERSION,
    CatalystEvidence,
    EvidenceRevision,
    EvidenceStatus,
    ThemeContext,
    catalyst_key,
)

NO_NEW_CATALYST_AFTER = timedelta(minutes=20)

_STRENGTH = {
    EvidenceStatus.SEARCHING: 0,
    EvidenceStatus.NO_NEW_CATALYST: 0,
    EvidenceStatus.SINGLE_SOURCE: 1,
    EvidenceStatus.REEMERGENCE: 1,
    EvidenceStatus.MULTI_SOURCE_CONFIRMED: 2,
    EvidenceStatus.AFTER_CLOSE_CONFIRMED: 3,
}


@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    evidence_status: EvidenceStatus
    summary: str | None
    news_ids: tuple[str, ...]
    catalyst_key: str | None
    reason: str
    policy_version: str = CATALYST_POLICY_VERSION


def decide(
    context: ThemeContext,
    evidence: Sequence[CatalystEvidence],
    *,
    now: datetime,
    previous: EvidenceRevision | None = None,
    sources_degraded: bool = False,
    after_close_summary: str | None = None,
    no_catalyst_after: timedelta = NO_NEW_CATALYST_AFTER,
) -> EvidenceDecision:
    if after_close_summary is not None:
        return EvidenceDecision(
            evidence_status=EvidenceStatus.AFTER_CLOSE_CONFIRMED,
            summary=after_close_summary,
            news_ids=tuple(item.news_id for item in evidence),
            catalyst_key=_key(context, evidence),
            reason="인포스탁 장후 확정을 같은 Event에 반영했습니다",
        )

    if not evidence:
        return _without_evidence(
            context,
            now=now,
            previous=previous,
            sources_degraded=sources_degraded,
            no_catalyst_after=no_catalyst_after,
        )

    publishers = {item.publisher.strip().casefold() for item in evidence if item.publisher.strip()}
    key = _key(context, evidence)
    if len(publishers) >= 2:
        status = EvidenceStatus.MULTI_SOURCE_CONFIRMED
        reason = f"독립 매체 {len(publishers)}곳에서 같은 소재를 확인했습니다"
    elif key is not None and key in context.known_catalyst_keys:
        status = EvidenceStatus.REEMERGENCE
        reason = "이전 거래일에 확인된 소재가 다시 부각됐습니다"
    else:
        status = EvidenceStatus.SINGLE_SOURCE
        reason = "단일 매체 보도로 확인된 추정입니다"
    return EvidenceDecision(
        evidence_status=status,
        summary=evidence[0].summary,
        news_ids=tuple(dict.fromkeys(item.news_id for item in evidence)),
        catalyst_key=key,
        reason=reason,
    )


def _without_evidence(
    context: ThemeContext,
    *,
    now: datetime,
    previous: EvidenceRevision | None,
    sources_degraded: bool,
    no_catalyst_after: timedelta,
) -> EvidenceDecision:
    if previous is not None and _STRENGTH[previous.evidence_status] > 0:
        return EvidenceDecision(
            evidence_status=previous.evidence_status,
            summary=previous.summary,
            news_ids=previous.news_ids,
            catalyst_key=previous.catalyst_key,
            reason="이미 확인된 근거를 유지했습니다",
        )
    if sources_degraded:
        return EvidenceDecision(
            evidence_status=EvidenceStatus.SEARCHING,
            summary=None,
            news_ids=(),
            catalyst_key=None,
            reason="공급원 수집이 지연돼 소재 없음으로 판정하지 않았습니다",
        )
    if now - context.activated_at >= no_catalyst_after:
        return EvidenceDecision(
            evidence_status=EvidenceStatus.NO_NEW_CATALYST,
            summary=None,
            news_ids=(),
            catalyst_key=None,
            reason="정상 수집 상태에서 관련 기사를 찾지 못했습니다",
        )
    return EvidenceDecision(
        evidence_status=EvidenceStatus.SEARCHING,
        summary=None,
        news_ids=(),
        catalyst_key=None,
        reason="근거를 계속 확인하는 중입니다",
    )


def _key(context: ThemeContext, evidence: Sequence[CatalystEvidence]) -> str | None:
    entities = tuple(dict.fromkeys(entity for item in evidence for entity in item.entities))
    if not entities:
        return None
    return catalyst_key(context.theme_id, entities)
