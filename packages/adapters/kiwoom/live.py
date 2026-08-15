"""실 키움 REST/WS를 읽기 전용 ReadOnlyKiwoomPort로 잇는 live 어댑터.

fixture 어댑터와 같은 계약을 지키고 전송 계층만 실제 네트워크다:

- WebSocket: LOGIN → CNSRLST → CNSRREQ(search_type=1) 등록 뒤 REAL(0B 체결,
  02 조건검색 편입/이탈)을 수신해 canonical envelope으로 만든다.
- REST: 허용 목록은 ka10095 관심종목 스냅샷 하나다. 주문·계좌 API는 없다.
- 자격증명(app key/secret, token)은 메모리에만 두고 envelope payload에
  절대 싣지 않는다.

2026-08-14 실캡처(data/market-replay)에서 확인된 실서버 동작을 반영한다:

- CNSRREQ(search_type=1) 응답 data는 null이고 후보는 전부 02 실시간으로 온다.
- CNSRREQ를 연속 전송하면 초당 한도(return_code 105110)에 걸린다. 등록은
  간격을 두고 보내고, 걸린 조건은 재시도한다.
- 02 항목에는 "0004V0" 같은 6자리-비숫자 코드가 섞여 온다. KRX 6자리 숫자
  코드가 아닌 항목은 여기서 걸러 normalizer의 엄격한 계약을 지킨다.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta, timezone
from typing import Protocol

import httpx

from .contract import (
    AdapterCapabilities,
    KiwoomConnection,
    KiwoomSourceEnvelope,
    SourceChannel,
    require_aware,
    require_stock_id,
)
from .fixture import KiwoomConnectionError, KiwoomConnectionLost
from .normalizer import KiwoomNormalizer

LOG = logging.getLogger("dayjaview.kiwoom_live")

REAL_HTTP_BASE = "https://api.kiwoom.com"
DEMO_HTTP_BASE = "https://mockapi.kiwoom.com"
REAL_WS_URL = "wss://api.kiwoom.com:10000/api/dostk/websocket"
DEMO_WS_URL = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"
KST = timezone(timedelta(hours=9))

SNAPSHOT_API_ID = "ka10095"
SNAPSHOT_BATCH_SIZE = 100
ALLOWED_REST_API_IDS = frozenset({SNAPSHOT_API_ID})
CONDITION_RATE_LIMIT_CODE = 105110
_WEBSOCKET_SCHEMA = KiwoomNormalizer.SOURCE_SCHEMA_BY_CHANNEL[SourceChannel.WEBSOCKET]
_SNAPSHOT_SCHEMA = KiwoomNormalizer.SOURCE_SCHEMA_BY_CHANNEL[SourceChannel.REST_SNAPSHOT]


class WebSocketLike(Protocol):
    """live 어댑터가 쓰는 최소 WS 표면. 테스트는 scripted 구현을 주입한다."""

    def send(self, message: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | bytes: ...

    def close(self) -> None: ...


WsConnect = Callable[[str], WebSocketLike]
Clock = Callable[[], datetime]


def _default_ws_connect(url: str) -> WebSocketLike:
    from websockets.sync.client import connect

    return connect(url, open_timeout=20, ping_interval=None, max_queue=4096)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_json(raw: str | bytes) -> dict[str, object] | None:
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def normalize_live_stock_code(value: object) -> str | None:
    """실 피드의 종목코드를 KRX 6자리 숫자로 정규화한다. 규격 밖은 None."""

    text = str(value or "").strip().upper()
    if text.startswith("A") and len(text) == 7 and text[1:].isdigit():
        text = text[1:]
    for suffix in ("_AL", "_NX"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text if len(text) == 6 and text.isdigit() else None


def _sanitize_real_items(
    data: object,
) -> tuple[list[Mapping[str, object]], int]:
    """REAL data 항목 중 normalizer가 수용 가능한 0B·02만 남긴다."""

    if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
        return [], 0
    kept: list[Mapping[str, object]] = []
    dropped = 0
    for item in data:
        if not isinstance(item, Mapping):
            dropped += 1
            continue
        item_type = str(item.get("type") or "")
        if item_type not in ("0B", "02"):
            continue  # 0J·0U 등은 계약 밖 채널이므로 이상 계수 없이 지나간다.
        values = item.get("values")
        values_map: Mapping[str, object] = values if isinstance(values, Mapping) else {}
        code = normalize_live_stock_code(item.get("item") or values_map.get("9001"))
        if code is None:
            dropped += 1
            continue
        if item_type == "02":
            action = str(values_map.get("843") or "").upper()
            condition_id = str(values_map.get("841") or "").strip()
            if action not in ("I", "D") or not condition_id:
                dropped += 1
                continue
        kept.append({**item, "item": code})
    return kept, dropped


def _sanitize_condition_rows(
    data: object,
) -> tuple[list[Mapping[str, object]], int]:
    """CNSRREQ 초기 명단 행을 normalizer가 읽는 item 키로 정규화한다."""

    if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
        return [], 0
    kept: list[Mapping[str, object]] = []
    dropped = 0
    for row in data:
        if not isinstance(row, Mapping):
            dropped += 1
            continue
        code = normalize_live_stock_code(
            row.get("item") or row.get("9001") or row.get("jmcode") or row.get("stk_cd")
        )
        if code is None:
            dropped += 1
            continue
        kept.append({**row, "item": code})
    return kept, dropped


def _parse_condition_list(payload: Mapping[str, object]) -> list[dict[str, str]]:
    data = payload.get("data")
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
        return []
    result: list[dict[str, str]] = []
    for row in data:
        if isinstance(row, Mapping):
            sequence = str(row.get("seq") or "").strip()
            name = str(row.get("name") or "").strip()
        elif isinstance(row, Sequence) and not isinstance(row, (str, bytes)) and len(row) >= 2:
            sequence, name = str(row[0]).strip(), str(row[1]).strip()
        else:
            continue
        if sequence:
            result.append({"seq": sequence, "name": name})
    return result


def _source_clock_to_datetime(value: object, received_at: datetime) -> datetime:
    """키움 HHMMSS[mmm] 체결시각을 수신일 KST 기준 datetime으로 바꾼다."""

    digits = "".join(char for char in str(value or "") if char.isdigit())
    if len(digits) < 6:
        return received_at
    try:
        clock = datetime.strptime(digits[:6], "%H%M%S").time()
    except ValueError:
        return received_at
    occurred = datetime.combine(received_at.astimezone(KST).date(), clock, tzinfo=KST)
    if len(digits) >= 9:
        occurred = occurred.replace(microsecond=int(digits[6:9]) * 1000)
    return occurred


def _first_source_clock(payload: Mapping[str, object], received_at: datetime) -> datetime:
    data = payload.get("data")
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        for item in data:
            if not isinstance(item, Mapping):
                continue
            values = item.get("values")
            if isinstance(values, Mapping) and values.get("20"):
                return _source_clock_to_datetime(values.get("20"), received_at)
    return received_at


class _RateLimiter:
    def __init__(self, minimum_interval: float) -> None:
        self._minimum_interval = minimum_interval
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self, sleep: Callable[[float], None]) -> None:
        with self._lock:
            delay = max(0.0, self._next_allowed - time.monotonic())
            if delay:
                sleep(delay)
            self._next_allowed = time.monotonic() + self._minimum_interval


class _ConditionRetry:
    __slots__ = ("attempts", "due_at")

    def __init__(self, attempts: int, due_at: float) -> None:
        self.attempts = attempts
        self.due_at = due_at


class _LiveSession:
    def __init__(self, session_id: str, ws: WebSocketLike) -> None:
        self.session_id = session_id
        self.ws = ws
        self.queue: queue.SimpleQueue[KiwoomSourceEnvelope] = queue.SimpleQueue()
        self.stop_event = threading.Event()
        self.closed_event = threading.Event()
        self.close_reason = ""
        self.reader: threading.Thread | None = None
        self.last_message_at: datetime | None = None
        self.dropped_items = 0
        self.invalid_messages = 0
        self.control_errors = 0
        self.condition_retries: dict[str, _ConditionRetry] = {}
        self.last_condition_send = 0.0
        self._sequence = 0
        self._sequence_lock = threading.Lock()

    @property
    def closed(self) -> bool:
        return self.closed_event.is_set()

    def next_sequence(self) -> int:
        with self._sequence_lock:
            self._sequence += 1
            return self._sequence

    def mark_closed(self, reason: str) -> None:
        if not self.close_reason:
            self.close_reason = reason
        self.closed_event.set()


class LiveKiwoomAdapter:
    """조건검색 후보·0B 체결·ka10095 스냅샷만 다루는 read-only live 포트."""

    def __init__(
        self,
        *,
        mode: str,
        app_key: str,
        app_secret: str,
        condition_ids: Sequence[str] = (),
        max_conditions: int = 8,
        http_transport: httpx.BaseTransport | None = None,
        ws_connect: WsConnect | None = None,
        clock: Clock = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
        condition_request_interval: float = 0.35,
        condition_retry_interval: float = 1.0,
        handshake_timeout: float = 20.0,
        poll_timeout: float = 1.0,
        request_timeout: float = 30.0,
        rest_minimum_interval: float = 0.26,
    ) -> None:
        if mode not in ("real", "demo"):
            raise ValueError("mode는 real 또는 demo여야 합니다")
        if not app_key or not app_secret:
            raise ValueError("KIWOOM_APP_KEY와 KIWOOM_APP_SECRET이 필요합니다")
        if not 1 <= max_conditions <= 8:
            raise ValueError("max_conditions는 1 이상 8 이하여야 합니다")
        self._mode = mode
        self._app_key = app_key
        self._app_secret = app_secret
        self._condition_ids = tuple(str(value).strip() for value in condition_ids if str(value).strip())
        self._max_conditions = max_conditions
        self._http_base = REAL_HTTP_BASE if mode == "real" else DEMO_HTTP_BASE
        self._ws_url = REAL_WS_URL if mode == "real" else DEMO_WS_URL
        self._ws_connect = ws_connect or _default_ws_connect
        self._clock = clock
        self._sleep = sleep
        self._condition_request_interval = condition_request_interval
        self._condition_retry_interval = condition_retry_interval
        self._handshake_timeout = handshake_timeout
        self._poll_timeout = poll_timeout
        self._http = httpx.Client(
            timeout=httpx.Timeout(request_timeout),
            transport=http_transport,
        )
        self._rest_limiter = _RateLimiter(rest_minimum_interval)
        self._token: str | None = None
        self._token_expires_at: datetime | None = None
        self._session: _LiveSession | None = None
        self._capabilities = AdapterCapabilities()

    @property
    def capabilities(self) -> AdapterCapabilities:
        return self._capabilities

    @property
    def last_message_at(self) -> datetime | None:
        session = self._session
        return None if session is None else session.last_message_at

    def connect(self, *, now: datetime) -> KiwoomConnection:
        require_aware(now, "now")
        if self._session is not None and not self._session.closed:
            raise KiwoomConnectionError("이미 열린 live session이 있습니다")
        self._session = None
        token = self._access_token()
        try:
            ws = self._ws_connect(self._ws_url)
        except Exception as exc:
            raise KiwoomConnectionError(f"websocket 연결 실패: {exc}") from exc
        try:
            self._handshake(ws, token)
        except KiwoomConnectionError:
            self._close_ws(ws)
            raise
        except Exception as exc:
            self._close_ws(ws)
            raise KiwoomConnectionError(f"websocket 핸드셰이크 실패: {exc}") from exc
        session = _LiveSession(f"kiwoom-live-{uuid.uuid4().hex[:16]}", ws)
        reader = threading.Thread(
            target=self._reader_loop,
            args=(session,),
            name=f"kiwoom-live-reader-{session.session_id}",
            daemon=True,
        )
        session.reader = reader
        self._session = session
        reader.start()
        return KiwoomConnection(session.session_id, now)

    def read(self, session_id: str) -> KiwoomSourceEnvelope | None:
        session = self._require_session(session_id)
        try:
            return session.queue.get_nowait()
        except queue.Empty:
            if session.closed:
                raise KiwoomConnectionLost(
                    session.close_reason or "kiwoom live 연결이 끊어졌습니다"
                ) from None
            return None

    def replace_trade_subscriptions(
        self,
        session_id: str,
        stock_ids: Sequence[str],
    ) -> None:
        session = self._require_session(session_id)
        normalized = tuple(stock_ids)
        if len(normalized) > 200:
            raise ValueError("0B 구독은 200종목을 초과할 수 없습니다")
        if len(set(normalized)) != len(normalized):
            raise ValueError("0B 구독 요청에 중복 stock_id가 있습니다")
        for stock_id in normalized:
            require_stock_id(stock_id)
        codes = [stock_id.removeprefix("KRX:") for stock_id in normalized]
        chunks = [codes[index : index + 100] for index in range(0, len(codes), 100)]
        packet = {
            "trnm": "REG",
            "grp_no": "1",
            # refresh=0은 그룹 전체 교체라 재전송이 결정적이다.
            "refresh": "0",
            "data": [{"item": chunk, "type": ["0B"]} for chunk in chunks] or [
                {"item": [], "type": ["0B"]}
            ],
        }
        try:
            session.ws.send(_canonical_json(packet))
        except Exception as exc:
            session.mark_closed(f"구독 등록 전송 실패: {exc}")
            raise KiwoomConnectionLost(session.close_reason) from exc

    def fetch_watchlist_snapshots(
        self,
        session_id: str,
        stock_ids: Sequence[str],
        *,
        requested_at: datetime,
    ) -> tuple[KiwoomSourceEnvelope, ...]:
        session = self._require_session(session_id)
        require_aware(requested_at, "requested_at")
        normalized = tuple(stock_ids)
        if len(set(normalized)) != len(normalized):
            raise ValueError("snapshot 요청에 중복 stock_id가 있습니다")
        for stock_id in normalized:
            require_stock_id(stock_id)
        codes = [stock_id.removeprefix("KRX:") for stock_id in normalized]
        requested_codes = set(codes)
        envelopes: list[KiwoomSourceEnvelope] = []
        for position in range(0, len(codes), SNAPSHOT_BATCH_SIZE):
            chunk = codes[position : position + SNAPSHOT_BATCH_SIZE]
            try:
                payload = self._rest_post(
                    SNAPSHOT_API_ID,
                    "/api/dostk/stkinfo",
                    {"stk_cd": "|".join(chunk)},
                )
            except KiwoomConnectionError as exc:
                LOG.warning("ka10095 chunk 실패(계속 진행): %s", exc)
                continue
            received_at = self._clock()
            rows_value = payload.get("atn_stk_infr")
            rows: list[Mapping[str, object]] = []
            if isinstance(rows_value, Sequence) and not isinstance(
                rows_value, (str, bytes, bytearray)
            ):
                for row in rows_value:
                    if not isinstance(row, Mapping):
                        continue
                    code = normalize_live_stock_code(row.get("stk_cd") or row.get("code"))
                    if code is None or code not in requested_codes:
                        session.dropped_items += 1
                        continue
                    rows.append({**row, "stk_cd": code})
            if not rows:
                continue
            sequence = session.next_sequence()
            envelopes.append(
                KiwoomSourceEnvelope(
                    source_schema_version=_SNAPSHOT_SCHEMA,
                    channel=SourceChannel.REST_SNAPSHOT,
                    session_id=session.session_id,
                    source_message_id=f"live:{session.session_id}:{sequence}",
                    source_sequence=sequence,
                    source_timestamp=received_at,
                    received_at=received_at,
                    market_date=received_at.astimezone(KST).date(),
                    payload={"apiId": SNAPSHOT_API_ID, "atn_stk_infr": rows},
                    request_id=f"{SNAPSHOT_API_ID}:{sequence}",
                )
            )
        return tuple(envelopes)

    def close_session(self, session_id: str) -> None:
        session = self._session
        if session is None or session.session_id != session_id:
            raise KiwoomConnectionError("알 수 없는 live session입니다")
        session.stop_event.set()
        session.mark_closed("close_session 호출")
        self._close_ws(session.ws)
        reader = session.reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=5.0)
        self._session = None

    def close(self) -> None:
        """프로세스 종료 시 세션과 HTTP 클라이언트를 정리한다. token은 버린다."""

        session = self._session
        if session is not None:
            self.close_session(session.session_id)
        self._token = None
        self._token_expires_at = None
        self._http.close()

    def _require_session(self, session_id: str) -> _LiveSession:
        session = self._session
        if session is None or session.session_id != session_id:
            raise KiwoomConnectionLost("요청한 live session은 현재 연결이 아닙니다")
        return session

    def _handshake(self, ws: WebSocketLike, token: str) -> None:
        ws.send(_canonical_json({"trnm": "LOGIN", "token": token}))
        login = self._await_message(ws, expected_trnm="LOGIN")
        if login.get("return_code") != 0:
            raise KiwoomConnectionError(
                f"websocket 로그인 실패: {login.get('return_msg')}"
            )
        ws.send(_canonical_json({"trnm": "CNSRLST"}))
        condition_response = self._await_message(ws, expected_trnm="CNSRLST")
        available = _parse_condition_list(condition_response)
        if self._condition_ids:
            wanted = set(self._condition_ids)
            available = [item for item in available if item["seq"] in wanted]
        selected = available[: self._max_conditions]
        if not selected:
            raise KiwoomConnectionError(
                "사용할 조건검색식이 없습니다 (CNSRLST 결과가 비었거나 "
                "KIWOOM_CONDITION_IDS와 일치하는 조건이 없습니다)"
            )
        LOG.info(
            "조건검색 등록 대상 %d건: %s",
            len(selected),
            ", ".join(f"{item['seq']}:{item['name']}" for item in selected),
        )
        for index, condition in enumerate(selected):
            if index:
                # 실서버는 CNSRREQ 연속 전송에 초당 한도(105110)를 건다.
                self._sleep(self._condition_request_interval)
            ws.send(
                _canonical_json(
                    {
                        "trnm": "CNSRREQ",
                        "seq": condition["seq"],
                        "search_type": "1",
                        "stex_tp": "K",
                    }
                )
            )

    def _await_message(
        self,
        ws: WebSocketLike,
        *,
        expected_trnm: str,
    ) -> dict[str, object]:
        deadline = time.monotonic() + self._handshake_timeout
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                raw = ws.recv(timeout=remaining)
            except TimeoutError:
                break
            payload = _parse_json(raw)
            if payload is None:
                continue
            trnm = str(payload.get("trnm") or "").upper()
            if trnm == "PING":
                ws.send(_canonical_json(payload))
                continue
            if trnm == expected_trnm:
                return payload
        raise KiwoomConnectionError(f"{expected_trnm} 응답 대기 시간 초과")

    def _reader_loop(self, session: _LiveSession) -> None:
        while not session.stop_event.is_set():
            self._flush_condition_retries(session)
            try:
                raw = session.ws.recv(timeout=self._poll_timeout)
            except TimeoutError:
                continue
            except Exception as exc:
                session.mark_closed(f"websocket 수신 실패: {exc}")
                return
            payload = _parse_json(raw)
            if payload is None:
                session.invalid_messages += 1
                continue
            session.last_message_at = self._clock()
            trnm = str(payload.get("trnm") or "").upper()
            if trnm == "PING":
                try:
                    session.ws.send(_canonical_json(payload))
                except Exception as exc:
                    session.mark_closed(f"PING 응답 전송 실패: {exc}")
                    return
                continue
            if trnm == "REAL":
                items, dropped = _sanitize_real_items(payload.get("data"))
                session.dropped_items += dropped
                if items:
                    self._enqueue(session, {**payload, "data": items})
                continue
            if trnm == "CNSRREQ":
                self._handle_condition_response(session, payload)
                continue
            return_code = payload.get("return_code")
            if return_code not in (None, 0):
                session.control_errors += 1
                LOG.warning(
                    "kiwoom 제어 응답 오류 trnm=%s return_code=%s", trnm, return_code
                )
        session.mark_closed("reader 중지")

    def _handle_condition_response(
        self,
        session: _LiveSession,
        payload: Mapping[str, object],
    ) -> None:
        sequence = str(payload.get("seq") or "").strip()
        return_code = payload.get("return_code")
        if return_code == CONDITION_RATE_LIMIT_CODE:
            retry = session.condition_retries.get(sequence)
            attempts = 0 if retry is None else retry.attempts
            if not sequence or attempts >= 3:
                session.control_errors += 1
                LOG.warning("조건검색 등록 재시도 포기 seq=%s", sequence)
                return
            session.condition_retries[sequence] = _ConditionRetry(
                attempts,
                time.monotonic() + self._condition_retry_interval,
            )
            return
        if return_code not in (None, 0):
            session.control_errors += 1
            LOG.warning(
                "조건검색 응답 오류 seq=%s return_code=%s", sequence, return_code
            )
            return
        session.condition_retries.pop(sequence, None)
        rows, dropped = _sanitize_condition_rows(payload.get("data"))
        session.dropped_items += dropped
        if rows and sequence:
            self._enqueue(
                session,
                {"trnm": "CNSRREQ", "seq": sequence, "data": rows},
            )

    def _flush_condition_retries(self, session: _LiveSession) -> None:
        if not session.condition_retries:
            return
        now = time.monotonic()
        if now - session.last_condition_send < self._condition_request_interval:
            return
        for sequence, retry in sorted(session.condition_retries.items()):
            if retry.due_at > now:
                continue
            retry.attempts += 1
            retry.due_at = now + self._condition_retry_interval
            session.last_condition_send = now
            try:
                session.ws.send(
                    _canonical_json(
                        {
                            "trnm": "CNSRREQ",
                            "seq": sequence,
                            "search_type": "1",
                            "stex_tp": "K",
                        }
                    )
                )
            except Exception as exc:
                session.mark_closed(f"조건검색 재시도 전송 실패: {exc}")
            return  # 재시도도 초당 한도를 지키려 한 번에 하나만 보낸다.

    def _enqueue(self, session: _LiveSession, payload: Mapping[str, object]) -> None:
        received_at = self._clock()
        sequence = session.next_sequence()
        session.queue.put(
            KiwoomSourceEnvelope(
                source_schema_version=_WEBSOCKET_SCHEMA,
                channel=SourceChannel.WEBSOCKET,
                session_id=session.session_id,
                source_message_id=f"live:{session.session_id}:{sequence}",
                source_sequence=sequence,
                source_timestamp=_first_source_clock(payload, received_at),
                received_at=received_at,
                market_date=received_at.astimezone(KST).date(),
                payload=payload,
            )
        )

    def _access_token(self) -> str:
        if self._token is not None and self._token_expires_at is not None:
            if self._clock() < self._token_expires_at - timedelta(minutes=10):
                return self._token
        try:
            response = self._http.post(
                f"{self._http_base}/oauth2/token",
                headers={"Content-Type": "application/json;charset=UTF-8"},
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._app_key,
                    "secretkey": self._app_secret,
                },
            )
        except httpx.HTTPError as exc:
            raise KiwoomConnectionError(f"token 발급 요청 실패: {exc}") from exc
        payload = _safe_json(response)
        if response.status_code >= 400 or payload.get("return_code") not in (None, 0):
            raise KiwoomConnectionError(
                "token 발급 실패: "
                f"HTTP {response.status_code} return_code={payload.get('return_code')}"
            )
        token = str(payload.get("token") or "")
        expires = str(payload.get("expires_dt") or "")
        if not token or len(expires) != 14:
            raise KiwoomConnectionError("token 응답에 token 또는 만료 시각이 없습니다")
        self._token = token
        self._token_expires_at = datetime.strptime(expires, "%Y%m%d%H%M%S").replace(
            tzinfo=KST
        )
        return token

    def _rest_post(
        self,
        api_id: str,
        path: str,
        body: Mapping[str, object],
    ) -> dict[str, object]:
        if api_id not in ALLOWED_REST_API_IDS:
            raise KiwoomConnectionError(f"허용되지 않은 REST API입니다: {api_id}")
        last_error: str = "시도 없음"
        for attempt in range(3):
            self._rest_limiter.wait(self._sleep)
            try:
                token = self._access_token()
                response = self._http.post(
                    f"{self._http_base}{path}",
                    headers={
                        "Content-Type": "application/json;charset=UTF-8",
                        "authorization": f"Bearer {token}",
                        "api-id": api_id,
                    },
                    json=dict(body),
                )
            except (httpx.HTTPError, KiwoomConnectionError) as exc:
                last_error = str(exc)
                continue
            payload = _safe_json(response)
            if response.status_code in (401, 403) and attempt == 0:
                self._token = None
                last_error = f"HTTP {response.status_code}"
                continue
            if response.status_code >= 400:
                last_error = f"HTTP {response.status_code}"
                continue
            if payload.get("return_code") not in (None, 0):
                last_error = (
                    f"return_code={payload.get('return_code')} "
                    f"msg={payload.get('return_msg')}"
                )
                continue
            return payload
        raise KiwoomConnectionError(f"{api_id} 호출 실패: {last_error}")

    @staticmethod
    def _close_ws(ws: WebSocketLike) -> None:
        try:
            ws.close()
        except Exception:  # noqa: BLE001 - 종료 정리 실패는 무시한다.
            pass


def _safe_json(response: httpx.Response) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}
