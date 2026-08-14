#!/usr/bin/env python3
"""Resume or repair the read-only one-minute-bar backfill for a replay run.

This command is intentionally separate from the live collector.  Run it only
after the live process has stopped.  It can call only the allow-listed Kiwoom
minute-chart API and never persists credentials or access tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from collect_market_replay import CaptureError, KiwoomReadOnlyClient, RestRequest
from market_replay_common import (
    ReplayStore,
    iso_utc,
    latest_run_id,
    load_env_file,
    normalize_stock_code,
    parse_trade_date,
)

LOG = logging.getLogger("dayjaview.market_backfill_repair")


def load_run(connection: Any, run_id: str) -> tuple[date, str]:
    row = connection.execute(
        "SELECT trade_date,status FROM collection_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        raise CaptureError(f"unknown replay run: {run_id}")
    return parse_trade_date(str(row[0])), str(row[1])


def load_master_codes(connection: Any, run_id: str) -> set[str]:
    codes: set[str] = set()
    rows = connection.execute(
        "SELECT payload_json FROM events "
        "WHERE run_id=? AND event_type='reference.stock_master' ORDER BY sequence",
        (run_id,),
    )
    for (payload_json,) in rows:
        payload = json.loads(payload_json)
        for item in (payload.get("response") or {}).get("list") or []:
            if isinstance(item, dict):
                code = normalize_stock_code(item.get("code"))
                if code:
                    codes.add(code)
    if not codes:
        raise CaptureError("stock master is missing from the replay database")
    return codes


def completed_codes(connection: Any, run_id: str) -> set[str]:
    rows = connection.execute(
        "SELECT DISTINCT stock_code FROM events "
        "WHERE run_id=? AND event_type IN "
        "('backfill.minute.completed','backfill.repair.completed') "
        "AND stock_code IS NOT NULL",
        (run_id,),
    )
    return {str(row[0]) for row in rows}


def select_repair_codes(
    connection: Any, run_id: str, *, scope: str = "incomplete"
) -> list[str]:
    master = load_master_codes(connection, run_id)
    if scope == "all":
        return sorted(master)
    return sorted(master - completed_codes(connection, run_id))


async def fetch_and_store(
    *,
    client: KiwoomReadOnlyClient,
    store: ReplayStore,
    run_id: str,
    trade_date: date,
    code: str,
    position: int,
    target_count: int,
) -> int:
    target_prefix = trade_date.strftime("%Y%m%d")
    request = RestRequest(
        "ka10080",
        "/api/dostk/chart",
        {
            "stk_cd": code,
            "tic_scope": "1",
            "upd_stkpc_tp": "1",
            "base_dt": target_prefix,
        },
    )
    payload, _ = await client.post(request, retries=2)
    received_at = iso_utc()
    rows = [
        row
        for row in payload.get("stk_min_pole_chart_qry") or []
        if isinstance(row, dict)
        and str(row.get("cntr_tm") or "").startswith(target_prefix)
    ]
    inserted = store.append_minute_bars(
        run_id=run_id,
        stock_code=code,
        rows=rows,
        source_received_at=received_at,
    )
    store.append_event(
        run_id=run_id,
        event_type="backfill.repair.completed",
        source="kiwoom_rest",
        payload={
            "apiId": "ka10080",
            "position": position,
            "targetCount": target_count,
            "barCount": inserted,
        },
        stock_code=code,
        received_at=received_at,
    )
    return inserted


async def repair(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    database = output_dir / "market-replay.sqlite3"
    if not database.exists():
        raise CaptureError(f"replay database does not exist: {database}")

    load_env_file(Path(args.env_file))
    client = KiwoomReadOnlyClient(
        args.mode,
        os.getenv("KIWOOM_APP_KEY", "").strip(),
        os.getenv("KIWOOM_APP_SECRET", "").strip(),
    )
    try:
        with ReplayStore(output_dir) as store:
            run_id = args.run_id or latest_run_id(database)
            trade_date, status = load_run(store.connection, run_id)
            if status == "RUNNING" and not args.allow_stale_running:
                raise CaptureError(
                    "run is still marked RUNNING; stop/check the live collector first, "
                    "then use --allow-stale-running only for a confirmed stale run"
                )
            targets = select_repair_codes(
                store.connection, run_id, scope=args.scope
            )
            store.append_event(
                run_id=run_id,
                event_type="backfill.repair.started",
                source="collector",
                payload={"scope": args.scope, "targetCount": len(targets)},
            )
            completed = 0
            failed = 0
            row_count = 0
            for position, code in enumerate(targets, start=1):
                try:
                    row_count += await fetch_and_store(
                        client=client,
                        store=store,
                        run_id=run_id,
                        trade_date=trade_date,
                        code=code,
                        position=position,
                        target_count=len(targets),
                    )
                    completed += 1
                except Exception as exc:
                    failed += 1
                    store.append_event(
                        run_id=run_id,
                        event_type="backfill.repair.failed",
                        source="kiwoom_rest",
                        payload={"apiId": "ka10080", "error": str(exc)},
                        stock_code=code,
                    )
                if position % 50 == 0:
                    store.flush()
                    LOG.info(
                        "repair %d/%d completed=%d failed=%d rows=%d",
                        position,
                        len(targets),
                        completed,
                        failed,
                        row_count,
                    )

            store.flush()
            remaining = select_repair_codes(store.connection, run_id)
            summary = {
                "runId": run_id,
                "targetCount": len(targets),
                "completed": completed,
                "failed": failed,
                "remainingIncomplete": len(remaining),
                "rows": row_count,
            }
            store.append_event(
                run_id=run_id,
                event_type="backfill.repair.finished",
                source="collector",
                payload=summary,
            )
            if failed or remaining:
                store.finish_run(
                    run_id,
                    status="FAILED",
                    error=(
                        f"minute backfill repair incomplete: failed={failed}, "
                        f"remaining={len(remaining)}"
                    ),
                )
            else:
                store.finish_run(run_id, status="COMPLETED")
            return summary
    finally:
        await client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--env-file", default=".env.local")
    parser.add_argument("--mode", choices=("real", "demo"), default="real")
    parser.add_argument("--run-id")
    parser.add_argument("--scope", choices=("incomplete", "all"), default="incomplete")
    parser.add_argument("--allow-stale-running", action="store_true")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    result = asyncio.run(repair(build_parser().parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["failed"] and not result["remainingIncomplete"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CaptureError, OSError, ValueError) as exc:
        print(f"market backfill repair failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
