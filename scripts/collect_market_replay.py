#!/usr/bin/env python3
"""Capture the 2026-08-14 DAYJAVIEW market replay fixture.

This is a read-only, one-trading-day collector. It deliberately contains no
order, account, or balance API calls. Access tokens remain in memory and are
never passed to the replay store.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import httpx
import websockets

from market_replay_common import (
    COLLECTOR_VERSION,
    KST,
    ReplayStore,
    canonical_json,
    iso_utc,
    load_env_file,
    market_datetime,
    normalize_stock_code,
    parse_clock,
    parse_trade_date,
    source_clock_to_utc,
    utc_now,
)


LOG = logging.getLogger("dayjaview.market_capture")
REAL_HTTP_BASE = "https://api.kiwoom.com"
DEMO_HTTP_BASE = "https://mockapi.kiwoom.com"
REAL_WS_URL = "wss://api.kiwoom.com:10000/api/dostk/websocket"
DEMO_WS_URL = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"


class CaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class RestRequest:
    api_id: str
    path: str
    body: dict[str, Any]


SAFETY_NET_REQUESTS = (
    RestRequest(
        "ka10019",
        "/api/dostk/stkinfo",
        {
            "mrkt_tp": "000",
            "flu_tp": "1",
            "tm_tp": "1",
            "tm": "5",
            "trde_qty_tp": "00010",
            "stk_cnd": "4",
            "crd_cnd": "0",
            "pric_cnd": "8",
            "updown_incls": "1",
            "stex_tp": "1",
        },
    ),
    RestRequest(
        "ka10023",
        "/api/dostk/rkinfo",
        {
            "mrkt_tp": "000",
            "sort_tp": "2",
            "tm_tp": "1",
            "trde_qty_tp": "5",
            "stk_cnd": "20",
            "pric_tp": "8",
            "stex_tp": "1",
            "tm": "5",
        },
    ),
    RestRequest(
        "ka10027",
        "/api/dostk/rkinfo",
        {
            "mrkt_tp": "000",
            "sort_tp": "1",
            "trde_qty_cnd": "0010",
            "stk_cnd": "16",
            "crd_cnd": "0",
            "updown_incls": "1",
            "pric_cnd": "8",
            "trde_prica_cnd": "10",
            "stex_tp": "1",
        },
    ),
    RestRequest(
        "ka10032",
        "/api/dostk/rkinfo",
        {"mrkt_tp": "000", "mang_stk_incls": "0", "stex_tp": "1"},
    ),
)


class AsyncRateLimiter:
    def __init__(self, minimum_interval: float = 0.26) -> None:
        self.minimum_interval = minimum_interval
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_allowed - now)
            if delay:
                await asyncio.sleep(delay)
            self._next_allowed = time.monotonic() + self.minimum_interval


class KiwoomReadOnlyClient:
    """Small explicit allow-listed client; it cannot call order/account APIs."""

    ALLOWED_API_IDS = {
        "ka10019",
        "ka10023",
        "ka10027",
        "ka10032",
        "ka10080",
        "ka10084",
        "ka10095",
        "ka10099",
    }

    def __init__(self, mode: str, app_key: str, app_secret: str) -> None:
        if mode not in {"real", "demo"}:
            raise CaptureError("live capture mode must be 'real' or 'demo'")
        if not app_key or not app_secret:
            raise CaptureError("KIWOOM_APP_KEY and KIWOOM_APP_SECRET are required")
        self.mode = mode
        self.app_key = app_key
        self.app_secret = app_secret
        self.http_base = REAL_HTTP_BASE if mode == "real" else DEMO_HTTP_BASE
        self.ws_url = REAL_WS_URL if mode == "real" else DEMO_WS_URL
        self._token: str | None = None
        self._token_expires_at: datetime | None = None
        self._rate_limiter = AsyncRateLimiter()
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    async def close(self) -> None:
        await self._client.aclose()

    async def access_token(self) -> str:
        if self._token and self._token_expires_at:
            if utc_now() < self._token_expires_at - timedelta(minutes=10):
                return self._token
        response = await self._client.post(
            f"{self.http_base}/oauth2/token",
            headers={"Content-Type": "application/json;charset=UTF-8"},
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "secretkey": self.app_secret,
            },
        )
        payload = self._safe_json(response)
        if response.status_code >= 400 or payload.get("return_code") not in (None, 0):
            raise CaptureError(
                f"Kiwoom token issuance failed: HTTP {response.status_code}; "
                f"code={payload.get('return_code')} msg={payload.get('return_msg')}"
            )
        token = str(payload.get("token") or "")
        expires = str(payload.get("expires_dt") or "")
        if not token or len(expires) != 14:
            raise CaptureError("Kiwoom token response is missing token or expiry")
        self._token = token
        self._token_expires_at = datetime.strptime(expires, "%Y%m%d%H%M%S").replace(
            tzinfo=KST
        ).astimezone(tz=utc_now().tzinfo)
        return token

    async def post(
        self,
        request: RestRequest,
        *,
        retries: int = 3,
        cont_yn: str | None = None,
        next_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        if request.api_id not in self.ALLOWED_API_IDS:
            raise CaptureError(f"API is not on the read-only allow-list: {request.api_id}")
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                await self._rate_limiter.wait()
                headers = {
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {await self.access_token()}",
                    "api-id": request.api_id,
                }
                if cont_yn:
                    headers["cont-yn"] = cont_yn
                if next_key:
                    headers["next-key"] = next_key
                response = await self._client.post(
                    f"{self.http_base}{request.path}", headers=headers, json=request.body
                )
                payload = self._safe_json(response)
                if response.status_code in {401, 403} and attempt == 0:
                    self._token = None
                    continue
                if response.status_code >= 400:
                    raise CaptureError(f"HTTP {response.status_code}")
                if payload.get("return_code") not in (None, 0):
                    raise CaptureError(
                        f"code={payload.get('return_code')} msg={payload.get('return_msg')}"
                    )
                safe_headers = {
                    "cont-yn": response.headers.get("cont-yn", ""),
                    "next-key": response.headers.get("next-key", ""),
                }
                return payload, safe_headers
            except (httpx.HTTPError, CaptureError) as exc:
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(min(8.0, 0.5 * (2**attempt)) + random.random() / 4)
        raise CaptureError(
            f"{request.api_id} failed after {retries + 1} attempts: {last_error}"
        )

    @staticmethod
    def _safe_json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}


@dataclass
class Candidate:
    stock_code: str
    last_seen: float
    hits: int = 0
    sources: set[str] = field(default_factory=set)


class CandidateManager:
    def __init__(
        self,
        stock_to_themes: dict[str, set[str]],
        theme_members: dict[str, list[str]],
        *,
        max_subscriptions: int,
        candidate_ttl_seconds: int = 1800,
        minimum_update_interval_seconds: float = 1.0,
    ) -> None:
        self.stock_to_themes = stock_to_themes
        self.theme_members = theme_members
        self.max_subscriptions = max_subscriptions
        self.candidate_ttl_seconds = candidate_ttl_seconds
        self.minimum_update_interval_seconds = minimum_update_interval_seconds
        self.candidates: dict[str, Candidate] = {}
        self.current_targets: tuple[str, ...] = ()
        self.dirty = False
        self.last_applied_at = 0.0

    def observe(self, stock_code: str, source: str) -> bool:
        code = normalize_stock_code(stock_code)
        if not code:
            return False
        candidate = self.candidates.get(code)
        if candidate is None:
            candidate = Candidate(code, time.monotonic())
            self.candidates[code] = candidate
        candidate.last_seen = time.monotonic()
        candidate.hits += 1
        candidate.sources.add(source)
        self.dirty = True
        return True

    def select_targets(self) -> tuple[str, ...]:
        now = time.monotonic()
        if (
            self.current_targets
            and now - self.last_applied_at < self.minimum_update_interval_seconds
        ):
            # Coalesce bursts of condition events instead of replacing the
            # entire Kiwoom registration packet once per incoming message.
            self.dirty = False
            return self.current_targets
        active = [
            candidate
            for candidate in self.candidates.values()
            if now - candidate.last_seen <= self.candidate_ttl_seconds
        ]
        active.sort(key=lambda item: (-item.last_seen, -item.hits, item.stock_code))
        candidate_limit = min(60, self.max_subscriptions)
        primary = [item.stock_code for item in active[:candidate_limit]]

        related_scores: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 10**9))
        for candidate in active:
            for theme_id in self.stock_to_themes.get(candidate.stock_code, set()):
                for source_order, member in enumerate(self.theme_members.get(theme_id, [])):
                    score, best_order = related_scores[member]
                    related_scores[member] = (score + 1, min(best_order, source_order))
        related = sorted(
            related_scores,
            key=lambda code: (
                -related_scores[code][0],
                related_scores[code][1],
                code,
            ),
        )
        selected: list[str] = []
        seen: set[str] = set()
        for code in [*primary, *related]:
            if code not in seen:
                selected.append(code)
                seen.add(code)
            if len(selected) >= self.max_subscriptions:
                break
        # Preserve the order of targets that remain selected. A set-equivalent
        # ranking change must not cause a needless refresh=0 registration.
        selected_set = set(selected)
        stable = [code for code in self.current_targets if code in selected_set]
        stable.extend(code for code in selected if code not in set(stable))
        result = tuple(stable)
        self.dirty = result != self.current_targets
        return result

    def applied(self, targets: tuple[str, ...]) -> None:
        self.current_targets = targets
        self.last_applied_at = time.monotonic()
        self.dirty = False

    def explain_targets(self, targets: Iterable[str]) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        active = {
            code: candidate
            for code, candidate in self.candidates.items()
            if now - candidate.last_seen <= self.candidate_ttl_seconds
        }
        active_by_theme: dict[str, list[str]] = defaultdict(list)
        for code in active:
            for theme_id in self.stock_to_themes.get(code, set()):
                active_by_theme[theme_id].append(code)
        reasons: dict[str, dict[str, Any]] = {}
        for code in targets:
            candidate = active.get(code)
            if candidate is not None:
                reasons[code] = {
                    "kind": "direct_candidate",
                    "sources": sorted(candidate.sources),
                    "hits": candidate.hits,
                }
                continue
            theme_ids = sorted(
                theme_id
                for theme_id in self.stock_to_themes.get(code, set())
                if theme_id in active_by_theme
            )
            reasons[code] = {
                "kind": "theme_expansion" if theme_ids else "unresolved",
                "themeIds": theme_ids,
                "candidateStockCodes": sorted(
                    {
                        candidate_code
                        for theme_id in theme_ids
                        for candidate_code in active_by_theme[theme_id]
                    }
                ),
            }
        return reasons


def iter_stock_rows(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        code = next(
            (
                normalize_stock_code(value.get(key))
                for key in ("stk_cd", "jmcode", "9001", "code")
                if value.get(key) is not None
            ),
            None,
        )
        if code:
            yield value
        for child in value.values():
            yield from iter_stock_rows(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_stock_rows(child)


def stock_code_from_row(row: dict[str, Any]) -> str | None:
    for key in ("stk_cd", "jmcode", "9001", "code"):
        code = normalize_stock_code(row.get(key))
        if code:
            return code
    return None


def parse_condition_list(payload: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for row in payload.get("data") or []:
        if isinstance(row, dict):
            sequence = str(row.get("seq") or "").strip()
            name = str(row.get("name") or "").strip()
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            sequence, name = str(row[0]).strip(), str(row[1]).strip()
        else:
            continue
        if sequence:
            result.append({"seq": sequence, "name": name})
    return result


class MarketCapture:
    def __init__(
        self,
        *,
        client: KiwoomReadOnlyClient,
        store: ReplayStore,
        run_id: str,
        trade_date: date,
        start_at: datetime,
        end_at: datetime,
        poll_seconds: float,
        max_subscriptions: int,
        condition_ids: set[str],
        max_conditions: int,
        infostock_dir: Path,
    ) -> None:
        self.client = client
        self.store = store
        self.run_id = run_id
        self.trade_date = trade_date
        self.start_at = start_at
        self.end_at = end_at
        self.poll_seconds = poll_seconds
        self.max_subscriptions = max_subscriptions
        self.condition_ids = condition_ids
        self.max_conditions = max_conditions
        self.infostock_dir = infostock_dir
        self.stock_to_themes: dict[str, set[str]] = defaultdict(set)
        self.theme_members: dict[str, list[str]] = {}
        self.stock_master: dict[str, dict[str, Any]] = {}
        self.candidates = CandidateManager(
            self.stock_to_themes,
            self.theme_members,
            max_subscriptions=max_subscriptions,
        )
        self.selected_conditions: list[dict[str, str]] = []
        self.stop_event = asyncio.Event()

    def record_status(self, status: str, **details: Any) -> None:
        payload = {"status": status, **details}
        self.store.append_event(
            run_id=self.run_id,
            event_type="source.status",
            source="collector",
            payload=payload,
        )
        LOG.info("source status=%s details=%s", status, details)

    def freeze_infostock(self) -> None:
        files = sorted(self.infostock_dir.glob("theme-*.json"))
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                theme_id = str(payload.get("themeId") or "").strip()
                members: list[str] = []
                for row in payload.get("relatedStocks") or []:
                    code = normalize_stock_code(row.get("stockCode"))
                    if code:
                        members.append(code)
                        self.stock_to_themes[code].add(theme_id)
                if theme_id:
                    self.theme_members[theme_id] = members
                self.store.append_event(
                    run_id=self.run_id,
                    event_type="reference.infostock_theme",
                    source="infostock_frozen_file",
                    payload={"file": path.name, "content": payload},
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self.store.append_event(
                    run_id=self.run_id,
                    event_type="reference.error",
                    source="infostock_frozen_file",
                    payload={"file": path.name, "error": str(exc)},
                )
        self.record_status(
            "INFOSTOCK_FROZEN",
            themeCount=len(self.theme_members),
            mappedStockCount=len(self.stock_to_themes),
        )

    async def collect_stock_master(self) -> None:
        for market_code, market_name in (("0", "KOSPI"), ("10", "KOSDAQ")):
            request = RestRequest(
                "ka10099", "/api/dostk/stkinfo", {"mrkt_tp": market_code}
            )
            payload, headers = await self.client.post(request)
            received = iso_utc()
            self.store.append_event(
                run_id=self.run_id,
                event_type="reference.stock_master",
                source="kiwoom_rest",
                payload={
                    "apiId": request.api_id,
                    "market": market_name,
                    "response": payload,
                    "responseHeaders": headers,
                },
                received_at=received,
            )
            for row in payload.get("list") or []:
                if not isinstance(row, dict):
                    continue
                code = normalize_stock_code(row.get("code"))
                if code:
                    self.stock_master[code] = {**row, "dayjaviewMarket": market_name}
        self.record_status("STOCK_MASTER_READY", stockCount=len(self.stock_master))

    async def run(self) -> None:
        self.freeze_infostock()
        await self.collect_stock_master()
        self.record_status(
            "PREOPEN_READY",
            startAt=iso_utc(self.start_at),
            endAt=iso_utc(self.end_at),
            collectorVersion=COLLECTOR_VERSION,
        )
        # This snapshot is expensive and required for replay. Persist it before
        # waiting for the opening bell so an early process restart loses none
        # of the frozen reference state.
        self.store.flush()
        websocket_task = asyncio.create_task(self.websocket_loop(), name="market-websocket")
        poll_task = asyncio.create_task(self.rest_poll_loop(), name="rest-safety-net")
        try:
            delay = max(0.0, (self.end_at - datetime.now(KST)).total_seconds())
            if delay:
                await asyncio.sleep(delay)
        finally:
            self.stop_event.set()
            for task in (websocket_task, poll_task):
                task.cancel()
            await asyncio.gather(websocket_task, poll_task, return_exceptions=True)
            self.record_status("REALTIME_CAPTURE_ENDED")

    async def websocket_loop(self) -> None:
        attempt = 0
        while not self.stop_event.is_set() and datetime.now(KST) < self.end_at:
            try:
                self.record_status("WEBSOCKET_CONNECTING", attempt=attempt)
                async with websockets.connect(
                    self.client.ws_url,
                    open_timeout=20,
                    ping_interval=None,
                    max_queue=32768,
                ) as websocket:
                    await websocket.send(
                        canonical_json(
                            {"trnm": "LOGIN", "token": await self.client.access_token()}
                        )
                    )
                    login = await asyncio.wait_for(websocket.recv(), timeout=20)
                    login_payload = json.loads(login)
                    if login_payload.get("return_code") != 0:
                        raise CaptureError(
                            f"WebSocket login failed: {login_payload.get('return_msg')}"
                        )
                    self.record_status("WEBSOCKET_CONNECTED")
                    attempt = 0
                    await websocket.send(canonical_json({"trnm": "CNSRLST"}))
                    condition_response = await self._receive_until(
                        websocket, expected_trnm="CNSRLST"
                    )
                    self.store.append_event(
                        run_id=self.run_id,
                        event_type="candidate.condition_list",
                        source="kiwoom_websocket",
                        payload=condition_response,
                    )
                    available = parse_condition_list(condition_response)
                    if self.condition_ids:
                        available = [
                            item for item in available if item["seq"] in self.condition_ids
                        ]
                    self.selected_conditions = available[: self.max_conditions]
                    self.record_status(
                        "CONDITIONS_SELECTED",
                        availableCount=len(parse_condition_list(condition_response)),
                        selected=self.selected_conditions,
                    )
                    await self._register_indices(websocket)
                    for condition in self.selected_conditions:
                        await websocket.send(
                            canonical_json(
                                {
                                    "trnm": "CNSRREQ",
                                    "seq": condition["seq"],
                                    "search_type": "1",
                                    "stex_tp": "K",
                                }
                            )
                        )
                    if self.candidates.current_targets:
                        await self._register_stocks(
                            websocket, self.candidates.current_targets
                        )
                    self.store.flush()

                    while (
                        not self.stop_event.is_set()
                        and datetime.now(KST) < self.end_at
                    ):
                        try:
                            raw = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                            payload = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
                            if str(payload.get("trnm") or "").upper() == "PING":
                                await websocket.send(canonical_json(payload))
                                continue
                            self.handle_websocket_payload(payload)
                        except TimeoutError:
                            pass
                        if self.candidates.dirty:
                            targets = self.candidates.select_targets()
                            if targets != self.candidates.current_targets:
                                await self._register_stocks(websocket, targets)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.record_status(
                    "WEBSOCKET_DISCONNECTED", error=str(exc), attempt=attempt
                )
                attempt += 1
                await asyncio.sleep(min(30.0, 1.0 * (2 ** min(attempt, 5))))

    async def _receive_until(
        self, websocket: Any, *, expected_trnm: str
    ) -> dict[str, Any]:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(websocket.recv(), timeout=20)
            payload = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
            trnm = str(payload.get("trnm") or "").upper()
            if trnm == "PING":
                await websocket.send(canonical_json(payload))
            elif trnm == expected_trnm:
                return payload
            else:
                self.handle_websocket_payload(payload)
        raise CaptureError(f"timed out waiting for {expected_trnm}")

    async def _register_indices(self, websocket: Any) -> None:
        request = {
            "trnm": "REG",
            "grp_no": "1",
            "refresh": "0",
            "data": [{"item": ["001", "101"], "type": ["0J", "0U"]}],
        }
        await websocket.send(canonical_json(request))
        self.store.append_event(
            run_id=self.run_id,
            event_type="subscription.changed",
            source="collector",
            payload={"group": "1", "kind": "market_indices", "request": request},
        )

    async def _register_stocks(
        self, websocket: Any, targets: tuple[str, ...]
    ) -> None:
        chunks = [list(targets[index : index + 100]) for index in range(0, len(targets), 100)]
        request = {
            "trnm": "REG",
            "grp_no": "1",
            "refresh": "0",
            # refresh=0 gives a deterministic replacement. Keep the market
            # registrations in the same packet so a target update cannot
            # accidentally unsubscribe the indices/breadth feeds.
            "data": [
                {"item": ["001", "101"], "type": ["0J", "0U"]},
                *({"item": chunk, "type": ["0B"]} for chunk in chunks),
            ],
        }
        old = set(self.candidates.current_targets)
        new = set(targets)
        added = sorted(new - old)
        await websocket.send(canonical_json(request))
        self.store.append_event(
            run_id=self.run_id,
            event_type="subscription.changed",
            source="collector",
            payload={
                "group": "1",
                "kind": "stock_trade",
                "targetCount": len(targets),
                "targets": list(targets),
                "added": added,
                "removed": sorted(old - new),
                "addedReasons": self.candidates.explain_targets(added),
                "maxSubscriptions": self.max_subscriptions,
                "request": request,
            },
        )
        self.candidates.applied(targets)
        LOG.info("0B targets updated count=%d", len(targets))

    def handle_websocket_payload(self, payload: dict[str, Any]) -> None:
        received = iso_utc()
        self.store.append_event(
            run_id=self.run_id,
            event_type="kiwoom.websocket.raw",
            source="kiwoom_websocket",
            payload=payload,
            received_at=received,
        )
        trnm = str(payload.get("trnm") or "").upper()
        if trnm == "CNSRREQ":
            condition_id = str(payload.get("seq") or "").strip()
            for rank, row in enumerate(payload.get("data") or [], start=1):
                if not isinstance(row, dict):
                    continue
                code = stock_code_from_row(row)
                if not code:
                    continue
                self.candidates.observe(code, f"condition:{condition_id}")
                self.store.append_event(
                    run_id=self.run_id,
                    event_type="candidate.condition",
                    source="kiwoom_websocket",
                    payload={
                        "conditionId": condition_id,
                        "action": "INITIAL",
                        "rank": rank,
                        "raw": row,
                    },
                    received_at=received,
                    stock_code=code,
                )
            return

        if trnm != "REAL":
            return
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            event_type = str(item.get("type") or "")
            values = item.get("values") if isinstance(item.get("values"), dict) else {}
            code = normalize_stock_code(item.get("item") or values.get("9001"))
            occurred = source_clock_to_utc(
                self.trade_date, values.get("20"), received
            )
            if event_type == "0B":
                canonical_type = "market.trade"
            elif event_type == "0J":
                canonical_type = "market.index"
            elif event_type == "0U":
                canonical_type = "market.breadth"
            elif event_type == "02":
                canonical_type = "candidate.condition"
                action = str(values.get("843") or "")
                if action == "I" and code:
                    self.candidates.observe(code, f"condition:{values.get('841', '')}")
            else:
                canonical_type = "market.other"
            self.store.append_event(
                run_id=self.run_id,
                event_type=canonical_type,
                source="kiwoom_websocket",
                payload=item,
                received_at=received,
                occurred_at=occurred,
                stock_code=code,
            )

    async def rest_poll_loop(self) -> None:
        delay = max(0.0, (self.start_at - datetime.now(KST)).total_seconds())
        if delay:
            self.record_status("WAITING_FOR_MARKET_OPEN", seconds=round(delay, 3))
            await asyncio.sleep(delay)
        poll_number = 0
        while not self.stop_event.is_set() and datetime.now(KST) < self.end_at:
            cycle_started = time.monotonic()
            for request in SAFETY_NET_REQUESTS:
                if self.stop_event.is_set():
                    return
                try:
                    payload, headers = await self.client.post(request)
                    received = iso_utc()
                    self.store.append_event(
                        run_id=self.run_id,
                        event_type="kiwoom.rest.raw",
                        source="kiwoom_rest",
                        payload={
                            "apiId": request.api_id,
                            "requestBody": request.body,
                            "responseHeaders": headers,
                            "response": payload,
                            "pollNumber": poll_number,
                        },
                        received_at=received,
                    )
                    seen: set[str] = set()
                    for rank, row in enumerate(iter_stock_rows(payload), start=1):
                        code = stock_code_from_row(row)
                        if not code or code in seen:
                            continue
                        seen.add(code)
                        self.candidates.observe(code, f"rest:{request.api_id}")
                        self.store.append_event(
                            run_id=self.run_id,
                            event_type="candidate.rest",
                            source="kiwoom_rest",
                            payload={
                                "apiId": request.api_id,
                                "rank": rank,
                                "pollNumber": poll_number,
                                "raw": row,
                            },
                            received_at=received,
                            stock_code=code,
                        )
                except Exception as exc:
                    self.store.append_event(
                        run_id=self.run_id,
                        event_type="source.error",
                        source="kiwoom_rest",
                        payload={"apiId": request.api_id, "error": str(exc)},
                    )
            poll_number += 1
            # Bound the loss window even on a quiet market or a hard process
            # failure that bypasses the normal finally block.
            self.store.flush()
            elapsed = time.monotonic() - cycle_started
            await asyncio.sleep(max(0.1, self.poll_seconds - elapsed))

    async def backfill_minute_bars(self) -> dict[str, int]:
        target_prefix = self.trade_date.strftime("%Y%m%d")
        completed = 0
        failed = 0
        row_count = 0
        self.record_status(
            "MINUTE_BACKFILL_STARTED", targetStockCount=len(self.stock_master)
        )
        for position, code in enumerate(sorted(self.stock_master), start=1):
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
            try:
                payload, _ = await self.client.post(request, retries=2)
                received = iso_utc()
                rows = [
                    row
                    for row in payload.get("stk_min_pole_chart_qry") or []
                    if isinstance(row, dict)
                    and str(row.get("cntr_tm") or "").startswith(target_prefix)
                ]
                inserted = self.store.append_minute_bars(
                    run_id=self.run_id,
                    stock_code=code,
                    rows=rows,
                    source_received_at=received,
                )
                row_count += inserted
                completed += 1
                self.store.append_event(
                    run_id=self.run_id,
                    event_type="backfill.minute.completed",
                    source="kiwoom_rest",
                    payload={
                        "apiId": "ka10080",
                        "position": position,
                        "targetCount": len(self.stock_master),
                        "barCount": inserted,
                    },
                    stock_code=code,
                    received_at=received,
                )
            except Exception as exc:
                failed += 1
                self.store.append_event(
                    run_id=self.run_id,
                    event_type="backfill.minute.failed",
                    source="kiwoom_rest",
                    payload={"apiId": "ka10080", "error": str(exc)},
                    stock_code=code,
                )
            if position % 100 == 0:
                LOG.info(
                    "minute backfill %d/%d completed=%d failed=%d rows=%d",
                    position,
                    len(self.stock_master),
                    completed,
                    failed,
                    row_count,
                )
        summary = {"completed": completed, "failed": failed, "rows": row_count}
        self.record_status("MINUTE_BACKFILL_FINISHED", **summary)
        self.store.flush()
        if failed:
            raise CaptureError(
                f"minute backfill incomplete: {failed} of {len(self.stock_master)} stocks failed"
            )
        return summary


async def websocket_doctor(client: KiwoomReadOnlyClient) -> dict[str, Any]:
    token = await client.access_token()
    async with websockets.connect(
        client.ws_url, open_timeout=20, ping_interval=None
    ) as websocket:
        await websocket.send(canonical_json({"trnm": "LOGIN", "token": token}))
        login = json.loads(await asyncio.wait_for(websocket.recv(), timeout=20))
        if login.get("return_code") != 0:
            raise CaptureError(f"WebSocket login failed: {login.get('return_msg')}")
        await websocket.send(canonical_json({"trnm": "CNSRLST"}))
        while True:
            payload = json.loads(await asyncio.wait_for(websocket.recv(), timeout=20))
            if str(payload.get("trnm") or "").upper() == "PING":
                await websocket.send(canonical_json(payload))
                continue
            if str(payload.get("trnm") or "").upper() == "CNSRLST":
                conditions = parse_condition_list(payload)
                break
    master, _ = await client.post(
        RestRequest("ka10099", "/api/dostk/stkinfo", {"mrkt_tp": "0"})
    )
    return {
        "mode": client.mode,
        "oauth": "ok",
        "websocketLogin": "ok",
        "conditionCount": len(conditions),
        "conditions": conditions,
        "kospiMasterCount": len(master.get("list") or []),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.local")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Read-only live connectivity check")
    doctor.add_argument("--mode", choices=("real", "demo"), default="real")

    capture = subparsers.add_parser("capture", help="Run the one-day capture")
    capture.add_argument("--mode", choices=("real", "demo"), default="real")
    capture.add_argument("--trade-date", required=True)
    capture.add_argument("--start-at", default="09:00:00")
    capture.add_argument("--end-at", default="15:40:00")
    capture.add_argument("--poll-seconds", type=float, default=30.0)
    capture.add_argument("--max-subscriptions", type=int, default=180)
    capture.add_argument("--max-conditions", type=int, default=8)
    capture.add_argument("--condition-id", action="append", default=[])
    capture.add_argument("--infostock-dir", default="data/infostock/import")
    capture.add_argument("--output-dir")
    capture.add_argument("--backfill-minute-bars", action="store_true")
    return parser


async def async_main(args: argparse.Namespace) -> int:
    load_env_file(Path(args.env_file))
    client = KiwoomReadOnlyClient(
        args.mode,
        os.getenv("KIWOOM_APP_KEY", "").strip(),
        os.getenv("KIWOOM_APP_SECRET", "").strip(),
    )
    try:
        if args.command == "doctor":
            result = await websocket_doctor(client)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        trade_date = parse_trade_date(args.trade_date)
        start_at = market_datetime(trade_date, parse_clock(args.start_at))
        end_at = market_datetime(trade_date, parse_clock(args.end_at))
        if end_at <= start_at:
            raise CaptureError("end-at must be later than start-at")
        if not 1 <= args.max_subscriptions <= 180:
            raise CaptureError("max-subscriptions must be between 1 and 180")
        if not 0 <= args.max_conditions <= 8:
            raise CaptureError("max-conditions must be between 0 and 8")
        output_dir = Path(
            args.output_dir or f"data/market-replay/{trade_date.isoformat()}"
        )
        settings = {
            "purpose": "one-time deterministic DAYJAVIEW market replay",
            "tradeDate": trade_date.isoformat(),
            "mode": args.mode,
            "startAt": iso_utc(start_at),
            "endAt": iso_utc(end_at),
            "pollSeconds": args.poll_seconds,
            "maxSubscriptions": args.max_subscriptions,
            "maxConditions": args.max_conditions,
            "requestedConditionIds": args.condition_id,
            "backfillMinuteBars": args.backfill_minute_bars,
            "orderApisEnabled": False,
        }
        with ReplayStore(output_dir) as store:
            run_id = store.start_run(
                trade_date=trade_date, mode=args.mode, settings=settings
            )
            capture = MarketCapture(
                client=client,
                store=store,
                run_id=run_id,
                trade_date=trade_date,
                start_at=start_at,
                end_at=end_at,
                poll_seconds=args.poll_seconds,
                max_subscriptions=args.max_subscriptions,
                condition_ids=set(args.condition_id),
                max_conditions=args.max_conditions,
                infostock_dir=Path(args.infostock_dir),
            )
            try:
                await capture.run()
                if args.backfill_minute_bars:
                    await capture.backfill_minute_bars()
                manifest = store.finish_run(run_id, status="COMPLETED")
            except asyncio.CancelledError:
                store.finish_run(run_id, status="INTERRUPTED", error="cancelled")
                raise
            except KeyboardInterrupt:
                store.finish_run(run_id, status="INTERRUPTED", error="keyboard interrupt")
                raise
            except Exception as exc:
                store.finish_run(run_id, status="FAILED", error=str(exc))
                raise
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    finally:
        await client.close()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CaptureError, httpx.HTTPError, OSError, ValueError) as exc:
        print(f"market capture failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
