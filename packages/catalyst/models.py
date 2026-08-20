"""근거 매칭 결과와 Event별 근거 revision의 값 타입."""

from __future__ import annotations

import re

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

CATALYST_POLICY_VERSION = "catalyst-policy-2026.08.1"


class EvidenceStatus(StrEnum):
    SEARCHING = "SEARCHING"
    SINGLE_SOURCE = "SINGLE_SOURCE"
    MULTI_SOURCE_CONFIRMED = "MULTI_SOURCE_CONFIRMED"
    NO_NEW_CATALYST = "NO_NEW_CATALYST"
    REEMERGENCE = "REEMERGENCE"
    AFTER_CLOSE_CONFIRMED = "AFTER_CLOSE_CONFIRMED"


class MatchBasis(StrEnum):
    THEME = "THEME"
    STOCK = "STOCK"
    TIME = "TIME"


class MatchTrigger(StrEnum):
    """어느 방향의 조회로 만들어진 매칭인지."""

    THEME_TO_NEWS = "THEME_TO_NEWS"
    NEWS_TO_EVENT = "NEWS_TO_EVENT"


class ExtractionMethod(StrEnum):
    RULE = "RULE"
    LLM_GROUNDED = "LLM_GROUNDED"


_NAME_TOKEN_SPLIT = re.compile("[/,()\\s\u00b7]+")
_NAME_TOKEN_SUFFIXES = ("관련주", "관련", "테마", "대표주", "수혜주")
_NAME_TOKEN_STOPWORDS = frozenset({"등", "기타", *_NAME_TOKEN_SUFFIXES})


def _name_tokens(name: str) -> tuple[str, ...]:
    """인포스탁 공식 명칭을 기사에 실제로 나오는 낱말로 쪼갠다.

    기사는 "반도체 관련주"가 아니라 "반도체"라고 쓴다. 명칭 전체 문자열만
    비교하면 테마명 신호가 사실상 죽는다(운영 2026-08-19 실측).
    """

    tokens: list[str] = []
    for part in _NAME_TOKEN_SPLIT.split(name):
        part = part.strip()
        for suffix in _NAME_TOKEN_SUFFIXES:
            if part.endswith(suffix) and len(part) > len(suffix):
                part = part[: -len(suffix)]
                break
        if len(part) < 2 or part in _NAME_TOKEN_STOPWORDS:
            continue
        tokens.append(part)
    return tuple(tokens)


@dataclass(frozen=True, slots=True)
class ThemeContext:
    """매칭 시점에 확정된 Event·테마 정보."""

    event_id: str
    theme_id: str
    display_name: str
    market_date: date
    activated_at: datetime
    synonyms: tuple[str, ...] = ()
    leader_names: tuple[str, ...] = ()
    leader_stock_ids: tuple[str, ...] = ()
    related_stock_ids: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    known_catalyst_keys: frozenset[str] = frozenset()

    @property
    def theme_keywords(self) -> tuple[str, ...]:
        names = (self.display_name, *self.synonyms)
        return tuple(
            dict.fromkeys(
                (
                    *names,
                    *(token for name in names for token in _name_tokens(name)),
                    *self.entities,
                )
            )
        )

    @property
    def stock_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.leader_stock_ids, *self.related_stock_ids)))


@dataclass(frozen=True, slots=True)
class NewsThemeMatch:
    news_id: str
    event_id: str
    theme_id: str
    matched_stock_ids: tuple[str, ...]
    match_basis: tuple[MatchBasis, ...]
    trigger: MatchTrigger
    rule_score: float
    relevance_score: float
    matched_at: datetime

    def __post_init__(self) -> None:
        if not self.match_basis:
            raise ValueError("match_basis는 최소 하나가 필요합니다")


@dataclass(frozen=True, slots=True)
class CatalystEvidence:
    """한 기사에서 확인된 근거 한 건."""

    news_id: str
    event_id: str
    publisher: str
    title: str
    summary: str
    match_basis: tuple[MatchBasis, ...]
    entities: tuple[str, ...]
    published_at: datetime | None
    received_at: datetime
    original_url: str
    quality_flags: tuple[str, ...]
    extraction_method: ExtractionMethod
    model_name: str | None
    prompt_version: str | None
    confidence: float | None
    generated_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceRevision:
    """Event별 append-only 근거 이력."""

    event_id: str
    revision: int
    evidence_status: EvidenceStatus
    summary: str | None
    news_ids: tuple[str, ...]
    catalyst_key: str | None
    reason: str
    policy_version: str
    decided_at: datetime
    evidence_confirmed_at: datetime | None

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("revision은 1 이상이어야 합니다")


def catalyst_key(theme_id: str, entities: tuple[str, ...]) -> str:
    """같은 소재의 재부각을 알아보기 위한 결정적 키."""

    raw = "|".join((theme_id, *sorted({entity.strip().casefold() for entity in entities if entity.strip()})))
    return f"cat_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"
