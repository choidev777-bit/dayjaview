#!/usr/bin/env python3
"""Replay the recorded 2026-08-14 Kiwoom capture through the live adapter.

A-3 ③ needs an open market. This script closes everything that does not:
it feeds the *raw recorded WebSocket frames* into the real LiveKiwoomAdapter
transport seam, so the adapter, MarketGateway, LiveMarketRunner and
MarketDataPipeline all run exactly as they would in production. Only the
sockets are replaced - no network call is made.

Snapshot (ka10095) requests are answered from the supplemental capture, so the
REST path is exercised against recorded provider payloads too.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.adapters.kiwoom import (  # noqa: E402
    LiveKiwoomAdapter,
    MarketGateway,
)
from packages.adapters.kiwoom.live import KST  # noqa: E402
from packages.events import InMemoryEventStore  # noqa: E402
from packages.pipeline import (  # noqa: E402
    LiveMarketRunner,
    MarketDataPipeline,
    MarketPublishLoop,
    load_collected_references,
    load_theme_universe,
)
from packages.realtime import InMemorySnapshotRepository  # noqa: E402

TOKEN = "replay-token-not-a-real-credential"


class VirtualClock:
    def __init__(self, now: datetime) -> None:
        self._now = now
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self._now

    def set(self, value: datetime) -> None:
        with self._lock:
            self._now = value


class ReplayWebSocket:
    """Serve recorded raw frames in order, pacing them by the virtual clock."""

    def __init__(
        self,
        database: Path,
        clock: VirtualClock,
        *,
        condition_list: dict[str, Any],
        limit: int | None,
    ) -> None:
        self._connection = sqlite3.connect(database, check_same_thread=False)
        self._connection.execute("PRAGMA query_only=ON")
        query = (
            "SELECT received_at,payload_json FROM events "
            "WHERE event_type='kiwoom.websocket.raw' ORDER BY sequence"
        )
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        self._cursor = self._connection.execute(query)
        self._clock = clock
        self._condition_list = condition_list
        self._lock = threading.Lock()
        self._pending: list[str] = []
        self._lookahead: tuple[datetime, str] | None = None
        self.registrations: list[dict[str, Any]] = []
        self.frames_served = 0
        self.exhausted = False
        self._advance()

    def _advance(self) -> None:
        row = self._cursor.fetchone()
        if row is None:
            self._lookahead = None
            self.exhausted = True
            return
        self._lookahead = (datetime.fromisoformat(row[0]), row[1])

    @property
    def next_frame_at(self) -> datetime | None:
        with self._lock:
            return None if self._lookahead is None else self._lookahead[0]

    def send(self, message: str) -> None:
        payload = json.loads(message)
        transaction = str(payload.get("trnm") or "")
        with self._lock:
            if transaction == "LOGIN":
                self._pending.append(
                    json.dumps({"trnm": "LOGIN", "return_code": 0})
                )
            elif transaction == "CNSRLST":
                self._pending.append(
                    json.dumps(self._condition_list, ensure_ascii=False)
                )
            elif transaction == "CNSRREQ":
                self._pending.append(
                    json.dumps(
                        {
                            "trnm": "CNSRREQ",
                            "seq": payload.get("seq"),
                            "return_code": 0,
                            "data": None,
                        }
                    )
                )
            elif transaction == "REG":
                self.registrations.append(payload)
                self._pending.append(
                    json.dumps({"trnm": "REG", "return_code": 0, "return_msg": ""})
                )

    def recv(self, timeout: float | None = None) -> str:
        with self._lock:
            if self._pending:
                return self._pending.pop(0)
            if self._lookahead is not None and self._lookahead[0] <= self._clock():
                frame = self._lookahead[1]
                self._advance()
                self.frames_served += 1
                return frame
        raise TimeoutError

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class SnapshotResponder:
    """Answer ka10095 from the supplemental capture at the virtual clock."""

    def __init__(self, database: Path | None, clock: VirtualClock) -> None:
        self._clock = clock
        self._by_code: dict[str, tuple[list[datetime], list[dict[str, Any]]]] = {}
        self.requests = 0
        self.rows_served = 0
        if database is None or not database.is_file():
            return
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA query_only=ON")
        collected: dict[str, list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
        try:
            for received_at, payload_json in connection.execute(
                "SELECT received_at,payload_json FROM events "
                "WHERE event_type='market.snapshot' ORDER BY sequence"
            ):
                payload = json.loads(payload_json)
                row = payload.get("raw")
                if not isinstance(row, dict):
                    continue
                code = str(row.get("stk_cd") or "").strip()
                if len(code) == 6 and code.isdigit():
                    collected[code].append((datetime.fromisoformat(received_at), row))
        finally:
            connection.close()
        for code, entries in collected.items():
            entries.sort(key=lambda entry: entry[0])
            self._by_code[code] = (
                [entry[0] for entry in entries],
                [entry[1] for entry in entries],
            )

    def rows_for(self, codes: list[str]) -> list[dict[str, Any]]:
        now = self._clock()
        self.requests += 1
        rows: list[dict[str, Any]] = []
        for code in codes:
            entry = self._by_code.get(code)
            if entry is None:
                continue
            times, values = entry
            position = bisect_right(times, now)
            if position:
                rows.append(values[position - 1])
        self.rows_served += len(rows)
        return rows


def http_transport(responder: SnapshotResponder) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(
                200,
                json={
                    "return_code": 0,
                    "token": TOKEN,
                    "expires_dt": "20991231235959",
                },
            )
        if request.url.path == "/api/dostk/stkinfo":
            codes = json.loads(request.content)["stk_cd"].split("|")
            return httpx.Response(
                200,
                json={"return_code": 0, "atn_stk_infr": responder.rows_for(codes)},
            )
        return httpx.Response(404, json={"return_code": 404})

    return httpx.MockTransport(handler)


def load_condition_list(database: Path) -> dict[str, Any]:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA query_only=ON")
    try:
        row = connection.execute(
            "SELECT payload_json FROM events "
            "WHERE event_type='candidate.condition_list' ORDER BY sequence LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise SystemExit("capture has no recorded condition list")
    payload = json.loads(row[0])
    payload["trnm"] = "CNSRLST"
    return payload


def capture_window(database: Path, limit: int | None) -> tuple[datetime, datetime]:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA query_only=ON")
    query = (
        "SELECT received_at FROM events WHERE event_type='kiwoom.websocket.raw' "
        "ORDER BY sequence"
    )
    if limit is not None:
        query += f" LIMIT {int(limit)}"
    try:
        rows = connection.execute(
            f"SELECT MIN(received_at),MAX(received_at) FROM ({query})"
        ).fetchone()
    finally:
        connection.close()
    return datetime.fromisoformat(rows[0]), datetime.fromisoformat(rows[1])


def run(args: argparse.Namespace) -> dict[str, Any]:
    database = Path(args.database)
    started_at, ended_at = capture_window(database, args.limit)
    market_date = started_at.astimezone(KST).date()
    clock = VirtualClock(started_at - timedelta(seconds=1))

    universe = load_theme_universe(
        Path(args.infostock_dir),
        effective_from=market_date,
        known_at=started_at - timedelta(hours=1),
    )
    if args.reference_data_dir:
        universe = type(universe)(
            version=universe.version,
            snapshots=universe.snapshots,
            theme_names=universe.theme_names,
            stock_names=universe.stock_names,
            references=load_collected_references(
                Path(args.reference_data_dir),
                market_date=market_date,
                decision_at=started_at,
                stock_ids=universe.stock_names,
            ),
        )

    websocket = ReplayWebSocket(
        database,
        clock,
        condition_list=load_condition_list(database),
        limit=args.limit,
    )
    responder = SnapshotResponder(
        Path(args.supplemental_database) if args.supplemental_database else None,
        clock,
    )
    adapter = LiveKiwoomAdapter(
        mode="real",
        app_key="replay-key",
        app_secret="replay-secret",
        max_conditions=args.max_conditions,
        http_transport=http_transport(responder),
        ws_connect=lambda url: websocket,
        clock=clock,
        sleep=lambda seconds: None,
        poll_timeout=0.005,
    )
    gateway = MarketGateway(adapter)
    runner = LiveMarketRunner(
        gateway=gateway,
        market_date=market_date,
        theme_members={
            snapshot.theme_id: tuple(member.stock_id for member in snapshot.members)
            for snapshot in universe.snapshots
        },
        clock=clock,
    )
    pipeline = MarketDataPipeline(
        market_date=market_date,
        stream_id="stream_replay_verify",
        schema_version="2026-08-14.1",
        catalog=universe.catalog(),
        references=universe.references,
        membership_version=universe.version,
        theme_names=universe.theme_names,
        stock_names=universe.stock_names,
        event_store=InMemoryEventStore(),
        snapshot_repository=InMemorySnapshotRepository(),
    )
    published: list[Any] = []
    loop = MarketPublishLoop(
        pipeline=pipeline,
        on_published=published.append,
        data_status=runner.data_status,
        interval=timedelta(seconds=args.publish_interval),
        poll_updates=runner.poll_updates,
        clock=clock,
    )

    step = timedelta(seconds=args.publish_interval)
    virtual = started_at
    update_count = 0
    max_subscriptions = 0
    wall_started = time.monotonic()
    while virtual <= ended_at + step:
        clock.set(virtual)
        # Let the adapter's reader thread pull every frame now due before the
        # pipeline observes this instant.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            pending_at = websocket.next_frame_at
            if pending_at is None or pending_at > virtual:
                break
            time.sleep(0.001)
        loop.tick()
        update_count += 0  # updates are applied inside tick
        max_subscriptions = max(max_subscriptions, len(gateway.subscriptions.current))
        virtual += step
    wall_seconds = time.monotonic() - wall_started

    accepted = Counter(
        event.event_type.value for event in gateway.accepted_events
    )
    lifecycle = Counter(
        event.lifecycle_status.value for event in pipeline.current_events()
    )
    view = published[-1] if published else None
    items = list(view.rankings.payload["items"]) if view is not None else []
    coverage = gateway.coverage(
        gateway.subscriptions.current, now=clock()
    )
    adapter.close()

    return {
        "capture": {
            "database": str(database),
            "framesServed": websocket.frames_served,
            "windowKst": [
                started_at.astimezone(KST).isoformat(),
                ended_at.astimezone(KST).isoformat(),
            ],
            "wallSeconds": round(wall_seconds, 1),
        },
        "adapter": {
            "canonicalEventsAccepted": dict(sorted(accepted.items())),
            "registrationPackets": len(websocket.registrations),
            "maxSubscribedStocks": max_subscriptions,
            "snapshotRequests": responder.requests,
            "snapshotRowsServed": responder.rows_served,
            "normalizationErrors": runner.normalization_errors,
        },
        "pipeline": {
            "publishes": len(published),
            "updatesApplied": update_count or None,
            "eventsByLifecycle": dict(sorted(lifecycle.items())),
            "dataStatus": runner.data_status().value,
            "coverage": {
                "status": coverage.status.value,
                "requested": coverage.requested_count,
                "fresh": coverage.fresh_count,
                "stale": coverage.stale_count,
                "missing": coverage.missing_count,
            },
        },
        "rankings": {
            "count": len(items),
            "top": [
                {
                    "rank": item["rank"],
                    "theme": item["classification"]["displayName"],
                    "weightedReturn": item["weightedReturn"],
                    "advancing": item["advancingCount"],
                    "valid": item["validCount"],
                    "leader": (item.get("leader") or {}).get("name"),
                }
                for item in items[:10]
            ],
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", default="data/market-replay/2026-08-14/market-replay.sqlite3"
    )
    parser.add_argument(
        "--supplemental-database",
        default="data/market-replay-supplemental/2026-08-14/market-replay.sqlite3",
    )
    parser.add_argument("--infostock-dir", default="data/infostock/import")
    parser.add_argument("--reference-data-dir", default="data/reference-data/2026-08-14")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--publish-interval", type=float, default=2.0)
    parser.add_argument("--max-conditions", type=int, default=8)
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = run(build_parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
