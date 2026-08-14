#!/usr/bin/env python3
"""Shared storage and time helpers for the one-time market replay fixture."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from datetime import time as datetime_time
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = "1.0.0"
COLLECTOR_VERSION = "2026.08.14.1"
KST = timezone(timedelta(hours=9))
UTC = timezone.utc


def load_env_file(path: Path) -> None:
    """Load a simple dotenv file without overriding the process environment."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[:1] == value[-1:] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(value: datetime | None = None) -> str:
    current = value or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat(timespec="microseconds")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def parse_trade_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_clock(value: str) -> datetime_time:
    for pattern in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, pattern).time()
        except ValueError:
            pass
    raise ValueError(f"invalid clock value: {value}")


def market_datetime(trade_date: date, clock: datetime_time) -> datetime:
    return datetime.combine(trade_date, clock, tzinfo=KST)


def source_clock_to_utc(trade_date: date, value: Any, fallback: str) -> str:
    """Convert Kiwoom HHMMSS/HHMMSSmmm values to UTC; preserve receive time on errors."""
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) < 6:
        return fallback
    try:
        clock = datetime.strptime(digits[:6], "%H%M%S").time()
        occurred = datetime.combine(trade_date, clock, tzinfo=KST)
        if len(digits) >= 9:
            occurred = occurred.replace(microsecond=int(digits[6:9]) * 1000)
        return iso_utc(occurred)
    except ValueError:
        return fallback


def normalize_stock_code(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if text.startswith("A") and len(text) == 7 and text[1:].isdigit():
        text = text[1:]
    for suffix in ("_AL", "_NX"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text if len(text) == 6 and text.isdigit() else None


@dataclass(frozen=True)
class EventRecord:
    sequence: int
    run_id: str
    event_type: str
    source: str
    occurred_at: str
    received_at: str
    stock_code: str | None
    source_sequence: str | None
    payload: Any
    payload_sha256: str
    schema_version: str

    def envelope(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "runId": self.run_id,
            "eventType": self.event_type,
            "source": self.source,
            "occurredAt": self.occurred_at,
            "receivedAt": self.received_at,
            "stockCode": self.stock_code,
            "sourceSequence": self.source_sequence,
            "payload": self.payload,
            "payloadSha256": self.payload_sha256,
            "schemaVersion": self.schema_version,
        }


class ReplayStore:
    """Append-only SQLite/NDJSON writer optimized for one trading-day capture."""

    def __init__(self, output_dir: Path, *, batch_size: int = 500) -> None:
        self.output_dir = output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.output_dir / "market-replay.sqlite3"
        self.ndjson_path = self.output_dir / "events.ndjson"
        self.manifest_path = self.output_dir / "manifest.json"
        self.batch_size = batch_size
        self.connection = sqlite3.connect(self.db_path, timeout=30.0)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()
        row = self.connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM events").fetchone()
        self._next_sequence = int(row[0]) + 1
        self._pending: list[tuple[Any, ...]] = []
        self._ndjson = self.ndjson_path.open("a", encoding="utf-8", buffering=1024 * 1024)

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS collection_runs (
                run_id TEXT PRIMARY KEY,
                trade_date TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                settings_json TEXT NOT NULL,
                error TEXT,
                collector_version TEXT NOT NULL,
                schema_version TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                stock_code TEXT,
                source_sequence TEXT,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                schema_version TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_run_received_idx
                ON events(run_id, received_at, sequence);
            CREATE INDEX IF NOT EXISTS events_run_sequence_idx
                ON events(run_id, sequence);
            CREATE INDEX IF NOT EXISTS events_type_idx
                ON events(event_type, stock_code, sequence);
            CREATE TABLE IF NOT EXISTS minute_bars (
                run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
                stock_code TEXT NOT NULL,
                trade_at TEXT NOT NULL,
                open TEXT,
                high TEXT,
                low TEXT,
                close TEXT,
                volume TEXT,
                previous_change TEXT,
                previous_change_sign TEXT,
                source_received_at TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                PRIMARY KEY (run_id, stock_code, trade_at)
            );
            CREATE INDEX IF NOT EXISTS minute_bars_trade_at_idx
                ON minute_bars(run_id, trade_at, stock_code);
            CREATE TABLE IF NOT EXISTS integrity_checks (
                run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
                checked_at TEXT NOT NULL,
                check_name TEXT NOT NULL,
                passed INTEGER NOT NULL,
                details_json TEXT NOT NULL,
                PRIMARY KEY (run_id, checked_at, check_name)
            );
            """
        )
        self.connection.commit()

    def start_run(self, *, trade_date: date, mode: str, settings: dict[str, Any]) -> str:
        run_id = f"market-{trade_date.isoformat()}-{uuid.uuid4().hex[:12]}"
        self.connection.execute(
            """INSERT INTO collection_runs
               (run_id, trade_date, mode, status, started_at, settings_json,
                collector_version, schema_version)
               VALUES (?, ?, ?, 'RUNNING', ?, ?, ?, ?)""",
            (
                run_id,
                trade_date.isoformat(),
                mode,
                iso_utc(),
                canonical_json(settings),
                COLLECTOR_VERSION,
                SCHEMA_VERSION,
            ),
        )
        self.connection.commit()
        return run_id

    def append_event(
        self,
        *,
        run_id: str,
        event_type: str,
        source: str,
        payload: Any,
        received_at: str | None = None,
        occurred_at: str | None = None,
        stock_code: str | None = None,
        source_sequence: str | None = None,
    ) -> EventRecord:
        received = received_at or iso_utc()
        occurred = occurred_at or received
        digest = payload_hash(payload)
        sequence = self._next_sequence
        self._next_sequence += 1
        record = EventRecord(
            sequence=sequence,
            run_id=run_id,
            event_type=event_type,
            source=source,
            occurred_at=occurred,
            received_at=received,
            stock_code=normalize_stock_code(stock_code),
            source_sequence=str(source_sequence) if source_sequence is not None else None,
            payload=payload,
            payload_sha256=digest,
            schema_version=SCHEMA_VERSION,
        )
        self._pending.append(
            (
                record.sequence,
                record.run_id,
                record.event_type,
                record.source,
                record.occurred_at,
                record.received_at,
                record.stock_code,
                record.source_sequence,
                canonical_json(record.payload),
                record.payload_sha256,
                record.schema_version,
            )
        )
        self._ndjson.write(canonical_json(record.envelope()) + "\n")
        if len(self._pending) >= self.batch_size:
            self.flush()
        return record

    def append_minute_bars(
        self,
        *,
        run_id: str,
        stock_code: str,
        rows: Iterable[dict[str, Any]],
        source_received_at: str,
    ) -> int:
        normalized_code = normalize_stock_code(stock_code)
        if not normalized_code:
            raise ValueError(f"invalid stock code: {stock_code}")
        values: list[tuple[Any, ...]] = []
        for row in rows:
            trade_at = str(row.get("cntr_tm") or "")
            values.append(
                (
                    run_id,
                    normalized_code,
                    trade_at,
                    row.get("open_pric"),
                    row.get("high_pric"),
                    row.get("low_pric"),
                    row.get("cur_prc"),
                    row.get("trde_qty"),
                    row.get("pred_pre"),
                    row.get("pred_pre_sig"),
                    source_received_at,
                    canonical_json(row),
                )
            )
        if values:
            self.connection.executemany(
                """INSERT OR REPLACE INTO minute_bars
                   (run_id, stock_code, trade_at, open, high, low, close, volume,
                    previous_change, previous_change_sign, source_received_at, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            self.connection.commit()
        return len(values)

    def flush(self) -> None:
        if self._pending:
            self.connection.executemany(
                """INSERT INTO events
                   (sequence, run_id, event_type, source, occurred_at, received_at,
                    stock_code, source_sequence, payload_json, payload_sha256, schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._pending,
            )
            self.connection.commit()
            self._pending.clear()
        self._ndjson.flush()

    def finish_run(
        self, run_id: str, *, status: str, error: str | None = None
    ) -> dict[str, Any]:
        self.flush()
        self.connection.execute(
            "UPDATE collection_runs SET status=?, finished_at=?, error=? WHERE run_id=?",
            (status, iso_utc(), error, run_id),
        )
        self.connection.commit()
        return self.write_manifest(run_id)

    def write_manifest(self, run_id: str) -> dict[str, Any]:
        self.flush()
        run = self.connection.execute(
            """SELECT run_id, trade_date, mode, status, started_at, finished_at,
                      settings_json, error, collector_version, schema_version
               FROM collection_runs WHERE run_id=?""",
            (run_id,),
        ).fetchone()
        if run is None:
            raise ValueError(f"unknown run: {run_id}")
        counts = {
            row[0]: row[1]
            for row in self.connection.execute(
                "SELECT event_type, COUNT(*) FROM events WHERE run_id=? GROUP BY event_type",
                (run_id,),
            )
        }
        minute_summary = self.connection.execute(
            """SELECT COUNT(DISTINCT stock_code), COUNT(*), MIN(trade_at), MAX(trade_at)
               FROM minute_bars WHERE run_id=?""",
            (run_id,),
        ).fetchone()
        first_last = self.connection.execute(
            "SELECT MIN(sequence), MAX(sequence), COUNT(*) FROM events WHERE run_id=?",
            (run_id,),
        ).fetchone()
        event_digest = hashlib.sha256()
        for sequence, digest in self.connection.execute(
            "SELECT sequence, payload_sha256 FROM events WHERE run_id=? ORDER BY sequence",
            (run_id,),
        ):
            event_digest.update(f"{sequence}:{digest}\n".encode("ascii"))
        reference_digest = hashlib.sha256()
        reference_count = 0
        for event_type, payload_json in self.connection.execute(
            """SELECT event_type,payload_json FROM events
               WHERE run_id=? AND event_type IN
               ('reference.infostock_theme','reference.stock_master')
               ORDER BY event_type,sequence""",
            (run_id,),
        ):
            reference_digest.update(
                (f"{event_type}:" + payload_json + "\n").encode("utf-8")
            )
            reference_count += 1
        manifest = {
            "dataset": "dayjaview-one-time-market-replay",
            "runId": run[0],
            "tradeDate": run[1],
            "mode": run[2],
            "status": run[3],
            "startedAt": run[4],
            "finishedAt": run[5],
            "settings": json.loads(run[6]),
            "error": run[7],
            "collectorVersion": run[8],
            "schemaVersion": run[9],
            "events": {
                "firstSequence": first_last[0],
                "lastSequence": first_last[1],
                "count": first_last[2],
                "byType": counts,
                "sequencePayloadHash": event_digest.hexdigest(),
            },
            "minuteBars": {
                "stockCount": minute_summary[0],
                "rowCount": minute_summary[1],
                "firstTradeAt": minute_summary[2],
                "lastTradeAt": minute_summary[3],
            },
            "references": {
                "count": reference_count,
                "canonicalSnapshotSha256": reference_digest.hexdigest(),
            },
            "files": {
                "database": self.db_path.name,
                "events": self.ndjson_path.name,
                "eventsSha256": file_sha256(self.ndjson_path),
            },
        }
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.manifest_path)
        return manifest

    def close(self) -> None:
        self.flush()
        self._ndjson.close()
        self.connection.close()

    def __enter__(self) -> "ReplayStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def iter_events(
    db_path: Path,
    *,
    run_id: str | None = None,
    event_types: set[str] | None = None,
    received_from: str | None = None,
    received_before: str | None = None,
    order_by_received: bool = False,
    occurred_from: str | None = None,
    occurred_before: str | None = None,
    order_by_occurred: bool = False,
) -> Iterator[EventRecord]:
    connection = sqlite3.connect(db_path)
    try:
        clauses: list[str] = []
        parameters: list[Any] = []
        if run_id:
            clauses.append("run_id=?")
            parameters.append(run_id)
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            parameters.extend(sorted(event_types))
        if received_from:
            clauses.append("received_at>=?")
            parameters.append(received_from)
        if received_before:
            clauses.append("received_at<?")
            parameters.append(received_before)
        if occurred_from:
            clauses.append("occurred_at>=?")
            parameters.append(occurred_from)
        if occurred_before:
            clauses.append("occurred_at<?")
            parameters.append(occurred_before)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        if order_by_received and order_by_occurred:
            raise ValueError("choose only one event-time ordering")
        order_by = (
            "received_at, sequence"
            if order_by_received
            else ("occurred_at, sequence" if order_by_occurred else "sequence")
        )
        query = f"""SELECT sequence, run_id, event_type, source, occurred_at,
                            received_at, stock_code, source_sequence, payload_json,
                            payload_sha256, schema_version
                     FROM events {where} ORDER BY {order_by}"""
        for row in connection.execute(query, parameters):
            yield EventRecord(
                sequence=row[0],
                run_id=row[1],
                event_type=row[2],
                source=row[3],
                occurred_at=row[4],
                received_at=row[5],
                stock_code=row[6],
                source_sequence=row[7],
                payload=json.loads(row[8]),
                payload_sha256=row[9],
                schema_version=row[10],
            )
    finally:
        connection.close()


def latest_run_id(db_path: Path) -> str:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT run_id FROM collection_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise ValueError("database has no collection run")
        return str(row[0])
    finally:
        connection.close()
