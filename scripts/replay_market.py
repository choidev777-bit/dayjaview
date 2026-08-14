#!/usr/bin/env python3
"""Verify and replay a DAYJAVIEW one-time market capture database."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import heapq
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from datetime import time as datetime_time
from itertools import chain
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Iterator

import websockets
from market_replay_common import (
    KST,
    EventRecord,
    canonical_json,
    iter_events,
    latest_run_id,
    normalize_stock_code,
    payload_hash,
)


class VerificationError(RuntimeError):
    pass


SERVICE_EVENT_TYPES = {
    "candidate.condition",
    "candidate.rest",
    "market.breadth",
    "market.index",
    "market.minute_state.recovered",
    "market.other",
    "market.snapshot",
    "market.trade",
    "source.error",
    "source.status",
    "subscription.changed",
    "supplemental.coverage",
}


def parse_event_types(values: Iterable[str]) -> set[str] | None:
    result = {item.strip() for value in values for item in value.split(",") if item.strip()}
    return result or None


def verify_database(db_path: Path, run_id: str | None = None) -> dict[str, Any]:
    if not db_path.exists():
        raise VerificationError(f"database does not exist: {db_path}")
    resolved_run = run_id or latest_run_id(db_path)
    connection = sqlite3.connect(db_path)
    checks: list[dict[str, Any]] = []
    try:
        run = connection.execute(
            "SELECT trade_date, status, started_at, finished_at FROM collection_runs WHERE run_id=?",
            (resolved_run,),
        ).fetchone()
        if run is None:
            raise VerificationError(f"unknown run: {resolved_run}")
        trade_date, status, started_at, finished_at = run
        checks.append(
            {
                "name": "run_completed",
                "passed": status == "COMPLETED" and bool(finished_at),
                "details": {"status": status, "finishedAt": finished_at},
            }
        )

        event_rows = connection.execute(
            """SELECT sequence, occurred_at, received_at, payload_json, payload_sha256,
                      event_type
               FROM events WHERE run_id=? ORDER BY sequence""",
            (resolved_run,),
        )

        bad_hashes: list[int] = []
        invalid_times: list[int] = []
        event_counts: Counter[str] = Counter()
        event_digest = hashlib.sha256()
        first_sequence: int | None = None
        previous_sequence: int | None = None
        last_sequence: int | None = None
        event_count = 0
        sequence_ok = True
        previous_received: datetime | None = None
        received_regression_count = 0
        max_received_regression_seconds = 0.0
        previous_trade_received: datetime | None = None
        max_trade_gap_seconds = 0.0
        trade_wrong_day_count = 0
        trade_negative_latency_count = 0
        trade_clock_ahead_over_one_second_count = 0
        trade_over_five_second_count = 0
        trade_latency_milliseconds: Counter[int] = Counter()
        sensitive_payload_count = 0
        sensitive_payload_sequences: list[int] = []
        secret_markers = (
            '\"token\":',
            '\"authorization\":',
            '\"appkey\":',
            '\"secretkey\":',
        )
        for sequence, occurred_at, received_at, payload_json, expected_hash, event_type in event_rows:
            sequence = int(sequence)
            if first_sequence is None:
                first_sequence = sequence
            elif previous_sequence is not None and sequence != previous_sequence + 1:
                sequence_ok = False
            previous_sequence = sequence
            last_sequence = sequence
            event_count += 1
            event_counts[str(event_type)] += 1
            event_digest.update(f"{sequence}:{expected_hash}\n".encode("ascii"))
            lower_payload_json = payload_json.lower()
            if any(marker in lower_payload_json for marker in secret_markers):
                sensitive_payload_count += 1
                if len(sensitive_payload_sequences) < 20:
                    sensitive_payload_sequences.append(sequence)
            try:
                # ReplayStore persists canonical JSON.  Hash its UTF-8 bytes
                # directly on the hot path; parse only a non-canonical or
                # mismatching row to preserve compatibility with old fixtures.
                actual_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
                if actual_hash != expected_hash:
                    payload = json.loads(payload_json)
                    if payload_hash(payload) != expected_hash:
                        bad_hashes.append(sequence)
                occurred_datetime = datetime.fromisoformat(occurred_at)
                received_datetime = datetime.fromisoformat(received_at)
                if previous_received is not None and received_datetime < previous_received:
                    received_regression_count += 1
                    max_received_regression_seconds = max(
                        max_received_regression_seconds,
                        (previous_received - received_datetime).total_seconds(),
                    )
                previous_received = received_datetime
                if event_type == "market.trade":
                    if occurred_datetime.astimezone(KST).date().isoformat() != trade_date:
                        trade_wrong_day_count += 1
                    trade_latency = (received_datetime - occurred_datetime).total_seconds()
                    if trade_latency < 0:
                        trade_negative_latency_count += 1
                    if trade_latency < -1:
                        trade_clock_ahead_over_one_second_count += 1
                    if trade_latency > 5:
                        trade_over_five_second_count += 1
                    latency_bucket = max(
                        -60_000, min(60_000, int(round(trade_latency * 1000)))
                    )
                    trade_latency_milliseconds[latency_bucket] += 1
                    if previous_trade_received is not None:
                        max_trade_gap_seconds = max(
                            max_trade_gap_seconds,
                            (received_datetime - previous_trade_received).total_seconds(),
                        )
                    previous_trade_received = received_datetime
            except (ValueError, TypeError, json.JSONDecodeError):
                invalid_times.append(sequence)
        sequence_ok = sequence_ok and event_count > 0
        checks.append(
            {
                "name": "sequence_contiguous",
                "passed": sequence_ok,
                "details": {
                    "first": first_sequence,
                    "last": last_sequence,
                    "count": event_count,
                },
            }
        )
        checks.append(
            {
                "name": "payload_hashes",
                "passed": not bad_hashes,
                "details": {"badSequences": bad_hashes[:20], "badCount": len(bad_hashes)},
            }
        )
        checks.append(
            {
                "name": "timestamps_parse",
                "passed": not invalid_times,
                "details": {
                    "badSequences": invalid_times[:20],
                    "badCount": len(invalid_times),
                },
            }
        )

        wrong_day_bars = connection.execute(
            """SELECT COUNT(*) FROM minute_bars
               WHERE run_id=? AND substr(trade_at, 1, 8) != replace(?, '-', '')""",
            (resolved_run, trade_date),
        ).fetchone()[0]
        checks.append(
            {
                "name": "minute_bar_trade_date",
                "passed": wrong_day_bars == 0,
                "details": {"wrongDayCount": wrong_day_bars},
            }
        )
        minute_summary = connection.execute(
            "SELECT COUNT(DISTINCT stock_code), COUNT(*) FROM minute_bars WHERE run_id=?",
            (resolved_run,),
        ).fetchone()

        def latency_percentile(percentile: float) -> float | None:
            total = sum(trade_latency_milliseconds.values())
            if not total:
                return None
            target = max(1, int(total * percentile + 0.999999))
            seen = 0
            for milliseconds, count in sorted(trade_latency_milliseconds.items()):
                seen += count
                if seen >= target:
                    return milliseconds / 1000
            return None

        result = {
            "database": str(db_path.resolve()),
            "runId": resolved_run,
            "tradeDate": trade_date,
            "startedAt": started_at,
            "finishedAt": finished_at,
            "passed": all(check["passed"] for check in checks),
            "checks": checks,
            "eventCount": event_count,
            "eventsByType": dict(sorted(event_counts.items())),
            "sequencePayloadHash": event_digest.hexdigest(),
            "minuteBars": {
                "stockCount": minute_summary[0],
                "rowCount": minute_summary[1],
            },
            "continuity": {
                "receivedAtRegressionCount": received_regression_count,
                "maxReceivedAtRegressionSeconds": round(
                    max_received_regression_seconds, 6
                ),
                "maxTradeGapSeconds": round(max_trade_gap_seconds, 6),
                "tradeWrongDayCount": trade_wrong_day_count,
                "tradeNegativeLatencyCount": trade_negative_latency_count,
                "tradeClockAheadOverOneSecondCount": (
                    trade_clock_ahead_over_one_second_count
                ),
                "tradeOverFiveSecondCount": trade_over_five_second_count,
                "tradeLatencySeconds": {
                    "p50": latency_percentile(0.50),
                    "p95": latency_percentile(0.95),
                    "p99": latency_percentile(0.99),
                    "maxBucket": (
                        max(trade_latency_milliseconds) / 1000
                        if trade_latency_milliseconds
                        else None
                    ),
                },
            },
            "sensitivePayloads": {
                "count": sensitive_payload_count,
                "sequences": sensitive_payload_sequences,
            },
        }
        return result
    finally:
        connection.close()


def prove_replay_files(db_path: Path, run_id: str | None = None) -> dict[str, Any]:
    """Stream DB and NDJSON and prove they encode the same replay envelopes."""
    if not db_path.exists():
        raise VerificationError(f"database does not exist: {db_path}")
    resolved_run = run_id or latest_run_id(db_path)
    ndjson_path = db_path.parent / "events.ndjson"
    manifest_path = db_path.parent / "manifest.json"

    db_digest = hashlib.sha256()
    sequence_digest = hashlib.sha256()
    service_digest = hashlib.sha256()
    reference_digest = hashlib.sha256()
    db_count = 0
    service_count = 0
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            """SELECT sequence,run_id,event_type,source,occurred_at,received_at,
                      stock_code,source_sequence,payload_json,payload_sha256,schema_version
               FROM events WHERE run_id=? ORDER BY sequence""",
            (resolved_run,),
        )
        for row in rows:
            (
                sequence,
                row_run_id,
                event_type,
                source,
                occurred_at,
                received_at,
                stock_code,
                source_sequence,
                payload_json,
                payload_sha256,
                schema_version,
            ) = row
            # Keys are in canonical sort order and payload_json is already
            # canonical, so this is byte-identical to canonical_json(envelope)
            # without allocating/parsing a nested payload object.
            envelope_line = (
                "{\"eventType\":"
                + canonical_json(event_type)
                + ",\"occurredAt\":"
                + canonical_json(occurred_at)
                + ",\"payload\":"
                + payload_json
                + ",\"payloadSha256\":"
                + canonical_json(payload_sha256)
                + ",\"receivedAt\":"
                + canonical_json(received_at)
                + ",\"runId\":"
                + canonical_json(row_run_id)
                + ",\"schemaVersion\":"
                + canonical_json(schema_version)
                + ",\"sequence\":"
                + str(sequence)
                + ",\"source\":"
                + canonical_json(source)
                + ",\"sourceSequence\":"
                + canonical_json(source_sequence)
                + ",\"stockCode\":"
                + canonical_json(stock_code)
                + "}\n"
            ).encode("utf-8")
            db_digest.update(envelope_line)
            sequence_digest.update(
                f"{sequence}:{payload_sha256}\n".encode("ascii")
            )
            db_count += 1
            if event_type in SERVICE_EVENT_TYPES:
                service_digest.update(envelope_line)
                service_count += 1
            if event_type in {"reference.infostock_theme", "reference.stock_master"}:
                reference_digest.update(
                    (f"{event_type}:" + payload_json + "\n").encode("utf-8")
                )
    finally:
        connection.close()

    ndjson_digest = hashlib.sha256()
    ndjson_file_digest = hashlib.sha256()
    ndjson_count = 0
    framing_errors = 0
    target_marker = (
        b',"runId":' + canonical_json(resolved_run).encode("utf-8") + b',"schemaVersion":'
    )
    if ndjson_path.exists():
        with ndjson_path.open("rb") as stream:
            for raw_line in stream:
                ndjson_file_digest.update(raw_line)
                if not raw_line.endswith((b"\n", b"\r")):
                    framing_errors += 1
                stripped = raw_line.rstrip(b"\r\n")
                run_marker_position = stripped.rfind(b',"runId":')
                if run_marker_position < 0 or not stripped[
                    run_marker_position:
                ].startswith(target_marker):
                    continue
                ndjson_digest.update(stripped + b"\n")
                ndjson_count += 1

    manifest: dict[str, Any] | None = None
    manifest_error: str | None = None
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
            else:
                manifest_error = "manifest root is not an object"
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            manifest_error = str(exc)

    db_hash = db_digest.hexdigest()
    ndjson_hash = ndjson_digest.hexdigest()
    raw_file_hash = ndjson_file_digest.hexdigest()
    manifest_events = (manifest or {}).get("events") or {}
    manifest_references = (manifest or {}).get("references") or {}
    manifest_files = (manifest or {}).get("files") or {}
    checks = [
        _audit_check("ndjson_exists", ndjson_path.exists(), str(ndjson_path)),
        _audit_check(
            "db_ndjson_event_count",
            db_count > 0 and db_count == ndjson_count,
            {"database": db_count, "ndjson": ndjson_count},
        ),
        _audit_check(
            "db_ndjson_envelope_hash",
            db_count > 0 and db_hash == ndjson_hash,
            {"database": db_hash, "ndjson": ndjson_hash},
        ),
        _audit_check(
            "ndjson_framing",
            framing_errors == 0,
            {"framingErrorCount": framing_errors},
        ),
        _audit_check(
            "manifest_present",
            manifest is not None and manifest_error is None,
            {"path": str(manifest_path), "error": manifest_error},
        ),
        _audit_check(
            "manifest_run",
            bool(manifest)
            and manifest.get("runId") == resolved_run
            and manifest.get("status") == "COMPLETED",
            {
                "expectedRunId": resolved_run,
                "actualRunId": (manifest or {}).get("runId"),
                "status": (manifest or {}).get("status"),
            },
        ),
        _audit_check(
            "manifest_event_count",
            manifest_events.get("count") == db_count,
            {"database": db_count, "manifest": manifest_events.get("count")},
        ),
        _audit_check(
            "manifest_sequence_payload_hash",
            manifest_events.get("sequencePayloadHash") == sequence_digest.hexdigest(),
            {
                "database": sequence_digest.hexdigest(),
                "manifest": manifest_events.get("sequencePayloadHash"),
            },
        ),
        _audit_check(
            "manifest_reference_snapshot_hash",
            manifest_references.get("canonicalSnapshotSha256")
            == reference_digest.hexdigest(),
            {
                "database": reference_digest.hexdigest(),
                "manifest": manifest_references.get("canonicalSnapshotSha256"),
            },
        ),
        _audit_check(
            "manifest_ndjson_file_hash",
            bool(manifest)
            and manifest_files.get("eventsSha256") == raw_file_hash,
            {
                "actual": raw_file_hash,
                "manifest": manifest_files.get("eventsSha256"),
            },
        ),
    ]
    return {
        "database": str(db_path.resolve()),
        "runId": resolved_run,
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "allEvents": {"count": db_count, "envelopeSha256": db_hash},
        "serviceReplay": {
            "count": service_count,
            "envelopeSha256": service_digest.hexdigest(),
        },
    }


def _audit_check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def discover_capture_logs(db_path: Path, trade_date: str) -> list[Path]:
    candidates = [db_path.parent / "collector.log"]
    for ancestor in db_path.parents:
        if ancestor.name.lower() == "data":
            log_dir = ancestor.parent / "logs"
            candidates.extend(sorted(log_dir.glob(f"market-capture-{trade_date}*.log")))
            break
    return sorted({path.resolve() for path in candidates if path.is_file()})


def audit_capture(
    db_path: Path,
    run_id: str | None = None,
    log_paths: Iterable[Path] | None = None,
    integrity_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prove that a completed capture contains every required replay input."""
    integrity = integrity_result or verify_database(db_path, run_id)
    if run_id is not None and integrity.get("runId") != run_id:
        raise VerificationError("integrity result belongs to a different run")
    resolved_run = integrity["runId"]
    connection = sqlite3.connect(db_path)
    checks = list(integrity["checks"])
    warnings: list[dict[str, Any]] = []
    try:
        by_type: Counter[str] = Counter(
            {
                str(event_type): int(count)
                for event_type, count in connection.execute(
                    "SELECT event_type,COUNT(*) FROM events "
                    "WHERE run_id=? GROUP BY event_type",
                    (resolved_run,),
                )
            }
        )
        event_count = sum(by_type.values())
        rest_api_ids: set[str] = set()
        index_items: set[str] = set()
        breadth_items: set[str] = set()
        market_bounds = connection.execute(
            "SELECT MIN(received_at),MAX(received_at) FROM events "
            "WHERE run_id=? AND event_type IN "
            "('market.trade','market.index','market.breadth','candidate.rest')",
            (resolved_run,),
        ).fetchone()
        first_market = (
            datetime.fromisoformat(market_bounds[0]).astimezone(KST)
            if market_bounds and market_bounds[0]
            else None
        )
        last_market = (
            datetime.fromisoformat(market_bounds[1]).astimezone(KST)
            if market_bounds and market_bounds[1]
            else None
        )
        previous_rest_by_api: dict[str, datetime] = {}
        max_rest_gap_by_api: dict[str, float] = {}
        rest_count_by_api: Counter[str] = Counter()
        master_stock_codes: set[str] = set()
        for payload_json, received_at in connection.execute(
            "SELECT payload_json,received_at FROM events "
            "WHERE run_id=? AND event_type='kiwoom.rest.raw' ORDER BY sequence",
            (resolved_run,),
        ):
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict) and payload.get("apiId"):
                api_id = str(payload["apiId"])
                rest_api_ids.add(api_id)
            else:
                api_id = ""
            try:
                received = datetime.fromisoformat(received_at)
            except (TypeError, ValueError):
                received = None
            if received is not None and api_id:
                previous_rest = previous_rest_by_api.get(api_id)
                if previous_rest is not None:
                    max_rest_gap_by_api[api_id] = max(
                        max_rest_gap_by_api.get(api_id, 0.0),
                        (received - previous_rest).total_seconds(),
                    )
                previous_rest_by_api[api_id] = received
                rest_count_by_api[api_id] += 1

        signal_previous: dict[tuple[str, str], datetime] = {}
        signal_max_gaps: dict[tuple[str, str], float] = {}
        signal_counts: Counter[tuple[str, str]] = Counter()
        for event_type, received_at, payload_json in connection.execute(
            "SELECT event_type,received_at,payload_json FROM events "
            "WHERE run_id=? AND event_type IN ('market.index','market.breadth')",
            (resolved_run,),
        ):
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or not payload.get("item"):
                continue
            if event_type == "market.index":
                index_items.add(str(payload["item"]))
            else:
                breadth_items.add(str(payload["item"]))
            signal_key = (str(event_type), str(payload["item"]))
            try:
                signal_received = datetime.fromisoformat(received_at)
            except (TypeError, ValueError):
                continue
            previous_signal = signal_previous.get(signal_key)
            if previous_signal is not None:
                signal_max_gaps[signal_key] = max(
                    signal_max_gaps.get(signal_key, 0.0),
                    (signal_received - previous_signal).total_seconds(),
                )
            signal_previous[signal_key] = signal_received
            signal_counts[signal_key] += 1

        for (payload_json,) in connection.execute(
            "SELECT payload_json FROM events "
            "WHERE run_id=? AND event_type='reference.stock_master'",
            (resolved_run,),
        ):
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                continue
            response = payload.get("response") if isinstance(payload, dict) else None
            if isinstance(response, dict):
                for item in response.get("list") or []:
                    if isinstance(item, dict):
                        code = normalize_stock_code(item.get("code"))
                        if code:
                            master_stock_codes.add(code)

        completed_backfills = {
            str(stock_code)
            for (stock_code,) in connection.execute(
                "SELECT DISTINCT stock_code FROM events WHERE run_id=? "
                "AND event_type IN "
                "('backfill.minute.completed','backfill.repair.completed') "
                "AND stock_code IS NOT NULL",
                (resolved_run,),
            )
        }
        failed_backfills = {
            str(stock_code)
            for (stock_code,) in connection.execute(
                "SELECT DISTINCT stock_code FROM events WHERE run_id=? "
                "AND event_type IN "
                "('backfill.minute.failed','backfill.repair.failed') "
                "AND stock_code IS NOT NULL",
                (resolved_run,),
            )
        }

        required_types = {
            "candidate.condition",
            "candidate.condition_list",
            "candidate.rest",
            "kiwoom.rest.raw",
            "kiwoom.websocket.raw",
            "market.index",
            "market.breadth",
            "market.trade",
            "reference.infostock_theme",
            "reference.stock_master",
            "source.status",
            "subscription.changed",
        }
        missing_types = sorted(required_types - set(by_type))
        checks.append(
            _audit_check(
                "required_event_types",
                not missing_types,
                {"missing": missing_types, "counts": dict(sorted(by_type.items()))},
            )
        )

        required_candidate_rest_ids = {"ka10019", "ka10023", "ka10027", "ka10032"}
        selected_condition_contract_ids = {
            "7", "12", "19", "25", "35", "54", "56", "71"
        }
        candidate_rest_counts: Counter[str] = Counter()
        candidate_condition_counts: Counter[str] = Counter()
        candidate_condition_actions: Counter[str] = Counter()
        candidate_rest_stocks: set[str] = set()
        candidate_condition_stocks: set[str] = set()
        candidate_contract_failures = 0
        for event_type, stock_code, payload_json in connection.execute(
            "SELECT event_type,stock_code,payload_json FROM events "
            "WHERE run_id=? AND event_type IN "
            "('candidate.rest','candidate.condition')",
            (resolved_run,),
        ):
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                candidate_contract_failures += 1
                continue
            if not isinstance(payload, dict):
                candidate_contract_failures += 1
                continue
            if event_type == "candidate.rest":
                api_id = str(payload.get("apiId") or "")
                candidate_rest_counts[api_id] += 1
                if stock_code:
                    candidate_rest_stocks.add(str(stock_code))
                if (
                    api_id not in required_candidate_rest_ids
                    or not isinstance(payload.get("raw"), dict)
                    or not isinstance(payload.get("rank"), int)
                ):
                    candidate_contract_failures += 1
            else:
                values = payload.get("values")
                if isinstance(values, dict):
                    condition_id = str(values.get("841") or "")
                    action = str(values.get("843") or "")
                    valid_condition_payload = (
                        payload.get("type") == "02"
                        and bool(payload.get("item"))
                        and condition_id in selected_condition_contract_ids
                        and action in {"I", "D"}
                    )
                else:
                    condition_id = str(payload.get("conditionId") or "")
                    action = str(payload.get("action") or "")
                    valid_condition_payload = (
                        action == "INITIAL"
                        and condition_id in selected_condition_contract_ids
                        and isinstance(payload.get("rank"), int)
                        and isinstance(payload.get("raw"), dict)
                    )
                candidate_condition_counts[condition_id] += 1
                candidate_condition_actions[action] += 1
                if stock_code:
                    candidate_condition_stocks.add(str(stock_code))
                if not valid_condition_payload:
                    candidate_contract_failures += 1
        checks.append(
            _audit_check(
                "candidate_discovery_contract",
                required_candidate_rest_ids <= set(candidate_rest_counts)
                and bool(candidate_rest_stocks)
                and bool(candidate_condition_counts)
                and set(candidate_condition_counts) <= selected_condition_contract_ids
                and bool(candidate_condition_stocks)
                and set(candidate_condition_actions) <= {"INITIAL", "I", "D"}
                and candidate_contract_failures == 0,
                {
                    "restCountByApi": dict(sorted(candidate_rest_counts.items())),
                    "restDistinctStockCount": len(candidate_rest_stocks),
                    "conditionCountById": dict(
                        sorted(candidate_condition_counts.items())
                    ),
                    "conditionActions": dict(
                        sorted(candidate_condition_actions.items())
                    ),
                    "conditionDistinctStockCount": len(candidate_condition_stocks),
                    "invalidEventCount": candidate_contract_failures,
                },
            )
        )

        theme_detail_count = 0
        theme_index_count = 0
        theme_ids: set[str] = set()
        stock_to_theme_ids: dict[str, set[str]] = defaultdict(set)
        theme_hash_failures: list[dict[str, Any]] = []
        for (payload_json,) in connection.execute(
            "SELECT payload_json FROM events "
            "WHERE run_id=? AND event_type='reference.infostock_theme'",
            (resolved_run,),
        ):
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                if len(theme_hash_failures) < 20:
                    theme_hash_failures.append({"reason": "invalid_json"})
                continue
            content = payload.get("content") if isinstance(payload, dict) else None
            if not isinstance(content, dict):
                if len(theme_hash_failures) < 20:
                    theme_hash_failures.append({"reason": "missing_content"})
                continue
            source_type = str(content.get("sourceType") or "")
            theme_id = str(content.get("themeId") or "")
            if source_type == "theme_index":
                theme_index_count += 1
                actual_hash = payload_hash(content.get("items") or [])
                expected_hash = str(content.get("contentHash") or "")
                if actual_hash != expected_hash and len(theme_hash_failures) < 20:
                    theme_hash_failures.append(
                        {
                            "sourceType": source_type,
                            "expected": expected_hash,
                            "actual": actual_hash,
                        }
                    )
                continue
            if source_type != "theme_detail":
                if len(theme_hash_failures) < 20:
                    theme_hash_failures.append(
                        {"sourceType": source_type, "reason": "unknown_source_type"}
                    )
                continue
            theme_detail_count += 1
            if theme_id:
                theme_ids.add(theme_id)
            for related_stock in content.get("relatedStocks") or []:
                if isinstance(related_stock, dict):
                    related_code = normalize_stock_code(
                        related_stock.get("stockCode")
                    )
                    if related_code and theme_id:
                        stock_to_theme_ids[related_code].add(theme_id)
            hash_input = {
                "themeId": theme_id,
                "themeName": str(content.get("themeName") or "").strip(),
                "description": str(content.get("description") or "").strip(),
                "history": content.get("history") or [],
                "relatedStocks": content.get("relatedStocks") or [],
            }
            actual_hash = payload_hash(hash_input)
            expected_hash = str(content.get("contentHash") or "")
            if actual_hash != expected_hash and len(theme_hash_failures) < 20:
                theme_hash_failures.append(
                    {
                        "themeId": theme_id,
                        "expected": expected_hash,
                        "actual": actual_hash,
                    }
                )
        reference_error_count = by_type.get("reference.error", 0)
        checks.append(
            _audit_check(
                "infostock_reference_integrity",
                theme_detail_count > 0
                and len(theme_ids) == theme_detail_count
                and theme_index_count == 1
                and reference_error_count == 0,
                {
                    "themeDetailCount": theme_detail_count,
                    "themeIndexCount": theme_index_count,
                    "uniqueThemeIdCount": len(theme_ids),
                    "referenceErrorCount": reference_error_count,
                },
            )
        )
        warnings.append(
            {
                "name": "infostock_embedded_content_hashes",
                "passed": not theme_hash_failures,
                "details": {
                    "staleOrInvalidCount": len(theme_hash_failures),
                    "items": theme_hash_failures,
                    "note": (
                        "warning only; frozen canonical content is protected by each "
                        "event payload SHA-256 and the manifest reference snapshot hash"
                    ),
                },
            }
        )

        master_markets: dict[str, int] = {}
        master_payload_failures = 0
        for (payload_json,) in connection.execute(
            "SELECT payload_json FROM events "
            "WHERE run_id=? AND event_type='reference.stock_master'",
            (resolved_run,),
        ):
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                master_payload_failures += 1
                continue
            market = str(payload.get("market") or "") if isinstance(payload, dict) else ""
            response = payload.get("response") if isinstance(payload, dict) else None
            rows = response.get("list") if isinstance(response, dict) else None
            if market not in {"KOSPI", "KOSDAQ"} or not isinstance(rows, list):
                master_payload_failures += 1
                continue
            numeric_codes = {
                code
                for row in rows
                if isinstance(row, dict)
                for code in [normalize_stock_code(row.get("code"))]
                if code
            }
            master_markets[market] = len(numeric_codes)
        checks.append(
            _audit_check(
                "stock_master_integrity",
                {"KOSPI", "KOSDAQ"} <= set(master_markets)
                and all(master_markets[market] > 0 for market in ("KOSPI", "KOSDAQ"))
                and master_payload_failures == 0,
                {
                    "numericCodesByMarket": master_markets,
                    "invalidPayloadCount": master_payload_failures,
                },
            )
        )

        expected_condition_ids = {"7", "12", "19", "25", "35", "54", "56", "71"}
        selected_condition_ids: set[str] = set()
        source_status_counts: Counter[str] = Counter()
        for (payload_json,) in connection.execute(
            "SELECT payload_json FROM events "
            "WHERE run_id=? AND event_type='source.status'",
            (resolved_run,),
        ):
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            status_name = str(payload.get("status") or "")
            if status_name:
                source_status_counts[status_name] += 1
            if status_name != "CONDITIONS_SELECTED":
                continue
            for condition in payload.get("selected") or []:
                if isinstance(condition, dict) and condition.get("seq") is not None:
                    selected_condition_ids.add(str(condition["seq"]))
        checks.append(
            _audit_check(
                "selected_conditions",
                expected_condition_ids <= selected_condition_ids,
                {
                    "required": sorted(expected_condition_ids),
                    "observed": sorted(selected_condition_ids),
                },
            )
        )
        required_lifecycle_statuses = {
            "INFOSTOCK_FROZEN",
            "STOCK_MASTER_READY",
            "PREOPEN_READY",
            "WEBSOCKET_CONNECTED",
            "CONDITIONS_SELECTED",
            "REALTIME_CAPTURE_ENDED",
            "MINUTE_BACKFILL_STARTED",
            "MINUTE_BACKFILL_FINISHED",
        }
        checks.append(
            _audit_check(
                "capture_lifecycle_complete",
                required_lifecycle_statuses <= set(source_status_counts),
                {
                    "required": sorted(required_lifecycle_statuses),
                    "counts": dict(sorted(source_status_counts.items())),
                    "missing": sorted(
                        required_lifecycle_statuses - set(source_status_counts)
                    ),
                },
            )
        )

        subscription_count = 0
        stock_subscription_count = 0
        subscription_seconds: Counter[str] = Counter()
        no_change_subscription_count = 0
        subscription_violations: list[dict[str, Any]] = []
        saw_full_subscription = False
        saw_index_and_breadth_request = False
        saw_trade_request = False
        for received_at, payload_json in connection.execute(
            "SELECT received_at,payload_json FROM events "
            "WHERE run_id=? AND event_type='subscription.changed' ORDER BY sequence",
            (resolved_run,),
        ):
            subscription_count += 1
            subscription_seconds[str(received_at)[:19]] += 1
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                subscription_violations.append({"reason": "invalid_json"})
                continue
            targets = payload.get("targets") or []
            target_count = payload.get("targetCount")
            maximum = payload.get("maxSubscriptions")
            request_data = ((payload.get("request") or {}).get("data") or [])
            trade_items: list[str] = []
            for request_item in request_data:
                if not isinstance(request_item, dict):
                    continue
                types = {str(value) for value in request_item.get("type") or []}
                items = [str(value) for value in request_item.get("item") or []]
                if {"0J", "0U"} <= types and {"001", "101"} <= set(items):
                    saw_index_and_breadth_request = True
                if "0B" in types:
                    saw_trade_request = True
                    trade_items.extend(items)
            if payload.get("kind") != "stock_trade":
                continue
            stock_subscription_count += 1
            if not (payload.get("added") or []) and not (payload.get("removed") or []):
                no_change_subscription_count += 1
            valid = (
                isinstance(targets, list)
                and isinstance(target_count, int)
                and isinstance(maximum, int)
                and target_count == len(targets) == len(set(targets))
                and target_count == len(trade_items) == len(set(trade_items))
                and target_count <= maximum <= 180
            )
            if not valid and len(subscription_violations) < 20:
                subscription_violations.append(
                    {
                        "targetCount": target_count,
                        "targetListCount": len(targets) if isinstance(targets, list) else None,
                        "tradeRequestCount": len(trade_items),
                        "maxSubscriptions": maximum,
                    }
                )
            saw_full_subscription = saw_full_subscription or target_count == 180
        checks.append(
            _audit_check(
                "subscription_contract",
                subscription_count > 0
                and stock_subscription_count > 0
                and not subscription_violations
                and saw_full_subscription
                and saw_index_and_breadth_request
                and saw_trade_request,
                {
                    "eventCount": subscription_count,
                    "stockTradeEventCount": stock_subscription_count,
                    "saw180Targets": saw_full_subscription,
                    "sawKospiKosdaqIndexBreadth": saw_index_and_breadth_request,
                    "saw0BTradeRequest": saw_trade_request,
                    "violations": subscription_violations,
                },
            )
        )

        # A subscription decision is explainable when each target was either
        # a candidate observed in the previous 30 minutes or a member of a
        # frozen theme touched by such an active candidate. This replays the
        # collector's exact CandidateManager provenance from recorded inputs.
        candidate_last_seen: dict[str, datetime] = {}
        provenance_decision_count = 0
        provenance_target_count = 0
        provenance_direct_count = 0
        provenance_theme_expanded_count = 0
        provenance_unresolved_count = 0
        provenance_unresolved_examples: list[dict[str, Any]] = []
        for event_type, received_at, stock_code, payload_json in connection.execute(
            "SELECT event_type,received_at,stock_code,payload_json FROM events "
            "WHERE run_id=? AND event_type IN "
            "('candidate.rest','candidate.condition','subscription.changed') "
            "ORDER BY sequence",
            (resolved_run,),
        ):
            try:
                event_received = datetime.fromisoformat(received_at)
            except (TypeError, ValueError):
                continue
            if event_type in {"candidate.rest", "candidate.condition"}:
                refresh_candidate = event_type == "candidate.rest"
                if event_type == "candidate.condition":
                    try:
                        candidate_payload = json.loads(payload_json)
                    except (TypeError, json.JSONDecodeError):
                        candidate_payload = {}
                    values = (
                        candidate_payload.get("values")
                        if isinstance(candidate_payload, dict)
                        else None
                    )
                    action = (
                        str(values.get("843") or "")
                        if isinstance(values, dict)
                        else str(candidate_payload.get("action") or "")
                    )
                    refresh_candidate = action in {"I", "INITIAL"}
                if stock_code and refresh_candidate:
                    candidate_last_seen[str(stock_code)] = event_received
                continue
            try:
                subscription_payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if (
                not isinstance(subscription_payload, dict)
                or subscription_payload.get("kind") != "stock_trade"
            ):
                continue
            provenance_decision_count += 1
            active_candidates = {
                code
                for code, seen_at in candidate_last_seen.items()
                if 0 <= (event_received - seen_at).total_seconds() <= 1800
            }
            active_theme_ids: set[str] = set()
            for code in active_candidates:
                active_theme_ids.update(stock_to_theme_ids.get(code, set()))
            for raw_target in subscription_payload.get("targets") or []:
                target = normalize_stock_code(raw_target)
                provenance_target_count += 1
                if target and target in active_candidates:
                    provenance_direct_count += 1
                    continue
                if target and stock_to_theme_ids.get(target, set()) & active_theme_ids:
                    provenance_theme_expanded_count += 1
                    continue
                provenance_unresolved_count += 1
                if len(provenance_unresolved_examples) < 20:
                    provenance_unresolved_examples.append(
                        {
                            "receivedAt": received_at,
                            "stockCode": target or str(raw_target),
                        }
                    )
        checks.append(
            _audit_check(
                "subscription_reason_provenance",
                provenance_decision_count == stock_subscription_count
                and provenance_target_count > 0
                and provenance_direct_count + provenance_theme_expanded_count
                == provenance_target_count
                and provenance_unresolved_count == 0,
                {
                    "decisionCount": provenance_decision_count,
                    "targetInstanceCount": provenance_target_count,
                    "directCandidateCount": provenance_direct_count,
                    "themeExpandedCount": provenance_theme_expanded_count,
                    "unresolvedCount": provenance_unresolved_count,
                    "unresolvedExamples": provenance_unresolved_examples,
                    "candidateTtlSeconds": 1800,
                },
            )
        )

        required_trade_field_names = {
            "20": "trade_time",
            "10": "current_price",
            "11": "change_from_previous",
            "12": "change_rate",
            "27": "best_ask",
            "28": "best_bid",
            "15": "trade_volume",
            "13": "cumulative_volume",
            "14": "cumulative_trade_value",
            "16": "open",
            "17": "high",
            "18": "low",
            "228": "execution_strength",
            "1313": "instant_trade_value",
            "311": "market_cap_100m_krw",
        }
        required_trade_fields = set(required_trade_field_names)
        observed_trade_fields: set[str] = set()
        trade_contract_rows = 0
        trade_source_clock_mismatches = 0
        for occurred_at, payload_json in connection.execute(
            "SELECT occurred_at,payload_json FROM events "
            "WHERE run_id=? AND event_type='market.trade' LIMIT 200",
            (resolved_run,),
        ):
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if (
                isinstance(payload, dict)
                and payload.get("type") == "0B"
                and normalize_stock_code(payload.get("item"))
                and isinstance(payload.get("values"), dict)
            ):
                trade_contract_rows += 1
                observed_trade_fields.update(str(key) for key in payload["values"])
                source_clock = str(payload["values"].get("20") or "")
                try:
                    occurred_clock = datetime.fromisoformat(occurred_at).astimezone(
                        KST
                    ).strftime("%H%M%S")
                except (TypeError, ValueError):
                    occurred_clock = ""
                if len(source_clock) < 6 or source_clock[:6] != occurred_clock:
                    trade_source_clock_mismatches += 1
        checks.append(
            _audit_check(
                "trade_payload_contract",
                trade_contract_rows > 0
                and required_trade_fields <= observed_trade_fields
                and trade_source_clock_mismatches == 0,
                {
                    "validSampleCount": trade_contract_rows,
                    "requiredFields": required_trade_field_names,
                    "missingFields": {
                        field: required_trade_field_names[field]
                        for field in sorted(required_trade_fields - observed_trade_fields)
                    },
                    "observedFields": sorted(observed_trade_fields),
                    "sourceClockSampleMismatchCount": trade_source_clock_mismatches,
                },
            )
        )

        required_rest_ids = {"ka10019", "ka10023", "ka10027", "ka10032"}
        checks.append(
            _audit_check(
                "rest_safety_net_complete",
                required_rest_ids <= rest_api_ids,
                {
                    "required": sorted(required_rest_ids),
                    "observed": sorted(rest_api_ids),
                },
            )
        )

        checks.append(
            _audit_check(
                "kospi_kosdaq_indices",
                {"001", "101"} <= index_items,
                {"observed": sorted(index_items)},
            )
        )
        checks.append(
            _audit_check(
                "kospi_kosdaq_breadth",
                {"001", "101"} <= breadth_items,
                {"observed": sorted(breadth_items)},
            )
        )

        opening_ok = bool(first_market and first_market.time() <= datetime_time(9, 2))
        closing_ok = bool(last_market and last_market.time() >= datetime_time(15, 30))
        checks.append(
            _audit_check(
                "regular_session_time_coverage",
                opening_ok and closing_ok,
                {
                    "firstMarketAt": first_market.isoformat() if first_market else None,
                    "lastMarketAt": last_market.isoformat() if last_market else None,
                    "openingDeadline": "09:02:00 Asia/Seoul",
                    "closingThreshold": "15:30:00 Asia/Seoul",
                },
            )
        )

        checks.append(
            _audit_check(
                "rest_poll_gap",
                required_rest_ids <= set(rest_count_by_api)
                and all(
                    rest_count_by_api[api_id] >= 2
                    and max_rest_gap_by_api.get(api_id, float("inf")) <= 120
                    for api_id in required_rest_ids
                ),
                {
                    "countByApi": dict(sorted(rest_count_by_api.items())),
                    "maxGapSecondsByApi": {
                        key: round(value, 6)
                        for key, value in sorted(max_rest_gap_by_api.items())
                    },
                    "limitSeconds": 120,
                },
            )
        )

        required_signal_keys = {
            (event_type, item)
            for event_type in ("market.index", "market.breadth")
            for item in ("001", "101")
        }
        checks.append(
            _audit_check(
                "market_signal_continuity",
                required_signal_keys <= set(signal_counts)
                and all(
                    signal_counts[key] >= 2
                    and signal_max_gaps.get(key, float("inf")) <= 120
                    for key in required_signal_keys
                ),
                {
                    "countBySignal": {
                        f"{event_type}:{item}": signal_counts[(event_type, item)]
                        for event_type, item in sorted(signal_counts)
                    },
                    "maxGapSecondsBySignal": {
                        f"{event_type}:{item}": round(value, 6)
                        for (event_type, item), value in sorted(signal_max_gaps.items())
                    },
                    "limitSeconds": 120,
                },
            )
        )

        trade_stock_count = connection.execute(
            """SELECT COUNT(DISTINCT stock_code) FROM events
               WHERE run_id=? AND event_type='market.trade'""",
            (resolved_run,),
        ).fetchone()[0]
        checks.append(
            _audit_check(
                "trade_feed_nonempty",
                by_type.get("market.trade", 0) > 0 and trade_stock_count > 0,
                {
                    "eventCount": by_type.get("market.trade", 0),
                    "stockCount": trade_stock_count,
                },
            )
        )
        max_trade_gap = float(
            (integrity.get("continuity") or {}).get("maxTradeGapSeconds") or 0.0
        )
        checks.append(
            _audit_check(
                "trade_stream_continuity",
                by_type.get("market.trade", 0) >= 2 and max_trade_gap <= 120,
                {
                    "eventCount": by_type.get("market.trade", 0),
                    "maxGapSeconds": max_trade_gap,
                    "limitSeconds": 120,
                },
            )
        )
        continuity = integrity.get("continuity") or {}
        checks.append(
            _audit_check(
                "market_event_time_contract",
                continuity.get("tradeWrongDayCount", 0) == 0
                and continuity.get("tradeClockAheadOverOneSecondCount", 0) == 0,
                {
                    "tradeWrongDayCount": continuity.get("tradeWrongDayCount", 0),
                    "tradeNegativeLatencyCount": continuity.get(
                        "tradeNegativeLatencyCount", 0
                    ),
                    "tradeClockAheadOverOneSecondCount": continuity.get(
                        "tradeClockAheadOverOneSecondCount", 0
                    ),
                    "clockSkewToleranceSeconds": 1,
                    "latencySeconds": continuity.get("tradeLatencySeconds") or {},
                },
            )
        )

        failed_backfills -= completed_backfills
        backfill_ratio = (
            len(completed_backfills) / len(master_stock_codes)
            if master_stock_codes
            else 0.0
        )
        bar_stock_count, bar_row_count, first_bar_at, last_bar_at = connection.execute(
            """SELECT COUNT(DISTINCT stock_code), COUNT(*), MIN(trade_at), MAX(trade_at)
               FROM minute_bars WHERE run_id=?""",
            (resolved_run,),
        ).fetchone()
        bar_stock_ratio = (
            bar_stock_count / len(master_stock_codes) if master_stock_codes else 0.0
        )
        trade_date_compact = integrity["tradeDate"].replace("-", "")
        minute_time_coverage = bool(
            first_bar_at
            and last_bar_at
            and str(first_bar_at) <= f"{trade_date_compact}090100"
            and str(last_bar_at) >= f"{trade_date_compact}153000"
        )
        checks.append(
            _audit_check(
                "minute_backfill_coverage",
                bool(master_stock_codes)
                and backfill_ratio >= 0.95
                and bar_stock_ratio >= 0.90
                and minute_time_coverage
                and len(failed_backfills) <= max(5, int(len(master_stock_codes) * 0.01)),
                {
                    "masterStockCount": len(master_stock_codes),
                    "completedStockCount": len(completed_backfills),
                    "failedStockCount": len(failed_backfills),
                    "coverageRatio": round(backfill_ratio, 6),
                    "minimumRatio": 0.95,
                    "barStockCount": bar_stock_count,
                    "barStockCoverageRatio": round(bar_stock_ratio, 6),
                    "minimumBarStockRatio": 0.90,
                    "barRowCount": bar_row_count,
                    "firstBarAt": first_bar_at,
                    "lastBarAt": last_bar_at,
                },
            )
        )

        # Exact canonical JSON keys are checked during verify's mandatory
        # full-table pass; benign metadata such as collectionAuthorization
        # does not match these keys.
        sensitive_payloads = integrity.get("sensitivePayloads") or {}
        secret_hits = int(sensitive_payloads.get("count") or 0)
        checks.append(
            _audit_check(
                "no_credentials_in_payloads",
                secret_hits == 0,
                {
                    "matchingPayloadCount": secret_hits,
                    "sequences": sensitive_payloads.get("sequences") or [],
                },
            )
        )

        resolved_logs = (
            [Path(path).resolve() for path in log_paths]
            if log_paths is not None
            else discover_capture_logs(db_path, integrity["tradeDate"])
        )
        log_secret_pattern = re.compile(
            r"(?i)(?:authorization|access[_-]?token|app[_-]?key|secret[_-]?key|token)"
            r"\s*[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._-]{12,}"
            r"|bearer\s+[A-Za-z0-9._-]{12,}"
        )
        log_hits: list[dict[str, Any]] = []
        missing_logs: list[str] = []
        for log_path in resolved_logs:
            if not log_path.is_file():
                missing_logs.append(str(log_path))
                continue
            with log_path.open("r", encoding="utf-8", errors="replace") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if log_secret_pattern.search(line):
                        log_hits.append(
                            {"path": str(log_path), "lineNumber": line_number}
                        )
        checks.append(
            _audit_check(
                "no_credentials_in_logs",
                not log_hits and not missing_logs,
                {
                    "scanned": [str(path) for path in resolved_logs if path.is_file()],
                    "missing": missing_logs,
                    "matchingLines": log_hits[:20],
                    "matchingLineCount": len(log_hits),
                },
            )
        )

        source_errors = by_type.get("source.error", 0)
        warnings.append(
            {
                "name": "source_errors",
                "passed": source_errors == 0,
                "details": {"count": source_errors},
            }
        )
        websocket_disconnects = source_status_counts.get("WEBSOCKET_DISCONNECTED", 0)
        warnings.append(
            {
                "name": "websocket_disconnects",
                "passed": websocket_disconnects == 0,
                "details": {
                    "count": websocket_disconnects,
                    "connectedCount": source_status_counts.get(
                        "WEBSOCKET_CONNECTED", 0
                    ),
                    "note": (
                        "warning only when recovered; source.status and REST safety-net "
                        "events preserve the interruption for replay"
                    ),
                },
            }
        )
        warnings.append(
            {
                "name": "received_at_sequence_regressions",
                "passed": continuity.get("receivedAtRegressionCount", 0) == 0,
                "details": {
                    "count": continuity.get("receivedAtRegressionCount", 0),
                    "maxRegressionSeconds": continuity.get(
                        "maxReceivedAtRegressionSeconds", 0.0
                    ),
                    "note": (
                        "warning only; replay ordering is authoritative by sequence "
                        "and negative timing deltas are emitted without delay"
                    ),
                },
            }
        )
        warnings.append(
            {
                "name": "trade_receive_latency",
                "passed": continuity.get("tradeOverFiveSecondCount", 0) == 0
                and continuity.get("tradeClockAheadOverOneSecondCount", 0) == 0,
                "details": {
                    "overFiveSecondCount": continuity.get(
                        "tradeOverFiveSecondCount", 0
                    ),
                    "sourceClockAheadCount": continuity.get(
                        "tradeNegativeLatencyCount", 0
                    ),
                    "sourceClockAheadOverOneSecondCount": continuity.get(
                        "tradeClockAheadOverOneSecondCount", 0
                    ),
                    "latencySeconds": continuity.get("tradeLatencySeconds") or {},
                    "note": (
                        "warning only; occurred_at preserves the source clock and "
                        "received_at preserves actual delivery timing"
                    ),
                },
            }
        )
        max_subscriptions_per_second = max(subscription_seconds.values(), default=0)
        no_change_ratio = (
            no_change_subscription_count / stock_subscription_count
            if stock_subscription_count
            else 0.0
        )
        warnings.append(
            {
                "name": "subscription_churn",
                "passed": max_subscriptions_per_second <= 5 and no_change_ratio <= 0.5,
                "details": {
                    "maxUpdatesPerSecond": max_subscriptions_per_second,
                    "noSetChangeCount": no_change_subscription_count,
                    "stockTradeUpdateCount": stock_subscription_count,
                    "noSetChangeRatio": round(no_change_ratio, 6),
                    "note": (
                        "warning only; exact subscription decisions remain replayable and "
                        "minute-bar backfill is audited separately"
                    ),
                },
            }
        )
        return {
            "database": str(db_path.resolve()),
            "runId": resolved_run,
            "passed": all(check["passed"] for check in checks),
            "checks": checks,
            "warnings": warnings,
            "eventCount": event_count,
            "eventsByType": dict(sorted(by_type.items())),
        }
    finally:
        connection.close()


def audit_supplemental_capture(
    db_path: Path,
    run_id: str | None = None,
    integrity_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit the ka10095 sidecar, including its explicitly known pre-start gap."""
    integrity = integrity_result or verify_database(db_path, run_id)
    if run_id is not None and integrity.get("runId") != run_id:
        raise VerificationError("integrity result belongs to a different run")
    resolved_run = integrity["runId"]
    connection = sqlite3.connect(db_path)
    checks = list(integrity["checks"])
    try:
        row = connection.execute(
            "SELECT trade_date,status,started_at,finished_at,settings_json "
            "FROM collection_runs WHERE run_id=?",
            (resolved_run,),
        ).fetchone()
        if row is None:
            raise VerificationError(f"unknown run: {resolved_run}")
        trade_date, status, started_at, finished_at, settings_json = row
        settings = json.loads(settings_json)
        by_type = Counter(
            {
                str(event_type): int(count)
                for event_type, count in connection.execute(
                    "SELECT event_type,COUNT(*) FROM events "
                    "WHERE run_id=? GROUP BY event_type",
                    (resolved_run,),
                )
            }
        )
        checks.append(
            _audit_check(
                "supplemental_purpose_contract",
                settings.get("purpose")
                == "ka10095 non-0B active-theme snapshot supplement"
                and bool(settings.get("parentRunId"))
                and settings.get("orderApisEnabled") is False,
                {
                    "purpose": settings.get("purpose"),
                    "parentRunId": settings.get("parentRunId"),
                    "orderApisEnabled": settings.get("orderApisEnabled"),
                },
            )
        )

        statuses: list[dict[str, Any]] = []
        for (payload_json,) in connection.execute(
            "SELECT payload_json FROM events WHERE run_id=? "
            "AND event_type='source.status' ORDER BY sequence",
            (resolved_run,),
        ):
            payload = json.loads(payload_json)
            if isinstance(payload, dict):
                statuses.append(payload)
        status_names = [str(payload.get("status") or "") for payload in statuses]
        checks.append(
            _audit_check(
                "supplemental_lifecycle",
                status_names.count("SNAPSHOT_SUPPLEMENT_STARTED") == 1
                and status_names.count("SNAPSHOT_SUPPLEMENT_FINISHED") == 1
                and status == "COMPLETED"
                and bool(finished_at),
                {
                    "runStatus": status,
                    "startedAt": started_at,
                    "finishedAt": finished_at,
                    "statuses": status_names,
                },
            )
        )
        checks.append(
            _audit_check(
                "supplemental_no_source_errors",
                by_type.get("source.error", 0) == 0,
                {"sourceErrorCount": by_type.get("source.error", 0)},
            )
        )

        coverage_rows: list[tuple[datetime, dict[str, Any]]] = []
        for received_at, payload_json in connection.execute(
            "SELECT received_at,payload_json FROM events WHERE run_id=? "
            "AND event_type='supplemental.coverage' ORDER BY sequence",
            (resolved_run,),
        ):
            payload = json.loads(payload_json)
            if isinstance(payload, dict):
                coverage_rows.append((datetime.fromisoformat(received_at), payload))
        coverage_cycles = [int(payload.get("cycle", -1)) for _, payload in coverage_rows]
        expected_cycles = list(range(len(coverage_cycles)))
        max_cycle_gap_seconds = max(
            (
                (current[0] - previous[0]).total_seconds()
                for previous, current in zip(coverage_rows, coverage_rows[1:])
            ),
            default=0.0,
        )
        total_requested = sum(
            int(payload.get("requestedStockCount") or 0)
            for _, payload in coverage_rows
        )
        total_returned = sum(
            int(payload.get("returnedStockCount") or 0)
            for _, payload in coverage_rows
        )
        total_batches = sum(
            int(payload.get("batchCount") or 0) for _, payload in coverage_rows
        )
        failed_batches = sum(
            int(payload.get("failedBatchCount") or 0)
            for _, payload in coverage_rows
        )
        response_ratio = total_returned / total_requested if total_requested else 0.0
        latest_missing_codes: list[str] = []
        if coverage_cycles:
            latest_cycle = coverage_cycles[-1]
            latest_requested: set[str] = set()
            latest_returned: set[str] = set()
            for (payload_json,) in connection.execute(
                "SELECT payload_json FROM events WHERE run_id=? "
                "AND event_type='kiwoom.ka10095.raw' "
                "AND CAST(json_extract(payload_json,'$.cycle') AS INTEGER)=?",
                (resolved_run, latest_cycle),
            ):
                payload = json.loads(payload_json)
                latest_requested.update(
                    str(code) for code in payload.get("requestedCodes") or []
                )
                response = payload.get("response") or {}
                for response_row in response.get("atn_stk_infr") or []:
                    if not isinstance(response_row, dict):
                        continue
                    code = normalize_stock_code(response_row.get("stk_cd"))
                    if code:
                        latest_returned.add(code)
            latest_missing_codes = sorted(latest_requested - latest_returned)
        checks.extend(
            [
                _audit_check(
                    "supplemental_cycles_contiguous",
                    bool(coverage_rows) and coverage_cycles == expected_cycles,
                    {
                        "cycleCount": len(coverage_rows),
                        "firstCycle": coverage_cycles[0] if coverage_cycles else None,
                        "lastCycle": coverage_cycles[-1] if coverage_cycles else None,
                    },
                ),
                _audit_check(
                    "supplemental_cycle_interval",
                    bool(coverage_rows) and max_cycle_gap_seconds <= 60.0,
                    {
                        "configuredSeconds": settings.get("pollSeconds"),
                        "maximumSeconds": round(max_cycle_gap_seconds, 6),
                        "allowedMaximumSeconds": 60.0,
                    },
                ),
                _audit_check(
                    "supplemental_batch_success",
                    total_batches > 0
                    and failed_batches == 0
                    and by_type.get("kiwoom.ka10095.raw", 0) == total_batches,
                    {
                        "expectedBatches": total_batches,
                        "rawBatchEvents": by_type.get("kiwoom.ka10095.raw", 0),
                        "failedBatches": failed_batches,
                    },
                ),
                _audit_check(
                    "supplemental_response_coverage",
                    total_requested > 0
                    and response_ratio >= 0.999
                    and by_type.get("market.snapshot", 0) == total_returned,
                    {
                        "requestedRows": total_requested,
                        "returnedRows": total_returned,
                        "responseRatio": round(response_ratio, 9),
                        "snapshotEvents": by_type.get("market.snapshot", 0),
                        "minimumRatio": 0.999,
                        "latestCycleMissingCodes": latest_missing_codes,
                    },
                ),
            ]
        )

        required_raw_fields = (
            "stk_cd",
            "dt",
            "cntr_tm",
            "cur_prc",
            "flu_rt",
            "pred_pre",
            "pred_pre_sig",
            "trde_qty",
            "trde_prica",
            "open_pric",
            "high_pric",
            "low_pric",
            "cntr_str",
            "mac",
        )
        missing_predicates = " OR ".join(
            f"json_type(payload_json, '$.raw.{field}') IS NULL"
            for field in required_raw_fields
        )
        snapshot_total, invalid_snapshot_count = connection.execute(
            "SELECT COUNT(*),SUM(CASE WHEN "
            "json_extract(payload_json,'$.apiId')!='ka10095' OR "
            "json_extract(payload_json,'$.source')!='REST_SNAPSHOT' OR "
            f"({missing_predicates}) THEN 1 ELSE 0 END) "
            "FROM events WHERE run_id=? AND event_type='market.snapshot'",
            (resolved_run,),
        ).fetchone()
        invalid_snapshot_count = int(invalid_snapshot_count or 0)
        checks.append(
            _audit_check(
                "supplemental_snapshot_contract",
                int(snapshot_total) > 0 and invalid_snapshot_count == 0,
                {
                    "snapshotCount": int(snapshot_total),
                    "invalidSnapshotCount": invalid_snapshot_count,
                    "requiredRawFields": list(required_raw_fields),
                },
            )
        )

        started_payload = next(
            (
                payload
                for payload in statuses
                if payload.get("status") == "SNAPSHOT_SUPPLEMENT_STARTED"
            ),
            {},
        )
        known_gap = started_payload.get("knownGap")
        gap_declared = (
            settings.get("knownGapBeforeStart") is True
            and isinstance(known_gap, dict)
            and bool(known_gap.get("from"))
            and bool(known_gap.get("to"))
            and bool(known_gap.get("reason"))
        )
        checks.append(
            _audit_check(
                "supplemental_known_gap_declared",
                gap_declared,
                {"settingsFlag": settings.get("knownGapBeforeStart"), "gap": known_gap},
            )
        )
        exact_session_check = _audit_check(
            "supplemental_exact_full_session_coverage",
            settings.get("knownGapBeforeStart") is not True,
            {
                "passedMeaning": "ka10095 coverage was live from 09:00:00 KST",
                "knownGap": known_gap,
                "note": (
                    "A declared gap is auditable but cannot be called an exact live replay; "
                    "historical minute bars may only provide an approximate fallback."
                ),
            },
        )
        checks.append(exact_session_check)
        operational_checks = [
            check
            for check in checks
            if check["name"] != "supplemental_exact_full_session_coverage"
        ]
        return {
            "database": str(db_path.resolve()),
            "runId": resolved_run,
            "tradeDate": trade_date,
            "passed": all(check["passed"] for check in checks),
            "operationalPassed": all(check["passed"] for check in operational_checks),
            "exactFullSessionCoverage": exact_session_check["passed"],
            "checks": checks,
            "eventCount": sum(by_type.values()),
            "eventsByType": dict(sorted(by_type.items())),
        }
    finally:
        connection.close()


def audit_gap_recovery(
    db_path: Path,
    run_id: str | None = None,
    integrity_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit the explicit, non-live ka10084 one-minute gap recovery."""
    integrity = integrity_result or verify_database(db_path, run_id)
    if run_id is not None and integrity.get("runId") != run_id:
        raise VerificationError("integrity result belongs to a different run")
    resolved_run = integrity["runId"]
    connection = sqlite3.connect(db_path)
    checks = list(integrity["checks"])
    warnings: list[dict[str, Any]] = []
    try:
        run = connection.execute(
            "SELECT trade_date,status,started_at,finished_at,settings_json "
            "FROM collection_runs WHERE run_id=?",
            (resolved_run,),
        ).fetchone()
        if run is None:
            raise VerificationError(f"unknown run: {resolved_run}")
        trade_date, status, started_at, finished_at, settings_json = run
        settings = json.loads(settings_json)
        by_type = Counter(
            {
                str(event_type): int(count)
                for event_type, count in connection.execute(
                    "SELECT event_type,COUNT(*) FROM events WHERE run_id=? "
                    "GROUP BY event_type",
                    (resolved_run,),
                )
            }
        )
        expected_purpose = (
            "ka10084 one-minute recovery for the pre-sidecar snapshot gap"
        )
        checks.append(
            _audit_check(
                "gap_recovery_contract",
                settings.get("purpose") == expected_purpose
                and settings.get("sourceApi") == "ka10084"
                and settings.get("resolutionSeconds") == 60
                and settings.get("exactFullSessionCoverage") is False
                and settings.get("orderApisEnabled") is False
                and bool(settings.get("parentRunId")),
                {
                    "purpose": settings.get("purpose"),
                    "sourceApi": settings.get("sourceApi"),
                    "resolutionSeconds": settings.get("resolutionSeconds"),
                    "exactFullSessionCoverage": settings.get(
                        "exactFullSessionCoverage"
                    ),
                    "orderApisEnabled": settings.get("orderApisEnabled"),
                    "parentRunId": settings.get("parentRunId"),
                    "limitations": settings.get("limitations"),
                },
            )
        )
        statuses = [
            json.loads(payload_json).get("status")
            for (payload_json,) in connection.execute(
                "SELECT payload_json FROM events WHERE run_id=? "
                "AND event_type='source.status' ORDER BY sequence",
                (resolved_run,),
            )
        ]
        checks.append(
            _audit_check(
                "gap_recovery_lifecycle",
                status == "COMPLETED"
                and bool(finished_at)
                and statuses.count("GAP_RECOVERY_STARTED") == 1
                and bool(statuses)
                and statuses[-1] == "GAP_RECOVERY_FINISHED",
                {
                    "runStatus": status,
                    "startedAt": started_at,
                    "finishedAt": finished_at,
                    "statuses": statuses,
                },
            )
        )

        completed_codes = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT stock_code FROM events WHERE run_id=? "
                "AND event_type='gap_recovery.stock.completed' "
                "AND stock_code IS NOT NULL",
                (resolved_run,),
            )
        }
        error_codes = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT stock_code FROM events WHERE run_id=? "
                "AND event_type='source.error' AND stock_code IS NOT NULL",
                (resolved_run,),
            )
        }
        unresolved_error_codes = sorted(error_codes - completed_codes)
        target_count = int(settings.get("targetCount") or 0)
        raw_count = by_type.get("kiwoom.ka10084.raw", 0)
        checks.extend(
            [
                _audit_check(
                    "gap_recovery_target_completion",
                    target_count > 0
                    and len(completed_codes) == target_count
                    and raw_count == target_count,
                    {
                        "targetCount": target_count,
                        "completedStockCount": len(completed_codes),
                        "rawResponseCount": raw_count,
                    },
                ),
                _audit_check(
                    "gap_recovery_unresolved_errors",
                    not unresolved_error_codes,
                    {
                        "recordedErrorStockCount": len(error_codes),
                        "unresolvedErrorStockCount": len(unresolved_error_codes),
                        "unresolvedCodes": unresolved_error_codes[:100],
                    },
                ),
            ]
        )

        gap_start = datetime.fromisoformat(str(settings.get("gapStart"))).astimezone(
            timezone.utc
        )
        gap_end = datetime.fromisoformat(str(settings.get("gapEnd"))).astimezone(
            timezone.utc
        )
        state_count = 0
        state_stocks: set[str] = set()
        invalid_sequences: list[int] = []
        outside_window_sequences: list[int] = []
        received_before_occurred_sequences: list[int] = []
        first_replay_at: str | None = None
        last_replay_at: str | None = None
        for (
            sequence,
            occurred_at,
            received_at,
            stock_code,
            payload_json,
        ) in connection.execute(
            "SELECT sequence,occurred_at,received_at,stock_code,payload_json "
            "FROM events WHERE run_id=? "
            "AND event_type='market.minute_state.recovered' ORDER BY occurred_at,sequence",
            (resolved_run,),
        ):
            state_count += 1
            if stock_code:
                state_stocks.add(str(stock_code))
            payload = json.loads(payload_json)
            occurred = datetime.fromisoformat(occurred_at)
            received = datetime.fromisoformat(received_at)
            first_replay_at = first_replay_at or occurred_at
            last_replay_at = occurred_at
            raw = payload.get("raw") if isinstance(payload, dict) else None
            selection = payload.get("selection") if isinstance(payload, dict) else None
            contract_ok = (
                payload.get("apiId") == "ka10084"
                and payload.get("source") == "HISTORICAL_MINUTE_RECOVERY"
                and payload.get("replayAt") == occurred_at
                and payload.get("capturedAt") == received_at
                and payload.get("resolutionSeconds") == 60
                and payload.get("exactLiveSnapshot") is False
                and isinstance(selection, dict)
                and selection.get("reason") == "ACTIVE_THEME_NON_0B"
                and selection.get("asOfMinute") == str((raw or {}).get("tm") or "")[:4]
                and isinstance(raw, dict)
                and all(
                    key in raw
                    for key in (
                        "tm",
                        "cur_prc",
                        "pre_rt",
                        "pri_sel_bid_unit",
                        "pri_buy_bid_unit",
                        "cntr_trde_qty",
                        "acc_trde_qty",
                        "acc_trde_prica",
                        "cntr_str",
                    )
                )
            )
            if not contract_ok and len(invalid_sequences) < 100:
                invalid_sequences.append(int(sequence))
            if not gap_start <= occurred <= gap_end:
                if len(outside_window_sequences) < 100:
                    outside_window_sequences.append(int(sequence))
            if received < occurred and len(received_before_occurred_sequences) < 100:
                received_before_occurred_sequences.append(int(sequence))
        zero_state_stocks = len(completed_codes - state_stocks)
        checks.extend(
            [
                _audit_check(
                    "gap_recovery_state_contract",
                    state_count > 0
                    and not invalid_sequences
                    and not received_before_occurred_sequences,
                    {
                        "stateCount": state_count,
                        "stateStockCount": len(state_stocks),
                        "invalidSequences": invalid_sequences,
                        "receivedBeforeOccurredSequences": received_before_occurred_sequences,
                    },
                ),
                _audit_check(
                    "gap_recovery_time_window",
                    state_count > 0 and not outside_window_sequences,
                    {
                        "expectedStart": gap_start.isoformat(),
                        "expectedEnd": gap_end.isoformat(),
                        "firstReplayAt": first_replay_at,
                        "lastReplayAt": last_replay_at,
                        "outsideWindowSequences": outside_window_sequences,
                    },
                ),
            ]
        )
        if zero_state_stocks:
            warnings.append(
                {
                    "name": "gap_recovery_zero_state_stocks",
                    "details": {
                        "count": zero_state_stocks,
                        "note": (
                            "request completed but no trade-state minute existed in the "
                            "window; suspended or non-trading stocks are expected here"
                        ),
                    },
                }
            )
        warnings.append(
            {
                "name": "gap_recovery_is_not_exact_live_capture",
                "details": {
                    "resolutionSeconds": 60,
                    "exactFullSessionCoverage": False,
                    "limitations": settings.get("limitations"),
                },
            }
        )
        return {
            "database": str(db_path.resolve()),
            "runId": resolved_run,
            "tradeDate": trade_date,
            "passed": all(check["passed"] for check in checks),
            "operationalPassed": all(check["passed"] for check in checks),
            "exactLiveRecovery": False,
            "checks": checks,
            "warnings": warnings,
            "eventCount": sum(by_type.values()),
            "eventsByType": dict(sorted(by_type.items())),
            "recoveredStateCount": state_count,
            "recoveredStateStockCount": len(state_stocks),
        }
    finally:
        connection.close()


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def iter_selected_events(
    db_path: Path,
    *,
    run_id: str,
    event_types: set[str] | None,
    from_time: str | None,
    to_time: str | None,
    order_by_received: bool = False,
    order_by_occurred: bool = False,
) -> Iterator[EventRecord]:
    from_clock = datetime.strptime(from_time, "%H:%M:%S").time() if from_time else None
    to_clock = datetime.strptime(to_time, "%H:%M:%S").time() if to_time else None
    received_from: str | None = None
    received_before: str | None = None
    if from_clock or to_clock:
        connection = sqlite3.connect(db_path)
        try:
            row = connection.execute(
                "SELECT trade_date FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise VerificationError(f"unknown run: {run_id}")
        trade_date = datetime.strptime(str(row[0]), "%Y-%m-%d").date()
        if from_clock:
            received_from = datetime.combine(
                trade_date, from_clock, tzinfo=KST
            ).astimezone(timezone.utc).isoformat()
        if to_clock:
            received_before = (
                datetime.combine(trade_date, to_clock, tzinfo=KST)
                + timedelta(seconds=1)
            ).astimezone(timezone.utc).isoformat()
    for event in iter_events(
        db_path,
        run_id=run_id,
        event_types=event_types,
        received_from=None if order_by_occurred else received_from,
        received_before=None if order_by_occurred else received_before,
        occurred_from=received_from if order_by_occurred else None,
        occurred_before=received_before if order_by_occurred else None,
        order_by_received=order_by_received,
        order_by_occurred=order_by_occurred,
    ):
        yield event


def iter_combined_selected_events(
    main_database: Path,
    *,
    main_run_id: str,
    supplemental_database: Path | None = None,
    supplemental_run_id: str | None = None,
    recovery_database: Path | None = None,
    recovery_run_id: str | None = None,
    event_types: set[str] | None,
    from_time: str | None,
    to_time: str | None,
) -> Iterator[EventRecord]:
    """Merge live, sidecar, and recovered stores into service-time order.

    Payloads, their hashes, source timestamps, and provider sequence values are
    unchanged.  Only the outer run id and sequence are virtualized so consumers
    see one monotonic stream instead of two colliding sequence spaces.
    """
    if (supplemental_database is None) != (supplemental_run_id is None):
        raise ValueError("supplemental database and run id must be supplied together")
    if (recovery_database is None) != (recovery_run_id is None):
        raise ValueError("recovery database and run id must be supplied together")
    run_ids = [main_run_id]
    if supplemental_run_id:
        run_ids.append(supplemental_run_id)
    if recovery_run_id:
        run_ids.append(recovery_run_id)
    combined_run_id = "combined:" + ":".join(run_ids)
    sources: list[tuple[Iterator[EventRecord], bool]] = [
        (
            iter_selected_events(
            main_database,
            run_id=main_run_id,
            event_types=event_types,
            from_time=from_time,
            to_time=to_time,
            order_by_received=True,
            ),
            False,
        )
    ]
    if supplemental_database is not None and supplemental_run_id is not None:
        sources.append(
            (
                iter_selected_events(
                    supplemental_database,
                    run_id=supplemental_run_id,
                    event_types=event_types,
                    from_time=from_time,
                    to_time=to_time,
                    order_by_received=True,
                ),
                False,
            )
        )
    if recovery_database is not None and recovery_run_id is not None:
        sources.append(
            (
                iter_selected_events(
                    recovery_database,
                    run_id=recovery_run_id,
                    event_types=event_types,
                    from_time=from_time,
                    to_time=to_time,
                    order_by_occurred=True,
                ),
                True,
            )
        )
    heap: list[
        tuple[str, int, int, EventRecord, Iterator[EventRecord], bool]
    ] = []
    for source_rank, (iterator, uses_occurred_at) in enumerate(sources):
        event = next(iterator, None)
        if event is not None:
            replay_at = event.occurred_at if uses_occurred_at else event.received_at
            heapq.heappush(
                heap,
                (
                    replay_at,
                    source_rank,
                    event.sequence,
                    event,
                    iterator,
                    uses_occurred_at,
                ),
            )
    replay_sequence = 0
    try:
        while heap:
            replay_at, source_rank, _, event, iterator, uses_occurred_at = heapq.heappop(
                heap
            )
            replay_sequence += 1
            yield EventRecord(
                sequence=replay_sequence,
                run_id=combined_run_id,
                event_type=event.event_type,
                source=event.source,
                occurred_at=event.occurred_at,
                received_at=replay_at if uses_occurred_at else event.received_at,
                stock_code=event.stock_code,
                source_sequence=event.source_sequence,
                payload=event.payload,
                payload_sha256=event.payload_sha256,
                schema_version=event.schema_version,
            )
            following = next(iterator, None)
            if following is not None:
                following_replay_at = (
                    following.occurred_at if uses_occurred_at else following.received_at
                )
                heapq.heappush(
                    heap,
                    (
                        following_replay_at,
                        source_rank,
                        following.sequence,
                        following,
                        iterator,
                        uses_occurred_at,
                    ),
                )
    finally:
        for iterator, _ in sources:
            close = getattr(iterator, "close", None)
            if close:
                close()


def prove_combined_service_replay(
    main_database: Path,
    *,
    main_run_id: str,
    supplemental_database: Path | None = None,
    supplemental_run_id: str | None = None,
    recovery_database: Path | None = None,
    recovery_run_id: str | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
) -> dict[str, Any]:
    """Stream the complete merged service profile and fingerprint its envelope."""
    digest = hashlib.sha256()
    event_count = 0
    by_type: Counter[str] = Counter()
    first_received_at: str | None = None
    last_received_at: str | None = None
    previous_received_at: str | None = None
    sequence_ok = True
    received_order_ok = True
    payload_hash_mismatches: list[int] = []
    for event in iter_combined_selected_events(
        main_database,
        main_run_id=main_run_id,
        supplemental_database=supplemental_database,
        supplemental_run_id=supplemental_run_id,
        recovery_database=recovery_database,
        recovery_run_id=recovery_run_id,
        event_types=SERVICE_EVENT_TYPES,
        from_time=from_time,
        to_time=to_time,
    ):
        event_count += 1
        sequence_ok = sequence_ok and event.sequence == event_count
        if previous_received_at is not None and event.received_at < previous_received_at:
            received_order_ok = False
        previous_received_at = event.received_at
        first_received_at = first_received_at or event.received_at
        last_received_at = event.received_at
        if payload_hash(event.payload) != event.payload_sha256:
            if len(payload_hash_mismatches) < 20:
                payload_hash_mismatches.append(event.sequence)
        by_type[event.event_type] += 1
        digest.update((canonical_json(event.envelope()) + "\n").encode("utf-8"))
    return {
        "passed": event_count > 0
        and sequence_ok
        and received_order_ok
        and not payload_hash_mismatches,
        "runId": "combined:"
        + ":".join(
            value
            for value in (main_run_id, supplemental_run_id, recovery_run_id)
            if value
        ),
        "eventCount": event_count,
        "eventsByType": dict(sorted(by_type.items())),
        "envelopeSha256": digest.hexdigest(),
        "firstReceivedAt": first_received_at,
        "lastReceivedAt": last_received_at,
        "sequenceMonotonic": sequence_ok,
        "receivedAtMonotonic": received_order_ok,
        "payloadHashMismatchSequences": payload_hash_mismatches,
    }


def selected_events(
    db_path: Path,
    *,
    run_id: str,
    event_types: set[str] | None,
    from_time: str | None,
    to_time: str | None,
) -> list[EventRecord]:
    """Compatibility helper for tests/small selections; CLI replay streams."""
    return list(
        iter_selected_events(
            db_path,
            run_id=run_id,
            event_types=event_types,
            from_time=from_time,
            to_time=to_time,
        )
    )


async def timed_events(
    events: Iterable[EventRecord], *, speed: float
) -> AsyncIterator[EventRecord]:
    recorded_origin: datetime | None = None
    wall_origin: float | None = None
    for event in events:
        current = parse_timestamp(event.received_at)
        if speed > 0:
            if recorded_origin is None:
                recorded_origin = current
                wall_origin = time.monotonic()
            else:
                target_elapsed = max(
                    0.0, (current - recorded_origin).total_seconds() / speed
                )
                actual_elapsed = time.monotonic() - (wall_origin or time.monotonic())
                remaining = target_elapsed - actual_elapsed
                if remaining > 0:
                    await asyncio.sleep(remaining)
        yield event


async def emit_events(events: Iterable[EventRecord], *, speed: float) -> None:
    async for event in timed_events(events, speed=speed):
        print(canonical_json(event.envelope()), flush=True)


async def serve_events(
    events: list[EventRecord],
    *,
    speed: float,
    host: str,
    port: int,
    loop_forever: bool,
) -> None:
    async def handler(websocket: Any) -> None:
        while True:
            async for event in timed_events(events, speed=speed):
                await websocket.send(canonical_json(event.envelope()))
            await websocket.send(
                canonical_json(
                    {
                        "eventType": "replay.completed",
                        "eventCount": len(events),
                        "schemaVersion": "1.0.0",
                    }
                )
            )
            if not loop_forever:
                return

    print(
        json.dumps(
            {
                "status": "LISTENING",
                "url": f"ws://{host}:{port}",
                "eventCount": len(events),
                "speed": speed,
                "loop": loop_forever,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    async with websockets.serve(handler, host, port, ping_interval=20):
        await asyncio.Future()


async def serve_event_factory(
    event_factory: Callable[[], Iterator[EventRecord]],
    *,
    speed: float,
    host: str,
    port: int,
    loop_forever: bool,
) -> None:
    """Serve each client from a fresh streaming DB cursor, without a full-day list."""

    async def handler(websocket: Any) -> None:
        while True:
            count = 0
            async for event in timed_events(event_factory(), speed=speed):
                await websocket.send(canonical_json(event.envelope()))
                count += 1
            await websocket.send(
                canonical_json(
                    {
                        "eventType": "replay.completed",
                        "eventCount": count,
                        "schemaVersion": "1.0.0",
                    }
                )
            )
            if not loop_forever:
                return

    print(
        json.dumps(
            {
                "status": "LISTENING",
                "url": f"ws://{host}:{port}",
                "eventCount": "streaming",
                "speed": speed,
                "loop": loop_forever,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    async with websockets.serve(handler, host, port, ping_interval=20):
        await asyncio.Future()


async def prove_websocket_replay(
    event_factory: Callable[[], Iterator[EventRecord]],
    *,
    speed: float = 0,
    max_events: int = 10_000,
) -> dict[str, Any]:
    """Round-trip a bounded DB selection through a real local WebSocket."""
    if max_events <= 0:
        raise ValueError("max-events must be positive")

    async def handler(websocket: Any) -> None:
        count = 0
        async for event in timed_events(event_factory(), speed=speed):
            if count >= max_events:
                break
            await websocket.send(canonical_json(event.envelope()))
            count += 1
        await websocket.send(
            canonical_json(
                {
                    "eventType": "replay.completed",
                    "eventCount": count,
                    "schemaVersion": "1.0.0",
                }
            )
        )

    expected_iterator = event_factory()
    expected_digest = hashlib.sha256()
    observed_digest = hashlib.sha256()
    mismatches: list[dict[str, Any]] = []
    observed_count = 0
    completion: dict[str, Any] | None = None
    first_received_at: datetime | None = None
    last_received_at: datetime | None = None
    wall_started: float | None = None
    wall_finished: float | None = None
    async with websockets.serve(handler, "127.0.0.1", 0, ping_interval=20) as server:
        sockets = server.sockets or []
        if not sockets:
            raise VerificationError("WebSocket proof server did not bind a socket")
        port = sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
            wall_started = time.monotonic()
            while True:
                raw_message = await client.recv()
                observed = json.loads(raw_message)
                if observed.get("eventType") == "replay.completed":
                    completion = observed
                    wall_finished = time.monotonic()
                    break
                expected = next(expected_iterator, None)
                if expected is None:
                    if len(mismatches) < 20:
                        mismatches.append(
                            {"position": observed_count + 1, "reason": "unexpected_event"}
                        )
                    continue
                expected_line = canonical_json(expected.envelope())
                observed_line = canonical_json(observed)
                expected_digest.update((expected_line + "\n").encode("utf-8"))
                observed_digest.update((observed_line + "\n").encode("utf-8"))
                if expected_line != observed_line and len(mismatches) < 20:
                    mismatches.append(
                        {
                            "position": observed_count + 1,
                            "expectedSequence": expected.sequence,
                            "observedSequence": observed.get("sequence"),
                        }
                    )
                try:
                    observed_received = datetime.fromisoformat(
                        str(observed.get("receivedAt"))
                    )
                    first_received_at = first_received_at or observed_received
                    last_received_at = observed_received
                except (TypeError, ValueError):
                    pass
                observed_count += 1

    expected_more = next(expected_iterator, None) is not None
    expected_hash = expected_digest.hexdigest()
    observed_hash = observed_digest.hexdigest()
    completion_count = completion.get("eventCount") if completion else None
    recorded_span_seconds = (
        (last_received_at - first_received_at).total_seconds()
        if first_received_at is not None and last_received_at is not None
        else 0.0
    )
    wall_elapsed_seconds = (
        wall_finished - wall_started
        if wall_started is not None and wall_finished is not None
        else None
    )
    passed = (
        observed_count > 0
        and not mismatches
        and expected_hash == observed_hash
        and completion_count == observed_count
    )
    return {
        "passed": passed,
        "eventCount": observed_count,
        "expectedEnvelopeSha256": expected_hash,
        "observedEnvelopeSha256": observed_hash,
        "completion": completion,
        "requestedSpeed": speed,
        "recordedSpanSeconds": round(recorded_span_seconds, 6),
        "wallElapsedSeconds": (
            round(wall_elapsed_seconds, 6)
            if wall_elapsed_seconds is not None
            else None
        ),
        "effectiveSpeed": (
            round(recorded_span_seconds / wall_elapsed_seconds, 3)
            if wall_elapsed_seconds and recorded_span_seconds > 0
            else None
        ),
        "selectionHasMoreEvents": expected_more,
        "maxEvents": max_events,
        "mismatches": mismatches,
    }


def add_replay_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("database", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--supplemental-database",
        type=Path,
        help="Optional second replay store merged by receivedAt.",
    )
    parser.add_argument("--supplemental-run-id")
    parser.add_argument(
        "--recovery-database",
        type=Path,
        help="Optional ka10084 gap-recovery store merged by historical occurredAt.",
    )
    parser.add_argument("--recovery-run-id")
    parser.add_argument(
        "--profile",
        choices=("service", "all"),
        default="service",
        help="service emits canonical runtime inputs; all also emits raw/audit events",
    )
    parser.add_argument(
        "--event-type",
        action="append",
        default=[],
        help="Repeat or pass comma-separated canonical event types.",
    )
    parser.add_argument("--from-time", help="Local HH:MM:SS lower bound")
    parser.add_argument("--to-time", help="Local HH:MM:SS upper bound")
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="1=recorded timing, 20=20x, 0=no delay",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("database", type=Path)
    verify.add_argument("--run-id")

    audit = subparsers.add_parser("audit")
    audit.add_argument("database", type=Path)
    audit.add_argument("--run-id")
    audit.add_argument("--log", action="append", type=Path, default=None)

    audit_supplement = subparsers.add_parser("audit-supplement")
    audit_supplement.add_argument("database", type=Path)
    audit_supplement.add_argument("--run-id")

    audit_recovery = subparsers.add_parser("audit-recovery")
    audit_recovery.add_argument("database", type=Path)
    audit_recovery.add_argument("--run-id")

    prove = subparsers.add_parser("prove")
    prove.add_argument("database", type=Path)
    prove.add_argument("--run-id")

    prove_combined = subparsers.add_parser("prove-combined")
    prove_combined.add_argument("database", type=Path)
    prove_combined.add_argument("--run-id")
    prove_combined.add_argument("--supplemental-database", type=Path)
    prove_combined.add_argument("--supplemental-run-id")
    prove_combined.add_argument("--recovery-database", type=Path)
    prove_combined.add_argument("--recovery-run-id")
    prove_combined.add_argument("--from-time")
    prove_combined.add_argument("--to-time")

    emit = subparsers.add_parser("emit")
    add_replay_filters(emit)

    serve = subparsers.add_parser("serve")
    add_replay_filters(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--loop", action="store_true")

    socket_prove = subparsers.add_parser("socket-prove")
    add_replay_filters(socket_prove)
    socket_prove.set_defaults(speed=0.0)
    socket_prove.add_argument("--max-events", type=int, default=10_000)
    return parser


def resolve_event_factory(
    args: argparse.Namespace,
) -> tuple[str, Callable[[], Iterator[EventRecord]]]:
    if args.speed < 0:
        raise ValueError("speed must be zero or positive")
    run_id = args.run_id or latest_run_id(args.database)
    requested_types = parse_event_types(args.event_type)
    event_types = (
        requested_types
        if requested_types is not None
        else (SERVICE_EVENT_TYPES if args.profile == "service" else None)
    )
    supplemental_database = args.supplemental_database
    recovery_database = args.recovery_database
    if args.supplemental_run_id and supplemental_database is None:
        raise ValueError("--supplemental-run-id requires --supplemental-database")
    if args.recovery_run_id and recovery_database is None:
        raise ValueError("--recovery-run-id requires --recovery-database")
    if supplemental_database is None and recovery_database is None:
        def event_factory() -> Iterator[EventRecord]:
            return iter_selected_events(
                args.database,
                run_id=run_id,
                event_types=event_types,
                from_time=args.from_time,
                to_time=args.to_time,
            )

        return run_id, event_factory

    supplemental_run_id = (
        (args.supplemental_run_id or latest_run_id(supplemental_database))
        if supplemental_database is not None
        else None
    )
    recovery_run_id = (
        (args.recovery_run_id or latest_run_id(recovery_database))
        if recovery_database is not None
        else None
    )
    combined_run_id = "combined:" + ":".join(
        value for value in (run_id, supplemental_run_id, recovery_run_id) if value
    )

    def combined_event_factory() -> Iterator[EventRecord]:
        return iter_combined_selected_events(
            args.database,
            main_run_id=run_id,
            supplemental_database=supplemental_database,
            supplemental_run_id=supplemental_run_id,
            recovery_database=recovery_database,
            recovery_run_id=recovery_run_id,
            event_types=event_types,
            from_time=args.from_time,
            to_time=args.to_time,
        )

    return combined_run_id, combined_event_factory


def resolve_events(args: argparse.Namespace) -> tuple[str, list[EventRecord]]:
    """Compatibility helper for callers that explicitly want a materialized list."""
    run_id, event_factory = resolve_event_factory(args)
    events = list(event_factory())
    if not events:
        raise VerificationError("no events match the replay selection")
    return run_id, events


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    if args.command == "verify":
        result = verify_database(args.database, args.run_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    if args.command == "audit":
        result = audit_capture(args.database, args.run_id, args.log)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    if args.command == "audit-supplement":
        result = audit_supplemental_capture(args.database, args.run_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    if args.command == "audit-recovery":
        result = audit_gap_recovery(args.database, args.run_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    if args.command == "prove":
        result = prove_replay_files(args.database, args.run_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    if args.command == "prove-combined":
        main_run_id = args.run_id or latest_run_id(args.database)
        supplemental_run_id = (
            (args.supplemental_run_id or latest_run_id(args.supplemental_database))
            if args.supplemental_database is not None
            else None
        )
        recovery_run_id = (
            (args.recovery_run_id or latest_run_id(args.recovery_database))
            if args.recovery_database is not None
            else None
        )
        if supplemental_run_id is None and recovery_run_id is None:
            raise ValueError(
                "prove-combined requires --supplemental-database or --recovery-database"
            )
        result = prove_combined_service_replay(
            args.database,
            main_run_id=main_run_id,
            supplemental_database=args.supplemental_database,
            supplemental_run_id=supplemental_run_id,
            recovery_database=args.recovery_database,
            recovery_run_id=recovery_run_id,
            from_time=args.from_time,
            to_time=args.to_time,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    _, event_factory = resolve_event_factory(args)
    probe = event_factory()
    first = next(probe, None)
    if first is None:
        raise VerificationError("no events match the replay selection")
    if args.command == "emit":
        asyncio.run(emit_events(chain((first,), probe), speed=args.speed))
    elif args.command == "socket-prove":
        close_probe = getattr(probe, "close", None)
        if close_probe:
            close_probe()
        result = asyncio.run(
            prove_websocket_replay(
                event_factory,
                speed=args.speed,
                max_events=args.max_events,
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    else:
        close_probe = getattr(probe, "close", None)
        if close_probe:
            close_probe()
        asyncio.run(
            serve_event_factory(
                event_factory,
                speed=args.speed,
                host=args.host,
                port=args.port,
                loop_forever=args.loop,
            )
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, ValueError, OSError, sqlite3.DatabaseError) as exc:
        print(f"market replay failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
