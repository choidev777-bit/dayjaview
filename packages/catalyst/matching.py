"""테마 → 뉴스, 새 뉴스 → 활성 Event 양방향 매칭과 후보 점수."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from news import NewsItem
else:
    NewsItem = import_module("packages." + "news").NewsItem

from .models import MatchBasis, MatchTrigger, NewsThemeMatch, ThemeContext

MATCH_MODEL_VERSION = "catalyst-match-2026.08.1"

THEME_WEIGHT = 0.35
LEADER_WEIGHT = 0.30
RELATED_WEIGHT = 0.15
TIME_WEIGHT = 0.20
BREADTH_WEIGHT = 0.10
ENTITY_WEIGHT = 0.05
RELEVANCE_THRESHOLD = 0.50
DEFAULT_LOOKBACK = timedelta(minutes=60)


@dataclass(frozen=True, slots=True)
class MatchConfig:
    lookback: timedelta = DEFAULT_LOOKBACK
    threshold: float = RELEVANCE_THRESHOLD


def _theme_hit(item: NewsItem, context: ThemeContext) -> bool:
    haystack = f"{item.title} {item.description} {' '.join(item.entities)}".casefold()
    return any(keyword.casefold() in haystack for keyword in context.theme_keywords if keyword)


def _score(
    item: NewsItem,
    context: ThemeContext,
    *,
    decision_at: datetime,
    config: MatchConfig,
) -> tuple[float, float, tuple[str, ...], tuple[MatchBasis, ...]]:
    matched_stocks = tuple(
        stock_id for stock_id in context.stock_ids if stock_id in item.stock_ids
    )
    leaders = frozenset(context.leader_stock_ids) & frozenset(matched_stocks)
    theme_hit = _theme_hit(item, context)
    window_start = min(context.activated_at - config.lookback, decision_at)
    published = item.published_at
    time_hit = published is not None and window_start <= published <= decision_at

    rule_score = 0.0
    if theme_hit:
        rule_score += THEME_WEIGHT
    if leaders:
        rule_score += LEADER_WEIGHT
    elif matched_stocks:
        rule_score += RELATED_WEIGHT
    if time_hit:
        rule_score += TIME_WEIGHT

    relevance = rule_score
    if len(matched_stocks) >= 2:
        relevance += BREADTH_WEIGHT
    if frozenset(item.entities) & frozenset(context.entities):
        relevance += ENTITY_WEIGHT

    basis: list[MatchBasis] = []
    if theme_hit:
        basis.append(MatchBasis.THEME)
    if matched_stocks:
        basis.append(MatchBasis.STOCK)
    if time_hit:
        basis.append(MatchBasis.TIME)
    return rule_score, min(relevance, 1.0), matched_stocks, tuple(basis)


def evaluate(
    item: NewsItem,
    context: ThemeContext,
    *,
    decision_at: datetime,
    trigger: MatchTrigger,
    config: MatchConfig | None = None,
) -> NewsThemeMatch | None:
    """판단 시점보다 나중에 발행된 기사는 근거로 쓰지 않는다."""

    config = config or MatchConfig()
    if item.published_at is not None and item.published_at > decision_at:
        return None
    rule_score, relevance, matched_stocks, basis = _score(
        item, context, decision_at=decision_at, config=config
    )
    if relevance < config.threshold or not basis:
        return None
    return NewsThemeMatch(
        news_id=item.news_id,
        event_id=context.event_id,
        theme_id=context.theme_id,
        matched_stock_ids=matched_stocks,
        match_basis=basis,
        trigger=trigger,
        rule_score=round(rule_score, 4),
        relevance_score=round(relevance, 4),
        matched_at=decision_at,
    )


def match_theme_to_news(
    context: ThemeContext,
    candidates: Sequence[NewsItem],
    *,
    decision_at: datetime,
    config: MatchConfig | None = None,
) -> tuple[NewsThemeMatch, ...]:
    matches = [
        match
        for item in candidates
        if (
            match := evaluate(
                item,
                context,
                decision_at=decision_at,
                trigger=MatchTrigger.THEME_TO_NEWS,
                config=config,
            )
        )
        is not None
    ]
    return tuple(sorted(matches, key=lambda match: (-match.relevance_score, match.news_id)))


def match_news_to_events(
    item: NewsItem,
    contexts: Sequence[ThemeContext],
    *,
    decision_at: datetime,
    config: MatchConfig | None = None,
) -> tuple[NewsThemeMatch, ...]:
    matches = [
        match
        for context in contexts
        if (
            match := evaluate(
                item,
                context,
                decision_at=decision_at,
                trigger=MatchTrigger.NEWS_TO_EVENT,
                config=config,
            )
        )
        is not None
    ]
    return tuple(sorted(matches, key=lambda match: (-match.relevance_score, match.event_id)))
