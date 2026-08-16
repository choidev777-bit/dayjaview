"""사건·소재 온톨로지 (E-17): 통제어휘 + versioned transform."""

from .labeling import (
    UNCLASSIFIED_GO_THRESHOLD,
    UNCLASSIFIED_REDESIGN_THRESHOLD,
    HistoryRecord,
    label_history_records,
    records_from_bundle,
)
from .models import (
    CatalystClassification,
    CatalystTypeDefinition,
    Certainty,
    Direction,
    EvidenceSpan,
    ParsedCauseSentence,
)
from .transform import TRANSFORM_VERSION, classify_catalyst, parse_cause_sentence
from .vocabulary import VOCABULARY, VOCABULARY_VERSION, vocabulary_content_hash

__all__ = [
    "TRANSFORM_VERSION",
    "UNCLASSIFIED_GO_THRESHOLD",
    "UNCLASSIFIED_REDESIGN_THRESHOLD",
    "VOCABULARY",
    "VOCABULARY_VERSION",
    "CatalystClassification",
    "CatalystTypeDefinition",
    "Certainty",
    "Direction",
    "EvidenceSpan",
    "HistoryRecord",
    "ParsedCauseSentence",
    "classify_catalyst",
    "label_history_records",
    "parse_cause_sentence",
    "records_from_bundle",
    "vocabulary_content_hash",
]
