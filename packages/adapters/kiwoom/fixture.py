"""Strict offline Kiwoom adapter used by contract and recovery fixtures only."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from .contract import (
    FIXTURE_SCHEMA_VERSION,
    AdapterCapabilities,
    KiwoomConnection,
    KiwoomSourceEnvelope,
    LiveValidationStatus,
    SourceChannel,
    require_aware,
    require_stock_id,
)


class FixtureContractError(ValueError):
    pass


class KiwoomConnectionError(RuntimeError):
    pass


class KiwoomConnectionLost(KiwoomConnectionError):
    pass


class FixtureCallKind(StrEnum):
    CONNECT = "CONNECT"
    REPLACE_SUBSCRIPTIONS = "REPLACE_SUBSCRIPTIONS"
    FETCH_SNAPSHOTS = "FETCH_SNAPSHOTS"
    CLOSE_SESSION = "CLOSE_SESSION"


@dataclass(frozen=True, slots=True)
class FixtureSession:
    connection: KiwoomConnection
    messages: tuple[KiwoomSourceEnvelope, ...]
    disconnect_after_messages: bool = False

    def __post_init__(self) -> None:
        if any(
            message.session_id != self.connection.session_id for message in self.messages
        ):
            raise FixtureContractError("session message의 session_id가 연결과 다릅니다")


@dataclass(frozen=True, slots=True)
class FixtureSnapshotCall:
    session_id: str
    responses: tuple[KiwoomSourceEnvelope, ...]

    def __post_init__(self) -> None:
        if any(response.session_id != self.session_id for response in self.responses):
            raise FixtureContractError("snapshot response의 session_id가 호출과 다릅니다")


@dataclass(frozen=True, slots=True)
class FixtureCall:
    kind: FixtureCallKind
    session_id: str
    occurred_at: datetime
    stock_ids: tuple[str, ...] = ()


class FixtureKiwoomAdapter:
    """A deterministic adapter with no credential, account, or order surface."""

    def __init__(
        self,
        sessions: Sequence[FixtureSession],
        snapshot_calls: Sequence[FixtureSnapshotCall] = (),
    ) -> None:
        self._sessions = tuple(sessions)
        self._snapshot_calls = tuple(snapshot_calls)
        self._session_position = 0
        self._snapshot_position = 0
        self._message_positions: dict[str, int] = {}
        self._active_session_id: str | None = None
        self._calls: list[FixtureCall] = []
        self._capabilities = AdapterCapabilities()

    @property
    def capabilities(self) -> AdapterCapabilities:
        return self._capabilities

    @property
    def calls(self) -> tuple[FixtureCall, ...]:
        return tuple(self._calls)

    def connect(self, *, now: datetime) -> KiwoomConnection:
        require_aware(now, "now")
        if self._active_session_id is not None:
            raise KiwoomConnectionError("이미 열린 fixture session이 있습니다")
        if self._session_position >= len(self._sessions):
            raise KiwoomConnectionError("더 이상 사용할 fixture session이 없습니다")
        session = self._sessions[self._session_position]
        self._session_position += 1
        self._active_session_id = session.connection.session_id
        self._message_positions.setdefault(session.connection.session_id, 0)
        self._calls.append(
            FixtureCall(
                FixtureCallKind.CONNECT,
                session.connection.session_id,
                now,
            )
        )
        return session.connection

    def read(self, session_id: str) -> KiwoomSourceEnvelope | None:
        session = self._active_session(session_id)
        position = self._message_positions[session_id]
        if position < len(session.messages):
            self._message_positions[session_id] = position + 1
            return session.messages[position]
        if session.disconnect_after_messages:
            self._active_session_id = None
            raise KiwoomConnectionLost("fixture가 연결 종료를 주입했습니다")
        return None

    def replace_trade_subscriptions(
        self,
        session_id: str,
        stock_ids: Sequence[str],
    ) -> None:
        self._active_session(session_id)
        normalized = tuple(stock_ids)
        if len(normalized) > 200:
            raise FixtureContractError("0B 구독은 200종목을 초과할 수 없습니다")
        if len(set(normalized)) != len(normalized):
            raise FixtureContractError("0B 구독 요청에 중복 stock_id가 있습니다")
        for stock_id in normalized:
            require_stock_id(stock_id)
        self._calls.append(
            FixtureCall(
                FixtureCallKind.REPLACE_SUBSCRIPTIONS,
                session_id,
                self._session_by_id(session_id).connection.connected_at,
                normalized,
            )
        )

    def fetch_watchlist_snapshots(
        self,
        session_id: str,
        stock_ids: Sequence[str],
        *,
        requested_at: datetime,
    ) -> tuple[KiwoomSourceEnvelope, ...]:
        self._active_session(session_id)
        require_aware(requested_at, "requested_at")
        normalized = tuple(stock_ids)
        if len(set(normalized)) != len(normalized):
            raise FixtureContractError("snapshot 요청에 중복 stock_id가 있습니다")
        for stock_id in normalized:
            require_stock_id(stock_id)
        self._calls.append(
            FixtureCall(
                FixtureCallKind.FETCH_SNAPSHOTS,
                session_id,
                requested_at,
                normalized,
            )
        )
        if self._snapshot_position >= len(self._snapshot_calls):
            return ()
        snapshot_call = self._snapshot_calls[self._snapshot_position]
        if snapshot_call.session_id != session_id:
            return ()
        self._snapshot_position += 1
        requested_codes = {stock_id.removeprefix("KRX:") for stock_id in normalized}
        returned_codes = {
            code
            for response in snapshot_call.responses
            for code in _snapshot_codes(response.payload)
        }
        if not returned_codes <= requested_codes:
            raise FixtureContractError("snapshot fixture가 요청하지 않은 종목을 반환했습니다")
        return snapshot_call.responses

    def close_session(self, session_id: str) -> None:
        session = self._session_by_id(session_id)
        self._calls.append(
            FixtureCall(
                FixtureCallKind.CLOSE_SESSION,
                session_id,
                session.connection.connected_at,
            )
        )
        if self._active_session_id == session_id:
            self._active_session_id = None

    @classmethod
    def from_path(cls, path: str | Path) -> FixtureKiwoomAdapter:
        fixture_path = Path(path)
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise FixtureContractError("fixture root는 object여야 합니다")
        _assert_no_sensitive_keys(raw)
        if raw.get("fixtureVersion") != FIXTURE_SCHEMA_VERSION:
            raise FixtureContractError("지원하지 않는 fixtureVersion입니다")
        adapter = _mapping(raw.get("adapter"), "adapter")
        if adapter.get("readOnly") is not True:
            raise FixtureContractError("fixture adapter는 readOnly=true여야 합니다")
        if adapter.get("liveValidation") != LiveValidationStatus.PENDING_EXTERNAL.value:
            raise FixtureContractError("liveValidation은 PENDING_EXTERNAL이어야 합니다")

        sessions_value = _sequence(raw.get("sessions"), "sessions")
        sessions: list[FixtureSession] = []
        for session_value in sessions_value:
            session_row = _mapping(session_value, "sessions[]")
            session_id = _required_text(session_row, "sessionId")
            connected_at = _datetime(session_row.get("connectedAt"), "connectedAt")
            messages = tuple(
                _envelope(message, expected_session_id=session_id)
                for message in _sequence(session_row.get("messages"), "messages")
            )
            sessions.append(
                FixtureSession(
                    connection=KiwoomConnection(session_id, connected_at),
                    messages=messages,
                    disconnect_after_messages=bool(
                        session_row.get("disconnectAfterMessages", False)
                    ),
                )
            )

        snapshot_values = _sequence(raw.get("snapshotCalls", []), "snapshotCalls")
        snapshot_calls: list[FixtureSnapshotCall] = []
        for snapshot_value in snapshot_values:
            snapshot_row = _mapping(snapshot_value, "snapshotCalls[]")
            session_id = _required_text(snapshot_row, "sessionId")
            snapshot_calls.append(
                FixtureSnapshotCall(
                    session_id=session_id,
                    responses=tuple(
                        _envelope(response, expected_session_id=session_id)
                        for response in _sequence(
                            snapshot_row.get("responses"), "responses"
                        )
                    ),
                )
            )
        return cls(sessions, snapshot_calls)

    def _active_session(self, session_id: str) -> FixtureSession:
        if self._active_session_id != session_id:
            raise KiwoomConnectionLost("요청한 fixture session은 현재 연결이 아닙니다")
        return self._session_by_id(session_id)

    def _session_by_id(self, session_id: str) -> FixtureSession:
        for session in self._sessions:
            if session.connection.session_id == session_id:
                return session
        raise KiwoomConnectionError("알 수 없는 fixture session입니다")


def _envelope(value: object, *, expected_session_id: str) -> KiwoomSourceEnvelope:
    row = _mapping(value, "source envelope")
    session_id = _required_text(row, "sessionId")
    if session_id != expected_session_id:
        raise FixtureContractError("source envelope sessionId가 부모 session과 다릅니다")
    try:
        channel = SourceChannel(_required_text(row, "channel"))
    except ValueError as exc:
        raise FixtureContractError("지원하지 않는 source channel입니다") from exc
    payload = _mapping(row.get("payload"), "payload")
    sequence = row.get("sourceSequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool):
        raise FixtureContractError("sourceSequence는 정수여야 합니다")
    return KiwoomSourceEnvelope(
        source_schema_version=_required_text(row, "sourceSchemaVersion"),
        channel=channel,
        session_id=session_id,
        source_message_id=_required_text(row, "sourceMessageId"),
        source_sequence=sequence,
        source_timestamp=_datetime(row.get("sourceTimestamp"), "sourceTimestamp"),
        received_at=_datetime(row.get("receivedAt"), "receivedAt"),
        market_date=_date(row.get("marketDate"), "marketDate"),
        payload=payload,
        request_id=_optional_text(row.get("requestId"), "requestId"),
    )


def _snapshot_codes(payload: Mapping[str, object]) -> tuple[str, ...]:
    rows_value = payload.get("rows")
    if rows_value is None:
        rows_value = payload.get("atn_stk_infr")
    codes: list[str] = []
    for value in _sequence(rows_value, "snapshot rows"):
        row = _mapping(value, "snapshot row")
        code = str(row.get("stk_cd") or row.get("code") or "").removeprefix("A")
        if len(code) != 6 or not code.isdigit():
            raise FixtureContractError("snapshot row 종목코드가 올바르지 않습니다")
        codes.append(code)
    return tuple(codes)


def _assert_no_sensitive_keys(value: object) -> None:
    forbidden = {
        "account",
        "accountnumber",
        "appkey",
        "appsecret",
        "credential",
        "order",
        "orderapi",
        "ordertype",
        "password",
        "secret",
        "token",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = "".join(character for character in str(key).lower() if character.isalnum())
            if normalized in forbidden:
                raise FixtureContractError(
                    "fixture에는 credential/account/order 정보가 금지됩니다"
                )
            _assert_no_sensitive_keys(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _assert_no_sensitive_keys(child)


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise FixtureContractError(f"{field_name}는 object여야 합니다")
    return value


def _sequence(value: object, field_name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise FixtureContractError(f"{field_name}는 배열이어야 합니다")
    return value


def _required_text(row: Mapping[str, object], field_name: str) -> str:
    value = row.get(field_name)
    text = str(value or "").strip()
    if not text:
        raise FixtureContractError(f"{field_name}는 비어 있을 수 없습니다")
    return text


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        raise FixtureContractError(f"{field_name}는 null 또는 비어 있지 않은 문자열이어야 합니다")
    return text


def _datetime(value: object, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise FixtureContractError(f"{field_name}는 ISO datetime이어야 합니다") from exc
    require_aware(parsed, field_name)
    return parsed


def _date(value: object, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise FixtureContractError(f"{field_name}는 ISO date여야 합니다") from exc
