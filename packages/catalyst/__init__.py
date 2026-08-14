"""저장된 뉴스와 활성 Event를 양방향으로 이어 근거 상태를 판정한다."""

from .matching import (
    MATCH_MODEL_VERSION,
    MatchConfig,
    evaluate,
    match_news_to_events,
    match_theme_to_news,
)
from .models import (
    CATALYST_POLICY_VERSION,
    CatalystEvidence,
    EvidenceRevision,
    EvidenceStatus,
    ExtractionMethod,
    MatchBasis,
    MatchTrigger,
    NewsThemeMatch,
    ThemeContext,
    catalyst_key,
)
from .policy import NO_NEW_CATALYST_AFTER, EvidenceDecision, decide
from .projection import (
    DEFAULT_PAGE_LIMIT,
    evidence_item,
    evidence_list_data,
    evidence_summary,
)
from .revisions import EvidenceRevisionStore
from .supplemental import (
    SUPPLEMENTAL_COOLDOWN,
    SupplementalDecision,
    SupplementalDenial,
    SupplementalSearchGate,
    SupplementalSearchRequest,
    query_terms,
)

__all__ = [
    "CATALYST_POLICY_VERSION",
    "DEFAULT_PAGE_LIMIT",
    "MATCH_MODEL_VERSION",
    "NO_NEW_CATALYST_AFTER",
    "SUPPLEMENTAL_COOLDOWN",
    "CatalystEvidence",
    "EvidenceDecision",
    "EvidenceRevision",
    "EvidenceRevisionStore",
    "EvidenceStatus",
    "ExtractionMethod",
    "MatchBasis",
    "MatchConfig",
    "MatchTrigger",
    "NewsThemeMatch",
    "SupplementalDecision",
    "SupplementalDenial",
    "SupplementalSearchGate",
    "SupplementalSearchRequest",
    "ThemeContext",
    "catalyst_key",
    "decide",
    "evaluate",
    "evidence_item",
    "evidence_list_data",
    "evidence_summary",
    "match_news_to_events",
    "match_theme_to_news",
    "query_terms",
]
