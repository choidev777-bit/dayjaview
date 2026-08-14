from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from packages.infostock import (
    DailyBackfillCursor,
    DailyBrowserDetail,
    DailyBrowserListPage,
    DataRightsBlockedError,
    FixtureValidationError,
    collect_daily_browser_backfill,
    parse_daily_body,
)
from packages.infostock.daily import derive_daily_post_key


class FakeDailySource:
    def __init__(self, pages: dict[int, DailyBrowserListPage]) -> None:
        self.pages = pages
        self.list_calls: list[int] = []
        self.detail_calls: list[str] = []

    def fetch_list_page(self, page_number: int) -> DailyBrowserListPage:
        self.list_calls.append(page_number)
        return self.pages[page_number]

    def fetch_detail(self, entry: dict[str, object]) -> DailyBrowserDetail:
        title = str(entry["title"])
        source_id = str(entry.get("sourceId") or "") or None
        published = cast(str, entry["date"])
        key = derive_daily_post_key(
            source_post_id=source_id,
            published_date=datetime.fromisoformat(published).date(),
            title=title,
        )
        self.detail_calls.append(key)
        now = datetime(2026, 8, 14, tzinfo=UTC)
        return DailyBrowserDetail(key, f"detail:{key}", "HTML", now, now)


def _page(
    number: int,
    entries: tuple[dict[str, object], ...],
    *,
    has_next: bool,
    next_page: int | None,
    raw: str | None = None,
) -> DailyBrowserListPage:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    return DailyBrowserListPage(
        page_number=number,
        entries=entries,
        raw_payload=raw or f"page:{number}",
        raw_format="HTML",
        collected_at=now,
        as_of=now,
        has_next=has_next,
        next_page=next_page,
    )


def test_daily_body_preserves_raw_projection_and_extracts_relations() -> None:
    body = """- 반도체 장비 -

AI 투자 확대로 관련주 상승

테마명\t등락률\t종목명\t종가(원)\t등락률\t거래량(주)\t시가(원)\t고가(원)\t저가(원)
반도체 장비\t+1.00%\t장비회사A\t1,000\t+2.00%\t10\t900\t1,100\t890
장비회사B\t2,000\t+1.00%\t20\t1,900\t2,100\t1,850
"""

    relations, status = parse_daily_body(body)

    assert status == "OK"
    assert [relation.relation_type for relation in relations] == [
        "DESCRIPTION",
        "THEME_STOCK",
        "THEME_STOCK",
    ]
    assert relations[0].description == "AI 투자 확대로 관련주 상승"
    assert relations[1].source_theme_name == "반도체 장비"
    assert relations[1].source_stock_name == "장비회사A"
    assert relations[1].source_stock_code is None
    assert relations[1].quality_status == "SOURCE_CODE_MISSING"


def test_daily_live_fetch_is_not_called_before_auth_and_rights_gates() -> None:
    source = FakeDailySource({})

    with pytest.raises(DataRightsBlockedError) as raised:
        collect_daily_browser_backfill(source)

    assert raised.value.blocker == "B-INFOSTOCK-AUTH"
    assert source.list_calls == []
    assert source.detail_calls == []


def test_daily_pagination_full_backfill_and_resume_checkpoint() -> None:
    first = {"sourceId": "10", "title": "첫 게시물", "date": "2026-08-13"}
    second = {"sourceId": "9", "title": "둘째 게시물", "date": "2026-08-12"}
    source = FakeDailySource(
        {
            1: _page(1, (first,), has_next=True, next_page=2),
            2: _page(2, (second,), has_next=False, next_page=None),
        }
    )

    full = collect_daily_browser_backfill(
        source, auth_verified=True, rights_verified=True
    )

    assert source.list_calls == [1, 2]
    assert len(full.details) == 2
    assert full.checkpoint.complete is True
    assert full.checkpoint.completed_pages == (1, 2)
    assert full.checkpoint.next_page == 3

    first_key = full.details[0].source_post_key
    resumed_source = FakeDailySource(
        {2: _page(2, (second,), has_next=False, next_page=None)}
    )
    resumed = collect_daily_browser_backfill(
        resumed_source,
        DailyBackfillCursor(
            next_page=2,
            completed_pages=(1,),
            seen_post_keys=(first_key,),
        ),
        auth_verified=True,
        rights_verified=True,
    )

    assert resumed_source.list_calls == [2]
    assert len(resumed.details) == 1
    assert resumed.checkpoint.completed_pages == (1, 2)
    assert resumed.checkpoint.complete is True


def test_daily_pagination_loop_and_invalid_cursor_fail_explicitly() -> None:
    entry = {"sourceId": "10", "title": "게시물", "date": "2026-08-13"}
    loop_source = FakeDailySource(
        {
            1: _page(1, (entry,), has_next=True, next_page=2, raw="same"),
            2: _page(2, (), has_next=True, next_page=3, raw="same"),
        }
    )

    with pytest.raises(FixtureValidationError) as loop_error:
        collect_daily_browser_backfill(
            loop_source, auth_verified=True, rights_verified=True
        )
    assert loop_error.value.code == "DAILY_PAGINATION_LOOP"

    invalid_source = FakeDailySource(
        {1: _page(1, (), has_next=True, next_page=1)}
    )
    with pytest.raises(FixtureValidationError) as cursor_error:
        collect_daily_browser_backfill(
            invalid_source, auth_verified=True, rights_verified=True
        )
    assert cursor_error.value.code == "DAILY_CURSOR_INVALID"
