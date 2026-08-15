"""Grounded 구조화의 입력·출력과 재현에 필요한 호출 기록."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

GROUNDING_PROMPT_VERSION = "catalyst-grounding-2026.08.1"
MIN_CONFIDENCE = 0.6
VERBATIM_WINDOW = 30


class GroundingRejection(StrEnum):
    NO_EVIDENCE = "NO_EVIDENCE"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    NOT_GROUNDED = "NOT_GROUNDED"
    UNKNOWN_STOCK = "UNKNOWN_STOCK"
    UNKNOWN_THEME = "UNKNOWN_THEME"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    UNSUPPORTED_ENTITY = "UNSUPPORTED_ENTITY"
    VERBATIM_QUOTE = "VERBATIM_QUOTE"


@dataclass(frozen=True, slots=True)
class GroundingArticle:
    """관련성 기준을 통과해 LLM에 전달이 허용된 기사 하나."""

    news_id: str
    publisher: str
    title: str
    text: str
    original_url: str
    published_at: datetime | None

    def to_payload(self) -> dict[str, object]:
        return {
            "newsId": self.news_id,
            "publisher": self.publisher,
            "title": self.title,
            "text": self.text,
            "originalUrl": self.original_url,
            "publishedAt": None if self.published_at is None else self.published_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class GroundingRequest:
    theme_id: str
    display_name: str
    candidate_stock_ids: tuple[str, ...]
    reaction_started_at: datetime
    articles: tuple[GroundingArticle, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "themeId": self.theme_id,
            "displayName": self.display_name,
            "candidateStockIds": list(self.candidate_stock_ids),
            "reactionStartedAt": self.reaction_started_at.isoformat(),
            "articles": [article.to_payload() for article in self.articles],
        }

    @property
    def news_ids(self) -> tuple[str, ...]:
        return tuple(article.news_id for article in self.articles)

    @property
    def source_text(self) -> str:
        return " ".join(f"{article.title} {article.text}" for article in self.articles)

    def fingerprint(self) -> str:
        raw = json.dumps(self.to_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class GroundedCatalyst:
    stock_ids: tuple[str, ...]
    candidate_theme_ids: tuple[str, ...]
    catalyst_summary: str
    event_entities: tuple[str, ...]
    confidence: float
    grounded: bool


@dataclass(frozen=True, slots=True)
class LlmCallRecord:
    """모델·프롬프트·입력 기사·출력을 남겨 결과를 재현한다."""

    model_name: str
    prompt_version: str
    news_ids: tuple[str, ...]
    request_fingerprint: str
    raw_output: str | None
    accepted: bool
    rejection: GroundingRejection | None
    called_at: datetime


@dataclass(frozen=True, slots=True)
class GroundingOutcome:
    called: bool
    catalyst: GroundedCatalyst | None
    record: LlmCallRecord | None
    rejection: GroundingRejection | None

    @property
    def accepted(self) -> bool:
        return self.catalyst is not None
