#!/usr/bin/env python3
"""Capture ka10095 snapshots for active-theme stocks outside the 0B slots.

This sidecar follows the one-time main replay database read-only.  It writes a
separate append-only replay store, so it can never contend for or corrupt the
main collector's sequence space.  Only the allow-listed read-only ka10095 API
is called; credentials and access tokens stay in memory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from collect_market_replay import CaptureError, KiwoomReadOnlyClient, RestRequest
from market_replay_common import (
    KST,
    ReplayStore,
    iso_utc,
    load_env_file,
    market_datetime,
    normalize_stock_code,
    parse_clock,
    parse_trade_date,
)

LOG = logging.getLogger("dayjaview.market_snapshot_supplement")


def chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for position in range(0, len(values), size):
        yield values[position : position + size]


class MainCaptureFollower:
    def __init__(self, database: Path, *, candidate_ttl_seconds: int = 1800) -> None:
        self.database = database
        self.candidate_ttl_seconds = candidate_ttl_seconds
        self.connection = sqlite3.connect(database, timeout=10)
        self.connection.execute("PRAGMA query_only=ON")
        self.run_id, self.trade_date = self._load_main_run()
        self.stock_to_themes: dict[str, set[str]] = defaultdict(set)
        self.theme_members: dict[str, list[str]] = defaultdict(list)
        self.master_codes: set[str] = set()
        self.candidate_last_seen: dict[str, datetime] = {}
        self.current_targets: set[str] = set()
        self.cursor = 0
        self._load_stock_master()
        self._load_membership()
        self._bootstrap_runtime_state()

    def close(self) -> None:
        self.connection.close()

    def _load_main_run(self) -> tuple[str, date]:
        row = self.connection.execute(
            "SELECT run_id,trade_date FROM collection_runs "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise CaptureError("main replay database has no collection run")
        return str(row[0]), parse_trade_date(str(row[1]))

    def _load_membership(self) -> None:
        rows = self.connection.execute(
            "SELECT payload_json FROM events INDEXED BY events_type_idx "
            "WHERE event_type='reference.infostock_theme'"
        )
        for (payload_json,) in rows:
            payload = json.loads(payload_json)
            content = payload.get("content") if isinstance(payload, dict) else None
            if not isinstance(content, dict) or content.get("sourceType") != "theme_detail":
                continue
            theme_id = str(content.get("themeId") or "")
            if not theme_id:
                continue
            for row in content.get("relatedStocks") or []:
                if not isinstance(row, dict):
                    continue
                code = normalize_stock_code(row.get("stockCode"))
                if code:
                    self.stock_to_themes[code].add(theme_id)
                    self.theme_members[theme_id].append(code)

    def _load_stock_master(self) -> None:
        rows = self.connection.execute(
            "SELECT payload_json FROM events INDEXED BY events_type_idx "
            "WHERE event_type='reference.stock_master'"
        )
        for (payload_json,) in rows:
            payload = json.loads(payload_json)
            response = payload.get("response") if isinstance(payload, dict) else None
            for row in (response or {}).get("list") or []:
                if not isinstance(row, dict):
                    continue
                code = normalize_stock_code(row.get("code"))
                if code:
                    self.master_codes.add(code)

    @staticmethod
    def _condition_refreshes_candidate(payload_json: str) -> bool:
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            return False
        values = payload.get("values") if isinstance(payload, dict) else None
        if isinstance(values, dict):
            return str(values.get("843") or "") == "I"
        return str(payload.get("action") or "") == "INITIAL"

    def _bootstrap_runtime_state(self) -> None:
        now = datetime.now().astimezone()
        cutoff = (now - timedelta(seconds=self.candidate_ttl_seconds)).astimezone(
            tz=timezone.utc
        ).isoformat()
        for event_type in ("candidate.rest", "candidate.condition"):
            rows = self.connection.execute(
                "SELECT received_at,stock_code,payload_json FROM events "
                "INDEXED BY events_type_idx WHERE event_type=? AND received_at>=? "
                "AND stock_code IS NOT NULL",
                (event_type, cutoff),
            )
            for received_at, stock_code, payload_json in rows:
                if event_type == "candidate.condition" and not self._condition_refreshes_candidate(
                    payload_json
                ):
                    continue
                self.candidate_last_seen[str(stock_code)] = datetime.fromisoformat(received_at)
        row = self.connection.execute(
            "SELECT payload_json FROM events INDEXED BY events_type_idx "
            "WHERE event_type='subscription.changed' AND stock_code IS NULL "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        if row:
            payload = json.loads(row[0])
            if payload.get("kind") == "stock_trade":
                self.current_targets = {
                    code
                    for value in payload.get("targets") or []
                    for code in [normalize_stock_code(value)]
                    if code
                }
        self.cursor = int(
            self.connection.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]
        )

    def refresh(self) -> None:
        rows = list(
            self.connection.execute(
                "SELECT sequence,event_type,received_at,stock_code FROM events "
                "WHERE sequence>? ORDER BY sequence",
                (self.cursor,),
            )
        )
        for sequence, event_type, received_at, stock_code in rows:
            self.cursor = max(self.cursor, int(sequence))
            if event_type == "candidate.rest" and stock_code:
                self.candidate_last_seen[str(stock_code)] = datetime.fromisoformat(received_at)
            elif event_type == "candidate.condition" and stock_code:
                payload_row = self.connection.execute(
                    "SELECT payload_json FROM events WHERE sequence=?", (sequence,)
                ).fetchone()
                if payload_row and self._condition_refreshes_candidate(payload_row[0]):
                    self.candidate_last_seen[str(stock_code)] = datetime.fromisoformat(
                        received_at
                    )
            elif event_type == "subscription.changed":
                payload_row = self.connection.execute(
                    "SELECT payload_json FROM events WHERE sequence=?", (sequence,)
                ).fetchone()
                if not payload_row:
                    continue
                payload = json.loads(payload_row[0])
                if payload.get("kind") == "stock_trade":
                    self.current_targets = {
                        code
                        for value in payload.get("targets") or []
                        for code in [normalize_stock_code(value)]
                        if code
                    }

    def selection(self, now: datetime) -> dict[str, Any]:
        active_candidates = {
            code
            for code, seen_at in self.candidate_last_seen.items()
            if 0 <= (now - seen_at).total_seconds() <= self.candidate_ttl_seconds
        }
        active_themes: set[str] = set()
        for code in active_candidates:
            active_themes.update(self.stock_to_themes.get(code, set()))
        related: set[str] = set()
        for theme_id in active_themes:
            related.update(self.theme_members.get(theme_id, []))
        if self.master_codes:
            related.intersection_update(self.master_codes)
        supplemental = sorted(related - self.current_targets)
        return {
            "activeCandidates": active_candidates,
            "activeThemes": active_themes,
            "relatedStocks": related,
            "subscribedStocks": set(self.current_targets),
            "supplementalStocks": supplemental,
        }


async def capture(args: argparse.Namespace) -> dict[str, Any]:
    main_database = Path(args.main_database).resolve()
    if not main_database.is_file():
        raise CaptureError(f"main replay database does not exist: {main_database}")
    follower = MainCaptureFollower(
        main_database, candidate_ttl_seconds=args.candidate_ttl_seconds
    )
    trade_date = follower.trade_date
    start_at = market_datetime(trade_date, parse_clock(args.start_at))
    end_at = market_datetime(trade_date, parse_clock(args.end_at))
    output_dir = Path(args.output_dir).resolve()
    load_env_file(Path(args.env_file))
    client = KiwoomReadOnlyClient(
        args.mode,
        os.getenv("KIWOOM_APP_KEY", "").strip(),
        os.getenv("KIWOOM_APP_SECRET", "").strip(),
    )
    try:
        with ReplayStore(output_dir) as store:
            run_id: str | None = None
            if args.resume_running:
                for candidate_run_id, settings_json in store.connection.execute(
                    "SELECT run_id,settings_json FROM collection_runs "
                    "WHERE trade_date=? AND status='RUNNING' ORDER BY started_at DESC",
                    (trade_date.isoformat(),),
                ):
                    settings = json.loads(settings_json)
                    if (
                        settings.get("purpose")
                        == "ka10095 non-0B active-theme snapshot supplement"
                        and settings.get("parentRunId") == follower.run_id
                    ):
                        run_id = str(candidate_run_id)
                        break
            if run_id is None:
                run_id = store.start_run(
                    trade_date=trade_date,
                    mode=args.mode,
                    settings={
                        "purpose": "ka10095 non-0B active-theme snapshot supplement",
                        "parentRunId": follower.run_id,
                        "mainDatabase": str(main_database),
                        "startAt": start_at.isoformat(),
                        "endAt": end_at.isoformat(),
                        "pollSeconds": args.poll_seconds,
                        "batchSize": args.batch_size,
                        "candidateTtlSeconds": args.candidate_ttl_seconds,
                        "orderApisEnabled": False,
                        "knownGapBeforeStart": True,
                    },
                )
                store.append_event(
                    run_id=run_id,
                    event_type="source.status",
                    source="supplemental_collector",
                    payload={
                        "status": "SNAPSHOT_SUPPLEMENT_STARTED",
                        "parentRunId": follower.run_id,
                        "knownGap": {
                            "from": market_datetime(
                                trade_date, parse_clock("09:00:00")
                            ).isoformat(),
                            "to": datetime.now(KST).isoformat(),
                            "reason": "ka10095 supplement enabled after main capture start",
                        },
                    },
                )
                cycle = 0
                total_rows = 0
                total_failed_batches = 0
            else:
                previous_cycle = store.connection.execute(
                    "SELECT COALESCE(MAX(CAST(json_extract(payload_json,'$.cycle') AS INTEGER)),-1) "
                    "FROM events WHERE run_id=? AND event_type IN "
                    "('kiwoom.ka10095.raw','supplemental.coverage')",
                    (run_id,),
                ).fetchone()[0]
                totals = store.connection.execute(
                    "SELECT COALESCE(SUM(CAST(json_extract(payload_json,'$.returnedStockCount') AS INTEGER)),0),"
                    "COALESCE(SUM(CAST(json_extract(payload_json,'$.failedBatchCount') AS INTEGER)),0) "
                    "FROM events WHERE run_id=? AND event_type='supplemental.coverage'",
                    (run_id,),
                ).fetchone()
                cycle = int(previous_cycle) + 1
                total_rows = int(totals[0])
                total_failed_batches = int(totals[1])
                store.append_event(
                    run_id=run_id,
                    event_type="source.status",
                    source="supplemental_collector",
                    payload={
                        "status": "SNAPSHOT_SUPPLEMENT_RESUMED",
                        "parentRunId": follower.run_id,
                        "nextCycle": cycle,
                    },
                )
            delay = max(0.0, (start_at - datetime.now(KST)).total_seconds())
            if delay:
                await asyncio.sleep(delay)
            while datetime.now(KST) < end_at:
                cycle_started = time.monotonic()
                follower.refresh()
                selection = follower.selection(datetime.now().astimezone())
                codes = selection["supplementalStocks"]
                cycle_rows = 0
                cycle_failed_batches = 0
                batch_count = (len(codes) + args.batch_size - 1) // args.batch_size
                for batch_position, batch in enumerate(
                    chunks(codes, args.batch_size), start=1
                ):
                    try:
                        received_at = iso_utc()
                        payload, headers = await client.post(
                            RestRequest(
                                "ka10095",
                                "/api/dostk/stkinfo",
                                {"stk_cd": "|".join(batch)},
                            ),
                            retries=2,
                        )
                        received_at = iso_utc()
                        rows = payload.get("atn_stk_infr") or []
                        store.append_event(
                            run_id=run_id,
                            event_type="kiwoom.ka10095.raw",
                            source="kiwoom_rest",
                            payload={
                                "apiId": "ka10095",
                                "cycle": cycle,
                                "batchPosition": batch_position,
                                "batchCount": batch_count,
                                "requestedCodes": batch,
                                "responseHeaders": headers,
                                "response": payload,
                            },
                            received_at=received_at,
                        )
                        for row in rows:
                            if not isinstance(row, dict):
                                continue
                            code = normalize_stock_code(row.get("stk_cd"))
                            if not code:
                                continue
                            store.append_event(
                                run_id=run_id,
                                event_type="market.snapshot",
                                source="kiwoom_rest",
                                payload={
                                    "apiId": "ka10095",
                                    "source": "REST_SNAPSHOT",
                                    "asOf": received_at,
                                    "cycle": cycle,
                                    "batchPosition": batch_position,
                                    "raw": row,
                                },
                                received_at=received_at,
                                occurred_at=received_at,
                                stock_code=code,
                            )
                            cycle_rows += 1
                    except Exception as exc:
                        cycle_failed_batches += 1
                        store.append_event(
                            run_id=run_id,
                            event_type="source.error",
                            source="kiwoom_rest",
                            payload={
                                "apiId": "ka10095",
                                "cycle": cycle,
                                "batchPosition": batch_position,
                                "error": str(exc),
                            },
                        )
                store.append_event(
                    run_id=run_id,
                    event_type="supplemental.coverage",
                    source="supplemental_collector",
                    payload={
                        "cycle": cycle,
                        "activeCandidateCount": len(selection["activeCandidates"]),
                        "activeThemeCount": len(selection["activeThemes"]),
                        "relatedStockCount": len(selection["relatedStocks"]),
                        "subscribedStockCount": len(selection["subscribedStocks"]),
                        "requestedStockCount": len(codes),
                        "returnedStockCount": cycle_rows,
                        "batchCount": batch_count,
                        "failedBatchCount": cycle_failed_batches,
                    },
                )
                store.flush()
                total_rows += cycle_rows
                total_failed_batches += cycle_failed_batches
                LOG.info(
                    "cycle=%d requested=%d returned=%d batches=%d failed=%d",
                    cycle,
                    len(codes),
                    cycle_rows,
                    batch_count,
                    cycle_failed_batches,
                )
                cycle += 1
                elapsed = time.monotonic() - cycle_started
                await asyncio.sleep(max(0.1, args.poll_seconds - elapsed))
            store.append_event(
                run_id=run_id,
                event_type="source.status",
                source="supplemental_collector",
                payload={
                    "status": "SNAPSHOT_SUPPLEMENT_FINISHED",
                    "cycles": cycle,
                    "rows": total_rows,
                    "failedBatches": total_failed_batches,
                },
            )
            manifest = store.finish_run(run_id, status="COMPLETED")
            return manifest
    except BaseException:
        raise
    finally:
        follower.close()
        await client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main-database", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--env-file", default=".env.local")
    parser.add_argument("--mode", choices=("real", "demo"), default="real")
    parser.add_argument("--start-at", default="09:00:00")
    parser.add_argument("--end-at", default="15:40:00")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--candidate-ttl-seconds", type=int, default=1800)
    parser.add_argument(
        "--no-resume-running",
        dest="resume_running",
        action="store_false",
        help="Start a new run instead of continuing a matching unfinished sidecar run.",
    )
    parser.set_defaults(resume_running=True)
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = build_parser().parse_args()
    if not 1 <= args.batch_size <= 100:
        raise CaptureError("batch-size must be between 1 and 100")
    if args.poll_seconds < 10:
        raise CaptureError("poll-seconds must be at least 10")
    manifest = asyncio.run(capture(args))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CaptureError, OSError, ValueError, sqlite3.DatabaseError) as exc:
        print(f"market snapshot supplement failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
