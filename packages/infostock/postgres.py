"""PostgreSQL 16 persistence for Infostock full-sync transactions."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Protocol, cast

from .errors import SnapshotConflictError, TemporalConflictError
from .hashing import canonical_json, sha256_json
from .models import (
    DailyPost,
    ImportBundle,
    QualityIssue,
    RawSnapshot,
    StockReference,
    ThemeDetail,
    ThemeHistory,
    ThemeIndexItem,
    ThemeMembership,
)
from .store import ApplyCounts, ImportTransaction, StoredImport

STOCK_CODE_RE = re.compile(r"^[0-9A-Z]{6}$")


class DbCursor(Protocol):
    rowcount: int

    def execute(
        self, query: str, params: Sequence[object] | None = None
    ) -> object: ...

    def fetchone(self) -> Sequence[Any] | None: ...

    def fetchall(self) -> Sequence[Sequence[Any]]: ...

    def close(self) -> None: ...


class DbConnection(Protocol):
    def cursor(self) -> DbCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


def _required_row(cursor: DbCursor, operation: str) -> Sequence[Any]:
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError(f"{operation}: PostgreSQL이 예상 row를 반환하지 않았습니다.")
    return row


def _ensure_forward(
    observed_at: datetime,
    last_seen_at: datetime,
    *,
    changed: bool,
    label: str,
) -> None:
    if observed_at < last_seen_at or (changed and observed_at == last_seen_at):
        raise TemporalConflictError(
            f"{label}: 변경 관측 시각 {observed_at.isoformat()}은 기존 마지막 관측 "
            f"{last_seen_at.isoformat()} 이후여야 합니다."
        )


def _stored(row: Sequence[Any]) -> StoredImport:
    blockers_value = row[4]
    blockers = tuple(str(value) for value in (blockers_value or ()))
    return StoredImport(
        run_id=int(row[0]),
        status=str(row[1]),
        core_status=str(row[2]),
        daily_status=str(row[3]),
        blockers=blockers,
        themes_imported=int(row[5]),
        snapshots_linked=int(row[6]),
        history_rows_seen=int(row[7]),
        related_stocks_seen=int(row[8]),
        leaders_seen=int(row[9]),
        historical_memberships_seen=int(row[10]),
        daily_list_entries_seen=int(row[11]),
        daily_posts_seen=int(row[12]),
        daily_bodies_seen=int(row[13]),
        daily_relations_seen=int(row[14]),
        theme_revisions_created=int(row[15]),
        membership_revisions_created=int(row[16]),
        history_revisions_created=int(row[17]),
        history_leaders_created=int(row[18]),
        history_memberships_created=int(row[19]),
        quality_issues_created=int(row[20]),
        daily_post_revisions_created=int(row[21]),
    )


_RUN_RESULT_COLUMNS = """
    import_run_id, status, core_status, daily_status, blockers,
    themes_imported, snapshots_linked, history_rows_seen,
    related_stocks_seen, leaders_seen, historical_memberships_seen,
    daily_list_entries_seen, daily_posts_seen, daily_bodies_seen,
    daily_relations_seen, theme_revisions_created,
    membership_revisions_created, history_revisions_created,
    history_leaders_created, history_memberships_created,
    quality_issues_created, daily_post_revisions_created
"""


class PostgresInfostockStore:
    """Driver-neutral wrapper; callers may supply a psycopg 3 connection."""

    def __init__(self, connection: DbConnection) -> None:
        self._connection = connection

    @contextmanager
    def transaction(self) -> Iterator[ImportTransaction]:
        cursor = self._connection.cursor()
        transaction = _PostgresImportTransaction(cursor)
        try:
            yield transaction
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()
        finally:
            cursor.close()


class _PostgresImportTransaction:
    def __init__(self, cursor: DbCursor) -> None:
        self._cursor = cursor

    def acquire_import_lock(self, input_hash: str) -> None:
        self._cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (input_hash,)
        )

    def find_completed_import(self, input_hash: str) -> StoredImport | None:
        self._cursor.execute(
            f"""
            SELECT {_RUN_RESULT_COLUMNS}
              FROM ingest.infostock_import_runs
             WHERE input_hash = %s
               AND status IN ('SUCCEEDED', 'PARTIAL')
            """,
            (input_hash,),
        )
        row = self._cursor.fetchone()
        return None if row is None else _stored(row)

    def create_import_run(self, bundle: ImportBundle) -> int:
        self._cursor.execute(
            """
            INSERT INTO ingest.infostock_import_runs (
                input_hash, dataset_hash, dataset, source_provider,
                parser_version, rights_scope, run_type, status,
                core_status, daily_status, blockers, expected_theme_count
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'FULL', 'RUNNING',
                    'COMPLETE', %s, %s, %s)
            RETURNING import_run_id
            """,
            (
                bundle.input_hash,
                bundle.dataset_hash,
                bundle.dataset,
                bundle.source_provider,
                bundle.parser_version,
                bundle.rights_scope,
                bundle.daily.component_status,
                list(bundle.daily.blockers),
                bundle.expected_theme_count,
            ),
        )
        return int(_required_row(self._cursor, "create import run")[0])

    def _record_blob(self, bundle: ImportBundle, snapshot: RawSnapshot) -> int:
        raw_payload: str | None = (
            snapshot.raw_payload_text if snapshot.raw_format == "JSON" else None
        )
        self._cursor.execute(
            """
            INSERT INTO ingest.infostock_source_blobs (
                source_provider, content_hash, source_content_hash,
                raw_format, raw_payload_text, raw_payload, rights_scope
            )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (source_provider, content_hash) DO NOTHING
            RETURNING source_blob_id
            """,
            (
                bundle.source_provider,
                snapshot.raw_hash,
                snapshot.source_content_hash,
                snapshot.raw_format,
                snapshot.raw_payload_text,
                raw_payload,
                bundle.rights_scope,
            ),
        )
        row = self._cursor.fetchone()
        if row is not None:
            return int(row[0])
        self._cursor.execute(
            """
            SELECT source_blob_id, source_content_hash, raw_format,
                   raw_payload_text, rights_scope
              FROM ingest.infostock_source_blobs
             WHERE source_provider = %s AND content_hash = %s
            """,
            (bundle.source_provider, snapshot.raw_hash),
        )
        existing = _required_row(self._cursor, "find source blob")
        if (
            existing[1] != snapshot.source_content_hash
            or str(existing[2]) != snapshot.raw_format
            or str(existing[3]) != snapshot.raw_payload_text
            or str(existing[4]) != bundle.rights_scope
        ):
            raise SnapshotConflictError(
                "동일한 source byte hash에 서로 다른 payload metadata가 지정되었습니다."
            )
        return int(existing[0])

    def record_snapshot(
        self, run_id: int, bundle: ImportBundle, snapshot: RawSnapshot
    ) -> int:
        blob_id = self._record_blob(bundle, snapshot)
        parser_version = snapshot.parser_version or bundle.parser_version
        self._cursor.execute(
            """
            INSERT INTO ingest.infostock_source_snapshots (
                first_import_run_id, source_blob_id, source_provider,
                page_type, source_entity_id, source_url, collected_at,
                as_of, parser_version, is_complete, quality_status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT ON CONSTRAINT uq_infostock_source_observation DO NOTHING
            RETURNING source_snapshot_id
            """,
            (
                run_id,
                blob_id,
                bundle.source_provider,
                snapshot.page_type,
                snapshot.source_entity_id,
                snapshot.source_url,
                snapshot.collected_at,
                snapshot.as_of,
                parser_version,
                snapshot.is_complete,
                snapshot.quality_status,
            ),
        )
        row = self._cursor.fetchone()
        if row is None:
            self._cursor.execute(
                """
                SELECT source_snapshot_id, source_blob_id, source_url, as_of,
                       parser_version, is_complete, quality_status
                  FROM ingest.infostock_source_snapshots
                 WHERE source_provider = %s
                   AND page_type = %s
                   AND source_entity_id IS NOT DISTINCT FROM %s
                   AND collected_at = %s
                """,
                (
                    bundle.source_provider,
                    snapshot.page_type,
                    snapshot.source_entity_id,
                    snapshot.collected_at,
                ),
            )
            existing = _required_row(self._cursor, "find source observation")
            if (
                int(existing[1]) != blob_id
                or str(existing[2]) != snapshot.source_url
                or cast(datetime, existing[3]) != snapshot.as_of
                or str(existing[4]) != parser_version
                or bool(existing[5]) is not snapshot.is_complete
                or str(existing[6]) != snapshot.quality_status
            ):
                raise SnapshotConflictError(
                    "같은 source/page/entity/collected_at 관측에 다른 원본 또는 parser가 지정되었습니다."
                )
            snapshot_id = int(existing[0])
        else:
            snapshot_id = int(row[0])
        self._cursor.execute(
            """
            INSERT INTO ingest.infostock_import_run_snapshots (
                import_run_id, source_snapshot_id
            ) VALUES (%s, %s) ON CONFLICT DO NOTHING
            """,
            (run_id, snapshot_id),
        )
        return snapshot_id

    def upsert_theme_index(
        self,
        bundle: ImportBundle,
        item: ThemeIndexItem,
        snapshot_id: int,
    ) -> int:
        observed_at = bundle.index_snapshot.collected_at
        self._cursor.execute(
            """
            SELECT theme_id, current_name, source_url, last_seen_at
              FROM core.infostock_themes
             WHERE source_provider = %s AND source_theme_id = %s
             FOR UPDATE
            """,
            (bundle.source_provider, item.source_theme_id),
        )
        row = self._cursor.fetchone()
        if row is None:
            self._cursor.execute(
                """
                INSERT INTO core.infostock_themes (
                    source_provider, source_theme_id, current_name, source_url,
                    source_order, is_active, first_seen_at, last_seen_at,
                    first_source_snapshot_id, last_source_snapshot_id
                ) VALUES (%s, %s, %s, %s, %s, true, %s, %s, %s, %s)
                RETURNING theme_id
                """,
                (
                    bundle.source_provider,
                    item.source_theme_id,
                    item.theme_name,
                    item.source_url,
                    item.source_order,
                    observed_at,
                    observed_at,
                    snapshot_id,
                    snapshot_id,
                ),
            )
            return int(_required_row(self._cursor, "insert theme")[0])
        changed = str(row[1]) != item.theme_name or str(row[2]) != item.source_url
        _ensure_forward(
            observed_at,
            cast(datetime, row[3]),
            changed=changed,
            label=f"theme {item.source_theme_id}",
        )
        self._cursor.execute(
            """
            UPDATE core.infostock_themes
               SET current_name = %s, source_url = %s, source_order = %s,
                   is_active = true,
                   last_seen_at = GREATEST(last_seen_at, %s),
                   last_source_snapshot_id = %s, updated_at = now()
             WHERE theme_id = %s
            """,
            (
                item.theme_name,
                item.source_url,
                item.source_order,
                observed_at,
                snapshot_id,
                int(row[0]),
            ),
        )
        return int(row[0])

    def _apply_theme_revision(
        self, theme_id: int, detail: ThemeDetail, snapshot_id: int
    ) -> int:
        observed_at = detail.snapshot.collected_at
        self._cursor.execute(
            """
            SELECT theme_revision_id, revision_no, normalized_hash, last_seen_at
              FROM core.infostock_theme_revisions
             WHERE theme_id = %s AND observed_to IS NULL FOR UPDATE
            """,
            (theme_id,),
        )
        row = self._cursor.fetchone()
        if row is None:
            revision_no = 1
        elif str(row[2]) == detail.theme_revision_hash:
            _ensure_forward(
                observed_at,
                cast(datetime, row[3]),
                changed=False,
                label=f"theme revision {detail.source_theme_id}",
            )
            self._cursor.execute(
                """
                UPDATE core.infostock_theme_revisions
                   SET last_seen_at = GREATEST(last_seen_at, %s),
                       last_source_snapshot_id = %s
                 WHERE theme_revision_id = %s
                """,
                (observed_at, snapshot_id, int(row[0])),
            )
            return 0
        else:
            _ensure_forward(
                observed_at,
                cast(datetime, row[3]),
                changed=True,
                label=f"theme revision {detail.source_theme_id}",
            )
            self._cursor.execute(
                """
                UPDATE core.infostock_theme_revisions
                   SET observed_to = %s WHERE theme_revision_id = %s
                """,
                (observed_at, int(row[0])),
            )
            revision_no = int(row[1]) + 1
        self._cursor.execute(
            """
            INSERT INTO core.infostock_theme_revisions (
                theme_id, revision_no, theme_name, description,
                normalized_hash, observed_from, last_seen_at,
                source_snapshot_id, last_source_snapshot_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                theme_id,
                revision_no,
                detail.theme_name,
                detail.description,
                detail.theme_revision_hash,
                observed_at,
                observed_at,
                snapshot_id,
                snapshot_id,
            ),
        )
        return 1

    @staticmethod
    def _stock_observations(detail: ThemeDetail) -> list[dict[str, object]]:
        observations: list[dict[str, object]] = []
        sequence = 0
        for membership in detail.memberships:
            if membership.stock_code and STOCK_CODE_RE.fullmatch(membership.stock_code):
                observations.append(
                    {
                        "code": membership.stock_code,
                        "name": membership.stock_name,
                        "authority": "CURRENT_MEMBERSHIP",
                        "rank": 30,
                        "sequence": sequence,
                    }
                )
                sequence += 1
        for history in detail.history:
            for reference in (*history.leaders, *history.member_stocks):
                if reference.stock_code and STOCK_CODE_RE.fullmatch(reference.stock_code):
                    observations.append(
                        {
                            "code": reference.stock_code,
                            "name": reference.name,
                            "authority": "HISTORICAL_REFERENCE",
                            "rank": 20,
                            "sequence": sequence,
                        }
                    )
                    sequence += 1
        return observations

    def _upsert_stocks_bulk(
        self,
        observations: list[dict[str, object]],
        observed_at: datetime,
        snapshot_id: int,
    ) -> None:
        if not observations:
            return
        payload = canonical_json(observations)
        self._cursor.execute(
            """
            WITH input AS (
                SELECT * FROM jsonb_to_recordset(%s::jsonb) AS x(
                    code text, name text, authority text,
                    rank integer, sequence integer
                )
            ), chosen AS (
                SELECT DISTINCT ON (code) code, name, authority, rank
                  FROM input
                 ORDER BY code, rank DESC, sequence ASC
            )
            INSERT INTO core.infostock_stocks (
                stock_code, current_name, name_authority, name_authority_rank,
                first_seen_at, last_seen_at,
                first_source_snapshot_id, last_source_snapshot_id
            )
            SELECT code, name, authority, rank, %s, %s, %s, %s
              FROM chosen
            ON CONFLICT (stock_code) DO UPDATE
               SET current_name = CASE
                       WHEN EXCLUDED.name_authority_rank >= core.infostock_stocks.name_authority_rank
                       THEN EXCLUDED.current_name
                       ELSE core.infostock_stocks.current_name
                   END,
                   name_authority = CASE
                       WHEN EXCLUDED.name_authority_rank >= core.infostock_stocks.name_authority_rank
                       THEN EXCLUDED.name_authority
                       ELSE core.infostock_stocks.name_authority
                   END,
                   name_authority_rank = GREATEST(
                       core.infostock_stocks.name_authority_rank,
                       EXCLUDED.name_authority_rank
                   ),
                   last_seen_at = GREATEST(
                       core.infostock_stocks.last_seen_at, EXCLUDED.last_seen_at
                   ),
                   last_source_snapshot_id = CASE
                       WHEN EXCLUDED.last_seen_at >= core.infostock_stocks.last_seen_at
                       THEN EXCLUDED.last_source_snapshot_id
                       ELSE core.infostock_stocks.last_source_snapshot_id
                   END,
                   updated_at = now()
            """,
            (payload, observed_at, observed_at, snapshot_id, snapshot_id),
        )
        self._cursor.execute(
            """
            WITH input AS (
                SELECT DISTINCT code, name, authority
                  FROM jsonb_to_recordset(%s::jsonb) AS x(
                    code text, name text, authority text,
                    rank integer, sequence integer
                  )
            )
            INSERT INTO core.infostock_stock_name_observations (
                stock_id, source_name, authority, first_seen_at, last_seen_at,
                first_source_snapshot_id, last_source_snapshot_id
            )
            SELECT stock.stock_id, input.name, input.authority,
                   %s, %s, %s, %s
              FROM input
              JOIN core.infostock_stocks AS stock
                ON stock.stock_code = input.code
            ON CONFLICT (stock_id, source_name, authority) DO UPDATE
               SET last_seen_at = GREATEST(
                       core.infostock_stock_name_observations.last_seen_at,
                       EXCLUDED.last_seen_at
                   ),
                   last_source_snapshot_id = CASE
                       WHEN EXCLUDED.last_seen_at >= core.infostock_stock_name_observations.last_seen_at
                       THEN EXCLUDED.last_source_snapshot_id
                       ELSE core.infostock_stock_name_observations.last_source_snapshot_id
                   END
            """,
            (payload, observed_at, observed_at, snapshot_id, snapshot_id),
        )

    @staticmethod
    def _membership_rows(memberships: Sequence[ThemeMembership]) -> list[dict[str, object]]:
        return [
            {
                "identity": (
                    membership.stock_code
                    if membership.stock_code and STOCK_CODE_RE.fullmatch(membership.stock_code)
                    else f"@{membership.source_order}"
                ),
                "stock_code": membership.stock_code,
                "stock_name": membership.stock_name,
                "rationale": membership.rationale,
                "source_rank": membership.source_order,
                "source_index": membership.source_index,
                "quality_status": membership.quality_status,
                "content_hash": membership.content_hash,
            }
            for membership in memberships
        ]

    def _apply_memberships(
        self,
        theme_id: int,
        memberships: Sequence[ThemeMembership],
        observed_at: datetime,
        snapshot_id: int,
    ) -> int:
        rows = self._membership_rows(memberships)
        desired = {str(row["identity"]): str(row["content_hash"]) for row in rows}
        self._cursor.execute(
            """
            SELECT membership_id,
                   CASE WHEN source_stock_code ~ '^[0-9A-Z]{6}$'
                        THEN source_stock_code ELSE '@' || source_rank::text END,
                   content_hash, last_seen_at
              FROM core.infostock_theme_stock_memberships
             WHERE theme_id = %s AND observed_to IS NULL
             FOR UPDATE
            """,
            (theme_id,),
        )
        for row in self._cursor.fetchall():
            identity = str(row[1])
            changed = desired.get(identity) != str(row[2])
            _ensure_forward(
                observed_at,
                cast(datetime, row[3]),
                changed=changed,
                label=f"membership {theme_id}/{identity}",
            )
        payload = canonical_json(rows)
        self._cursor.execute(
            """
            WITH input AS (
                SELECT * FROM jsonb_to_recordset(%s::jsonb) AS x(
                    identity text, stock_code text, stock_name text,
                    rationale text, source_rank integer, source_index text,
                    quality_status text, content_hash text
                )
            )
            UPDATE core.infostock_theme_stock_memberships AS membership
               SET last_seen_at = GREATEST(membership.last_seen_at, %s),
                   last_source_snapshot_id = %s
              FROM input
             WHERE membership.theme_id = %s
               AND membership.observed_to IS NULL
               AND (CASE WHEN membership.source_stock_code ~ '^[0-9A-Z]{6}$'
                         THEN membership.source_stock_code
                         ELSE '@' || membership.source_rank::text END) = input.identity
               AND membership.content_hash = input.content_hash
            """,
            (payload, observed_at, snapshot_id, theme_id),
        )
        self._cursor.execute(
            """
            WITH input AS (
                SELECT * FROM jsonb_to_recordset(%s::jsonb) AS x(
                    identity text, stock_code text, stock_name text,
                    rationale text, source_rank integer, source_index text,
                    quality_status text, content_hash text
                )
            )
            UPDATE core.infostock_theme_stock_memberships AS membership
               SET observed_to = %s
             WHERE membership.theme_id = %s
               AND membership.observed_to IS NULL
               AND NOT EXISTS (
                    SELECT 1 FROM input
                     WHERE input.identity = CASE
                         WHEN membership.source_stock_code ~ '^[0-9A-Z]{6}$'
                         THEN membership.source_stock_code
                         ELSE '@' || membership.source_rank::text END
                       AND input.content_hash = membership.content_hash
               )
            """,
            (payload, observed_at, theme_id),
        )
        self._cursor.execute(
            """
            WITH input AS (
                SELECT * FROM jsonb_to_recordset(%s::jsonb) AS x(
                    identity text, stock_code text, stock_name text,
                    rationale text, source_rank integer, source_index text,
                    quality_status text, content_hash text
                )
            )
            INSERT INTO core.infostock_theme_stock_memberships (
                theme_id, stock_id, source_stock_code, source_stock_name,
                rationale, source_rank, source_index, quality_status,
                content_hash, observed_from, last_seen_at,
                source_snapshot_id, last_source_snapshot_id
            )
            SELECT %s, stock.stock_id, input.stock_code, input.stock_name,
                   input.rationale, input.source_rank, input.source_index,
                   input.quality_status, input.content_hash,
                   %s, %s, %s, %s
              FROM input
              LEFT JOIN core.infostock_stocks AS stock
                ON stock.stock_code = input.stock_code
               AND input.stock_code ~ '^[0-9A-Z]{6}$'
             WHERE NOT EXISTS (
                SELECT 1
                  FROM core.infostock_theme_stock_memberships AS current
                 WHERE current.theme_id = %s AND current.observed_to IS NULL
                   AND (CASE WHEN current.source_stock_code ~ '^[0-9A-Z]{6}$'
                             THEN current.source_stock_code
                             ELSE '@' || current.source_rank::text END) = input.identity
             )
            """,
            (
                payload,
                theme_id,
                observed_at,
                observed_at,
                snapshot_id,
                snapshot_id,
                theme_id,
            ),
        )
        return self._cursor.rowcount

    @staticmethod
    def _history_rows(history_items: Sequence[ThemeHistory]) -> list[dict[str, object]]:
        return [
            {
                "source_history_key": history.source_history_key,
                "source_history_id": history.source_history_id,
                "event_date": history.event_date.isoformat() if history.event_date else None,
                "source_date": history.source_date,
                "source_created_at": (
                    history.source_created_at.isoformat() if history.source_created_at else None
                ),
                "source_updated_at": (
                    history.source_updated_at.isoformat() if history.source_updated_at else None
                ),
                "raw_text": history.raw_text,
                "direction": history.direction,
                "source_order": history.source_order,
                "source_fingerprint": history.source_fingerprint,
                "quality_status": history.quality_status,
                "author": history.author,
                "chart_flag": history.chart_flag,
                "content_hash": history.content_hash,
            }
            for history in history_items
        ]

    def _insert_references(
        self,
        table: str,
        rows: list[dict[str, object]],
        theme_id: int,
        observed_at: datetime,
    ) -> int:
        if not rows:
            return 0
        if table not in {
            "core.infostock_theme_history_leaders",
            "core.infostock_theme_history_memberships",
        }:
            raise ValueError("unexpected history relation table")
        self._cursor.execute(
            f"""
            WITH input AS (
                SELECT * FROM jsonb_to_recordset(%s::jsonb) AS x(
                    source_history_key text, source_order integer,
                    stock_code text, stock_name text, source_url text,
                    display_value text, quality_status text
                )
            )
            INSERT INTO {table} (
                history_id, source_order, stock_id, source_stock_code,
                source_stock_name, source_url, display_value,
                resolution_status, quality_status, resolved_at
            )
            SELECT history.history_id, input.source_order, stock.stock_id,
                   input.stock_code, input.stock_name, input.source_url,
                   input.display_value,
                   CASE WHEN stock.stock_id IS NOT NULL THEN 'RESOLVED'
                        WHEN input.stock_code IS NULL THEN 'SOURCE_CODE_MISSING'
                        ELSE 'CODE_INVALID' END,
                   input.quality_status,
                   CASE WHEN stock.stock_id IS NOT NULL THEN %s ELSE NULL END
              FROM input
              JOIN core.infostock_theme_history AS history
                ON history.theme_id = %s
               AND history.source_history_key = input.source_history_key
               AND history.observed_to IS NULL
              LEFT JOIN core.infostock_stocks AS stock
                ON stock.stock_code = input.stock_code
               AND input.stock_code ~ '^[0-9A-Z]{{6}}$'
            """,
            (canonical_json(rows), observed_at, theme_id),
        )
        return self._cursor.rowcount

    def _apply_history(
        self,
        theme_id: int,
        history_items: Sequence[ThemeHistory],
        observed_at: datetime,
        snapshot_id: int,
    ) -> tuple[int, int, int]:
        rows = self._history_rows(history_items)
        desired = {
            str(row["source_history_key"]): str(row["content_hash"]) for row in rows
        }
        self._cursor.execute(
            """
            SELECT source_history_key, content_hash, last_seen_at
              FROM core.infostock_theme_history
             WHERE theme_id = %s AND observed_to IS NULL FOR UPDATE
            """,
            (theme_id,),
        )
        for row in self._cursor.fetchall():
            key = str(row[0])
            if key in desired:
                _ensure_forward(
                    observed_at,
                    cast(datetime, row[2]),
                    changed=desired[key] != str(row[1]),
                    label=f"history {theme_id}/{key}",
                )
        payload = canonical_json(rows)
        self._cursor.execute(
            """
            WITH input AS (
                SELECT * FROM jsonb_to_recordset(%s::jsonb) AS x(
                    source_history_key text, source_history_id text,
                    event_date date, source_date text,
                    source_created_at timestamptz, source_updated_at timestamptz,
                    raw_text text, direction text, source_order integer,
                    source_fingerprint text, quality_status text,
                    author text, chart_flag text, content_hash text
                )
            )
            UPDATE core.infostock_theme_history AS history
               SET last_seen_at = GREATEST(history.last_seen_at, %s),
                   last_source_snapshot_id = %s
              FROM input
             WHERE history.theme_id = %s AND history.observed_to IS NULL
               AND history.source_history_key = input.source_history_key
               AND history.content_hash = input.content_hash
            """,
            (payload, observed_at, snapshot_id, theme_id),
        )
        self._cursor.execute(
            """
            WITH input AS (
                SELECT * FROM jsonb_to_recordset(%s::jsonb) AS x(
                    source_history_key text, source_history_id text,
                    event_date date, source_date text,
                    source_created_at timestamptz, source_updated_at timestamptz,
                    raw_text text, direction text, source_order integer,
                    source_fingerprint text, quality_status text,
                    author text, chart_flag text, content_hash text
                )
            )
            UPDATE core.infostock_theme_history AS history
               SET observed_to = %s
              FROM input
             WHERE history.theme_id = %s AND history.observed_to IS NULL
               AND history.source_history_key = input.source_history_key
               AND history.content_hash <> input.content_hash
            """,
            (payload, observed_at, theme_id),
        )
        self._cursor.execute(
            """
            WITH input AS (
                SELECT * FROM jsonb_to_recordset(%s::jsonb) AS x(
                    source_history_key text, source_history_id text,
                    event_date date, source_date text,
                    source_created_at timestamptz, source_updated_at timestamptz,
                    raw_text text, direction text, source_order integer,
                    source_fingerprint text, quality_status text,
                    author text, chart_flag text, content_hash text
                )
            )
            INSERT INTO core.infostock_theme_history (
                theme_id, source_history_key, source_history_id, revision_no,
                event_date, source_date, source_created_at, source_updated_at,
                raw_text, direction, source_order, source_fingerprint,
                quality_status, author, chart_flag, content_hash,
                observed_from, last_seen_at, point_in_time_safe,
                source_snapshot_id, last_source_snapshot_id
            )
            SELECT %s, input.source_history_key, input.source_history_id,
                   COALESCE((
                       SELECT max(previous.revision_no)
                         FROM core.infostock_theme_history AS previous
                        WHERE previous.theme_id = %s
                          AND previous.source_history_key = input.source_history_key
                   ), 0) + 1,
                   input.event_date, input.source_date,
                   input.source_created_at, input.source_updated_at,
                   input.raw_text, input.direction, input.source_order,
                   input.source_fingerprint, input.quality_status,
                   input.author, input.chart_flag, input.content_hash,
                   %s, %s, false, %s, %s
              FROM input
             WHERE NOT EXISTS (
                 SELECT 1 FROM core.infostock_theme_history AS current
                  WHERE current.theme_id = %s
                    AND current.source_history_key = input.source_history_key
                    AND current.observed_to IS NULL
             )
            RETURNING source_history_key
            """,
            (
                payload,
                theme_id,
                theme_id,
                observed_at,
                observed_at,
                snapshot_id,
                snapshot_id,
                theme_id,
            ),
        )
        inserted_keys = {str(row[0]) for row in self._cursor.fetchall()}
        if not inserted_keys:
            return 0, 0, 0
        leader_rows: list[dict[str, object]] = []
        member_rows: list[dict[str, object]] = []
        for history in history_items:
            if history.source_history_key not in inserted_keys:
                continue
            leader_rows.extend(
                self._reference_row(history.source_history_key, reference)
                for reference in history.leaders
            )
            member_rows.extend(
                self._reference_row(history.source_history_key, reference)
                for reference in history.member_stocks
            )
        leaders = self._insert_references(
            "core.infostock_theme_history_leaders",
            leader_rows,
            theme_id,
            observed_at,
        )
        memberships = self._insert_references(
            "core.infostock_theme_history_memberships",
            member_rows,
            theme_id,
            observed_at,
        )
        return len(inserted_keys), leaders, memberships

    @staticmethod
    def _reference_row(
        source_history_key: str, reference: StockReference
    ) -> dict[str, object]:
        return {
            "source_history_key": source_history_key,
            "source_order": reference.source_order,
            "stock_code": reference.stock_code,
            "stock_name": reference.name,
            "source_url": reference.source_url,
            "display_value": reference.display_value,
            "quality_status": reference.quality_status,
        }

    def apply_theme_detail(
        self,
        bundle: ImportBundle,
        theme_id: int,
        detail: ThemeDetail,
        snapshot_id: int,
    ) -> ApplyCounts:
        del bundle
        observed_at = detail.snapshot.collected_at
        theme_revisions = self._apply_theme_revision(theme_id, detail, snapshot_id)
        self._upsert_stocks_bulk(
            self._stock_observations(detail), observed_at, snapshot_id
        )
        membership_revisions = self._apply_memberships(
            theme_id, detail.memberships, observed_at, snapshot_id
        )
        history_revisions, leaders, historical_memberships = self._apply_history(
            theme_id, detail.history, observed_at, snapshot_id
        )
        return ApplyCounts(
            theme_revisions=theme_revisions,
            membership_revisions=membership_revisions,
            history_revisions=history_revisions,
            history_leaders=leaders,
            history_memberships=historical_memberships,
        )

    def _daily_post_snapshot_id(
        self,
        post: DailyPost,
        snapshot_ids: dict[tuple[str, str | None], int],
    ) -> int:
        if post.detail_snapshot is not None:
            key = ("DAILY_DETAIL", post.detail_snapshot.source_entity_id)
            if key in snapshot_ids:
                return snapshot_ids[key]
        list_ids = [
            snapshot_id
            for (page_type, _), snapshot_id in snapshot_ids.items()
            if page_type == "DAILY_LIST"
        ]
        if not list_ids:
            raise RuntimeError("Daily post 적재에는 DAILY_LIST snapshot이 필요합니다.")
        return list_ids[0]

    def _upsert_daily_post(
        self,
        post: DailyPost,
        observed_at: datetime,
        snapshot_id: int,
    ) -> tuple[int, int, int]:
        self._cursor.execute(
            """
            INSERT INTO core.infostock_daily_posts (
                source_provider, source_post_key, source_post_id,
                canonical_url, current_title, published_date,
                visibility_status, first_seen_at, last_seen_at,
                first_source_snapshot_id, last_source_snapshot_id
            ) VALUES ('INFOSTOCK', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_provider, source_post_key) DO UPDATE
               SET source_post_id = COALESCE(
                       EXCLUDED.source_post_id,
                       core.infostock_daily_posts.source_post_id
                   ),
                   canonical_url = COALESCE(
                       EXCLUDED.canonical_url,
                       core.infostock_daily_posts.canonical_url
                   ),
                   current_title = EXCLUDED.current_title,
                   published_date = COALESCE(
                       EXCLUDED.published_date,
                       core.infostock_daily_posts.published_date
                   ),
                   visibility_status = EXCLUDED.visibility_status,
                   last_seen_at = GREATEST(
                       core.infostock_daily_posts.last_seen_at,
                       EXCLUDED.last_seen_at
                   ),
                   last_source_snapshot_id = EXCLUDED.last_source_snapshot_id,
                   updated_at = now()
            RETURNING daily_post_id
            """,
            (
                post.source_post_key,
                post.source_post_id,
                post.source_url,
                post.title,
                post.published_date,
                post.visibility_status,
                observed_at,
                observed_at,
                snapshot_id,
                snapshot_id,
            ),
        )
        daily_post_id = int(_required_row(self._cursor, "upsert Daily post")[0])
        self._cursor.execute(
            """
            SELECT daily_post_revision_id, revision_no, normalized_hash,
                   visibility_status, last_seen_at
              FROM core.infostock_daily_post_revisions
             WHERE daily_post_id = %s AND observed_to IS NULL FOR UPDATE
            """,
            (daily_post_id,),
        )
        current = self._cursor.fetchone()
        if (
            current is not None
            and str(current[2]) == post.normalized_hash
            and str(current[3]) == post.visibility_status
        ):
            _ensure_forward(
                observed_at,
                cast(datetime, current[4]),
                changed=False,
                label=f"Daily post {post.source_post_key}",
            )
            self._cursor.execute(
                """
                UPDATE core.infostock_daily_post_revisions
                   SET last_seen_at = GREATEST(last_seen_at, %s),
                       last_source_snapshot_id = %s
                 WHERE daily_post_revision_id = %s
                """,
                (observed_at, snapshot_id, int(current[0])),
            )
            return daily_post_id, 0, 0
        revision_no = 1
        if current is not None:
            _ensure_forward(
                observed_at,
                cast(datetime, current[4]),
                changed=True,
                label=f"Daily post {post.source_post_key}",
            )
            self._cursor.execute(
                """
                UPDATE core.infostock_daily_post_revisions
                   SET observed_to = %s WHERE daily_post_revision_id = %s
                """,
                (observed_at, int(current[0])),
            )
            revision_no = int(current[1]) + 1
        self._cursor.execute(
            """
            INSERT INTO core.infostock_daily_post_revisions (
                daily_post_id, revision_no, title, published_date,
                source_date, raw_body, body_hash, normalized_hash,
                body_status, visibility_status, observed_from, last_seen_at,
                source_snapshot_id, last_source_snapshot_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s)
            RETURNING daily_post_revision_id
            """,
            (
                daily_post_id,
                revision_no,
                post.title,
                post.published_date,
                post.source_date,
                post.raw_body,
                post.body_hash,
                post.normalized_hash,
                post.body_status,
                post.visibility_status,
                observed_at,
                observed_at,
                snapshot_id,
                snapshot_id,
            ),
        )
        revision_id = int(_required_row(self._cursor, "insert Daily revision")[0])
        relation_rows = [
            {
                "source_order": relation.source_order,
                "relation_type": relation.relation_type,
                "source_theme_name": relation.source_theme_name,
                "source_stock_name": relation.source_stock_name,
                "source_stock_code": relation.source_stock_code,
                "description": relation.description,
                "raw_text": relation.raw_text,
                "quality_status": relation.quality_status,
                "paragraph_no": relation.paragraph_no,
                "theme_change_rate": relation.theme_change_rate,
                "close_price": relation.close_price,
                "change_rate": relation.change_rate,
                "trade_volume": relation.trade_volume,
                "open_price": relation.open_price,
                "high_price": relation.high_price,
                "low_price": relation.low_price,
            }
            for relation in post.relations
        ]
        if relation_rows:
            self._cursor.execute(
                """
                WITH input AS (
                    SELECT * FROM jsonb_to_recordset(%s::jsonb) AS x(
                        source_order integer, relation_type text,
                        source_theme_name text, source_stock_name text,
                        source_stock_code text, description text,
                        raw_text text, quality_status text,
                        paragraph_no integer, theme_change_rate numeric,
                        close_price bigint, change_rate numeric,
                        trade_volume bigint, open_price bigint,
                        high_price bigint, low_price bigint
                    )
                )
                INSERT INTO core.infostock_daily_relations (
                    daily_post_revision_id, source_order, relation_type,
                    theme_id, stock_id, source_theme_name, source_stock_name,
                    source_stock_code, description, raw_text, quality_status,
                    paragraph_no, theme_change_rate, close_price, change_rate,
                    trade_volume, open_price, high_price, low_price
                )
                SELECT %s, input.source_order, input.relation_type,
                       theme.theme_id, stock.stock_id,
                       input.source_theme_name, input.source_stock_name,
                       input.source_stock_code, input.description,
                       input.raw_text, input.quality_status,
                       input.paragraph_no, input.theme_change_rate,
                       input.close_price, input.change_rate,
                       input.trade_volume, input.open_price,
                       input.high_price, input.low_price
                  FROM input
                  LEFT JOIN core.infostock_themes AS theme
                    ON theme.current_name = input.source_theme_name
                   AND theme.source_provider = 'INFOSTOCK'
                  LEFT JOIN core.infostock_stocks AS stock
                    ON stock.stock_code = input.source_stock_code
                """,
                (canonical_json(relation_rows), revision_id),
            )
            relation_count = self._cursor.rowcount
        else:
            relation_count = 0
        return daily_post_id, 1, relation_count

    def _mark_missing_daily_posts(
        self,
        incoming_keys: set[str],
        observed_at: datetime,
        snapshot_id: int,
        *,
        window: tuple[date, date] | None = None,
    ) -> int:
        # 증분 run은 수집 구간 안의 게시물만 관측했으므로 구간 밖 visibility는
        # 판단하지 않는다. window 없는 FULL run은 전체를 본다.
        query = """
            SELECT post.daily_post_id, post.source_post_key,
                   revision.daily_post_revision_id, revision.revision_no,
                   revision.title, revision.published_date,
                   revision.source_date, revision.raw_body,
                   revision.body_hash, revision.body_status,
                   revision.normalized_hash, revision.last_seen_at
              FROM core.infostock_daily_posts AS post
              JOIN core.infostock_daily_post_revisions AS revision
                ON revision.daily_post_id = post.daily_post_id
               AND revision.observed_to IS NULL
             WHERE post.visibility_status = 'VISIBLE'
            """
        params: tuple[object, ...] = ()
        if window is not None:
            query += " AND post.published_date BETWEEN %s AND %s"
            params = (window[0], window[1])
        query += " FOR UPDATE OF post, revision"
        self._cursor.execute(query, params or None)
        created = 0
        for row in self._cursor.fetchall():
            key = str(row[1])
            if key in incoming_keys:
                continue
            _ensure_forward(
                observed_at,
                cast(datetime, row[11]),
                changed=True,
                label=f"Daily visibility {key}",
            )
            hidden_hash = sha256_json(
                {"previousHash": str(row[10]), "visibility": "NOT_VISIBLE"}
            )
            self._cursor.execute(
                """
                UPDATE core.infostock_daily_post_revisions
                   SET observed_to = %s WHERE daily_post_revision_id = %s
                """,
                (observed_at, int(row[2])),
            )
            self._cursor.execute(
                """
                UPDATE core.infostock_daily_posts
                   SET visibility_status = 'NOT_VISIBLE',
                       last_seen_at = %s, last_source_snapshot_id = %s,
                       updated_at = now()
                 WHERE daily_post_id = %s
                """,
                (observed_at, snapshot_id, int(row[0])),
            )
            self._cursor.execute(
                """
                INSERT INTO core.infostock_daily_post_revisions (
                    daily_post_id, revision_no, title, published_date,
                    source_date, raw_body, body_hash, normalized_hash,
                    body_status, visibility_status, observed_from, last_seen_at,
                    source_snapshot_id, last_source_snapshot_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                          'NOT_VISIBLE', %s, %s, %s, %s)
                """,
                (
                    int(row[0]),
                    int(row[3]) + 1,
                    str(row[4]),
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    hidden_hash,
                    str(row[9]),
                    observed_at,
                    observed_at,
                    snapshot_id,
                    snapshot_id,
                ),
            )
            created += 1
        return created

    def apply_daily(
        self,
        run_id: int,
        bundle: ImportBundle,
        snapshot_ids: dict[tuple[str, str | None], int],
        *,
        missing_window: tuple[date, date] | None = None,
    ) -> ApplyCounts:
        daily = bundle.daily
        if not daily.posts:
            empty_sweep_revisions = 0
            if daily.coverage_complete and missing_window is not None:
                # 구간을 다 봤는데 게시물이 없으면, 구간 안에 남아 있던
                # VISIBLE 게시물은 사라진 것이다.
                empty_list_ids = [
                    value
                    for (page_type, _), value in snapshot_ids.items()
                    if page_type == "DAILY_LIST"
                ]
                if empty_list_ids:
                    empty_sweep_revisions = self._mark_missing_daily_posts(
                        set(),
                        min(snapshot.collected_at for snapshot in daily.pages),
                        empty_list_ids[0],
                        window=missing_window,
                    )
            self._cursor.execute(
                """
                INSERT INTO ingest.infostock_daily_backfill_checkpoints (
                    import_run_id, status, first_page, last_page, next_page,
                    earliest_date, latest_date, listed_count, detailed_count,
                    coverage_complete, cursor_json, blockers
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 0, %s, %s::jsonb, %s)
                """,
                (
                    run_id,
                    daily.component_status,
                    daily.first_page,
                    daily.last_page,
                    daily.next_page,
                    daily.earliest_date,
                    daily.latest_date,
                    daily.coverage_complete,
                    canonical_json({"nextPage": daily.next_page}),
                    list(daily.blockers),
                ),
            )
            return ApplyCounts(daily_post_revisions=empty_sweep_revisions)
        list_snapshot_ids = [
            value for (page_type, _), value in snapshot_ids.items() if page_type == "DAILY_LIST"
        ]
        if not list_snapshot_ids:
            raise RuntimeError("Daily list entry 적재에는 DAILY_LIST snapshot이 필요합니다.")
        list_snapshot_id = list_snapshot_ids[0]
        observed_at = min(snapshot.collected_at for snapshot in daily.pages)
        post_ids: dict[str, int] = {}
        revisions = 0
        relations = 0
        for post in daily.posts:
            snapshot_id = self._daily_post_snapshot_id(post, snapshot_ids)
            post_observed_at = (
                post.detail_snapshot.collected_at
                if post.detail_snapshot is not None
                else observed_at
            )
            post_id, created, relation_count = self._upsert_daily_post(
                post, post_observed_at, snapshot_id
            )
            post_ids[post.source_post_key] = post_id
            revisions += created
            relations += relation_count
        entry_rows = [
            {
                "source_order": entry.source_order,
                "source_post_key": entry.source_post_key,
                "source_post_id": entry.source_post_id,
                "source_url": entry.source_url,
                "title": entry.title,
                "published_date": (
                    entry.published_date.isoformat() if entry.published_date else None
                ),
                "source_date": entry.source_date,
                "quality_status": entry.quality_status,
            }
            for entry in daily.entries
        ]
        self._cursor.execute(
            """
            WITH input AS (
                SELECT * FROM jsonb_to_recordset(%s::jsonb) AS x(
                    source_order integer, source_post_key text,
                    source_post_id text, source_url text, title text,
                    published_date date, source_date text, quality_status text
                )
            )
            INSERT INTO ingest.infostock_daily_list_entries (
                source_snapshot_id, source_order, daily_post_id,
                source_post_id, source_url, title, published_date,
                source_date, quality_status
            )
            SELECT %s, input.source_order, post.daily_post_id,
                   input.source_post_id, input.source_url, input.title,
                   input.published_date, input.source_date, input.quality_status
              FROM input
              JOIN core.infostock_daily_posts AS post
                ON post.source_provider = 'INFOSTOCK'
               AND post.source_post_key = input.source_post_key
            ON CONFLICT DO NOTHING
            """,
            (canonical_json(entry_rows), list_snapshot_id),
        )
        list_entries_created = self._cursor.rowcount
        if daily.coverage_complete:
            revisions += self._mark_missing_daily_posts(
                set(post_ids),
                observed_at,
                list_snapshot_id,
                window=missing_window,
            )
        self._cursor.execute(
            """
            INSERT INTO ingest.infostock_daily_backfill_checkpoints (
                import_run_id, status, first_page, last_page, next_page,
                earliest_date, latest_date, listed_count, detailed_count,
                coverage_complete, cursor_json, blockers
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                run_id,
                daily.component_status,
                daily.first_page,
                daily.last_page,
                daily.next_page,
                daily.earliest_date,
                daily.latest_date,
                len(daily.entries),
                daily.body_count,
                daily.coverage_complete,
                canonical_json(
                    {
                        "completedPages": [daily.first_page, daily.last_page],
                        "nextPage": daily.next_page,
                    }
                ),
                list(daily.blockers),
            ),
        )
        return ApplyCounts(
            daily_list_entries=list_entries_created,
            daily_post_revisions=revisions,
            daily_relations=relations,
        )

    def record_quality_issues(
        self, run_id: int, issues: tuple[QualityIssue, ...]
    ) -> int:
        if not issues:
            return 0
        rows = [
            {
                "component": issue.component,
                "issue_code": issue.issue_code,
                "severity": issue.severity,
                "entity_type": issue.entity_type,
                "source_entity_key": issue.source_entity_key,
                "source_order": issue.source_order,
                "detail": issue.detail,
            }
            for issue in issues
        ]
        self._cursor.execute(
            """
            WITH input AS (
                SELECT * FROM jsonb_to_recordset(%s::jsonb) AS x(
                    component text, issue_code text, severity text,
                    entity_type text, source_entity_key text,
                    source_order integer, detail jsonb
                )
            )
            INSERT INTO ingest.infostock_quality_issues (
                import_run_id, component, issue_code, severity,
                entity_type, source_entity_key, source_order, detail
            )
            SELECT %s, component, issue_code, severity, entity_type,
                   source_entity_key, source_order, detail
              FROM input
            ON CONFLICT DO NOTHING
            """,
            (canonical_json(rows), run_id),
        )
        return self._cursor.rowcount

    def complete_import_run(
        self,
        run_id: int,
        bundle: ImportBundle,
        *,
        snapshots_linked: int,
        counts: ApplyCounts,
    ) -> StoredImport:
        quality = bundle.quality_summary
        daily = bundle.daily
        status = "SUCCEEDED" if daily.component_status == "COMPLETE" else "PARTIAL"
        quality_summary = {
            "themeDatabase": {
                "duplicateHistoryRows": quality.duplicate_history_count,
                "missingLeaderCodes": quality.missing_leader_code_count,
                "missingHistoricalMembershipCodes": quality.missing_historical_membership_code_count,
                "missingHistoricalMembershipFields": quality.missing_historical_membership_field_count,
                "stockCodesWithNameVariants": quality.stock_name_variant_count,
            },
            "dailyFeaturedTheme": {
                "coverageComplete": daily.coverage_complete,
                "nextPage": daily.next_page,
            },
        }
        human_summary = (
            f"Theme DB COMPLETE: {quality.theme_count:,}/280개, history "
            f"{quality.history_count:,}건, related stock {quality.related_stock_count:,}건. "
            f"DailyFeaturedTheme {daily.component_status}: 목록 {len(daily.entries):,}건, "
            f"본문 {daily.body_count:,}건; blocker={','.join(daily.blockers) or '없음'}."
        )
        self._cursor.execute(
            """
            UPDATE ingest.infostock_import_runs
               SET status = %s, finished_at = now(),
                   core_status = 'COMPLETE', daily_status = %s, blockers = %s,
                   themes_imported = %s, snapshots_linked = %s,
                   history_rows_seen = %s, related_stocks_seen = %s,
                   leaders_seen = %s, historical_memberships_seen = %s,
                   daily_list_entries_seen = %s, daily_posts_seen = %s,
                   daily_bodies_seen = %s, daily_relations_seen = %s,
                   theme_revisions_created = %s,
                   membership_revisions_created = %s,
                   history_revisions_created = %s,
                   history_leaders_created = %s,
                   history_memberships_created = %s,
                   quality_issues_created = %s,
                   daily_post_revisions_created = %s,
                   quality_summary = %s::jsonb, human_summary = %s
             WHERE import_run_id = %s AND status = 'RUNNING'
            """,
            (
                status,
                daily.component_status,
                list(daily.blockers),
                quality.theme_count,
                snapshots_linked,
                quality.history_count,
                quality.related_stock_count,
                quality.leader_count,
                quality.historical_membership_count,
                len(daily.entries),
                len(daily.posts),
                daily.body_count,
                daily.relation_count,
                counts.theme_revisions,
                counts.membership_revisions,
                counts.history_revisions,
                counts.history_leaders,
                counts.history_memberships,
                counts.quality_issues,
                counts.daily_post_revisions,
                canonical_json(quality_summary),
                human_summary,
                run_id,
            ),
        )
        if self._cursor.rowcount != 1:
            raise RuntimeError("import run 완료 상태 전이가 정확히 한 row에 적용되지 않았습니다.")
        self._cursor.execute(
            """
            INSERT INTO ingest.infostock_sync_components (
                import_run_id, component, status, expected_count,
                discovered_count, imported_count, page_count, body_count,
                relation_count, pagination_range, blockers,
                quality_summary, human_summary
            ) VALUES
                (%s, 'THEME_DATABASE', 'COMPLETE', %s, %s, %s, 0, 0, 0,
                 '{}'::jsonb, '{}', %s::jsonb, %s),
                (%s, 'DAILY_FEATURED_THEME', %s, NULL, %s, %s, %s, %s, %s,
                 %s::jsonb, %s, %s::jsonb, %s)
            """,
            (
                run_id,
                bundle.expected_theme_count,
                len(bundle.index_items),
                quality.theme_count,
                canonical_json(quality_summary["themeDatabase"]),
                (
                    f"280-theme COMPLETE: history {quality.history_count:,}건, "
                    f"related stock {quality.related_stock_count:,}건."
                ),
                run_id,
                daily.component_status,
                len(daily.entries),
                len(daily.posts),
                len([page for page in daily.pages if page.page_type == "DAILY_LIST"]),
                daily.body_count,
                daily.relation_count,
                canonical_json(
                    {
                        "firstPage": daily.first_page,
                        "lastPage": daily.last_page,
                        "nextPage": daily.next_page,
                        "coverageComplete": daily.coverage_complete,
                    }
                ),
                list(daily.blockers),
                canonical_json(quality_summary["dailyFeaturedTheme"]),
                (
                    f"DailyFeaturedTheme {daily.component_status}: 목록 "
                    f"{len(daily.entries):,}건, 본문 {daily.body_count:,}건, "
                    f"관계 {daily.relation_count:,}건."
                ),
            ),
        )
        self._cursor.execute(
            f"SELECT {_RUN_RESULT_COLUMNS} FROM ingest.infostock_import_runs WHERE import_run_id = %s",
            (run_id,),
        )
        return _stored(_required_row(self._cursor, "read completed import"))

    def create_daily_increment_run(self, bundle: ImportBundle) -> int:
        """Daily만 갱신하는 INCREMENTAL run. theme 컴포넌트는 SKIPPED로 남긴다."""

        self._cursor.execute(
            """
            INSERT INTO ingest.infostock_import_runs (
                input_hash, dataset_hash, dataset, source_provider,
                parser_version, rights_scope, run_type, status,
                core_status, daily_status, blockers, expected_theme_count
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'INCREMENTAL', 'RUNNING',
                    'SKIPPED', %s, %s, 0)
            RETURNING import_run_id
            """,
            (
                bundle.input_hash,
                bundle.dataset_hash,
                bundle.dataset,
                bundle.source_provider,
                bundle.parser_version,
                bundle.rights_scope,
                bundle.daily.component_status,
                list(bundle.daily.blockers),
            ),
        )
        return int(_required_row(self._cursor, "create increment run")[0])

    def complete_daily_increment_run(
        self,
        run_id: int,
        bundle: ImportBundle,
        *,
        snapshots_linked: int,
        counts: ApplyCounts,
    ) -> StoredImport:
        daily = bundle.daily
        status = "SUCCEEDED" if daily.component_status == "COMPLETE" else "PARTIAL"
        quality_summary = {
            "dailyFeaturedTheme": {
                "coverageComplete": daily.coverage_complete,
                "nextPage": daily.next_page,
            }
        }
        human_summary = (
            f"DailyFeaturedTheme 증분 {daily.component_status}: 목록 "
            f"{len(daily.entries):,}건, 본문 {daily.body_count:,}건, revision "
            f"{counts.daily_post_revisions:,}건; blocker="
            f"{','.join(daily.blockers) or '없음'}."
        )
        self._cursor.execute(
            """
            UPDATE ingest.infostock_import_runs
               SET status = %s, finished_at = now(),
                   daily_status = %s, blockers = %s,
                   snapshots_linked = %s,
                   daily_list_entries_seen = %s, daily_posts_seen = %s,
                   daily_bodies_seen = %s, daily_relations_seen = %s,
                   quality_issues_created = %s,
                   daily_post_revisions_created = %s,
                   quality_summary = %s::jsonb, human_summary = %s
             WHERE import_run_id = %s AND status = 'RUNNING'
               AND run_type = 'INCREMENTAL'
            """,
            (
                status,
                daily.component_status,
                list(daily.blockers),
                snapshots_linked,
                len(daily.entries),
                len(daily.posts),
                daily.body_count,
                daily.relation_count,
                counts.quality_issues,
                counts.daily_post_revisions,
                canonical_json(quality_summary),
                human_summary,
                run_id,
            ),
        )
        if self._cursor.rowcount != 1:
            raise RuntimeError(
                "증분 run 완료 상태 전이가 정확히 한 row에 적용되지 않았습니다."
            )
        self._cursor.execute(
            """
            INSERT INTO ingest.infostock_sync_components (
                import_run_id, component, status, expected_count,
                discovered_count, imported_count, page_count, body_count,
                relation_count, pagination_range, blockers,
                quality_summary, human_summary
            ) VALUES (%s, 'DAILY_FEATURED_THEME', %s, NULL, %s, %s, %s, %s, %s,
                      %s::jsonb, %s, %s::jsonb, %s)
            """,
            (
                run_id,
                daily.component_status,
                len(daily.entries),
                len(daily.posts),
                len(
                    [
                        page
                        for page in daily.pages
                        if page.page_type == "DAILY_LIST"
                    ]
                ),
                daily.body_count,
                daily.relation_count,
                canonical_json(
                    {
                        "firstPage": daily.first_page,
                        "lastPage": daily.last_page,
                        "nextPage": daily.next_page,
                        "coverageComplete": daily.coverage_complete,
                    }
                ),
                list(daily.blockers),
                canonical_json(quality_summary["dailyFeaturedTheme"]),
                (
                    f"DailyFeaturedTheme 증분 {daily.component_status}: 목록 "
                    f"{len(daily.entries):,}건, 본문 {daily.body_count:,}건."
                ),
            ),
        )
        self._cursor.execute(
            f"SELECT {_RUN_RESULT_COLUMNS} FROM ingest.infostock_import_runs WHERE import_run_id = %s",
            (run_id,),
        )
        return _stored(_required_row(self._cursor, "read completed increment"))
