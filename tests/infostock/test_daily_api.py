from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.infostock import (
    DailyApiObservation,
    FixtureValidationError,
    collect_daily_api_backfill,
    load_daily_api_backfill,
    parse_daily_html_body,
)


def _raw(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _detail_html(number: int) -> str:
    code = f"{number:06d}"
    return f"""- 반도체 -<br><br>
AI 수요 증가 기대감 등에 상승<br>
<table>
<tr><th>테마명</th><th>등락률</th><th>종목명</th></tr>
<tr><td><a href="https://new.infostock.co.kr/Theme/ThemeDB/123">반도체</a></td>
<td>+1.00%</td><td><a href="https://new.infostock.co.kr/stockitem?code={code}">종목 {number}</a></td></tr>
</table>"""


class FakeDailyApi:
    def __init__(self, *, fail_detail: str | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.fail_detail = fail_detail
        self.now = datetime(2026, 8, 14, 1, 2, 3, tzinfo=UTC)

    def __call__(
        self, endpoint: str, payload: dict[str, object]
    ) -> DailyApiObservation:
        self.calls.append((endpoint, dict(payload)))
        if endpoint.endswith("/flash/list"):
            cursor = payload.get("nextKey")
            if cursor is None:
                value = {
                    "success": True,
                    "data": {
                        "items": [
                            {
                                "id": "3",
                                "title": "세 번째",
                                "sendDate": "20260814",
                                "sendTime": "170000",
                                "newsType1": "MARKET_THEME_DAILY",
                            },
                            {
                                "id": "2",
                                "title": "두 번째",
                                "sendDate": "20260813",
                                "sendTime": "170000",
                                "newsType1": "MARKET_THEME_DAILY",
                            },
                        ],
                        "nextKey": "CURSOR-2",
                    },
                }
            else:
                assert cursor == "CURSOR-2"
                value = {
                    "success": True,
                    "data": {
                        "items": [
                            {
                                "id": "1",
                                "title": "첫 번째",
                                "sendDate": "20260812",
                                "sendTime": "170000",
                                "newsType1": "MARKET_THEME_DAILY",
                            }
                        ],
                        "nextKey": None,
                    },
                }
        else:
            source_id = str(payload["id"])
            if source_id == self.fail_detail:
                raise RuntimeError("synthetic detail failure")
            value = {
                "success": True,
                "data": {"content": _detail_html(int(source_id))},
            }
        return DailyApiObservation(
            raw_bytes=_raw(value),
            status_code=200,
            content_type="application/json; charset=utf-8",
            collected_at=self.now,
        )


class NullCursorDailyApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.now = datetime(2026, 8, 14, 1, 2, 3, tzinfo=UTC)

    def __call__(
        self, endpoint: str, payload: dict[str, object]
    ) -> DailyApiObservation:
        self.calls.append((endpoint, dict(payload)))
        if endpoint.endswith("/flash/list"):
            if payload["endDate"] == "20260814":
                value = {
                    "success": True,
                    "data": {
                        "items": [
                            {
                                "id": "1",
                                "title": "첫 번째",
                                "sendDate": "20260814",
                                "newsType1": "MARKET_THEME_DAILY",
                            }
                        ],
                        "nextKey": "20260813null",
                    },
                }
            else:
                assert payload["endDate"] == "20260813"
                assert "nextKey" not in payload
                value = {"success": False, "message": "데이터가 없습니다."}
        else:
            value = {"success": True, "data": {"content": _detail_html(1)}}
        return DailyApiObservation(
            raw_bytes=_raw(value),
            status_code=200,
            content_type="application/json; charset=utf-8",
            collected_at=self.now,
        )


def test_html_projection_preserves_description_theme_stock_and_code() -> None:
    relations, status = parse_daily_html_body(_detail_html(5930))

    assert status == "OK"
    assert relations[0].relation_type == "DESCRIPTION"
    stock = next(item for item in relations if item.relation_type == "THEME_STOCK")
    assert stock.source_theme_name == "반도체"
    assert stock.source_stock_name == "종목 5930"
    assert stock.source_stock_code == "005930"
    assert stock.description == "AI 수요 증가 기대감 등에 상승"


def _quote_row(name: str, code: str, cells: tuple[str, ...]) -> str:
    columns = "".join(f"<td>{value}</td>" for value in cells)
    return (
        f'<tr><td><a href="https://new.infostock.co.kr/stockitem?code={code}">'
        f"{name}</a></td>{columns}</tr>"
    )


def _sectioned_html() -> str:
    """섹션 상세 문단과 시세 표를 가진 최신 형식."""

    return f"""- 2차전지 등 -<br>
K-배터리, 2분기 실적 반등 기대감 등에 상승<br>
▷언론에 따르면, 배터리 업계가 2분기 실적 반등의 기반을 마련.<br>
▷아울러 전기차 캐즘이 예상보다 빠르게 종식될 것이라는 기대감.<br>
<table>
<tr><th>테마명</th><th>등락률</th><th>종목명</th><th>종가(원)</th><th>등락률</th>
<th>거래량(주)</th><th>시가(원)</th><th>고가(원)</th><th>저가(원)</th></tr>
<tr><td><a href="https://new.infostock.co.kr/Theme/ThemeDB/1">2차전지</a></td>
<td>+10.86%</td>
<td><a href="https://new.infostock.co.kr/stockitem?code=065350">신성델타테크</a></td>
<td>34,000</td><td>+21.00%</td><td>480,880</td><td>27,350</td><td>34,000</td>
<td>27,350</td></tr>
{_quote_row("에코프로", "086520",
            ("118,000", "-23.69%", "2,874,885", "95,600", "118,700", "95,600"))}
</table>
- 로봇 등 -<br>
아틀라스, 韓 공급망 생태계 구축 추진 소식 등에 상승<br>
▷교보증권은 휴머노이드 OEM 양산이 26년 말 본격화될 것으로 분석.<br>
<table>
<tr><th>테마명</th><th>등락률</th><th>종목명</th><th>종가(원)</th><th>등락률</th>
<th>거래량(주)</th></tr>
<tr><td><a href="https://new.infostock.co.kr/Theme/ThemeDB/2">로봇</a></td>
<td>+9.24%</td>
<td><a href="https://new.infostock.co.kr/stockitem?code=065350">신성델타테크</a></td>
<td>34,000</td><td>+21.00%</td><td>480,880</td></tr>
</table>"""


def test_html_projection_keeps_every_section_detail_paragraph() -> None:
    relations, status = parse_daily_html_body(_sectioned_html())

    assert status == "OK"
    details = [item for item in relations if item.relation_type == "SECTION_DETAIL"]
    assert [(item.source_theme_name, item.paragraph_no) for item in details] == [
        ("2차전지 등", 1),
        ("2차전지 등", 2),
        ("로봇 등", 1),
    ]
    assert details[0].raw_text.startswith("▷언론에 따르면")
    assert details[0].description == "K-배터리, 2분기 실적 반등 기대감 등에 상승"
    headlines = [item for item in relations if item.relation_type == "DESCRIPTION"]
    assert [item.paragraph_no for item in headlines] == [0, 0]


def test_html_projection_splits_quote_table_into_values() -> None:
    relations, _ = parse_daily_html_body(_sectioned_html())
    rows = [item for item in relations if item.relation_type == "THEME_STOCK"]

    first = rows[0]
    assert first.source_stock_code == "065350"
    assert first.theme_change_rate == "10.86"
    assert (first.close_price, first.change_rate) == (34000, "21.00")
    assert (first.trade_volume, first.open_price) == (480880, 27350)
    assert (first.high_price, first.low_price) == (34000, 27350)
    assert rows[1].change_rate == "-23.69"


def test_html_projection_supports_table_without_open_high_low() -> None:
    relations, _ = parse_daily_html_body(_sectioned_html())
    row = [item for item in relations if item.relation_type == "THEME_STOCK"][-1]

    assert row.source_theme_name == "로봇"
    assert (row.close_price, row.change_rate, row.trade_volume) == (
        34000,
        "21.00",
        480880,
    )
    assert (row.open_price, row.high_price, row.low_price) == (None, None, None)


def test_html_projection_links_each_table_to_its_own_section() -> None:
    relations, _ = parse_daily_html_body(_sectioned_html())
    rows = [item for item in relations if item.relation_type == "THEME_STOCK"]

    assert all(item.description for item in rows)
    assert rows[0].description == "K-배터리, 2분기 실적 반등 기대감 등에 상승"
    assert rows[-1].description == "아틀라스, 韓 공급망 생태계 구축 추진 소식 등에 상승"


def test_html_projection_preserves_table_free_narrative_format() -> None:
    relations, status = parse_daily_html_body(
        "철강 주요종목 : POSCO 3분기 실적 호재로 동반 상승.<br>"
        "은행주 : 뉴욕發 호재 등으로 상승.<br>"
        "금일 국내증권시장은 글로벌 신용위기 재발 위협으로 하락 마감하였음.<br>"
    )

    assert status == "PARSE_PARTIAL"
    assert [(item.relation_type, item.source_theme_name) for item in relations] == [
        ("DESCRIPTION", "철강 주요종목"),
        ("DESCRIPTION", "은행주"),
        ("SECTION_DETAIL", "테마시황"),
    ]
    assert relations[0].description == "POSCO 3분기 실적 호재로 동반 상승."
    assert relations[2].raw_text.startswith("금일 국내증권시장은")


def test_html_projection_leaves_quote_fields_empty_on_unknown_layout() -> None:
    relations, _ = parse_daily_html_body(
        '- 반도체 -<br>AI 수요 증가 등에 상승<br><table>'
        '<tr><td><a href="https://new.infostock.co.kr/stockitem?code=005930">'
        "삼성전자</a></td><td>일부</td><td>미상</td></tr></table>"
    )
    row = next(item for item in relations if item.relation_type == "THEME_STOCK")

    assert row.close_price is None
    assert row.change_rate is None
    assert row.raw_text == "삼성전자\t일부\t미상"


def test_collector_resume_loader_lineage_and_hash_integrity(tmp_path: Path) -> None:
    source = FakeDailyApi()
    manifest = collect_daily_api_backfill(
        tmp_path,
        start_date="20000101",
        end_date="20260814",
        approved=True,
        page_size=100,
        request_delay_seconds=0,
        retry_delays=(),
        transport=source,
    )

    assert manifest["coverageComplete"] is True
    assert manifest["postsDiscovered"] == 3
    assert len(source.calls) == 5
    before_resume = list(source.calls)
    assert (
        collect_daily_api_backfill(
            tmp_path,
            start_date="20000101",
            end_date="20260814",
            approved=True,
            page_size=100,
            request_delay_seconds=0,
            retry_delays=(),
            transport=source,
        )["coverageComplete"]
        is True
    )
    assert source.calls == before_resume

    daily, hashes = load_daily_api_backfill(tmp_path)
    assert daily.component_status == "COMPLETE"
    assert daily.coverage_complete is True
    assert daily.blockers == ()
    assert (daily.first_page, daily.last_page, daily.next_page) == (1, 2, None)
    assert len(daily.entries) == 3
    assert daily.body_count == 3
    assert daily.earliest_date.isoformat() == "2026-08-12"
    assert daily.latest_date.isoformat() == "2026-08-14"
    assert len(daily.pages) == 6
    assert daily.pages[0].page_type == "DAILY_MANIFEST"
    assert daily.posts[0].source_post_id == "3"
    assert daily.posts[0].source_url == (
        "https://infostock.co.kr/Theme/DailyFeaturedTheme?sendDate=20260814"
    )
    assert all(item.parser_version == "infostock-daily-api/1.0.0" for item in daily.pages)
    assert len(hashes) == 6

    manifest_path = tmp_path / "manifest.json"
    original_manifest = manifest_path.read_bytes()
    invalid_manifest = json.loads(original_manifest)
    invalid_manifest["posts"]["3"]["bodyHash"] = "0" * 64
    manifest_path.write_text(json.dumps(invalid_manifest), encoding="utf-8")
    with pytest.raises(FixtureValidationError, match="DAILY_BODY_HASH_MISMATCH"):
        load_daily_api_backfill(tmp_path)
    manifest_path.write_bytes(original_manifest)

    detail_path = tmp_path / "details/3.json"
    detail_path.write_bytes(detail_path.read_bytes() + b" ")
    with pytest.raises(FixtureValidationError, match="DAILY_HASH_MISMATCH"):
        load_daily_api_backfill(tmp_path)


def test_collector_requires_approval_and_reports_failed_detail(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="--approved"):
        collect_daily_api_backfill(
            tmp_path,
            start_date="20000101",
            end_date="20260814",
        )

    source = FakeDailyApi(fail_detail="2")
    manifest = collect_daily_api_backfill(
        tmp_path,
        start_date="20000101",
        end_date="20260814",
        approved=True,
        page_size=100,
        request_delay_seconds=0,
        retry_delays=(),
        transport=source,
    )

    assert manifest["coverageComplete"] is False
    assert set(manifest["failures"]) == {"2"}
    daily, _ = load_daily_api_backfill(tmp_path)
    assert daily.component_status == "PARTIAL"
    assert any(issue.issue_code == "DETAIL_FETCH_FAILED" for issue in daily.quality_issues)


def test_collector_falls_back_from_null_cursor_to_date_window(tmp_path: Path) -> None:
    source = NullCursorDailyApi()

    manifest = collect_daily_api_backfill(
        tmp_path,
        start_date="20000101",
        end_date="20260814",
        approved=True,
        request_delay_seconds=0,
        retry_delays=(),
        transport=source,
    )

    assert manifest["coverageComplete"] is True
    assert manifest["postsDiscovered"] == 1
    assert len(manifest["pages"]) == 2
    assert manifest["pages"][0]["apiNextKey"] == "20260813null"
    assert manifest["pages"][0]["continuationEndDate"] == "20260813"
    assert manifest["pages"][1]["itemCount"] == 0
    assert source.calls[1][1]["endDate"] == "20260813"
    daily, _ = load_daily_api_backfill(tmp_path)
    assert daily.component_status == "COMPLETE"
