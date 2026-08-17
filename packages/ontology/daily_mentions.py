"""DailyFeaturedTheme 원천을 사건 mention으로 투영한다 (E-22 단계 1).

외부 재수집 없이 저장된 Daily post와 relation만 사용한다. 섹션 머리글·상세
문단·종목 행을 서로 다른 mention으로 만들고, 원문 대신 hash와 span을 정본으로
삼는다. 현실 사건 분리와 회사 역할 승격은 단계 4가 이 mention을 입력으로 한다.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Any, Literal

from packages.infostock.daily_api import DAILY_API_PARSER_VERSION
from packages.infostock.hashing import sha256_json, sha256_text
from packages.infostock.models import DailyBackfill, DailyPost, DailyRelation

DAILY_MENTION_TRANSFORM_VERSION = "daily-mention-transform/1.0.0"
_TITLE_DATE_RE = re.compile(r"\[(\d{1,2})\s*[/.-]\s*(\d{1,2})\]")
_NARRATIVE_SECTION = "테마시황"


class DailyMentionSourceKind(StrEnum):
    DESCRIPTION = "INFOSTOCK_DAILY_DESCRIPTION"
    THEME_STOCK = "INFOSTOCK_DAILY_THEME_STOCK"


class DailyMentionScope(StrEnum):
    HEADLINE = "HEADLINE"
    DETAIL = "DETAIL"
    STOCK_ROW = "STOCK_ROW"


class DailyMentionServingStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    EXCLUDED = "EXCLUDED"


class TradingDateBasis(StrEnum):
    TITLE = "TITLE"
    PUBLISHED_DATE = "PUBLISHED_DATE"
    UNKNOWN = "UNKNOWN"


class DailyFormatFamily(StrEnum):
    SECTIONED_WITH_STOCK_TABLE = "SECTIONED_WITH_STOCK_TABLE"
    SECTIONED_TEXT_ONLY = "SECTIONED_TEXT_ONLY"
    TABLE_ONLY = "TABLE_ONLY"
    NARRATIVE_THEMES = "NARRATIVE_THEMES"
    NARRATIVE_MIXED = "NARRATIVE_MIXED"
    NARRATIVE_UNATTRIBUTED = "NARRATIVE_UNATTRIBUTED"
    MISSING_BODY = "MISSING_BODY"
    PARSE_FAILED = "PARSE_FAILED"
    UNSUPPORTED_EMPTY = "UNSUPPORTED_EMPTY"


@dataclass(frozen=True, slots=True)
class DailySourceMention:
    source_post_key: str
    source_relation_order: int
    relation_type: str
    source_kind: DailyMentionSourceKind
    mention_scope: DailyMentionScope
    source_revision_hash: str
    source_text_hash: str
    start: int
    end: int
    raw_text: str
    published_date: date | None
    trading_date: date | None
    trading_date_basis: TradingDateBasis
    source_theme_name: str | None
    source_stock_name: str | None
    source_stock_code: str | None
    suggested_role: Literal["RELATED"] | None
    serving_status: DailyMentionServingStatus
    observed_at: datetime
    transform_version: str = DAILY_MENTION_TRANSFORM_VERSION
    review_status: str = "AI_DRAFT"

    def __post_init__(self) -> None:
        if not self.source_post_key:
            raise ValueError("Daily source_post_key는 비울 수 없습니다.")
        if self.source_relation_order < 0:
            raise ValueError("Daily relation 순서는 0 이상이어야 합니다.")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_revision_hash):
            raise ValueError("Daily source revision hash가 올바르지 않습니다.")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_text_hash):
            raise ValueError("Daily source text hash가 올바르지 않습니다.")
        if self.start < 0 or self.end <= self.start or self.end > len(self.raw_text):
            raise ValueError("Daily mention span이 올바르지 않습니다.")
        if sha256_text(self.raw_text) != self.source_text_hash:
            raise ValueError("Daily mention 원문과 hash가 다릅니다.")
        if self.observed_at.tzinfo is None:
            raise ValueError("Daily mention 관측 시각에는 timezone이 필요합니다.")
        if self.mention_scope is DailyMentionScope.STOCK_ROW:
            if self.source_kind is not DailyMentionSourceKind.THEME_STOCK:
                raise ValueError("종목 행은 Daily theme-stock 원천이어야 합니다.")
            if self.suggested_role != "RELATED":
                raise ValueError("Daily 종목 행은 RELATED로 시작해야 합니다.")
        elif self.source_kind is not DailyMentionSourceKind.DESCRIPTION:
            raise ValueError("머리글·상세 문단은 Daily description 원천이어야 합니다.")

    @property
    def output_hash(self) -> str:
        row = self.as_dict(include_raw_text=False)
        # 수집 시각과 검수 상태는 변환 결과가 아니다. 같은 source revision을
        # 다시 읽어도 output hash가 바뀌지 않아야 한다.
        row.pop("observedAt")
        row.pop("reviewStatus")
        return sha256_json(row)

    def as_dict(self, *, include_raw_text: bool = True) -> dict[str, Any]:
        row: dict[str, Any] = {
            "sourcePostKey": self.source_post_key,
            "sourceRelationOrder": self.source_relation_order,
            "relationType": self.relation_type,
            "sourceKind": self.source_kind.value,
            "mentionScope": self.mention_scope.value,
            "sourceRevisionHash": self.source_revision_hash,
            "sourceTextHash": self.source_text_hash,
            "start": self.start,
            "end": self.end,
            "publishedDate": (
                self.published_date.isoformat() if self.published_date else None
            ),
            "tradingDate": self.trading_date.isoformat() if self.trading_date else None,
            "tradingDateBasis": self.trading_date_basis.value,
            "sourceThemeName": self.source_theme_name,
            "sourceStockName": self.source_stock_name,
            "sourceStockCode": self.source_stock_code,
            "suggestedRole": self.suggested_role,
            "servingStatus": self.serving_status.value,
            "observedAt": self.observed_at.isoformat(),
            "transformVersion": self.transform_version,
            "reviewStatus": self.review_status,
        }
        if include_raw_text:
            row["rawText"] = self.raw_text
        return row


def infer_daily_trading_date(
    title: str, published_date: date | None
) -> tuple[date | None, TradingDateBasis]:
    """제목의 ``[M/D]``를 우선하고 없으면 발행일을 거래일로 사용한다."""

    match = _TITLE_DATE_RE.search(title)
    if match is not None and published_date is not None:
        month, day = (int(value) for value in match.groups())
        candidates: list[date] = []
        for year in (
            published_date.year - 1,
            published_date.year,
            published_date.year + 1,
        ):
            try:
                candidates.append(date(year, month, day))
            except ValueError:
                continue
        if candidates:
            return (
                min(
                    candidates,
                    key=lambda candidate: (
                        abs((candidate - published_date).days),
                        candidate > published_date,
                        candidate,
                    ),
                ),
                TradingDateBasis.TITLE,
            )
    if published_date is not None:
        return published_date, TradingDateBasis.PUBLISHED_DATE
    return None, TradingDateBasis.UNKNOWN


def classify_daily_format(post: DailyPost) -> DailyFormatFamily:
    if post.body_status == "MISSING":
        return DailyFormatFamily.MISSING_BODY
    if post.body_status == "PARSE_FAILED":
        return DailyFormatFamily.PARSE_FAILED

    kinds = Counter(relation.relation_type for relation in post.relations)
    has_stock = kinds["THEME_STOCK"] > 0
    has_headline = kinds["DESCRIPTION"] > 0
    details = [
        relation
        for relation in post.relations
        if relation.relation_type == "SECTION_DETAIL"
    ]
    attributed_details = [
        relation
        for relation in details
        if relation.source_theme_name != _NARRATIVE_SECTION
    ]
    if has_stock and (has_headline or attributed_details):
        return DailyFormatFamily.SECTIONED_WITH_STOCK_TABLE
    if has_stock:
        return DailyFormatFamily.TABLE_ONLY
    if has_headline and attributed_details:
        return DailyFormatFamily.SECTIONED_TEXT_ONLY
    if has_headline and details:
        return DailyFormatFamily.NARRATIVE_MIXED
    if has_headline:
        return DailyFormatFamily.NARRATIVE_THEMES
    if details:
        return DailyFormatFamily.NARRATIVE_UNATTRIBUTED
    return DailyFormatFamily.UNSUPPORTED_EMPTY


def _observed_at(post: DailyPost) -> datetime:
    if post.detail_snapshot is not None:
        return post.detail_snapshot.collected_at
    if post.published_date is not None:
        return datetime.combine(post.published_date, time.min, tzinfo=UTC)
    return datetime(1970, 1, 1, tzinfo=UTC)


def _mention_shape(
    relation: DailyRelation,
) -> tuple[
    DailyMentionSourceKind,
    DailyMentionScope,
    Literal["RELATED"] | None,
] | None:
    if relation.relation_type == "DESCRIPTION":
        return (
            DailyMentionSourceKind.DESCRIPTION,
            DailyMentionScope.HEADLINE,
            None,
        )
    if relation.relation_type == "SECTION_DETAIL":
        return (
            DailyMentionSourceKind.DESCRIPTION,
            DailyMentionScope.DETAIL,
            None,
        )
    if relation.relation_type == "THEME_STOCK":
        return (
            DailyMentionSourceKind.THEME_STOCK,
            DailyMentionScope.STOCK_ROW,
            "RELATED",
        )
    return None


def mentions_from_daily_post(post: DailyPost) -> tuple[DailySourceMention, ...]:
    """Daily post 한 건의 지원 relation을 source mention으로 바꾼다."""

    trading_date, trading_basis = infer_daily_trading_date(
        post.title, post.published_date
    )
    mentions: list[DailySourceMention] = []
    for relation in sorted(post.relations, key=lambda item: item.source_order):
        shape = _mention_shape(relation)
        if shape is None or not relation.raw_text:
            continue
        source_kind, scope, suggested_role = shape
        needs_review = (
            relation.source_theme_name in {None, "", _NARRATIVE_SECTION}
            or (
                scope is DailyMentionScope.STOCK_ROW
                and (
                    relation.source_stock_code is None
                    or relation.quality_status != "OK"
                )
            )
        )
        mentions.append(
            DailySourceMention(
                source_post_key=post.source_post_key,
                source_relation_order=relation.source_order,
                relation_type=relation.relation_type,
                source_kind=source_kind,
                mention_scope=scope,
                source_revision_hash=post.normalized_hash,
                source_text_hash=sha256_text(relation.raw_text),
                start=0,
                end=len(relation.raw_text),
                raw_text=relation.raw_text,
                published_date=post.published_date,
                trading_date=trading_date,
                trading_date_basis=trading_basis,
                source_theme_name=relation.source_theme_name,
                source_stock_name=relation.source_stock_name,
                source_stock_code=relation.source_stock_code,
                suggested_role=suggested_role,
                serving_status=(
                    DailyMentionServingStatus.REVIEW_REQUIRED
                    if needs_review
                    else DailyMentionServingStatus.ELIGIBLE
                ),
                observed_at=_observed_at(post),
            )
        )
    return tuple(mentions)


def label_daily_mentions(
    backfill: DailyBackfill,
) -> tuple[tuple[DailySourceMention, ...], dict[str, Any]]:
    """전체 Daily corpus를 mention으로 바꾸고 coverage 보고서를 만든다."""

    posts = tuple(
        sorted(
            backfill.posts,
            key=lambda post: (
                post.published_date is None,
                post.published_date or date.max,
                post.source_post_key,
            ),
        )
    )
    mentions = tuple(
        mention for post in posts for mention in mentions_from_daily_post(post)
    )
    families = Counter(classify_daily_format(post).value for post in posts)
    body_statuses = Counter(post.body_status for post in posts)
    relation_types = Counter(
        relation.relation_type for post in posts for relation in post.relations
    )
    unknown_quote_rows = sum(
        relation.relation_type == "THEME_STOCK"
        and relation.change_rate is None
        and relation.close_price is None
        for post in posts
        for relation in post.relations
    )
    dataset_hash = sha256_json(
        [
            {
                "sourcePostKey": post.source_post_key,
                "normalizedHash": post.normalized_hash,
            }
            for post in posts
        ]
    )
    report: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "datasetHash": dataset_hash,
        "parserVersion": DAILY_API_PARSER_VERSION,
        "transformVersion": DAILY_MENTION_TRANSFORM_VERSION,
        "reviewStatus": "AI_DRAFT",
        "totalPosts": len(posts),
        "totalRelations": sum(len(post.relations) for post in posts),
        "totalMentions": len(mentions),
        "bodyStatusCounts": dict(sorted(body_statuses.items())),
        "formatFamilyCounts": dict(sorted(families.items())),
        "relationTypeCounts": dict(sorted(relation_types.items())),
        "sourceKindCounts": dict(
            sorted(Counter(item.source_kind.value for item in mentions).items())
        ),
        "mentionScopeCounts": dict(
            sorted(Counter(item.mention_scope.value for item in mentions).items())
        ),
        "servingStatusCounts": dict(
            sorted(Counter(item.serving_status.value for item in mentions).items())
        ),
        "tradingDateBasisCounts": dict(
            sorted(Counter(item.trading_date_basis.value for item in mentions).items())
        ),
        "unknownQuoteLayoutRows": unknown_quote_rows,
        "unsupportedPostCount": (
            families[DailyFormatFamily.MISSING_BODY.value]
            + families[DailyFormatFamily.PARSE_FAILED.value]
            + families[DailyFormatFamily.UNSUPPORTED_EMPTY.value]
        ),
    }
    return mentions, report
