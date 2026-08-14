"""DailyFeaturedTheme parsing and resumable browser-source contract.

This module never opens a browser or reads credentials.  A future S6 worker must
provide an already authenticated ``DailyBrowserSource`` and pass both external
gates before any fetch method can be called.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from .errors import FixtureValidationError
from .hashing import canonical_json, sha256_json, sha256_text
from .models import (
    DailyBackfill,
    DailyListEntry,
    DailyPost,
    DailyRelation,
    QualityIssue,
    RawSnapshot,
)
from .policy import InfostockAccessPolicy

DAILY_LIST_URL = "https://infostock.co.kr/Theme/DailyFeaturedTheme"
_SECTION_RE = re.compile(r"^-\s*(.+?)\s*-$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TABLE_HEADER = "테마명\t등락률\t종목명\t"


def _date(value: object, path: str) -> date | None:
    if value in {None, ""}:
        return None
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise FixtureValidationError(
            "DAILY_DATE_INVALID", path, "Daily 등록일자는 YYYY-MM-DD 형식이어야 합니다."
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise FixtureValidationError(
            "DAILY_DATE_INVALID", path, "유효하지 않은 Daily 등록일자입니다."
        ) from exc


def derive_daily_post_key(
    *, source_post_id: str | None, published_date: date | None, title: str
) -> str:
    """Return a stable source key without inventing a source identifier."""

    if source_post_id:
        return f"source:{source_post_id}"
    day = published_date.isoformat() if published_date else "date-missing"
    return f"derived:{day}:{sha256_text(title)}"


def parse_daily_body(raw_body: str) -> tuple[tuple[DailyRelation, ...], str]:
    """Conservatively project section descriptions and tabular theme/stock rows.

    Narrative text remains in ``raw_body``.  The parser intentionally does not
    guess stock codes or split arbitrary prose into canonical theme identities.
    """

    if not raw_body.strip():
        return (), "MISSING"
    lines = [line.strip() for line in raw_body.splitlines() if line.strip()]
    relations: list[DailyRelation] = []
    section_name: str | None = None
    section_description = ""
    table_theme: str | None = None
    saw_unstructured_narrative = False

    for line in lines:
        section_match = _SECTION_RE.fullmatch(line)
        if section_match:
            section_name = section_match.group(1).strip()
            section_description = ""
            table_theme = None
            continue
        if section_name == "테마시황":
            saw_unstructured_narrative = True
            continue
        if line.startswith(_TABLE_HEADER):
            table_theme = None
            continue
        columns = line.split("\t")
        if len(columns) >= 9:
            table_theme = columns[0].strip() or section_name
            stock_name = columns[2].strip()
            if table_theme and stock_name:
                relations.append(
                    DailyRelation(
                        source_order=len(relations),
                        relation_type="THEME_STOCK",
                        source_theme_name=table_theme,
                        source_stock_name=stock_name,
                        source_stock_code=None,
                        description=section_description,
                        raw_text=line,
                        quality_status="SOURCE_CODE_MISSING",
                    )
                )
            continue
        if table_theme is not None and len(columns) >= 7:
            stock_name = columns[0].strip()
            if stock_name:
                relations.append(
                    DailyRelation(
                        source_order=len(relations),
                        relation_type="THEME_STOCK",
                        source_theme_name=table_theme,
                        source_stock_name=stock_name,
                        source_stock_code=None,
                        description=section_description,
                        raw_text=line,
                        quality_status="SOURCE_CODE_MISSING",
                    )
                )
            continue
        if section_name and not section_description:
            section_description = line
            relations.append(
                DailyRelation(
                    source_order=len(relations),
                    relation_type="DESCRIPTION",
                    source_theme_name=section_name,
                    source_stock_name=None,
                    source_stock_code=None,
                    description=line,
                    raw_text=line,
                    quality_status="OK",
                )
            )
        elif section_name:
            saw_unstructured_narrative = True

    status = "PARSE_PARTIAL" if saw_unstructured_narrative else "OK"
    return tuple(relations), status


def parse_legacy_daily_payload(
    payload: Mapping[str, Any],
    *,
    raw_text: str,
    parser_version: str,
) -> DailyBackfill:
    """Load the single permitted legacy Daily capture without claiming backfill."""

    del parser_version  # persisted by the enclosing import bundle/snapshots
    captured_raw = payload.get("capturedAt")
    if not isinstance(captured_raw, str):
        raise FixtureValidationError(
            "DAILY_CAPTURE_INVALID", "$.capturedAt", "Daily capturedAt이 없습니다."
        )
    try:
        captured_at = datetime.fromisoformat(captured_raw)
    except ValueError as exc:
        raise FixtureValidationError(
            "DAILY_CAPTURE_INVALID", "$.capturedAt", "ISO 8601 시각이 필요합니다."
        ) from exc
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise FixtureValidationError(
            "DAILY_CAPTURE_INVALID", "$.capturedAt", "timezone이 있는 시각이 필요합니다."
        )
    page = payload.get("listPage")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise FixtureValidationError(
            "DAILY_PAGE_INVALID", "$.listPage", "1 이상의 목록 페이지가 필요합니다."
        )
    raw_entries = payload.get("listEntries")
    if not isinstance(raw_entries, list):
        raise FixtureValidationError(
            "DAILY_LIST_INVALID", "$.listEntries", "Daily 목록 배열이 필요합니다."
        )
    raw_hash = sha256_text(raw_text)
    source_content_hash = payload.get("contentHash")
    if not isinstance(source_content_hash, str):
        source_content_hash = None
    list_snapshot = RawSnapshot(
        page_type="DAILY_LIST",
        source_entity_id=f"page:{page}",
        source_url=str(payload.get("sourceUrl") or DAILY_LIST_URL),
        collected_at=captured_at,
        as_of=captured_at,
        raw_hash=raw_hash,
        source_content_hash=source_content_hash,
        raw_payload_text=raw_text,
        raw_format="JSON",
        is_complete=False,
        quality_status="PARTIAL_BACKFILL",
    )

    entries: list[DailyListEntry] = []
    issues: list[QualityIssue] = [
        QualityIssue(
            "DAILY_FEATURED_THEME",
            "B-INFOSTOCK-AUTH",
            "BLOCKER",
            "BACKFILL",
            None,
            None,
            {"messageKo": "검증된 로그인 session이 없어 전체 pagination을 수집하지 않았습니다."},
        ),
        QualityIssue(
            "DAILY_FEATURED_THEME",
            "B-DATA-RIGHTS",
            "BLOCKER",
            "BACKFILL",
            None,
            None,
            {"messageKo": "production 저장·가공 권리 증거가 확인되지 않았습니다."},
        ),
        QualityIssue(
            "DAILY_FEATURED_THEME",
            "PAGINATION_INCOMPLETE",
            "BLOCKER",
            "LIST_PAGE",
            f"page:{page}",
            page,
            {"capturedPages": [page], "nextPage": page + 1},
        ),
    ]
    for position, value in enumerate(raw_entries):
        if not isinstance(value, dict):
            raise FixtureValidationError(
                "DAILY_LIST_INVALID",
                f"$.listEntries[{position}]",
                "Daily 목록 항목은 object여야 합니다.",
            )
        title = str(value.get("title") or "").strip()
        if not title:
            raise FixtureValidationError(
                "DAILY_TITLE_MISSING", f"$.listEntries[{position}].title", "제목이 없습니다."
            )
        published = _date(value.get("date"), f"$.listEntries[{position}].date")
        source_id_value = value.get("sourceId")
        source_id = str(source_id_value).strip() if source_id_value else None
        source_url_value = value.get("sourceUrl")
        source_url = str(source_url_value).strip() if source_url_value else None
        key = derive_daily_post_key(
            source_post_id=source_id, published_date=published, title=title
        )
        status_parts: list[str] = []
        if source_id is None:
            status_parts.append("SOURCE_ID_MISSING")
            issues.append(
                QualityIssue(
                    "DAILY_FEATURED_THEME",
                    "SOURCE_ID_MISSING",
                    "WARNING",
                    "DAILY_POST",
                    key,
                    position,
                    {"title": title},
                )
            )
        if source_url is None:
            status_parts.append("SOURCE_URL_MISSING")
            issues.append(
                QualityIssue(
                    "DAILY_FEATURED_THEME",
                    "SOURCE_URL_MISSING",
                    "WARNING",
                    "DAILY_POST",
                    key,
                    position,
                    {"title": title},
                )
            )
        entries.append(
            DailyListEntry(
                source_order=int(value.get("sourceOrder", position)),
                source_post_key=key,
                source_post_id=source_id,
                source_url=source_url,
                title=title,
                published_date=published,
                source_date=(str(value.get("sourceDate")) if value.get("sourceDate") else None),
                quality_status="|".join(status_parts) or "OK",
            )
        )

    detail_value = payload.get("currentDetail")
    detail = detail_value if isinstance(detail_value, dict) else {}
    detail_title = str(detail.get("title") or "").strip()
    detail_date = _date(detail.get("date"), "$.currentDetail.date") if detail else None
    raw_body_value = detail.get("rawBody")
    raw_body = str(raw_body_value) if isinstance(raw_body_value, str) else None
    matching_key: str | None = None
    if detail_title:
        matching_key = derive_daily_post_key(
            source_post_id=None, published_date=detail_date, title=detail_title
        )
        if not any(entry.source_post_key == matching_key for entry in entries):
            same_date = [
                entry for entry in entries if entry.published_date == detail_date
            ]
            if len(same_date) == 1:
                matching_key = same_date[0].source_post_key
                issues.append(
                    QualityIssue(
                        "DAILY_FEATURED_THEME",
                        "DETAIL_MATCHED_BY_DATE",
                        "WARNING",
                        "DAILY_POST",
                        matching_key,
                        same_date[0].source_order,
                        {
                            "detailTitle": detail_title,
                            "listTitle": same_date[0].title,
                            "messageKo": "source ID가 없어 같은 날짜의 유일한 목록 항목과 본문을 연결했습니다.",
                        },
                    )
                )
    relations: tuple[DailyRelation, ...] = ()
    body_status = "MISSING"
    if raw_body is not None:
        try:
            relations, body_status = parse_daily_body(raw_body)
        except (IndexError, TypeError, ValueError) as exc:
            body_status = "PARSE_FAILED"
            issues.append(
                QualityIssue(
                    "DAILY_FEATURED_THEME",
                    "BODY_PARSE_FAILED",
                    "ERROR",
                    "DAILY_POST",
                    matching_key,
                    None,
                    {"errorType": type(exc).__name__},
                )
            )
    if body_status == "PARSE_PARTIAL":
        issues.append(
            QualityIssue(
                "DAILY_FEATURED_THEME",
                "BODY_PARSE_PARTIAL",
                "WARNING",
                "DAILY_POST",
                matching_key,
                None,
                {"relationCount": len(relations)},
            )
        )

    detail_snapshot: RawSnapshot | None = None
    if matching_key and raw_body is not None:
        detail_snapshot = RawSnapshot(
            page_type="DAILY_DETAIL",
            source_entity_id=matching_key,
            source_url=DAILY_LIST_URL,
            collected_at=captured_at,
            as_of=captured_at,
            raw_hash=raw_hash,
            source_content_hash=source_content_hash,
            raw_payload_text=raw_text,
            raw_format="JSON",
            is_complete=True,
            quality_status=body_status,
        )

    posts: list[DailyPost] = []
    for entry in entries:
        has_body = entry.source_post_key == matching_key and raw_body is not None
        post_relations = relations if has_body else ()
        post_body = raw_body if has_body else None
        post_body_status = body_status if has_body else "MISSING"
        if not has_body:
            issues.append(
                QualityIssue(
                    "DAILY_FEATURED_THEME",
                    "BODY_MISSING",
                    "WARNING",
                    "DAILY_POST",
                    entry.source_post_key,
                    entry.source_order,
                    {"title": entry.title},
                )
            )
        normalized_title = detail_title if has_body and detail_title else entry.title
        normalized_hash = sha256_json(
            {
                "body": post_body,
                "bodyStatus": post_body_status,
                "publishedDate": (
                    entry.published_date.isoformat() if entry.published_date else None
                ),
                "relations": [
                    {
                        "description": relation.description,
                        "rawText": relation.raw_text,
                        "sourceStockCode": relation.source_stock_code,
                        "sourceStockName": relation.source_stock_name,
                        "sourceThemeName": relation.source_theme_name,
                        "type": relation.relation_type,
                    }
                    for relation in post_relations
                ],
                "title": normalized_title,
                "visibility": "VISIBLE",
            }
        )
        posts.append(
            DailyPost(
                source_post_key=entry.source_post_key,
                source_post_id=entry.source_post_id,
                source_url=entry.source_url,
                title=normalized_title,
                published_date=entry.published_date,
                source_date=entry.source_date,
                raw_body=post_body,
                body_hash=sha256_text(post_body) if post_body is not None else None,
                normalized_hash=normalized_hash,
                body_status=post_body_status,  # type: ignore[arg-type]
                visibility_status="VISIBLE",
                relations=post_relations,
                detail_snapshot=detail_snapshot if has_body else None,
            )
        )

    dated = [entry.published_date for entry in entries if entry.published_date]
    pages: tuple[RawSnapshot, ...]
    if detail_snapshot is None:
        pages = (list_snapshot,)
    else:
        pages = (list_snapshot, detail_snapshot)
    return DailyBackfill(
        component_status="BLOCKED",
        pages=pages,
        entries=tuple(entries),
        posts=tuple(posts),
        first_page=page,
        last_page=page,
        next_page=page + 1,
        earliest_date=min(dated) if dated else None,
        latest_date=max(dated) if dated else None,
        coverage_complete=False,
        blockers=("B-INFOSTOCK-AUTH", "B-DATA-RIGHTS"),
        quality_issues=tuple(issues),
    )


@dataclass(frozen=True, slots=True)
class DailyBackfillCursor:
    next_page: int = 1
    completed_pages: tuple[int, ...] = ()
    seen_post_keys: tuple[str, ...] = ()
    complete: bool = False


@dataclass(frozen=True, slots=True)
class DailyBrowserListPage:
    page_number: int
    entries: tuple[Mapping[str, object], ...]
    raw_payload: str
    raw_format: str
    collected_at: datetime
    as_of: datetime
    has_next: bool
    next_page: int | None


@dataclass(frozen=True, slots=True)
class DailyBrowserDetail:
    source_post_key: str
    raw_payload: str
    raw_format: str
    collected_at: datetime
    as_of: datetime


class DailyBrowserSource(Protocol):
    """Adapter boundary implemented by the S6 authenticated browser worker."""

    def fetch_list_page(self, page_number: int) -> DailyBrowserListPage: ...

    def fetch_detail(self, entry: Mapping[str, object]) -> DailyBrowserDetail: ...


@dataclass(frozen=True, slots=True)
class DailyBrowserBatch:
    list_pages: tuple[DailyBrowserListPage, ...]
    details: tuple[DailyBrowserDetail, ...]
    checkpoint: DailyBackfillCursor


def collect_daily_browser_backfill(
    source: DailyBrowserSource,
    checkpoint: DailyBackfillCursor | None = None,
    *,
    auth_verified: bool = False,
    rights_verified: bool = False,
    max_pages: int = 10_000,
) -> DailyBrowserBatch:
    """Execute deterministic pagination/resume only after both external gates."""

    InfostockAccessPolicy.require_daily_browser_collection(
        auth_verified=auth_verified, rights_verified=rights_verified
    )
    cursor = checkpoint or DailyBackfillCursor()
    if cursor.complete:
        return DailyBrowserBatch((), (), cursor)
    pages: list[DailyBrowserListPage] = []
    details: list[DailyBrowserDetail] = []
    completed = list(cursor.completed_pages)
    seen = set(cursor.seen_post_keys)
    page_number = cursor.next_page
    page_hashes: set[str] = set()
    for _ in range(max_pages):
        page = source.fetch_list_page(page_number)
        if page.page_number != page_number:
            raise FixtureValidationError(
                "DAILY_PAGE_CONFLICT",
                "$daily.pageNumber",
                "요청 page와 응답 page가 다릅니다.",
            )
        fingerprint = sha256_text(page.raw_payload)
        if fingerprint in page_hashes:
            raise FixtureValidationError(
                "DAILY_PAGINATION_LOOP",
                "$daily.pagination",
                "동일한 목록 원문이 반복되어 backfill을 중단했습니다.",
            )
        page_hashes.add(fingerprint)
        pages.append(page)
        completed.append(page_number)
        for entry in page.entries:
            title = str(entry.get("title") or "").strip()
            source_id = str(entry.get("sourceId") or "").strip() or None
            published = _date(entry.get("date"), "$daily.entry.date")
            key = derive_daily_post_key(
                source_post_id=source_id, published_date=published, title=title
            )
            if key in seen:
                continue
            detail = source.fetch_detail(entry)
            if detail.source_post_key != key:
                raise FixtureValidationError(
                    "DAILY_DETAIL_CONFLICT",
                    "$daily.detail.sourcePostKey",
                    "목록과 본문의 게시물 식별자가 다릅니다.",
                )
            details.append(detail)
            seen.add(key)
        if not page.has_next:
            return DailyBrowserBatch(
                tuple(pages),
                tuple(details),
                DailyBackfillCursor(
                    next_page=page_number + 1,
                    completed_pages=tuple(completed),
                    seen_post_keys=tuple(sorted(seen)),
                    complete=True,
                ),
            )
        if page.next_page is None or page.next_page <= page_number:
            raise FixtureValidationError(
                "DAILY_CURSOR_INVALID",
                "$daily.nextPage",
                "다음 pagination cursor가 현재 page보다 커야 합니다.",
            )
        page_number = page.next_page
    raise FixtureValidationError(
        "DAILY_PAGE_LIMIT",
        "$daily.pagination",
        f"안전 상한 {max_pages:,}페이지를 초과했습니다.",
    )


def daily_capture_hash(value: Mapping[str, object]) -> str:
    """Stable helper used by browser fixture adapters."""

    return sha256_text(canonical_json(value))
