from __future__ import annotations

import copy
import os
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from packages.infostock import (
    CommittedFixturePolicy,
    ExistingCollectionPolicy,
    PostgresInfostockStore,
    SnapshotConflictError,
    import_bundle,
    load_committed_fixture,
    load_existing_collection,
    parse_fixture_payload,
)
from packages.infostock.hashing import canonical_json, sha256_json, sha256_text
from packages.infostock.models import (
    DailyBackfill,
    DailyListEntry,
    DailyPost,
    DailyRelation,
    RawSnapshot,
)

from .generate_fixture import build_fixture_payload
from .support import move_observation_time, rehash_fixture

TEST_DSN = os.environ.get("INFOSTOCK_TEST_DSN")
COLLECTION_DIR = os.environ.get("INFOSTOCK_EXISTING_IMPORT_DIR")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPOSITORY_ROOT / "infra/migrations/0001_infostock_store.sql"
FIXTURE = Path(__file__).parent / "fixtures" / "infostock-280.synthetic.json"

pytestmark = pytest.mark.skipif(
    TEST_DSN is None or COLLECTION_DIR is None,
    reason=(
        "INFOSTOCK_TEST_DSN의 disposable PostgreSQL 16과 "
        "INFOSTOCK_EXISTING_IMPORT_DIR의 기존 수집본이 모두 필요합니다."
    ),
)


def _scalar(
    connection: Any,
    query: str,
    params: tuple[object, ...] | None = None,
) -> Any:
    cursor = (
        connection.execute(query)
        if params is None
        else connection.execute(query, params)
    )
    row = cursor.fetchone()
    assert row is not None
    return row[0]


def _complete_daily_bundle(
    base: Any,
    *,
    observed_at: datetime,
    bodies: tuple[tuple[str, str, str], ...],
) -> Any:
    list_payload = {
        "page": 1,
        "hasNext": False,
        "entries": [
            {"sourceId": source_id, "title": title, "date": "2026-08-14"}
            for source_id, title, _ in bodies
        ],
    }
    list_text = canonical_json(list_payload)
    list_hash = sha256_text(list_text)
    list_snapshot = RawSnapshot(
        page_type="DAILY_LIST",
        source_entity_id="page:1",
        source_url="https://infostock.co.kr/Theme/DailyFeaturedTheme?page=1",
        collected_at=observed_at,
        as_of=observed_at,
        raw_hash=list_hash,
        source_content_hash=list_hash,
        raw_payload_text=list_text,
        raw_format="JSON",
        is_complete=True,
    )
    entries: list[DailyListEntry] = []
    posts: list[DailyPost] = []
    pages: list[RawSnapshot] = [list_snapshot]
    for order, (source_id, title, body) in enumerate(bodies):
        key = f"source:{source_id}"
        source_url = f"https://infostock.co.kr/Theme/DailyFeaturedTheme/{source_id}"
        detail_text = canonical_json(
            {"sourceId": source_id, "title": title, "body": body}
        )
        detail_hash = sha256_text(detail_text)
        detail_snapshot = RawSnapshot(
            page_type="DAILY_DETAIL",
            source_entity_id=key,
            source_url=source_url,
            collected_at=observed_at,
            as_of=observed_at,
            raw_hash=detail_hash,
            source_content_hash=detail_hash,
            raw_payload_text=detail_text,
            raw_format="JSON",
            is_complete=True,
        )
        relation = DailyRelation(
            source_order=0,
            relation_type="THEME_STOCK",
            source_theme_name="합성 테마 001",
            source_stock_name="현재 관련주 A-001",
            source_stock_code="100001",
            description=body,
            raw_text=body,
            quality_status="OK",
        )
        normalized_hash = sha256_json(
            {
                "body": body,
                "publishedDate": "2026-08-14",
                "relations": [
                    {
                        "description": body,
                        "sourceStockCode": "100001",
                        "sourceStockName": "현재 관련주 A-001",
                        "sourceThemeName": "합성 테마 001",
                    }
                ],
                "title": title,
                "visibility": "VISIBLE",
            }
        )
        entries.append(
            DailyListEntry(
                source_order=order,
                source_post_key=key,
                source_post_id=source_id,
                source_url=source_url,
                title=title,
                published_date=date(2026, 8, 14),
                source_date="2026. 08. 14",
                quality_status="OK",
            )
        )
        posts.append(
            DailyPost(
                source_post_key=key,
                source_post_id=source_id,
                source_url=source_url,
                title=title,
                published_date=date(2026, 8, 14),
                source_date="2026. 08. 14",
                raw_body=body,
                body_hash=sha256_text(body),
                normalized_hash=normalized_hash,
                body_status="OK",
                visibility_status="VISIBLE",
                relations=(relation,),
                detail_snapshot=detail_snapshot,
            )
        )
        pages.append(detail_snapshot)
    daily = DailyBackfill(
        component_status="COMPLETE",
        pages=tuple(pages),
        entries=tuple(entries),
        posts=tuple(posts),
        first_page=1,
        last_page=1,
        next_page=None,
        earliest_date=date(2026, 8, 14),
        latest_date=date(2026, 8, 14),
        coverage_complete=True,
        blockers=(),
        quality_issues=(),
    )
    dataset_hash = sha256_json(
        {
            "core": base.dataset_hash,
            "daily": [snapshot.raw_hash for snapshot in pages],
        }
    )
    return replace(
        base,
        dataset=f"{base.dataset}-daily-complete",
        dataset_hash=dataset_hash,
        input_hash=sha256_json(
            {
                "datasetHash": dataset_hash,
                "observedAt": observed_at.isoformat(),
            }
        ),
        daily=daily,
    )


def test_postgres16_actual_full_import_daily_boundary_and_revision_contract() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert TEST_DSN is not None
    assert COLLECTION_DIR is not None

    with psycopg.connect(TEST_DSN, autocommit=True) as migration_connection:
        assert _scalar(migration_connection, "SHOW server_version_num") >= "160000"
        existing = migration_connection.execute(
            """
            SELECT to_regclass('ingest.infostock_import_runs'),
                   to_regclass('core.infostock_themes')
            """
        ).fetchone()
        assert existing == (None, None), "빈 disposable PostgreSQL database가 필요합니다."
        role_exists = _scalar(
            migration_connection,
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
            ("dayjaview_infostock_writer",),
        )
        if not role_exists:
            migration_connection.execute("CREATE ROLE dayjaview_infostock_writer NOLOGIN")
        migration_connection.execute(MIGRATION.read_text(encoding="utf-8"))
        privileges = migration_connection.execute(
            """
            SELECT has_schema_privilege(
                       'dayjaview_infostock_writer', 'ingest', 'USAGE'
                   ),
                   has_schema_privilege(
                       'dayjaview_infostock_writer', 'ingest', 'CREATE'
                   ),
                   has_table_privilege(
                       'dayjaview_infostock_writer',
                       'ingest.infostock_import_runs', 'UPDATE'
                   ),
                   has_table_privilege(
                       'dayjaview_infostock_writer',
                       'ingest.infostock_source_blobs', 'UPDATE'
                   ),
                   has_table_privilege(
                       'dayjaview_infostock_writer',
                       'core.infostock_themes', 'DELETE'
                   )
            """
        ).fetchone()
        assert privileges == (True, False, True, False, False)

    actual = load_existing_collection(
        Path(COLLECTION_DIR), ExistingCollectionPolicy()
    )
    with psycopg.connect(TEST_DSN) as connection:
        store = PostgresInfostockStore(connection)
        first = import_bundle(actual, store)
        second = import_bundle(actual, store)

        assert first.status == "PARTIAL"
        assert first.core_status == "COMPLETE"
        assert first.daily_status == "BLOCKED"
        assert first.blockers == ("B-INFOSTOCK-AUTH",)
        assert first.themes_imported == 280
        assert first.history_rows_seen == 39_696
        assert first.related_stocks_seen == 6_629
        assert first.leaders_seen == 65_526
        assert first.historical_memberships_seen == 652_241
        assert first.daily_list_entries_seen == 5
        assert first.daily_posts_seen == 5
        assert first.daily_bodies_seen == 1
        assert first.daily_relations_seen == 232
        assert first.snapshots_linked == 284
        assert first.theme_revisions_created == 280
        assert first.membership_revisions_created == 6_629
        assert first.history_revisions_created == 39_696
        assert first.history_leaders_created == 65_526
        assert first.history_memberships_created == 652_241
        assert first.daily_post_revisions_created == 5
        assert first.quality_issues_created == 8_198
        assert first.reused is False
        assert second.run_id == first.run_id
        assert second.reused is True

        expected_counts = {
            "ingest.infostock_import_runs": 1,
            "ingest.infostock_source_blobs": 283,
            "ingest.infostock_source_snapshots": 284,
            "ingest.infostock_import_run_snapshots": 284,
            "ingest.infostock_quality_issues": 8_198,
            "core.infostock_themes": 280,
            "core.infostock_theme_revisions": 280,
            "core.infostock_stocks": 2_673,
            "core.infostock_theme_stock_memberships": 6_629,
            "core.infostock_theme_history": 39_696,
            "core.infostock_theme_history_leaders": 65_526,
            "core.infostock_theme_history_memberships": 652_241,
            "core.infostock_daily_posts": 5,
            "core.infostock_daily_post_revisions": 5,
            "core.infostock_daily_relations": 232,
            "ingest.infostock_daily_list_entries": 5,
        }
        for table, expected in expected_counts.items():
            assert _scalar(connection, f"SELECT count(*) FROM {table}") == expected
        assert _scalar(
            connection,
            "SELECT sum(octet_length(convert_to(raw_payload_text, 'UTF8'))) "
            "FROM ingest.infostock_source_blobs",
        ) == 165_275_696
        assert _scalar(
            connection,
            "SELECT count(*) FROM core.infostock_theme_history "
            "WHERE quality_status = 'SOURCE_DUPLICATE'",
        ) == 4
        assert _scalar(
            connection,
            "SELECT count(*) FROM core.infostock_theme_history "
            "WHERE quality_status = 'DUPLICATE_GROUP_HEAD'",
        ) == 4
        assert _scalar(
            connection,
            "SELECT count(*) FROM core.infostock_theme_history_leaders "
            "WHERE source_stock_code IS NULL AND stock_id IS NULL",
        ) == 90
        assert _scalar(
            connection,
            "SELECT count(*) FROM core.infostock_theme_history_memberships "
            "WHERE source_stock_code IS NULL AND stock_id IS NULL",
        ) == 7_498
        assert _scalar(
            connection,
            "SELECT count(*) FROM ingest.infostock_quality_issues "
            "WHERE issue_code = 'HISTORICAL_MEMBERSHIP_FIELD_MISSING'",
        ) == 274
        assert _scalar(
            connection,
            "SELECT count(*) FROM ingest.infostock_source_snapshots "
            "WHERE quality_status = 'SOURCE_HASH_UNVERIFIED'",
        ) == 2
        assert _scalar(
            connection,
            "SELECT count(*) FROM core.infostock_stocks WHERE stock_code = '0015N0'",
        ) == 1
        assert _scalar(
            connection,
            "SELECT count(*) FROM core.infostock_theme_history_leaders "
            "WHERE source_stock_name LIKE '0015N0-%' AND source_stock_code IS NULL",
        ) >= 1
        component_rows = connection.execute(
            """
            SELECT component, status, discovered_count, imported_count,
                   page_count, body_count, relation_count, blockers
              FROM ingest.infostock_sync_components
             WHERE import_run_id = %s ORDER BY component
            """,
            (first.run_id,),
        ).fetchall()
        assert component_rows[0][:7] == (
            "DAILY_FEATURED_THEME",
            "BLOCKED",
            5,
            5,
            1,
            1,
            232,
        )
        assert tuple(component_rows[0][7]) == ("B-INFOSTOCK-AUTH",)
        assert component_rows[1][:4] == ("THEME_DATABASE", "COMPLETE", 280, 280)
        checkpoint = connection.execute(
            """
            SELECT status, first_page, last_page, next_page,
                   listed_count, detailed_count, coverage_complete, blockers
              FROM ingest.infostock_daily_backfill_checkpoints
             WHERE import_run_id = %s
            """,
            (first.run_id,),
        ).fetchone()
        assert checkpoint is not None
        assert checkpoint[:7] == ("BLOCKED", 1, 1, 2, 5, 1, False)
        connection.commit()

        fixture = load_committed_fixture(
            FIXTURE, CommittedFixturePolicy(REPOSITORY_ROOT)
        )
        fixture_first = import_bundle(fixture, store)
        assert fixture_first.themes_imported == 280

        conflicting_payload = build_fixture_payload()
        conflicting_payload["detailSnapshots"][0]["rawPayload"]["data"][
            "theme"
        ]["outline"] = "같은 관측 시각의 충돌 설명"
        rehash_fixture(conflicting_payload, 0)
        conflicting = parse_fixture_payload(conflicting_payload)
        run_count_before = _scalar(
            connection, "SELECT count(*) FROM ingest.infostock_import_runs"
        )
        connection.commit()
        with pytest.raises(SnapshotConflictError):
            import_bundle(conflicting, store)
        assert _scalar(
            connection, "SELECT count(*) FROM ingest.infostock_import_runs"
        ) == run_count_before
        connection.commit()

        daily_complete = _complete_daily_bundle(
            fixture,
            observed_at=datetime(2026, 8, 16, tzinfo=UTC),
            bodies=(
                ("SYN-2", "Daily 합성 둘", "최초 본문 둘"),
                ("SYN-1", "Daily 합성 하나", "최초 본문 하나"),
            ),
        )
        daily_first = import_bundle(daily_complete, store)
        daily_reused = import_bundle(daily_complete, store)
        assert daily_first.status == "SUCCEEDED"
        assert daily_first.daily_status == "COMPLETE"
        assert daily_reused.run_id == daily_first.run_id
        assert daily_reused.reused is True

        daily_changed = _complete_daily_bundle(
            fixture,
            observed_at=datetime(2026, 8, 17, tzinfo=UTC),
            bodies=(("SYN-1", "Daily 합성 하나", "수정된 본문 하나"),),
        )
        daily_second = import_bundle(daily_changed, store)
        assert daily_second.daily_post_revisions_created == 2
        synthetic_visibility = connection.execute(
            """
            SELECT source_post_key, visibility_status
              FROM core.infostock_daily_posts
             WHERE source_post_key IN ('source:SYN-1', 'source:SYN-2')
             ORDER BY source_post_key
            """
        ).fetchall()
        assert synthetic_visibility == [
            ("source:SYN-1", "VISIBLE"),
            ("source:SYN-2", "NOT_VISIBLE"),
        ]
        assert _scalar(
            connection,
            """
            SELECT count(*)
              FROM core.infostock_daily_post_revisions AS revision
              JOIN core.infostock_daily_posts AS post
                ON post.daily_post_id = revision.daily_post_id
             WHERE post.source_post_key = 'source:SYN-1'
            """,
        ) == 2
        assert _scalar(
            connection,
            """
            SELECT count(*)
              FROM core.infostock_daily_post_revisions AS revision
              JOIN core.infostock_daily_posts AS post
                ON post.daily_post_id = revision.daily_post_id
             WHERE post.source_post_key = 'source:SYN-2'
            """,
        ) == 2

        changed_payload = copy.deepcopy(build_fixture_payload())
        move_observation_time(changed_payload, "2026-08-18T00:00:00+00:00")
        changed_data = changed_payload["detailSnapshots"][0]["rawPayload"]["data"]
        changed_data["theme"]["outline"] = "새 description revision"
        changed_data["stockItems"][0] = {
            "code": "400001",
            "name": "새 현재 관련주 001",
            "outline": "새 관측부터 적용",
            "index": "0",
        }
        rehash_fixture(changed_payload, 0)
        changed = parse_fixture_payload(changed_payload)
        revised = import_bundle(changed, store)

        assert revised.theme_revisions_created == 1
        assert revised.membership_revisions_created == 1
        assert revised.history_revisions_created == 0
        assert revised.history_leaders_created == 0
        assert revised.history_memberships_created == 0
        assert _scalar(
            connection,
            """
            SELECT count(*)
              FROM core.infostock_theme_revisions AS revision
              JOIN core.infostock_themes AS theme
                ON theme.theme_id = revision.theme_id
             WHERE theme.source_theme_id = '1001'
            """,
        ) == 2
        old_as_of = connection.execute(
            """
            SELECT membership.source_stock_code
              FROM core.infostock_theme_stock_memberships AS membership
              JOIN core.infostock_themes AS theme
                ON theme.theme_id = membership.theme_id
             WHERE theme.source_theme_id = '1001'
               AND membership.observed_from <= %s
               AND (membership.observed_to IS NULL OR membership.observed_to > %s)
             ORDER BY membership.source_stock_code
            """,
            ("2026-08-14T12:00:00+00:00", "2026-08-14T12:00:00+00:00"),
        ).fetchall()
        current = connection.execute(
            """
            SELECT membership.source_stock_code
              FROM core.infostock_theme_stock_memberships AS membership
              JOIN core.infostock_themes AS theme
                ON theme.theme_id = membership.theme_id
             WHERE theme.source_theme_id = '1001'
               AND membership.observed_to IS NULL
             ORDER BY membership.source_stock_code
            """
        ).fetchall()
        leader = _scalar(
            connection,
            """
            SELECT leader.source_stock_code
              FROM core.infostock_theme_history_leaders AS leader
              JOIN core.infostock_theme_history AS history
                ON history.history_id = leader.history_id
              JOIN core.infostock_themes AS theme
                ON theme.theme_id = history.theme_id
             WHERE theme.source_theme_id = '1001'
            """,
        )
        assert old_as_of == [("100001",), ("200001",)]
        assert current == [("200001",), ("400001",)]
        assert leader == "300001"
        assert _scalar(
            connection,
            "SELECT count(*) FROM core.infostock_theme_history WHERE point_in_time_safe",
        ) == 0
