"""D-14 일일 증분: 창 수집→증분 번들→idempotent 적재, 수정·삭제 감지."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from packages.infostock import (
    INCREMENT_DATASET,
    DailyApiObservation,
    build_daily_increment_bundle,
    classify_collection_error,
    collect_daily_api_backfill,
    import_daily_increment,
)

from .support import ReferenceInfostockStore

TEST_DSN = os.environ.get("INFOSTOCK_TEST_DSN")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = tuple(
    REPOSITORY_ROOT / "infra/migrations" / name
    for name in ("0001_infostock_store.sql", "0005_infostock_increment.sql")
)

requires_dsn = pytest.mark.skipif(
    TEST_DSN is None,
    reason="INFOSTOCK_TEST_DSN의 disposable PostgreSQL 16이 필요합니다.",
)


def _raw(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _detail_html(description: str, *, stock_number: int = 1) -> str:
    code = f"{stock_number:06d}"
    return f"""- 반도체 -<br><br>
{description}<br>
<table>
<tr><th>테마명</th><th>등락률</th><th>종목명</th></tr>
<tr><td><a href="https://new.infostock.co.kr/Theme/ThemeDB/123">반도체</a></td>
<td>+1.00%</td><td><a href="https://new.infostock.co.kr/stockitem?code={code}">종목 {stock_number}</a></td></tr>
</table>"""


class WindowDailyApi:
    """한 창의 목록·본문을 재생하는 결정적 transport."""

    def __init__(
        self,
        *,
        posts: tuple[tuple[str, str, str], ...],
        bodies: dict[str, str],
        now: datetime,
    ) -> None:
        # posts: (id, title, sendDate) 목록 순서 그대로.
        self.posts = posts
        self.bodies = bodies
        self.now = now

    def __call__(
        self, endpoint: str, payload: dict[str, object]
    ) -> DailyApiObservation:
        if endpoint.endswith("/flash/list"):
            if self.posts:
                value: dict[str, object] = {
                    "success": True,
                    "data": {
                        "items": [
                            {
                                "id": source_id,
                                "title": title,
                                "sendDate": send_date,
                                "newsType1": "MARKET_THEME_DAILY",
                            }
                            for source_id, title, send_date in self.posts
                        ],
                        "nextKey": None,
                    },
                }
            else:
                value = {"success": False, "message": "데이터가 없습니다."}
        else:
            value = {
                "success": True,
                "data": {"content": self.bodies[str(payload["id"])]},
            }
        return DailyApiObservation(
            raw_bytes=_raw(value),
            status_code=200,
            content_type="application/json; charset=utf-8",
            collected_at=self.now,
        )


def _collect_window(
    directory: Path,
    *,
    start_date: str,
    end_date: str,
    transport: WindowDailyApi,
) -> None:
    collect_daily_api_backfill(
        directory,
        start_date=start_date,
        end_date=end_date,
        approved=True,
        request_delay_seconds=0.0,
        transport=transport,
    )


def test_collect_window_builds_increment_bundle_deterministically(
    tmp_path: Path,
) -> None:
    transport = WindowDailyApi(
        posts=(("11", "8월 12일 특징 테마", "20260812"),),
        bodies={"11": _detail_html("AI 수요 기대감 등에 상승")},
        now=datetime(2026, 8, 14, 9, 30, tzinfo=UTC),
    )
    _collect_window(
        tmp_path / "window", start_date="20260812", end_date="20260814",
        transport=transport,
    )

    bundle, window = build_daily_increment_bundle(tmp_path / "window")
    again, _ = build_daily_increment_bundle(tmp_path / "window")

    assert bundle.dataset == INCREMENT_DATASET
    assert bundle.expected_theme_count == 0
    assert bundle.details == () and bundle.index_items == ()
    assert bundle.manifest_snapshot.page_type == "DAILY_MANIFEST"
    assert window == (date(2026, 8, 12), date(2026, 8, 14))
    assert bundle.daily.coverage_complete is True
    assert len(bundle.daily.posts) == 1
    assert bundle.input_hash == again.input_hash


def test_import_daily_increment_is_idempotent_on_reference_store(
    tmp_path: Path,
) -> None:
    transport = WindowDailyApi(
        posts=(
            ("12", "8월 13일 특징 테마", "20260813"),
            ("11", "8월 12일 특징 테마", "20260812"),
        ),
        bodies={
            "11": _detail_html("AI 수요 기대감 등에 상승"),
            "12": _detail_html("실적 개선 기대감 등에 상승", stock_number=2),
        },
        now=datetime(2026, 8, 14, 9, 30, tzinfo=UTC),
    )
    _collect_window(
        tmp_path / "window", start_date="20260812", end_date="20260814",
        transport=transport,
    )
    bundle, window = build_daily_increment_bundle(tmp_path / "window")
    store = ReferenceInfostockStore()

    first = import_daily_increment(
        bundle, store, window_start=window[0], window_end=window[1]
    )
    second = import_daily_increment(
        bundle, store, window_start=window[0], window_end=window[1]
    )

    assert first.status == "SUCCEEDED"
    assert first.core_status == "SKIPPED"
    assert first.daily_posts_seen == 2
    assert first.reused is False
    assert second.reused is True
    assert second.run_id == first.run_id
    assert len(store.state.runs) == 1

    with pytest.raises(ValueError):
        import_daily_increment(
            replace(bundle, dataset="infostock-full-sync-with-daily"),
            store,
            window_start=window[0],
            window_end=window[1],
        )


def test_classify_collection_error_maps_auth_and_rate_limit() -> None:
    def _http_error(code: int) -> HTTPError:
        return HTTPError("https://api.example", code, "err", None, None)  # type: ignore[arg-type]

    def _wrapped(code: int) -> RuntimeError:
        error = RuntimeError("Daily API 요청 실패")
        error.__cause__ = _http_error(code)
        return error

    assert classify_collection_error(_http_error(401)) == "AUTH_REQUIRED"
    assert classify_collection_error(_wrapped(403)) == "AUTH_REQUIRED"
    assert classify_collection_error(_wrapped(429)) == "RATE_LIMITED"
    assert classify_collection_error(_wrapped(500)) == "FAILED"
    assert classify_collection_error(RuntimeError("기타")) == "FAILED"


@requires_dsn
def test_increment_apply_revises_hides_in_window_and_reuses(
    tmp_path: Path,
) -> None:
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(TEST_DSN, autocommit=True) as admin:
        admin.execute("DROP SCHEMA IF EXISTS ingest CASCADE")
        admin.execute("DROP SCHEMA IF EXISTS core CASCADE")
        role_exists = admin.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
            ("dayjaview_infostock_writer",),
        ).fetchone()[0]
        if not role_exists:
            admin.execute("CREATE ROLE dayjaview_infostock_writer NOLOGIN")
        for migration in MIGRATIONS:
            admin.execute(migration.read_text(encoding="utf-8"))

    def _scalar(connection: Any, query: str, params: tuple[object, ...] = ()) -> Any:
        with connection.cursor() as cursor:
            cursor.execute(query, params or None)
            row = cursor.fetchone()
            assert row is not None
            return row[0]

    def _apply(directory: Path) -> Any:
        from packages.infostock import PostgresInfostockStore

        bundle, window = build_daily_increment_bundle(directory)
        connection = psycopg.connect(TEST_DSN)
        try:
            return import_daily_increment(
                bundle,
                PostgresInfostockStore(connection),
                window_start=window[0],
                window_end=window[1],
            )
        finally:
            connection.close()

    # 1회차: 창 20260810~20260814에 게시물 3건 (0810은 다음 창 밖이 될 것).
    first_transport = WindowDailyApi(
        posts=(
            ("13", "8월 12일 특징 테마", "20260812"),
            ("12", "8월 13일 특징 테마", "20260813"),
            ("11", "8월 10일 특징 테마", "20260810"),
        ),
        bodies={
            "11": _detail_html("정책 지원 기대감 등에 상승", stock_number=1),
            "12": _detail_html("실적 개선 기대감 등에 상승", stock_number=2),
            "13": _detail_html("AI 수요 기대감 등에 상승", stock_number=3),
        },
        now=datetime(2026, 8, 14, 9, 30, tzinfo=UTC),
    )
    _collect_window(
        tmp_path / "run1", start_date="20260810", end_date="20260814",
        transport=first_transport,
    )
    first = _apply(tmp_path / "run1")
    assert first.status == "SUCCEEDED"
    assert first.core_status == "SKIPPED"
    assert first.reused is False
    assert first.daily_post_revisions_created == 3

    replay = _apply(tmp_path / "run1")
    assert replay.reused is True and replay.run_id == first.run_id

    check = psycopg.connect(TEST_DSN)
    try:
        assert (
            _scalar(
                check,
                """
                SELECT count(*) FROM ingest.infostock_import_runs
                 WHERE run_type = 'INCREMENTAL' AND core_status = 'SKIPPED'
                """,
            )
            == 1
        )

        # 2회차: 창 20260812~20260814. 13은 본문 수정, 12는 창 안에서 사라짐,
        # 11은 창 밖이라 판단 대상이 아니다.
        second_transport = WindowDailyApi(
            posts=(("13", "8월 12일 특징 테마", "20260812"),),
            bodies={"13": _detail_html("AI 수요 급증 재확인 등에 상승", stock_number=3)},
            now=datetime(2026, 8, 15, 9, 30, tzinfo=UTC),
        )
        _collect_window(
            tmp_path / "run2", start_date="20260812", end_date="20260814",
            transport=second_transport,
        )
        second = _apply(tmp_path / "run2")
        assert second.status == "SUCCEEDED"
        assert second.reused is False

        def _post_state(source_id: str) -> tuple[str, int]:
            return (
                _scalar(
                    check,
                    """
                    SELECT visibility_status FROM core.infostock_daily_posts
                     WHERE source_post_id = %s
                    """,
                    (source_id,),
                ),
                _scalar(
                    check,
                    """
                    SELECT max(revision.revision_no)
                      FROM core.infostock_daily_post_revisions AS revision
                      JOIN core.infostock_daily_posts AS post
                        ON post.daily_post_id = revision.daily_post_id
                     WHERE post.source_post_id = %s
                    """,
                    (source_id,),
                ),
            )

        assert _post_state("13") == ("VISIBLE", 2)  # 본문 수정 → revision 2
        assert _post_state("12") == ("NOT_VISIBLE", 2)  # 창 안 소실 → 숨김 revision
        assert _post_state("11") == ("VISIBLE", 1)  # 창 밖 → 판단하지 않음

        # 3회차: 같은 창이 비어서 돌아오면 창 안 게시물이 전부 소실로 남는다.
        third_transport = WindowDailyApi(
            posts=(),
            bodies={},
            now=datetime(2026, 8, 16, 9, 30, tzinfo=UTC),
        )
        _collect_window(
            tmp_path / "run3", start_date="20260812", end_date="20260814",
            transport=third_transport,
        )
        third = _apply(tmp_path / "run3")
        assert third.status == "SUCCEEDED"
        assert third.daily_post_revisions_created == 1  # 13 숨김
        assert _post_state("13") == ("NOT_VISIBLE", 3)
        assert _post_state("11") == ("VISIBLE", 1)
    finally:
        check.close()
