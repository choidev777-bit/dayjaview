#!/usr/bin/env python3
"""Recover the known pre-sidecar gap with ka10084 one-minute market state.

This is an explicit after-market, read-only recovery.  It does not pretend the
minute rows were received live: the archive keeps the real capture time while
``occurred_at`` and ``payload.replayAt`` describe the historical minute end.
The 60-second resolution and quote-only-change limitation stay in metadata and
are audited as a non-exact recovery.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Any

from collect_market_replay import CaptureError, KiwoomReadOnlyClient, RestRequest
from market_replay_common import (
    KST,
    ReplayStore,
    iso_utc,
    latest_run_id,
    load_env_file,
    normalize_stock_code,
    parse_trade_date,
)


LOG = logging.getLogger("dayjaview.market_gap_recovery")
PURPOSE = "ka10084 one-minute recovery for the pre-sidecar snapshot gap"


def load_main_metadata(database: Path, run_id: str | None) -> dict[str, Any]:
    connection = sqlite3.connect(database)
    try:
        resolved_run = run_id or latest_run_id(database)
        run = connection.execute(
            "SELECT trade_date,status FROM collection_runs WHERE run_id=?",
            (resolved_run,),
        ).fetchone()
        if run is None:
            raise CaptureError(f"unknown main run: {resolved_run}")
        master: dict[str, dict[str, Any]] = {}
        for (payload_json,) in connection.execute(
            "SELECT payload_json FROM events WHERE run_id=? "
            "AND event_type='reference.stock_master'",
            (resolved_run,),
        ):
            payload = json.loads(payload_json)
            for row in (payload.get("response") or {}).get("list") or []:
                if not isinstance(row, dict):
                    continue
                code = normalize_stock_code(row.get("code"))
                if code:
                    master[code] = row
        theme_codes: set[str] = set()
        stock_to_themes: dict[str, set[str]] = defaultdict(set)
        theme_members: dict[str, set[str]] = defaultdict(set)
        for (payload_json,) in connection.execute(
            "SELECT payload_json FROM events WHERE run_id=? "
            "AND event_type='reference.infostock_theme'",
            (resolved_run,),
        ):
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
                    theme_codes.add(code)
                    stock_to_themes[code].add(theme_id)
                    theme_members[theme_id].add(code)
        if not master or not theme_codes:
            raise CaptureError("main replay is missing stock master or theme membership")
        return {
            "runId": resolved_run,
            "tradeDate": parse_trade_date(str(run[0])),
            "status": str(run[1]),
            "master": master,
            "targets": sorted(theme_codes.intersection(master)),
            "stockToThemes": stock_to_themes,
            "themeMembers": theme_members,
        }
    finally:
        connection.close()


def minute_end_utc(trade_date: date, value: Any) -> str | None:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) < 4:
        return None
    try:
        local = datetime.combine(
            trade_date,
            clock_time(int(digits[:2]), int(digits[2:4]), 59, 999999),
            tzinfo=KST,
        )
    except ValueError:
        return None
    return iso_utc(local)


def in_window(value: Any, start_hhmm: str, end_hhmm: str) -> bool:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return len(digits) >= 4 and start_hhmm <= digits[:4] <= end_hhmm


def condition_refreshes_candidate(payload: dict[str, Any]) -> bool:
    values = payload.get("values") if isinstance(payload, dict) else None
    if isinstance(values, dict):
        return str(values.get("843") or "") == "I"
    return str(payload.get("action") or "") == "INITIAL"


def reconstruct_intended_codes_by_minute(
    database: Path,
    *,
    run_id: str,
    trade_date: date,
    master_codes: set[str],
    stock_to_themes: dict[str, set[str]],
    theme_members: dict[str, set[str]],
    start_hhmm: str,
    end_hhmm: str,
    candidate_ttl_seconds: int,
) -> dict[str, set[str]]:
    """Replay candidate/subscription decisions at each completed minute."""
    start_local = datetime.combine(
        trade_date,
        clock_time(int(start_hhmm[:2]), int(start_hhmm[2:4]), 59, 999999),
        tzinfo=KST,
    )
    end_local = datetime.combine(
        trade_date,
        clock_time(int(end_hhmm[:2]), int(end_hhmm[2:4]), 59, 999999),
        tzinfo=KST,
    )
    end_utc = end_local.astimezone(timezone.utc).isoformat()
    connection = sqlite3.connect(database)
    try:
        events: list[tuple[datetime, int, str, str | None, dict[str, Any]]] = []
        for event_type in (
            "candidate.rest",
            "candidate.condition",
            "subscription.changed",
        ):
            rows = connection.execute(
                "SELECT received_at,sequence,stock_code,payload_json FROM events "
                "INDEXED BY events_type_idx WHERE run_id=? AND event_type=? "
                "AND received_at<=?",
                (run_id, event_type, end_utc),
            )
            for received_at, sequence, stock_code, payload_json in rows:
                events.append(
                    (
                        datetime.fromisoformat(received_at),
                        int(sequence),
                        event_type,
                        str(stock_code) if stock_code else None,
                        json.loads(payload_json),
                    )
                )
        events.sort(key=lambda item: (item[0], item[1]))
    finally:
        connection.close()

    candidate_last_seen: dict[str, datetime] = {}
    current_targets: set[str] = set()
    event_position = 0
    result: dict[str, set[str]] = {}
    minute = start_local
    while minute <= end_local:
        minute_utc = minute.astimezone(timezone.utc)
        while event_position < len(events) and events[event_position][0] <= minute_utc:
            received_at, _, event_type, stock_code, payload = events[event_position]
            event_position += 1
            if event_type == "candidate.rest" and stock_code:
                candidate_last_seen[stock_code] = received_at
            elif (
                event_type == "candidate.condition"
                and stock_code
                and condition_refreshes_candidate(payload)
            ):
                candidate_last_seen[stock_code] = received_at
            elif event_type == "subscription.changed" and payload.get("kind") == "stock_trade":
                current_targets = {
                    code
                    for value in payload.get("targets") or []
                    for code in [normalize_stock_code(value)]
                    if code
                }
        active_candidates = {
            code
            for code, seen_at in candidate_last_seen.items()
            if 0 <= (minute_utc - seen_at).total_seconds() <= candidate_ttl_seconds
        }
        active_themes: set[str] = set()
        for code in active_candidates:
            active_themes.update(stock_to_themes.get(code, set()))
        related: set[str] = set()
        for theme_id in active_themes:
            related.update(theme_members.get(theme_id, set()))
        result[minute.strftime("%H%M")] = (
            related.intersection(master_codes) - current_targets
        )
        minute += timedelta(minutes=1)
    return result


def find_matching_run(
    store: ReplayStore, *, trade_date: date, parent_run_id: str
) -> tuple[str, str] | None:
    rows = store.connection.execute(
        "SELECT run_id,status,settings_json FROM collection_runs "
        "WHERE trade_date=? AND status IN ('RUNNING','COMPLETED') "
        "ORDER BY started_at DESC",
        (trade_date.isoformat(),),
    )
    for run_id, status, settings_json in rows:
        settings = json.loads(settings_json)
        if settings.get("purpose") == PURPOSE and settings.get("parentRunId") == parent_run_id:
            return str(run_id), str(status)
    return None


def completed_codes(store: ReplayStore, run_id: str) -> set[str]:
    return {
        str(row[0])
        for row in store.connection.execute(
            "SELECT DISTINCT stock_code FROM events WHERE run_id=? "
            "AND event_type='gap_recovery.stock.completed' AND stock_code IS NOT NULL",
            (run_id,),
        )
    }


async def recover(args: argparse.Namespace) -> dict[str, Any]:
    main_database = Path(args.main_database).resolve()
    if not main_database.is_file():
        raise CaptureError(f"main replay database does not exist: {main_database}")
    metadata = load_main_metadata(main_database, args.main_run_id)
    if metadata["status"] != "COMPLETED" and not args.allow_running_main:
        raise CaptureError(
            f"main run must be COMPLETED before gap recovery; status={metadata['status']}"
        )
    trade_date: date = metadata["tradeDate"]
    start_hhmm = args.gap_start.replace(":", "")[:4]
    end_hhmm = args.gap_end.replace(":", "")[:4]
    if len(start_hhmm) != 4 or len(end_hhmm) != 4 or start_hhmm > end_hhmm:
        raise CaptureError("gap start/end must be an increasing HH:MM window")
    intended_by_minute = reconstruct_intended_codes_by_minute(
        main_database,
        run_id=metadata["runId"],
        trade_date=trade_date,
        master_codes=set(metadata["master"]),
        stock_to_themes=metadata["stockToThemes"],
        theme_members=metadata["themeMembers"],
        start_hhmm=start_hhmm,
        end_hhmm=end_hhmm,
        candidate_ttl_seconds=args.candidate_ttl_seconds,
    )
    targets = sorted(
        set().union(*intended_by_minute.values()) if intended_by_minute else set()
    )
    if not targets:
        raise CaptureError("no intended active-theme/non-0B recovery targets were reconstructed")
    if args.max_stocks is not None:
        targets = targets[: args.max_stocks]

    load_env_file(Path(args.env_file))
    client = KiwoomReadOnlyClient(
        args.mode,
        os.getenv("KIWOOM_APP_KEY", "").strip(),
        os.getenv("KIWOOM_APP_SECRET", "").strip(),
    )
    output_dir = Path(args.output_dir).resolve()
    try:
        with ReplayStore(output_dir) as store:
            matching_run = find_matching_run(
                store,
                trade_date=trade_date,
                parent_run_id=metadata["runId"],
            )
            if matching_run is not None and matching_run[1] == "COMPLETED":
                run_id = matching_run[0]
                completed = completed_codes(store, run_id)
                state_count = store.connection.execute(
                    "SELECT COUNT(*) FROM events WHERE run_id=? "
                    "AND event_type='market.minute_state.recovered'",
                    (run_id,),
                ).fetchone()[0]
                return {
                    "runId": run_id,
                    "alreadyComplete": True,
                    "targetCount": len(targets),
                    "completedTotal": len(completed),
                    "recoveredStateCount": int(state_count),
                }
            run_id = matching_run[0] if matching_run is not None else None
            resumed = run_id is not None
            if run_id is None:
                run_id = store.start_run(
                    trade_date=trade_date,
                    mode=args.mode,
                    settings={
                        "purpose": PURPOSE,
                        "parentRunId": metadata["runId"],
                        "mainDatabase": str(main_database),
                        "sourceApi": "ka10084",
                        "gapStart": f"{trade_date.isoformat()}T{args.gap_start}:00+09:00",
                        "gapEnd": f"{trade_date.isoformat()}T{args.gap_end}:59.999999+09:00",
                        "queryTime": end_hhmm,
                        "resolutionSeconds": 60,
                        "exactFullSessionCoverage": False,
                        "limitations": [
                            "one-minute completed states, not 30-second live snapshots",
                            "quote-only changes between trades are not recoverable",
                            "capture received_at is after market; replay uses occurred_at",
                        ],
                        "targetCount": len(targets),
                        "intendedMinuteInstanceCount": sum(
                            len(codes.intersection(targets))
                            for codes in intended_by_minute.values()
                        ),
                        "candidateTtlSeconds": args.candidate_ttl_seconds,
                        "selectionContract": (
                            "candidate TTL -> frozen theme membership -> "
                            "minus contemporaneous 0B subscription"
                        ),
                        "orderApisEnabled": False,
                    },
                )
                store.append_event(
                    run_id=run_id,
                    event_type="source.status",
                    source="gap_recovery",
                    payload={
                        "status": "GAP_RECOVERY_STARTED",
                        "parentRunId": metadata["runId"],
                        "targetCount": len(targets),
                    },
                )
            else:
                store.append_event(
                    run_id=run_id,
                    event_type="source.status",
                    source="gap_recovery",
                    payload={"status": "GAP_RECOVERY_RESUMED"},
                )

            already_completed = completed_codes(store, run_id)
            pending = [code for code in targets if code not in already_completed]
            failed = 0
            recovered_states = 0
            for position, code in enumerate(pending, start=1):
                try:
                    payload, headers = await client.post(
                        RestRequest(
                            "ka10084",
                            "/api/dostk/stkinfo",
                            {
                                "stk_cd": code,
                                "tdy_pred": "1",
                                "tic_min": "1",
                                "tm": end_hhmm,
                            },
                        ),
                        retries=2,
                    )
                    captured_at = iso_utc()
                    rows = [
                        row
                        for row in payload.get("tdy_pred_cntr") or []
                        if isinstance(row, dict)
                        and in_window(row.get("tm"), start_hhmm, end_hhmm)
                    ]
                    rows.sort(key=lambda row: str(row.get("tm") or ""))
                    selected_rows = [
                        row
                        for row in rows
                        if code
                        in intended_by_minute.get(
                            "".join(
                                character
                                for character in str(row.get("tm") or "")
                                if character.isdigit()
                            )[:4],
                            set(),
                        )
                    ]
                    store.append_event(
                        run_id=run_id,
                        event_type="kiwoom.ka10084.raw",
                        source="kiwoom_rest",
                        payload={
                            "apiId": "ka10084",
                            "requestedCode": code,
                            "queryTime": end_hhmm,
                            "responseHeaders": headers,
                            "response": payload,
                        },
                        received_at=captured_at,
                        stock_code=code,
                    )
                    first_replay_at: str | None = None
                    last_replay_at: str | None = None
                    for row in selected_rows:
                        replay_at = minute_end_utc(trade_date, row.get("tm"))
                        if replay_at is None:
                            continue
                        first_replay_at = first_replay_at or replay_at
                        last_replay_at = replay_at
                        store.append_event(
                            run_id=run_id,
                            event_type="market.minute_state.recovered",
                            source="kiwoom_rest_historical_recovery",
                            payload={
                                "apiId": "ka10084",
                                "source": "HISTORICAL_MINUTE_RECOVERY",
                                "replayAt": replay_at,
                                "capturedAt": captured_at,
                                "resolutionSeconds": 60,
                                "exactLiveSnapshot": False,
                                "selection": {
                                    "reason": "ACTIVE_THEME_NON_0B",
                                    "asOfMinute": str(row.get("tm") or "")[:4],
                                },
                                "raw": row,
                            },
                            occurred_at=replay_at,
                            received_at=captured_at,
                            stock_code=code,
                            source_sequence=f"{code}:{row.get('tm')}",
                        )
                        recovered_states += 1
                    store.append_event(
                        run_id=run_id,
                        event_type="gap_recovery.stock.completed",
                        source="gap_recovery",
                        payload={
                            "position": position,
                            "pendingTargetCount": len(pending),
                            "rawWindowStateCount": len(rows),
                            "stateCount": len(selected_rows),
                            "firstReplayAt": first_replay_at,
                            "lastReplayAt": last_replay_at,
                            "masterAuditInfo": (metadata["master"].get(code) or {}).get(
                                "auditInfo"
                            ),
                        },
                        received_at=captured_at,
                        stock_code=code,
                    )
                except Exception as exc:
                    failed += 1
                    store.append_event(
                        run_id=run_id,
                        event_type="source.error",
                        source="kiwoom_rest",
                        payload={"apiId": "ka10084", "error": str(exc)},
                        stock_code=code,
                    )
                    store.append_event(
                        run_id=run_id,
                        event_type="gap_recovery.stock.failed",
                        source="gap_recovery",
                        payload={"error": str(exc)},
                        stock_code=code,
                    )
                if args.request_delay_seconds:
                    await asyncio.sleep(args.request_delay_seconds)
                if position % 50 == 0:
                    store.flush()
                    LOG.info(
                        "gap recovery %d/%d failed=%d states=%d",
                        position,
                        len(pending),
                        failed,
                        recovered_states,
                    )

            store.flush()
            complete_now = completed_codes(store, run_id)
            remaining = sorted(set(targets) - complete_now)
            summary = {
                "runId": run_id,
                "resumed": resumed,
                "targetCount": len(targets),
                "alreadyCompleted": len(already_completed),
                "attempted": len(pending),
                "completedTotal": len(complete_now),
                "failedThisRun": failed,
                "remaining": len(remaining),
                "recoveredStatesThisRun": recovered_states,
            }
            store.append_event(
                run_id=run_id,
                event_type="source.status",
                source="gap_recovery",
                payload={"status": "GAP_RECOVERY_FINISHED", **summary},
            )
            if failed or remaining:
                store.flush()
                raise CaptureError(
                    f"gap recovery incomplete: failed={failed} remaining={len(remaining)}"
                )
            store.finish_run(run_id, status="COMPLETED")
            return summary
    finally:
        await client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main-database", required=True)
    parser.add_argument("--main-run-id")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--env-file", default=".env.local")
    parser.add_argument("--mode", choices=("real", "demo"), default="real")
    parser.add_argument("--gap-start", default="09:00")
    parser.add_argument("--gap-end", default="10:09")
    parser.add_argument("--request-delay-seconds", type=float, default=0.05)
    parser.add_argument("--max-stocks", type=int)
    parser.add_argument("--candidate-ttl-seconds", type=int, default=1800)
    parser.add_argument("--allow-running-main", action="store_true")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = build_parser().parse_args()
    if args.request_delay_seconds < 0:
        raise CaptureError("request-delay-seconds must be non-negative")
    result = asyncio.run(recover(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CaptureError, OSError, ValueError, sqlite3.DatabaseError) as exc:
        print(f"market gap recovery failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
