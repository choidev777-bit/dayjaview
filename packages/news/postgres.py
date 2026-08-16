from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any, Protocol, cast

from .models import IngestionStatus, NewsItem, NewsSourceType, RightsScope
from .sources import SourceCursor, SourceStatus


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


_ITEM_COLUMNS = (
    "news_id, source_id, source_type, source_item_id, canonical_url, "
    "original_url, publisher, title, description, published_at, retrieved_at, "
    "normalized_title_hash, content_hash, rights_scope, ingestion_status, "
    "stock_ids, entities, body"
)


def _item(row: Sequence[Any]) -> NewsItem:
    return NewsItem(
        news_id=str(row[0]),
        source_id=str(row[1]),
        source_type=NewsSourceType(str(row[2])),
        source_item_id=str(row[3]),
        canonical_url=str(row[4]),
        original_url=str(row[5]),
        publisher=str(row[6]),
        title=str(row[7]),
        description=str(row[8]),
        published_at=cast(datetime | None, row[9]),
        retrieved_at=cast(datetime, row[10]),
        normalized_title_hash=str(row[11]),
        content_hash=str(row[12]),
        rights_scope=RightsScope(str(row[13])),
        ingestion_status=IngestionStatus(str(row[14])),
        stock_ids=tuple(str(value) for value in row[15]),
        entities=tuple(str(value) for value in row[16]),
        body=str(row[17]),
    )


class PostgresNewsStore:
    def __init__(self, connection: DbConnection) -> None:
        self._connection = connection

    def upsert(self, item: NewsItem) -> bool:
        db = self._connection.cursor()
        try:
            db.execute(
                """
                INSERT INTO news.items (
                    news_id, source_id, source_type, source_item_id,
                    canonical_url, original_url, publisher, title, description,
                    published_at, retrieved_at, normalized_title_hash,
                    content_hash, rights_scope, ingestion_status, stock_ids,
                    entities, body
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT DO NOTHING
                """,
                (
                    item.news_id,
                    item.source_id,
                    item.source_type.value,
                    item.source_item_id,
                    item.canonical_url,
                    item.original_url,
                    item.publisher,
                    item.title,
                    item.description,
                    item.published_at,
                    item.retrieved_at,
                    item.normalized_title_hash,
                    item.content_hash,
                    item.rights_scope.value,
                    item.ingestion_status.value,
                    list(item.stock_ids),
                    list(item.entities),
                    item.body,
                ),
            )
            created = db.rowcount == 1
            self._connection.commit()
            return created
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()

    def get(self, news_id: str) -> NewsItem | None:
        db = self._connection.cursor()
        try:
            db.execute(
                f"SELECT {_ITEM_COLUMNS} FROM news.items WHERE news_id = %s",
                (news_id,),
            )
            row = db.fetchone()
            return None if row is None else _item(row)
        finally:
            db.close()

    def find_duplicate(self, item: NewsItem) -> NewsItem | None:
        db = self._connection.cursor()
        try:
            db.execute(
                f"""
                SELECT {_ITEM_COLUMNS}
                  FROM news.items
                 WHERE canonical_url = %s
                    OR (normalized_title_hash = %s AND publisher = %s
                        AND published_at IS NOT DISTINCT FROM %s)
                 ORDER BY CASE WHEN canonical_url = %s THEN 0 ELSE 1 END
                 LIMIT 1
                """,
                (
                    item.canonical_url,
                    item.normalized_title_hash,
                    item.publisher,
                    item.published_at,
                    item.canonical_url,
                ),
            )
            row = db.fetchone()
            return None if row is None else _item(row)
        finally:
            db.close()

    def search(
        self,
        *,
        stock_ids: Iterable[str] = (),
        keywords: Iterable[str] = (),
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[NewsItem, ...]:
        stocks = tuple(dict.fromkeys(stock_ids))
        terms = tuple(
            dict.fromkeys(term.strip().casefold() for term in keywords if term.strip())
        )
        clauses = [
            "(%s::timestamptz IS NULL OR COALESCE(published_at, retrieved_at) >= %s)",
            "(%s::timestamptz IS NULL OR COALESCE(published_at, retrieved_at) <= %s)",
        ]
        params: list[object] = [since, since, until, until]
        if stocks or terms:
            matches: list[str] = []
            if stocks:
                matches.append("stock_ids && %s::text[]")
                params.append(list(stocks))
            for term in terms:
                matches.append(
                    "lower(title || ' ' || description || ' ' || array_to_string(entities, ' ')) LIKE %s"
                )
                params.append(f"%{term}%")
            clauses.append("(" + " OR ".join(matches) + ")")
        db = self._connection.cursor()
        try:
            db.execute(
                f"SELECT {_ITEM_COLUMNS} FROM news.items WHERE "
                + " AND ".join(clauses)
                + " ORDER BY COALESCE(published_at, retrieved_at) DESC, news_id DESC",
                tuple(params),
            )
            return tuple(_item(row) for row in db.fetchall())
        finally:
            db.close()

    def get_cursor(self, source_id: str) -> SourceCursor | None:
        db = self._connection.cursor()
        try:
            db.execute(
                """
                SELECT source_id, source_type, last_source_item_id,
                       last_published_at, last_polled_at, next_poll_at,
                       status, last_error, consecutive_failures
                  FROM news.collection_cursors WHERE source_id = %s
                """,
                (source_id,),
            )
            row = db.fetchone()
            if row is None:
                return None
            return SourceCursor(
                source_id=str(row[0]),
                source_type=NewsSourceType(str(row[1])),
                last_source_item_id=None if row[2] is None else str(row[2]),
                last_published_at=cast(datetime | None, row[3]),
                last_polled_at=cast(datetime | None, row[4]),
                next_poll_at=cast(datetime | None, row[5]),
                status=SourceStatus(str(row[6])),
                last_error=None if row[7] is None else str(row[7]),
                consecutive_failures=int(row[8]),
            )
        finally:
            db.close()

    def put_cursor(self, cursor: SourceCursor) -> None:
        db = self._connection.cursor()
        try:
            db.execute(
                """
                INSERT INTO news.collection_cursors (
                    source_id, source_type, last_source_item_id,
                    last_published_at, last_polled_at, next_poll_at,
                    status, last_error, consecutive_failures, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (source_id) DO UPDATE
                   SET source_type = EXCLUDED.source_type,
                       last_source_item_id = EXCLUDED.last_source_item_id,
                       last_published_at = EXCLUDED.last_published_at,
                       last_polled_at = EXCLUDED.last_polled_at,
                       next_poll_at = EXCLUDED.next_poll_at,
                       status = EXCLUDED.status,
                       last_error = EXCLUDED.last_error,
                       consecutive_failures = EXCLUDED.consecutive_failures,
                       updated_at = now()
                """,
                (
                    cursor.source_id,
                    cursor.source_type.value,
                    cursor.last_source_item_id,
                    cursor.last_published_at,
                    cursor.last_polled_at,
                    cursor.next_poll_at,
                    cursor.status.value,
                    cursor.last_error,
                    cursor.consecutive_failures,
                ),
            )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()
