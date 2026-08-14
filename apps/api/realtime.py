from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol, cast

from packages.identity import IdentityError, IdentityService
from packages.identity.security import Clock

from .app_types import JsonObject, JsonValue
from .config import ApiSettings
from .cookies import SESSION_COOKIE
from .http import Receive, Send
from .product import ensure_public_projection

_AUTHENTICATION_CLOSE = 4401
_AUTHENTICATION_TIMEOUT_CLOSE = 4408
_INVALID_MESSAGE_CLOSE = 4400
_MAX_REQUEST_ID_LENGTH = 128


class SnapshotTopic(StrEnum):
    THEME_RANK = "theme_rank_snapshot"
    THEME_TREEMAP = "theme_treemap_snapshot"
    EVENT_STATE_CHANGED = "event_state_changed"


class SnapshotVersionsView(Protocol):
    @property
    def schema_version(self) -> str: ...


class ReadSnapshot(Protocol):
    @property
    def snapshot_id(self) -> str: ...

    @property
    def stream_id(self) -> str: ...

    @property
    def topic(self) -> object: ...

    @property
    def params_key(self) -> str: ...

    @property
    def sequence(self) -> int: ...

    @property
    def versions(self) -> SnapshotVersionsView: ...

    @property
    def content_hash(self) -> str: ...

    def to_ws_message(self, *, subscription_id: str) -> dict[str, object]: ...


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _params_key(params: JsonObject) -> str:
    digest = hashlib.sha256(_canonical_json(params).encode("utf-8")).hexdigest()[:32]
    return f"params_{digest}"


def _copy_object(value: JsonObject) -> JsonObject:
    return cast(JsonObject, deepcopy(value))


class InvalidSubscription(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NormalizedTopic:
    topic: SnapshotTopic
    params: JsonObject
    canonical_params: str

    @property
    def scope_key(self) -> tuple[SnapshotTopic, str]:
        return self.topic, self.canonical_params


def normalize_topic_request(value: object) -> NormalizedTopic:
    if not isinstance(value, dict) or set(value) != {"name", "params"}:
        raise InvalidSubscription("topic 형식을 확인해 주세요.")
    name = value.get("name")
    params = value.get("params")
    if not isinstance(name, str) or not isinstance(params, dict):
        raise InvalidSubscription("topic 형식을 확인해 주세요.")

    if name == SnapshotTopic.THEME_RANK.value:
        normalized = _normalize_limit(params, maximum=50)
        topic = SnapshotTopic.THEME_RANK
    elif name == SnapshotTopic.THEME_TREEMAP.value:
        normalized = _normalize_limit(params, maximum=12)
        topic = SnapshotTopic.THEME_TREEMAP
    elif name == SnapshotTopic.EVENT_STATE_CHANGED.value:
        normalized = _normalize_event_ids(params)
        topic = SnapshotTopic.EVENT_STATE_CHANGED
    else:
        raise InvalidSubscription("지원하지 않는 topic입니다.")
    return NormalizedTopic(topic, normalized, _canonical_json(normalized))


def _normalize_limit(params: Mapping[object, object], *, maximum: int) -> JsonObject:
    if set(params) != {"limit"}:
        raise InvalidSubscription("topic parameter를 확인해 주세요.")
    limit = params.get("limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
        raise InvalidSubscription("topic limit 범위를 확인해 주세요.")
    return {"limit": limit}


def _normalize_event_ids(params: Mapping[object, object]) -> JsonObject:
    if set(params) != {"eventIds"}:
        raise InvalidSubscription("topic parameter를 확인해 주세요.")
    raw_ids = params.get("eventIds")
    if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= 50:
        raise InvalidSubscription("eventIds 범위를 확인해 주세요.")
    event_ids: list[str] = []
    for event_id in raw_ids:
        if not isinstance(event_id, str) or not _valid_opaque_id(event_id):
            raise InvalidSubscription("eventId 형식을 확인해 주세요.")
        event_ids.append(event_id)
    if len(event_ids) != len(set(event_ids)):
        raise InvalidSubscription("eventIds에는 중복 값을 사용할 수 없습니다.")
    normalized_ids = cast(list[JsonValue], sorted(event_ids))
    return {"eventIds": normalized_ids}


def _valid_opaque_id(value: str) -> bool:
    return (
        1 <= len(value) <= 128
        and value == value.strip()
        and not any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    )


class SnapshotIngressDisposition(StrEnum):
    APPLIED = "APPLIED"
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    RETIRED_STREAM = "RETIRED_STREAM"


class RealtimeSnapshotHub:
    """Latest-only fan-out with full-snapshot ordering fences.

    Each listener has one event bit, not an unbounded per-snapshot queue. A slow
    listener therefore wakes once and reads the latest accepted full snapshot.
    """

    def __init__(self) -> None:
        self._latest: dict[tuple[SnapshotTopic, str], ReadSnapshot] = {}
        self._retired_streams: dict[tuple[SnapshotTopic, str], set[str]] = {}
        self._subscriptions: dict[
            RealtimeListener,
            dict[str, tuple[NormalizedTopic, ...]],
        ] = {}
        self._lock = RLock()

    def publish(
        self,
        snapshot: ReadSnapshot,
        *,
        params: JsonObject,
    ) -> SnapshotIngressDisposition:
        if (
            not _valid_opaque_id(snapshot.snapshot_id)
            or not _valid_opaque_id(snapshot.stream_id)
            or isinstance(snapshot.sequence, bool)
            or not isinstance(snapshot.sequence, int)
            or snapshot.sequence < 1
            or not snapshot.content_hash
        ):
            raise ValueError("snapshot ordering identity is invalid")
        try:
            snapshot_topic = SnapshotTopic(str(snapshot.topic))
        except ValueError as error:
            raise ValueError("snapshot topic is unsupported") from error
        normalized = normalize_topic_request(
            {"name": snapshot_topic.value, "params": params}
        )
        if snapshot.params_key != _params_key(normalized.params):
            raise ValueError("snapshot params_key does not match normalized params")
        scope_key = normalized.scope_key
        listeners: tuple[RealtimeListener, ...] = ()
        with self._lock:
            current = self._latest.get(scope_key)
            if current is not None and snapshot.stream_id == current.stream_id:
                if snapshot.sequence < current.sequence:
                    return SnapshotIngressDisposition.OUT_OF_ORDER
                if snapshot.sequence == current.sequence:
                    if (
                        snapshot.snapshot_id == current.snapshot_id
                        and snapshot.content_hash == current.content_hash
                    ):
                        return SnapshotIngressDisposition.DUPLICATE
                    return SnapshotIngressDisposition.OUT_OF_ORDER
            elif current is not None:
                retired = self._retired_streams.setdefault(scope_key, set())
                if snapshot.stream_id in retired:
                    return SnapshotIngressDisposition.RETIRED_STREAM
                retired.add(current.stream_id)

            self._latest[scope_key] = snapshot
            listeners = tuple(
                listener
                for listener, subscriptions in self._subscriptions.items()
                if any(
                    topic.scope_key == scope_key
                    for topics in subscriptions.values()
                    for topic in topics
                )
            )
        for listener in listeners:
            listener._notify()
        return SnapshotIngressDisposition.APPLIED

    def latest(self, topic: NormalizedTopic) -> ReadSnapshot | None:
        with self._lock:
            return self._latest.get(topic.scope_key)

    def open_listener(self) -> RealtimeListener:
        listener = RealtimeListener(self)
        with self._lock:
            self._subscriptions[listener] = {}
        return listener

    def _add_subscription(
        self,
        listener: RealtimeListener,
        subscription_id: str,
        topics: tuple[NormalizedTopic, ...],
    ) -> None:
        with self._lock:
            subscriptions = self._subscriptions.get(listener)
            if subscriptions is None:
                raise RuntimeError("realtime listener is closed")
            subscriptions[subscription_id] = topics

    def _remove_subscription(
        self,
        listener: RealtimeListener,
        subscription_id: str,
    ) -> bool:
        with self._lock:
            subscriptions = self._subscriptions.get(listener)
            if subscriptions is None:
                return False
            return subscriptions.pop(subscription_id, None) is not None

    def _listener_subscriptions(
        self,
        listener: RealtimeListener,
    ) -> dict[str, tuple[NormalizedTopic, ...]]:
        with self._lock:
            subscriptions = self._subscriptions.get(listener, {})
            return dict(subscriptions)

    def _close_listener(self, listener: RealtimeListener) -> None:
        with self._lock:
            self._subscriptions.pop(listener, None)


class RealtimeListener:
    def __init__(self, hub: RealtimeSnapshotHub) -> None:
        self._hub = hub
        self._loop = asyncio.get_running_loop()
        self._changed = asyncio.Event()
        self._closed = False

    def add_subscription(
        self,
        subscription_id: str,
        topics: tuple[NormalizedTopic, ...],
    ) -> None:
        self._hub._add_subscription(self, subscription_id, topics)

    def remove_subscription(self, subscription_id: str) -> bool:
        return self._hub._remove_subscription(self, subscription_id)

    def subscriptions(self) -> dict[str, tuple[NormalizedTopic, ...]]:
        return self._hub._listener_subscriptions(self)

    async def wait(self) -> None:
        await self._changed.wait()
        self._changed.clear()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._hub._close_listener(self)

    def _notify(self) -> None:
        if self._closed:
            return
        try:
            self._loop.call_soon_threadsafe(self._changed.set)
        except RuntimeError:
            self.close()


class RealtimeWebSocketServer:
    def __init__(
        self,
        *,
        identity_service: IdentityService,
        hub: RealtimeSnapshotHub,
        settings: ApiSettings,
        clock: Clock,
    ) -> None:
        self._identity_service = identity_service
        self._hub = hub
        self._settings = settings
        self._clock = clock

    async def __call__(
        self,
        scope: Mapping[str, Any],
        receive: Receive,
        send: Send,
    ) -> None:
        connection = await receive()
        if connection.get("type") != "websocket.connect":
            await self._close(send, _INVALID_MESSAGE_CLOSE, "연결 요청 형식 오류")
            return
        origin, session_token = _connection_credentials(scope)
        if (
            scope.get("path") != "/v1/realtime"
            or scope.get("query_string", b"")
            or origin != self._settings.app_base_url
            or self._identity_service.authenticate(session_token) is None
        ):
            await self._close(send, _AUTHENTICATION_CLOSE, "인증 필요")
            return

        await send({"type": "websocket.accept"})
        try:
            await self._serve_authenticated_connection(
                receive=receive,
                send=send,
                origin=origin,
                session_token=cast(str, session_token),
            )
        except Exception:  # noqa: BLE001 - never expose internal failure text
            try:
                await self._close(send, 1011, "연결 처리 오류")
            except Exception:  # noqa: BLE001 - transport is already gone
                return

    async def _serve_authenticated_connection(
        self,
        *,
        receive: Receive,
        send: Send,
        origin: str,
        session_token: str,
    ) -> None:
        try:
            auth_event = await asyncio.wait_for(
                receive(),
                timeout=self._settings.realtime_auth_deadline.total_seconds(),
            )
        except TimeoutError:
            await self._send_error(
                send,
                request_id=None,
                code="AUTHENTICATION_REQUIRED",
                message="연결 후 제한 시간 안에 인증해야 합니다.",
            )
            await self._close(send, _AUTHENTICATION_TIMEOUT_CLOSE, "인증 시간 만료")
            return
        if auth_event.get("type") == "websocket.disconnect":
            return
        try:
            auth = self._decode_message(auth_event)
            ticket = _auth_ticket(auth)
            principal = self._identity_service.consume_realtime_ticket(
                ticket=ticket,
                session_token=session_token,
                origin=origin,
            )
        except (IdentityError, InvalidSubscription, ValueError, json.JSONDecodeError):
            await self._send_error(
                send,
                request_id=None,
                code="AUTHENTICATION_REQUIRED",
                message="실시간 연결 인증을 확인할 수 없습니다.",
            )
            await self._close(send, _AUTHENTICATION_CLOSE, "인증 실패")
            return

        listener = self._hub.open_listener()
        last_sent: dict[tuple[str, SnapshotTopic, str], tuple[str, int, str]] = {}
        try:
            while True:
                current = self._identity_service.authenticate(session_token)
                if (
                    current is None
                    or current.session_token_hash != principal.session_token_hash
                    or current.user.user_id != principal.user.user_id
                ):
                    await self._send_error(
                        send,
                        request_id=None,
                        code="AUTHENTICATION_REQUIRED",
                        message="로그인 세션이 만료되었습니다.",
                    )
                    await self._close(send, _AUTHENTICATION_CLOSE, "세션 만료")
                    return

                receive_task: asyncio.Future[Any] = asyncio.ensure_future(receive())
                update_task: asyncio.Future[Any] = asyncio.ensure_future(
                    listener.wait()
                )
                wait_tasks = {receive_task, update_task}
                remaining = max(
                    0.0,
                    (current.expires_at - self._clock.now()).total_seconds(),
                )
                done, pending = await asyncio.wait(
                    wait_tasks,
                    timeout=min(remaining, 30.0),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                if not done:
                    continue

                refreshed = self._identity_service.authenticate(session_token)
                if (
                    refreshed is None
                    or refreshed.session_token_hash != principal.session_token_hash
                    or refreshed.user.user_id != principal.user.user_id
                ):
                    await self._send_error(
                        send,
                        request_id=None,
                        code="AUTHENTICATION_REQUIRED",
                        message="로그인 세션이 만료되었습니다.",
                    )
                    await self._close(send, _AUTHENTICATION_CLOSE, "세션 만료")
                    return

                if receive_task in done:
                    event = receive_task.result()
                    if event.get("type") == "websocket.disconnect":
                        return
                    await self._handle_client_message(
                        event=event,
                        send=send,
                        listener=listener,
                        last_sent=last_sent,
                    )
                if update_task in done:
                    await self._send_pending(send, listener, last_sent)
        finally:
            listener.close()

    async def _handle_client_message(
        self,
        *,
        event: Mapping[str, Any],
        send: Send,
        listener: RealtimeListener,
        last_sent: dict[tuple[str, SnapshotTopic, str], tuple[str, int, str]],
    ) -> None:
        request_id: str | None = None
        try:
            message = self._decode_message(event)
            if "requestId" in message:
                request_id = _request_id(message["requestId"])
            message_type = message.get("type")
            if message_type == "subscribe":
                if set(message) != {"type", "requestId", "topics"}:
                    raise InvalidSubscription("subscribe 형식을 확인해 주세요.")
                topics_value = message.get("topics")
                if not isinstance(topics_value, list) or not 1 <= len(topics_value) <= 3:
                    raise InvalidSubscription("구독 topic 수를 확인해 주세요.")
                topics = tuple(normalize_topic_request(item) for item in topics_value)
                scope_keys = [topic.scope_key for topic in topics]
                if len(scope_keys) != len(set(scope_keys)):
                    raise InvalidSubscription("같은 topic을 중복 구독할 수 없습니다.")
                subscription_id = f"sub_{secrets.token_urlsafe(18)}"
                listener.add_subscription(subscription_id, topics)
                await self._send_json(
                    send,
                    {
                        "type": "subscribed",
                        "requestId": cast(str, request_id),
                        "subscriptionId": subscription_id,
                        "topics": [topic.topic.value for topic in topics],
                    },
                )
                await self._send_pending(
                    send,
                    listener,
                    last_sent,
                    only_subscription=subscription_id,
                )
                return
            if message_type == "unsubscribe":
                if set(message) != {"type", "requestId", "subscriptionId"}:
                    raise InvalidSubscription("unsubscribe 형식을 확인해 주세요.")
                raw_subscription_id = message.get("subscriptionId")
                if not isinstance(raw_subscription_id, str) or not _valid_opaque_id(
                    raw_subscription_id
                ):
                    raise InvalidSubscription("subscriptionId를 확인해 주세요.")
                if not listener.remove_subscription(raw_subscription_id):
                    raise InvalidSubscription("구독을 찾을 수 없습니다.")
                for key in tuple(last_sent):
                    if key[0] == raw_subscription_id:
                        last_sent.pop(key, None)
                # AsyncAPI does not define an unsubscribe acknowledgement. The
                # control is therefore intentionally silent on success.
                return
            if message_type == "pong":
                if set(message) != {"type", "sentAt"}:
                    raise InvalidSubscription("pong 형식을 확인해 주세요.")
                sent_at = message.get("sentAt")
                if not isinstance(sent_at, str):
                    raise InvalidSubscription("pong 시각을 확인해 주세요.")
                timestamp = datetime.fromisoformat(sent_at)
                if timestamp.tzinfo is None:
                    raise InvalidSubscription("pong 시각을 확인해 주세요.")
                return
            raise InvalidSubscription("지원하지 않는 실시간 message입니다.")
        except (InvalidSubscription, ValueError, json.JSONDecodeError):
            await self._send_error(
                send,
                request_id=request_id,
                code="INVALID_SUBSCRIPTION",
                message="지원하지 않는 topic 또는 parameter입니다.",
            )

    async def _send_pending(
        self,
        send: Send,
        listener: RealtimeListener,
        last_sent: dict[tuple[str, SnapshotTopic, str], tuple[str, int, str]],
        *,
        only_subscription: str | None = None,
    ) -> None:
        for subscription_id, topics in listener.subscriptions().items():
            if only_subscription is not None and subscription_id != only_subscription:
                continue
            for topic in topics:
                snapshot = self._hub.latest(topic)
                if snapshot is None:
                    continue
                key = (subscription_id, topic.topic, topic.canonical_params)
                version = (
                    snapshot.stream_id,
                    snapshot.sequence,
                    snapshot.snapshot_id,
                )
                if last_sent.get(key) == version:
                    continue
                if snapshot.versions.schema_version != self._settings.schema_version:
                    await self._send_error(
                        send,
                        request_id=None,
                        code="SCHEMA_VERSION_UNSUPPORTED",
                        message="지원하지 않는 실시간 schema version입니다.",
                    )
                    continue
                message = cast(
                    JsonObject,
                    snapshot.to_ws_message(subscription_id=subscription_id),
                )
                ensure_public_projection(message)
                await self._send_json(send, message)
                last_sent[key] = version

    def _decode_message(self, event: Mapping[str, Any]) -> JsonObject:
        if event.get("type") != "websocket.receive" or event.get("bytes") is not None:
            raise InvalidSubscription("UTF-8 JSON text message가 필요합니다.")
        text = event.get("text")
        if not isinstance(text, str):
            raise InvalidSubscription("UTF-8 JSON text message가 필요합니다.")
        if len(text.encode("utf-8")) > self._settings.realtime_maximum_message_bytes:
            raise InvalidSubscription("실시간 message가 너무 큽니다.")
        value = json.loads(text)
        if not isinstance(value, dict):
            raise InvalidSubscription("실시간 message는 JSON object여야 합니다.")
        return cast(JsonObject, value)

    async def _send_error(
        self,
        send: Send,
        *,
        request_id: str | None,
        code: str,
        message: str,
    ) -> None:
        await self._send_json(
            send,
            {
                "type": "error",
                "requestId": request_id,
                "code": code,
                "message": message,
                "retryable": False,
            },
        )

    @staticmethod
    async def _send_json(send: Send, payload: JsonObject) -> None:
        await send(
            {
                "type": "websocket.send",
                "text": json.dumps(
                    cast(JsonValue, payload),
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ),
            }
        )

    @staticmethod
    async def _close(send: Send, code: int, reason: str) -> None:
        await send({"type": "websocket.close", "code": code, "reason": reason})


def _auth_ticket(message: JsonObject) -> str:
    if set(message) != {"type", "ticket"} or message.get("type") != "auth":
        raise InvalidSubscription("첫 message는 auth여야 합니다.")
    ticket = message.get("ticket")
    if not isinstance(ticket, str) or not _valid_opaque_id(ticket):
        raise InvalidSubscription("realtime ticket 형식을 확인해 주세요.")
    return ticket


def _request_id(value: JsonValue) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= _MAX_REQUEST_ID_LENGTH
        or value != value.strip()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise InvalidSubscription("requestId 형식을 확인해 주세요.")
    return value


def _connection_credentials(scope: Mapping[str, Any]) -> tuple[str | None, str | None]:
    headers: dict[str, list[str]] = {}
    try:
        for raw_name, raw_value in scope.get("headers", []):
            name = raw_name.decode("latin-1").casefold()
            headers.setdefault(name, []).append(raw_value.decode("latin-1"))
    except (AttributeError, UnicodeDecodeError):
        return None, None
    origins = headers.get("origin", [])
    if len(origins) != 1:
        return None, None
    cookies: dict[str, str] = {}
    for header in headers.get("cookie", []):
        for part in header.split(";"):
            name, separator, value = part.strip().partition("=")
            if not separator or not name or name in cookies:
                return None, None
            cookies[name] = value
    return origins[0], cookies.get(SESSION_COOKIE)
