from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from jsonschema import Draft202012Validator, FormatChecker

from apps.api import ApiSettings, RealtimeSnapshotHub, create_fixture_app
from apps.api.app_types import JsonObject
from apps.api.cookies import SESSION_COOKIE
from packages.domain import DataStatus
from packages.identity import GoogleIdentity
from packages.realtime import (
    InMemorySnapshotRepository,
    SnapshotPublication,
    SnapshotTopic,
    SnapshotVersions,
)

_ROOT = Path(__file__).resolve().parents[2]
_REALTIME_FIXTURES = _ROOT / "contracts" / "fixtures" / "realtime"
_SCHEMA = json.loads(
    (_ROOT / "contracts" / "schemas" / "stage0.schema.json").read_text(
        encoding="utf-8"
    )
)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())


@dataclass(slots=True)
class MutableClock:
    current: datetime = datetime(2026, 8, 14, 3, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, duration: timedelta) -> None:
        self.current += duration


class WebSocketHarness:
    def __init__(
        self,
        app: Any,
        *,
        session_token: str | None,
        origin: str = "https://dayjaview.vercel.app",
        query_string: bytes = b"",
    ) -> None:
        headers = [(b"origin", origin.encode("latin-1"))]
        if session_token is not None:
            headers.append(
                (
                    b"cookie",
                    f"{SESSION_COOKIE}={session_token}".encode("latin-1"),
                )
            )
        self._scope = {
            "type": "websocket",
            "path": "/v1/realtime",
            "query_string": query_string,
            "headers": headers,
        }
        self._app = app
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> dict[str, Any]:
        self._task = asyncio.create_task(
            self._app(self._scope, self._incoming.get, self._outgoing.put)
        )
        await self._incoming.put({"type": "websocket.connect"})
        return await self.next_event()

    async def send_json(self, payload: JsonObject) -> None:
        await self._incoming.put(
            {
                "type": "websocket.receive",
                "text": json.dumps(payload, ensure_ascii=False),
            }
        )

    async def next_event(self, *, timeout: float = 0.5) -> dict[str, Any]:
        return await asyncio.wait_for(self._outgoing.get(), timeout=timeout)

    async def next_json(self) -> JsonObject:
        event = await self.next_event()
        assert event["type"] == "websocket.send"
        return cast(JsonObject, json.loads(event["text"]))

    async def assert_silent(self, *, timeout: float = 0.04) -> None:
        try:
            event = await self.next_event(timeout=timeout)
        except TimeoutError:
            return
        raise AssertionError(f"unexpected WebSocket output: {event}")

    async def disconnect(self) -> None:
        if self._task is None or self._task.done():
            if self._task is not None:
                await self._task
            return
        await self._incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(self._task, timeout=0.5)


def _fixture(name: str) -> JsonObject:
    return cast(
        JsonObject,
        json.loads((_REALTIME_FIXTURES / name).read_text(encoding="utf-8")),
    )


def _assert_contract(definition: str, payload: object) -> None:
    validator = _VALIDATOR.evolve(schema={"$ref": f"#/$defs/{definition}"})
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    assert not errors, "\n".join(error.message for error in errors)


def _service_login(environment, *, subject: str):
    started = environment.service.begin_google_login("/today")
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    code = f"code-{subject}"
    environment.oauth_provider.register_code(
        code,
        GoogleIdentity(subject, "실시간 사용자"),
    )
    return environment.service.complete_google_login(
        code=code,
        state=state,
        browser_nonce=started.browser_nonce,
    )


def _ticket(environment, completion):
    return environment.service.issue_realtime_ticket(
        session_token=completion.session_token,
        origin="https://dayjaview.vercel.app",
        csrf_token=completion.csrf_token,
        csrf_cookie=completion.csrf_token,
    )


def _snapshot(
    fixture_name: str,
    *,
    params: JsonObject,
    publication_id: str,
):
    fixture = _fixture(fixture_name)
    topic = SnapshotTopic(cast(str, fixture["topic"]))
    payload = cast(JsonObject, fixture["payload"]).copy()
    public_snapshot_id = payload.pop("snapshotId", None)
    generated_at = datetime.fromisoformat(cast(str, fixture["generatedAt"]))
    as_of = datetime.fromisoformat(cast(str, fixture["asOf"]))
    publication = SnapshotPublication(
        publication_id=publication_id,
        stream_id=cast(str, fixture["streamId"]),
        topic=topic,
        params=params,
        market_date=date.fromisoformat(cast(str, fixture["marketDate"])),
        generated_at=generated_at,
        as_of=as_of,
        data_status=DataStatus(cast(str, fixture["dataStatus"])),
        quality_flags=tuple(cast(list[str], fixture["qualityFlags"])),
        payload=payload,
        versions=SnapshotVersions(
            schema_version=cast(str, fixture["schemaVersion"]),
            calculation_version="theme-metrics-2026.08.1",
            ranking_model_version="theme-rank-2026.08.1",
            membership_version="membership-2026-08-14T00:10:00Z",
        ),
    )
    snapshot = InMemorySnapshotRepository().publish(publication)
    changes: dict[str, object] = {"sequence": fixture["sequence"]}
    if isinstance(public_snapshot_id, str):
        changes["snapshot_id"] = public_snapshot_id
    return replace(snapshot, **changes)


def _populated_hub() -> RealtimeSnapshotHub:
    hub = RealtimeSnapshotHub()
    hub.publish(
        _snapshot(
            "ranking-snapshot.json",
            params={"limit": 10},
            publication_id="fixture-ranking",
        ),
        params={"limit": 10},
    )
    hub.publish(
        _snapshot(
            "treemap-snapshot.json",
            params={"limit": 12},
            publication_id="fixture-treemap",
        ),
        params={"limit": 12},
    )
    hub.publish(
        _snapshot(
            "event-state-changed.json",
            params={"eventIds": ["evt_current"]},
            publication_id="fixture-event",
        ),
        params={"eventIds": ["evt_current"]},
    )
    return hub


def test_authenticated_websocket_subscribe_sends_contract_full_snapshots_only() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        hub = _populated_hub()
        environment = create_fixture_app(clock=clock, realtime_hub=hub)
        completion = _service_login(environment, subject="google-ws-smoke")
        ticket = _ticket(environment, completion)
        websocket = WebSocketHarness(
            environment.app,
            session_token=completion.session_token,
        )
        assert (await websocket.start())["type"] == "websocket.accept"
        await websocket.assert_silent()
        await websocket.send_json({"type": "auth", "ticket": ticket.ticket})
        await websocket.send_json(_fixture("subscribe.json"))

        subscribed = await websocket.next_json()
        _assert_contract("WsSubscribed", subscribed)
        snapshots = [await websocket.next_json() for _ in range(3)]
        expected = {
            "theme_rank_snapshot": "WsRankingSnapshot",
            "theme_treemap_snapshot": "WsTreemapSnapshot",
            "event_state_changed": "WsEventStateChanged",
        }
        for snapshot in snapshots:
            _assert_contract(expected[cast(str, snapshot["type"])], snapshot)
            assert snapshot["subscriptionId"] == subscribed["subscriptionId"]
        ranking = next(
            item for item in snapshots if item["type"] == "theme_rank_snapshot"
        )
        assert ranking["streamId"] == "stream_market_20260814"
        assert ranking["sequence"] == 1842
        assert ranking["payload"]["items"] == []  # type: ignore[index]
        serialized = json.dumps([subscribed, snapshots])
        assert completion.session_token not in serialized
        assert completion.csrf_token not in serialized
        assert ticket.ticket not in serialized
        await websocket.disconnect()

    asyncio.run(scenario())


def test_ticket_is_single_use_expiring_and_bound_to_issuing_user() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        environment = create_fixture_app(clock=clock, realtime_hub=_populated_hub())
        first = _service_login(environment, subject="google-ws-first")
        second = _service_login(environment, subject="google-ws-second")
        issued = _ticket(environment, first)

        wrong_user = WebSocketHarness(
            environment.app,
            session_token=second.session_token,
        )
        assert (await wrong_user.start())["type"] == "websocket.accept"
        await wrong_user.send_json({"type": "auth", "ticket": issued.ticket})
        wrong_error = await wrong_user.next_json()
        _assert_contract("WsError", wrong_error)
        assert wrong_error["code"] == "AUTHENTICATION_REQUIRED"
        assert (await wrong_user.next_event())["type"] == "websocket.close"

        first_use = WebSocketHarness(
            environment.app,
            session_token=first.session_token,
        )
        assert (await first_use.start())["type"] == "websocket.accept"
        await first_use.send_json({"type": "auth", "ticket": issued.ticket})
        await first_use.disconnect()

        replay = WebSocketHarness(environment.app, session_token=first.session_token)
        assert (await replay.start())["type"] == "websocket.accept"
        await replay.send_json({"type": "auth", "ticket": issued.ticket})
        replay_error = await replay.next_json()
        assert replay_error["code"] == "AUTHENTICATION_REQUIRED"
        assert (await replay.next_event())["type"] == "websocket.close"

        expired_ticket = _ticket(environment, first)
        clock.advance(timedelta(seconds=31))
        expired = WebSocketHarness(environment.app, session_token=first.session_token)
        assert (await expired.start())["type"] == "websocket.accept"
        await expired.send_json({"type": "auth", "ticket": expired_ticket.ticket})
        expired_error = await expired.next_json()
        assert expired_error["code"] == "AUTHENTICATION_REQUIRED"
        assert (await expired.next_event())["type"] == "websocket.close"

    asyncio.run(scenario())


def test_unauthenticated_origin_query_and_auth_deadline_send_zero_snapshots() -> None:
    async def scenario() -> None:
        settings = ApiSettings(realtime_auth_deadline=timedelta(milliseconds=20))
        environment = create_fixture_app(
            settings=settings,
            clock=MutableClock(),
            realtime_hub=_populated_hub(),
        )
        completion = _service_login(environment, subject="google-ws-handshake")

        anonymous = WebSocketHarness(environment.app, session_token=None)
        anonymous_close = await anonymous.start()
        assert anonymous_close["type"] == "websocket.close"

        wrong_origin = WebSocketHarness(
            environment.app,
            session_token=completion.session_token,
            origin="https://evil.example",
        )
        assert (await wrong_origin.start())["type"] == "websocket.close"

        ticket_in_url = WebSocketHarness(
            environment.app,
            session_token=completion.session_token,
            query_string=b"ticket=must-not-be-in-url",
        )
        assert (await ticket_in_url.start())["type"] == "websocket.close"

        deadline = WebSocketHarness(
            environment.app,
            session_token=completion.session_token,
        )
        assert (await deadline.start())["type"] == "websocket.accept"
        error = await deadline.next_json()
        assert error["code"] == "AUTHENTICATION_REQUIRED"
        close = await deadline.next_event()
        assert close["type"] == "websocket.close"
        for value in (anonymous_close, error, close):
            serialized = json.dumps(value)
            assert "snapshotId" not in serialized
            assert "evt_current" not in serialized

    asyncio.run(scenario())


def test_unsubscribe_stops_updates_and_resubscribe_gets_latest_gap_snapshot() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        hub = _populated_hub()
        environment = create_fixture_app(clock=clock, realtime_hub=hub)
        completion = _service_login(environment, subject="google-ws-unsubscribe")
        issued = _ticket(environment, completion)
        websocket = WebSocketHarness(
            environment.app,
            session_token=completion.session_token,
        )
        assert (await websocket.start())["type"] == "websocket.accept"
        await websocket.send_json({"type": "auth", "ticket": issued.ticket})
        await websocket.send_json(
            {
                "type": "subscribe",
                "requestId": "client_rank_1",
                "topics": [
                    {"name": "theme_rank_snapshot", "params": {"limit": 10}}
                ],
            }
        )
        subscribed = await websocket.next_json()
        initial = await websocket.next_json()
        subscription_id = cast(str, subscribed["subscriptionId"])
        assert initial["sequence"] == 1842

        await websocket.send_json(
            {
                "type": "unsubscribe",
                "requestId": "client_unsubscribe_1",
                "subscriptionId": subscription_id,
            }
        )
        current = _snapshot(
            "ranking-snapshot.json",
            params={"limit": 10},
            publication_id="fixture-ranking-gap",
        )
        gap = replace(
            current,
            sequence=1845,
            snapshot_id="snap_rank_gap",
            content_hash="gap-content-hash",
        )
        hub.publish(gap, params={"limit": 10})
        await websocket.assert_silent()

        await websocket.send_json(
            {
                "type": "subscribe",
                "requestId": "client_rank_2",
                "topics": [
                    {"name": "theme_rank_snapshot", "params": {"limit": 10}}
                ],
            }
        )
        resubscribed = await websocket.next_json()
        latest = await websocket.next_json()
        assert resubscribed["subscriptionId"] != subscription_id
        assert latest["sequence"] == 1845
        assert latest["payload"]["snapshotId"] == "snap_rank_gap"  # type: ignore[index]
        _assert_contract("WsRankingSnapshot", latest)
        await websocket.disconnect()

    asyncio.run(scenario())


def test_reconnect_new_stream_and_session_expiry_use_full_snapshot_boundary() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        hub = _populated_hub()
        environment = create_fixture_app(clock=clock, realtime_hub=hub)
        completion = _service_login(environment, subject="google-ws-reconnect")

        first_ticket = _ticket(environment, completion)
        first = WebSocketHarness(environment.app, session_token=completion.session_token)
        assert (await first.start())["type"] == "websocket.accept"
        await first.send_json({"type": "auth", "ticket": first_ticket.ticket})
        await first.send_json(
            {
                "type": "subscribe",
                "requestId": "client_before_restart",
                "topics": [
                    {"name": "theme_rank_snapshot", "params": {"limit": 10}}
                ],
            }
        )
        await first.next_json()
        old_snapshot = await first.next_json()
        assert old_snapshot["streamId"] == "stream_market_20260814"
        await first.disconnect()

        restarted = _snapshot(
            "reconnect-full-snapshot.json",
            params={"limit": 10},
            publication_id="fixture-reconnect",
        )
        hub.publish(restarted, params={"limit": 10})
        reconnect_ticket = _ticket(environment, completion)
        reconnect = WebSocketHarness(
            environment.app,
            session_token=completion.session_token,
        )
        assert (await reconnect.start())["type"] == "websocket.accept"
        await reconnect.send_json({"type": "auth", "ticket": reconnect_ticket.ticket})
        await reconnect.send_json(
            {
                "type": "subscribe",
                "requestId": "client_after_restart",
                "topics": [
                    {"name": "theme_rank_snapshot", "params": {"limit": 10}}
                ],
            }
        )
        await reconnect.next_json()
        full_snapshot = await reconnect.next_json()
        assert full_snapshot["streamId"] == "stream_restarted_20260814"
        assert full_snapshot["sequence"] == 1
        assert full_snapshot["payload"] == {
            "snapshotId": "snap_rank_reconnect",
            "items": [],
        }

        clock.advance(timedelta(hours=8, seconds=1))
        await reconnect.send_json(
            {
                "type": "subscribe",
                "requestId": "client_after_expiry",
                "topics": [
                    {"name": "theme_rank_snapshot", "params": {"limit": 10}}
                ],
            }
        )
        expiry_error = await reconnect.next_json()
        _assert_contract("WsError", expiry_error)
        assert expiry_error["code"] == "AUTHENTICATION_REQUIRED"
        assert (await reconnect.next_event())["type"] == "websocket.close"

    asyncio.run(scenario())
