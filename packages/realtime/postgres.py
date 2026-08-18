"""PostgreSQL repository for atomic versioned realtime snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Protocol

from .snapshots import (
    ReadSnapshot,
    SnapshotIdempotencyConflict,
    SnapshotPublication,
    SnapshotTopic,
    StaleSnapshotPublication,
    _canonical_json,
    _copy_json_object,
    _opaque,
)


class DbCursor(Protocol):
    rowcount: int

    def execute(
        self,
        query: str,
        params: Sequence[object] | None = None,
    ) -> object: ...

    def fetchone(self) -> Sequence[Any] | None: ...

    def close(self) -> None: ...


class DbConnection(Protocol):
    def cursor(self) -> DbCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


def _json_object(value: object) -> dict[str, object]:
    decoded: object = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, dict):
        raise TypeError("저장된 realtime snapshot JSON이 object가 아닙니다")
    return decoded


class PostgresSnapshotRepository:
    """Atomically increments sequence and records an idempotency receipt."""

    def __init__(self, connection: DbConnection) -> None:
        self._connection = connection

    def publish(self, publication: SnapshotPublication) -> ReadSnapshot:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"snapshot-publication:{publication.publication_id}",),
            )
            existing = self._find_publication(cursor, publication.publication_id)
            if existing is not None:
                fingerprint, snapshot = existing
                if fingerprint != publication.fingerprint:
                    raise SnapshotIdempotencyConflict(
                        "같은 publication_id에 서로 다른 snapshot이 있습니다"
                    )
                self._connection.commit()
                return snapshot

            scope_lock = (
                f"snapshot-scope:{publication.stream_id}:"
                f"{publication.topic.value}:{publication.params_key}"
            )
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (scope_lock,),
            )
            cursor.execute(
                """
                SELECT as_of
                  FROM serving.realtime_snapshots
                 WHERE stream_id = %s AND topic = %s AND params_key = %s
                 ORDER BY sequence DESC
                 LIMIT 1
                """,
                (
                    publication.stream_id,
                    publication.topic.value,
                    publication.params_key,
                ),
            )
            latest_row = cursor.fetchone()
            if latest_row is not None and publication.as_of < latest_row[0]:
                raise StaleSnapshotPublication(
                    "snapshot as_of가 현재 full snapshot보다 과거입니다"
                )
            cursor.execute(
                """
                INSERT INTO serving.realtime_stream_sequences (
                    stream_id, topic, params_key, last_sequence
                ) VALUES (%s, %s, %s, 0)
                ON CONFLICT DO NOTHING
                """,
                (
                    publication.stream_id,
                    publication.topic.value,
                    publication.params_key,
                ),
            )
            cursor.execute(
                """
                SELECT last_sequence
                  FROM serving.realtime_stream_sequences
                 WHERE stream_id = %s AND topic = %s AND params_key = %s
                 FOR UPDATE
                """,
                (
                    publication.stream_id,
                    publication.topic.value,
                    publication.params_key,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("realtime stream sequence row를 찾을 수 없습니다")
            sequence = int(row[0]) + 1
            cursor.execute(
                """
                UPDATE serving.realtime_stream_sequences
                   SET last_sequence = %s, updated_at = now()
                 WHERE stream_id = %s AND topic = %s AND params_key = %s
                """,
                (
                    sequence,
                    publication.stream_id,
                    publication.topic.value,
                    publication.params_key,
                ),
            )
            snapshot = self._build_snapshot(publication, sequence=sequence)
            cursor.execute(
                """
                INSERT INTO serving.realtime_snapshots (
                    snapshot_id, stream_id, topic, params_key, sequence,
                    schema_version, market_date, generated_at, as_of,
                    data_status, quality_flags, versions, payload,
                    snapshot_json, content_hash
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s
                )
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.stream_id,
                    snapshot.topic.value,
                    snapshot.params_key,
                    snapshot.sequence,
                    snapshot.versions.schema_version,
                    snapshot.market_date,
                    snapshot.generated_at,
                    snapshot.as_of,
                    snapshot.data_status.value,
                    list(snapshot.quality_flags),
                    _canonical_json(snapshot.versions.to_dict()),
                    _canonical_json(snapshot.payload),
                    _canonical_json(snapshot.to_dict()),
                    snapshot.content_hash,
                ),
            )
            cursor.execute(
                """
                INSERT INTO serving.realtime_snapshot_requests (
                    publication_id, request_fingerprint, snapshot_id
                ) VALUES (%s, %s, %s)
                """,
                (
                    publication.publication_id,
                    publication.fingerprint,
                    snapshot.snapshot_id,
                ),
            )
            self._connection.commit()
            return snapshot
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def latest(
        self,
        *,
        stream_id: str,
        topic: SnapshotTopic,
        params: dict[str, object],
    ) -> ReadSnapshot | None:
        params_key = _opaque("params", _canonical_json(params))
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                """
                SELECT snapshot_json
                  FROM serving.realtime_snapshots
                 WHERE stream_id = %s AND topic = %s AND params_key = %s
                 ORDER BY sequence DESC
                 LIMIT 1
                """,
                (stream_id, topic.value, params_key),
            )
            row = cursor.fetchone()
            return None if row is None else ReadSnapshot.from_dict(_json_object(row[0]))
        finally:
            cursor.close()

    def latest_for_market_date(
        self,
        *,
        topic: SnapshotTopic,
        params: dict[str, object],
        market_date: date,
        as_of_until: datetime,
    ) -> ReadSnapshot | None:
        """그 거래일 as_of_until 이전의 마지막 발행분 (stream 무관, 복원용)."""

        params_key = _opaque("params", _canonical_json(params))
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                """
                SELECT snapshot_json
                  FROM serving.realtime_snapshots
                 WHERE topic = %s AND params_key = %s
                   AND market_date = %s AND as_of <= %s
                 ORDER BY as_of DESC, generated_at DESC, sequence DESC
                 LIMIT 1
                """,
                (topic.value, params_key, market_date, as_of_until),
            )
            row = cursor.fetchone()
            return None if row is None else ReadSnapshot.from_dict(_json_object(row[0]))
        finally:
            cursor.close()

    @staticmethod
    def _find_publication(
        cursor: DbCursor,
        publication_id: str,
    ) -> tuple[str, ReadSnapshot] | None:
        cursor.execute(
            """
            SELECT request_fingerprint, snapshot_json
              FROM serving.realtime_snapshot_requests request
              JOIN serving.realtime_snapshots snapshot
                ON snapshot.snapshot_id = request.snapshot_id
             WHERE request.publication_id = %s
            """,
            (publication_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return str(row[0]), ReadSnapshot.from_dict(_json_object(row[1]))

    @staticmethod
    def _build_snapshot(
        publication: SnapshotPublication,
        *,
        sequence: int,
    ) -> ReadSnapshot:
        snapshot_id = _opaque(
            "snap",
            f"{publication.publication_id}:{publication.fingerprint}",
        )
        payload = _copy_json_object(publication.payload)
        content_hash = hashlib.sha256(
            _canonical_json(
                {
                    "scope": [
                        publication.stream_id,
                        publication.topic.value,
                        publication.params_key,
                    ],
                    "sequence": sequence,
                    "payload": payload,
                    "versions": publication.versions.to_dict(),
                }
            ).encode("utf-8")
        ).hexdigest()
        return ReadSnapshot(
            snapshot_id=snapshot_id,
            publication_id=publication.publication_id,
            stream_id=publication.stream_id,
            topic=publication.topic,
            params_key=publication.params_key,
            sequence=sequence,
            market_date=publication.market_date,
            generated_at=publication.generated_at,
            as_of=publication.as_of,
            data_status=publication.data_status,
            quality_flags=tuple(sorted(publication.quality_flags)),
            payload=payload,
            versions=publication.versions,
            content_hash=content_hash,
        )
