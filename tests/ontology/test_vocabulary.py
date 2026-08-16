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
    FALLBACK_VOCABULARY,
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


def test_fallback_keywords_valid_and_not_shadowed() -> None:
    type_ids = {definition.type_id for definition in VOCABULARY}
    normal_keywords = {
        keyword for definition in VOCABULARY for keyword in definition.keywords
    }
    fallback_counts: Counter[str] = Counter()
    for type_id, keywords in FALLBACK_VOCABULARY:
        assert type_id in type_ids
        fallback_counts.update(keywords)
    duplicated = sorted(k for k, count in fallback_counts.items() if count > 1)
    assert duplicated == []
    # 본 어휘와 겹치는 폴백 키워드는 영원히 발화하지 못한다(죽은 항목).
    dead = sorted(set(fallback_counts) & normal_keywords)
    assert dead == [], f"본 어휘에 가려진 폴백 keyword: {dead}"


def test_certainty_marker_families_do_not_overlap() -> None:
    overlap = set(ANTICIPATION_MARKERS) & set(CONFIRMED_MARKERS)
    assert overlap == set()
    assert set(CONTINUATION_MARKERS).isdisjoint(
        set(ANTICIPATION_MARKERS) | set(CONFIRMED_MARKERS)
    )


def test_content_hash_is_stable_and_versioned() -> None:
    assert VOCABULARY_VERSION == "1.2.0"
    first = vocabulary_content_hash()
    assert first == vocabulary_content_hash()
    assert len(first) == 64
