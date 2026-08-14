"""Validated, source-preserving values for Infostock full-sync imports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

PageType = Literal[
    "IMPORT_MANIFEST",
    "THEME_LIST",
    "THEME_DETAIL",
    "DAILY_MANIFEST",
    "DAILY_LIST",
    "DAILY_DETAIL",
]
RawFormat = Literal["JSON", "HTML"]
Direction = Literal["UP", "DOWN", "MIXED", "UNKNOWN"]
ComponentStatus = Literal["COMPLETE", "PARTIAL", "BLOCKED", "FAILED"]
HistoryQualityStatus = Literal[
    "OK",
    "DUPLICATE_GROUP_HEAD",
    "SOURCE_DUPLICATE",
    "DATE_MISSING",
    "CONTENT_MISSING",
]
ReferenceQualityStatus = Literal["OK", "SOURCE_CODE_MISSING", "CODE_INVALID"]
DailyBodyStatus = Literal["OK", "MISSING", "PARSE_PARTIAL", "PARSE_FAILED"]
DailyVisibilityStatus = Literal["VISIBLE", "NOT_VISIBLE", "UNKNOWN"]


@dataclass(frozen=True, slots=True)
class RawSnapshot:
    """One immutable source observation, including its exact source bytes."""

    page_type: PageType
    source_entity_id: str | None
    source_url: str
    collected_at: datetime
    as_of: datetime
    raw_hash: str
    source_content_hash: str | None
    raw_payload_text: str
    raw_format: RawFormat
    is_complete: bool
    quality_status: str = "OK"
    parser_version: str | None = None


@dataclass(frozen=True, slots=True)
class ThemeIndexItem:
    source_order: int
    source_theme_id: str
    theme_name: str
    source_url: str


@dataclass(frozen=True, slots=True)
class StockReference:
    source_order: int
    name: str
    stock_code: str | None
    source_url: str | None
    display_value: str
    quality_status: ReferenceQualityStatus


@dataclass(frozen=True, slots=True)
class ThemeHistory:
    source_order: int
    source_history_id: str | None
    source_history_key: str
    event_date: date | None
    source_date: str | None
    source_created_at: datetime | None
    source_updated_at: datetime | None
    raw_text: str
    direction: Direction
    leaders: tuple[StockReference, ...]
    member_stocks: tuple[StockReference, ...]
    author: str | None
    chart_flag: str | None
    source_fingerprint: str
    quality_status: HistoryQualityStatus
    content_hash: str


@dataclass(frozen=True, slots=True)
class ThemeMembership:
    source_order: int
    stock_code: str | None
    stock_name: str
    rationale: str
    source_index: str | None
    content_hash: str
    quality_status: ReferenceQualityStatus = "OK"


@dataclass(frozen=True, slots=True)
class QualitySummary:
    theme_count: int
    history_count: int
    related_stock_count: int
    leader_count: int
    historical_membership_count: int
    duplicate_history_count: int
    missing_history_date_count: int
    missing_history_content_count: int
    missing_leader_code_count: int
    missing_related_stock_code_count: int
    missing_historical_membership_code_count: int
    missing_historical_membership_field_count: int = 0
    stock_name_variant_count: int = 0


@dataclass(frozen=True, slots=True)
class QualityIssue:
    component: Literal["THEME_DATABASE", "DAILY_FEATURED_THEME"]
    issue_code: str
    severity: Literal["INFO", "WARNING", "ERROR", "BLOCKER"]
    entity_type: str
    source_entity_key: str | None
    source_order: int | None
    detail: dict[str, object]


@dataclass(frozen=True, slots=True)
class ThemeDetail:
    source_theme_id: str
    theme_name: str
    description: str
    theme_revision_hash: str
    history: tuple[ThemeHistory, ...]
    memberships: tuple[ThemeMembership, ...]
    snapshot: RawSnapshot


@dataclass(frozen=True, slots=True)
class DailyListEntry:
    source_order: int
    source_post_key: str
    source_post_id: str | None
    source_url: str | None
    title: str
    published_date: date | None
    source_date: str | None
    quality_status: str


@dataclass(frozen=True, slots=True)
class DailyRelation:
    source_order: int
    relation_type: Literal["THEME", "STOCK", "THEME_STOCK", "DESCRIPTION"]
    source_theme_name: str | None
    source_stock_name: str | None
    source_stock_code: str | None
    description: str
    raw_text: str
    quality_status: str


@dataclass(frozen=True, slots=True)
class DailyPost:
    source_post_key: str
    source_post_id: str | None
    source_url: str | None
    title: str
    published_date: date | None
    source_date: str | None
    raw_body: str | None
    body_hash: str | None
    normalized_hash: str
    body_status: DailyBodyStatus
    visibility_status: DailyVisibilityStatus
    relations: tuple[DailyRelation, ...]
    detail_snapshot: RawSnapshot | None


@dataclass(frozen=True, slots=True)
class DailyBackfill:
    component_status: ComponentStatus
    pages: tuple[RawSnapshot, ...]
    entries: tuple[DailyListEntry, ...]
    posts: tuple[DailyPost, ...]
    first_page: int | None
    last_page: int | None
    next_page: int | None
    earliest_date: date | None
    latest_date: date | None
    coverage_complete: bool
    blockers: tuple[str, ...]
    quality_issues: tuple[QualityIssue, ...]

    @property
    def body_count(self) -> int:
        return sum(post.raw_body is not None for post in self.posts)

    @property
    def relation_count(self) -> int:
        return sum(len(post.relations) for post in self.posts)


@dataclass(frozen=True, slots=True)
class ImportBundle:
    fixture_version: str
    dataset: str
    source_provider: str
    rights_scope: str
    parser_version: str
    expected_theme_count: int
    input_hash: str
    dataset_hash: str
    manifest_snapshot: RawSnapshot
    index_snapshot: RawSnapshot
    index_items: tuple[ThemeIndexItem, ...]
    details: tuple[ThemeDetail, ...]
    quality_summary: QualitySummary
    quality_issues: tuple[QualityIssue, ...]
    daily: DailyBackfill

    @property
    def core_status(self) -> ComponentStatus:
        return "COMPLETE"
