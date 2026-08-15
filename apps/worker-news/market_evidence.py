"""실시간 파이프라인의 활성 Event와 저장된 뉴스를 잇는 실행 경로.

MarketDataPipeline이 ACTIVE로 올린 Event를 ThemeContext로 받아 저장된 뉴스와
맞춰보고, 판정된 EvidenceStatus를 다시 파이프라인에 실어 rankings에 나가게
한다. 상태 이력은 EvidencePipeline이 들고 있는 EvidenceRevisionStore에
append 된다.

주기마다 활성 Event 전체를 다시 판정한다. 새 기사가 없어도 판정이 바뀌기
때문이다(활성화 후 20분간 관련 기사가 없으면 NO_NEW_CATALYST).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packages.pipeline import MarketDataPipeline

    from .pipeline import EvidenceOutcome, EvidencePipeline


def refresh_market_evidence(
    evidence: EvidencePipeline,
    market: MarketDataPipeline,
    *,
    now: datetime,
    window_start: datetime,
    sources_degraded: bool = False,
) -> tuple[EvidenceOutcome, ...]:
    outcomes = tuple(
        evidence.refresh_event(
            context,
            now=now,
            window_start=window_start,
            sources_degraded=sources_degraded,
        )
        for context in market.active_theme_contexts()
    )
    for outcome in outcomes:
        market.record_evidence(outcome.revision, outcome.evidence)
    return outcomes
