"""통제어휘 무결성 (E-17)."""

from __future__ import annotations

from collections import Counter

from packages.ontology import (
    VOCABULARY,
    VOCABULARY_VERSION,
    vocabulary_content_hash,
)
from packages.ontology.vocabulary import (
    ANTICIPATION_MARKERS,
    CONFIRMED_MARKERS,
    CONTINUATION_MARKERS,
)


def test_type_ids_are_unique() -> None:
    type_ids = [definition.type_id for definition in VOCABULARY]
    assert len(set(type_ids)) == len(type_ids)
    assert len(type_ids) == 28


def test_keywords_do_not_repeat_across_types() -> None:
    counts: Counter[str] = Counter()
    for definition in VOCABULARY:
        counts.update(definition.keywords)
    duplicated = sorted(keyword for keyword, count in counts.items() if count > 1)
    assert duplicated == [], f"유형 간 중복 keyword: {duplicated}"


def test_certainty_marker_families_do_not_overlap() -> None:
    overlap = set(ANTICIPATION_MARKERS) & set(CONFIRMED_MARKERS)
    assert overlap == set()
    assert set(CONTINUATION_MARKERS).isdisjoint(
        set(ANTICIPATION_MARKERS) | set(CONFIRMED_MARKERS)
    )


def test_content_hash_is_stable_and_versioned() -> None:
    assert VOCABULARY_VERSION == "1.0.0"
    first = vocabulary_content_hash()
    assert first == vocabulary_content_hash()
    assert len(first) == 64
