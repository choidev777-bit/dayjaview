"""LiveKiwoomAdapter를 mock 전송 계층(WS 스크립트 + httpx MockTransport)으로 검증한다.

실제 네트워크 호출은 없다. 스크립트된 WS는 2026-08-14 실캡처에서 확인된
실서버 응답 형태(LOGIN ack, CNSRLST, CNSRREQ data:null, 105110 한도,
6자리-비숫자 코드)를 그대로 흉내 낸다.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import UTC, datetime

import httpx
import pytest

from packages.adapters.kiwoom import (
    CanonicalEventType,
    KiwoomConnectionError,
    KiwoomConnectionLost,
    KiwoomNormalizer,
    LiveKiwoomAdapter,
    MarketGateway,
)

NOW = datetime(2026, 8, 14, 0, 30, tzinfo=UTC)
TOKEN = "fake-live-token"
APP_KEY = "test-app-key"
APP_SECRET = "test-app-secret"

TRADE_ITEM = {
    "type": "0B",
    "item": "000080",
    "name": "주식체결",
    "values": {
        "10": "+15560",
        "12": "+1.24",
        "13": "10532",
        "14": "163",
        "15": "+356",
        "16": "-15340",
        "17": "+15560",
        "18": "-15330",
        "20": "091902",
        "228": "227.49",
        "311": "10913",
        "1315": " 0",
    },
}


class ScriptedWebSocket:
    """LOGIN·CNSRLST·CNSRREQ·REG에 자동 응답하고 push된 frame을 순서대로 준다."""

    def __init__(
        self,
        *,
        conditions: list[object] | None = None,
        rate_limited_seqs: set[str] | None = None,
        login_ok: bool = True,
    ) -> None:
        self.sent: list[dict[str, object]] = []
        self._incoming: deque[str] = deque()
        self._lock = threading.Lock()
        self.closed = False
        self._conditions = (
            conditions if conditions is not None else [{"seq": "7", "name": "급등"}]
        )
        self._rate_limited = set(rate_limited_seqs or ())
        self._login_ok = login_ok

    def push(self, payload: dict[str, object]) -> None:
        with self._lock:
            self._incoming.append(json.dumps(payload, ensure_ascii=False))

    def push_disconnect(self) -> None:
        with self._lock:
            self.closed = True

    def send(self, message: str) -> None:
        payload = json.loads(message)
        with self._lock:
            self.sent.append(payload)
        trnm = payload.get("trnm")
        if trnm == "LOGIN":
            self.push(
                {"trnm": "LOGIN", "return_code": 0 if self._login_ok else 8005}
            )
        elif trnm == "CNSRLST":
            self.push(
                {"trnm": "CNSRLST", "return_code": 0, "data": self._conditions}
            )
        elif trnm == "CNSRREQ":
            sequence = str(payload.get("seq"))
            if sequence in self._rate_limited:
                self._rate_limited.discard(sequence)
                self.push(
                    {
                        "trnm": "CNSRREQ",
                        "seq": sequence,
                        "return_code": 105110,
                        "return_msg": "요청 건수 초과",
                    }
                )
            else:
                self.push(
                    {"trnm": "CNSRREQ", "seq": sequence, "return_code": 0, "data": None}
                )
        elif trnm == "REG":
            self.push({"trnm": "REG", "return_code": 0, "return_msg": ""})

    def recv(self, timeout: float | None = None) -> str:
        with self._lock:
            if self._incoming:
                return self._incoming.popleft()
            if self.closed:
                raise RuntimeError("scripted websocket closed")
        time.sleep(0.002)
        raise TimeoutError

    def close(self) -> None:
        with self._lock:
            self.closed = True

    def sent_by_trnm(self, trnm: str) -> list[dict[str, object]]:
        with self._lock:
            return [message for message in self.sent if message.get("trnm") == trnm]


def http_handler(recorded: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
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
            rows = [
                {"stk_cd": code, "cur_prc": "+1000", "flu_rt": "1.0", "acc_trde_prica": "5"}
                for code in codes
            ]
            rows.append(
                {"stk_cd": "999999", "cur_prc": "+1", "acc_trde_prica": "1"}
            )
            return httpx.Response(200, json={"return_code": 0, "atn_stk_infr": rows})
        return httpx.Response(404)

    return handler


def make_adapter(
    ws: ScriptedWebSocket,
    recorded: list[httpx.Request] | None = None,
    **kwargs: object,
) -> LiveKiwoomAdapter:
    return LiveKiwoomAdapter(
        mode="real",
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        http_transport=httpx.MockTransport(http_handler(recorded if recorded is not None else [])),
        ws_connect=lambda url: ws,
        sleep=lambda seconds: None,
        condition_retry_interval=0.05,
        poll_timeout=0.01,
        **kwargs,
    )


def drain(
    adapter: LiveKiwoomAdapter,
    session_id: str,
    count: int,
    timeout: float = 3.0,
):
    envelopes = []
    deadline = time.monotonic() + timeout
    while len(envelopes) < count and time.monotonic() < deadline:
        envelope = adapter.read(session_id)
        if envelope is None:
            time.sleep(0.01)
            continue
        envelopes.append(envelope)
    assert len(envelopes) == count, f"{count}개 대신 {len(envelopes)}개를 받았습니다"
    return envelopes


def wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("조건이 시간 안에 충족되지 않았습니다")


def test_connect_handshake_registers_selected_conditions() -> None:
    ws = ScriptedWebSocket(
        conditions=[{"seq": "7", "name": "급등"}, ["12", "거래량"], {"seq": "19", "name": "기타"}]
    )
    adapter = make_adapter(ws, condition_ids=("7", "12"))
    try:
        connection = adapter.connect(now=NOW)
        assert connection.connected_at == NOW
        assert adapter.capabilities.read_only is True
        assert adapter.capabilities.orders is False
        assert adapter.capabilities.accounts is False
        login_messages = ws.sent_by_trnm("LOGIN")
        assert login_messages == [{"trnm": "LOGIN", "token": TOKEN}]
        requests = ws.sent_by_trnm("CNSRREQ")
        assert [message["seq"] for message in requests] == ["7", "12"]
        assert all(
            message["search_type"] == "1" and message["stex_tp"] == "K"
            for message in requests
        )
    finally:
        adapter.close()


def test_login_failure_and_empty_conditions_raise() -> None:
    adapter = make_adapter(ScriptedWebSocket(login_ok=False))
    with pytest.raises(KiwoomConnectionError):
        adapter.connect(now=NOW)
    adapter.close()

    adapter = make_adapter(ScriptedWebSocket(conditions=[]))
    with pytest.raises(KiwoomConnectionError):
        adapter.connect(now=NOW)
    adapter.close()


def test_real_messages_become_normalizable_envelopes() -> None:
    ws = ScriptedWebSocket()
    adapter = make_adapter(ws)
    try:
        connection = adapter.connect(now=NOW)
        # 실캡처 형태 그대로: 체결 1건 + 유효/비유효 조건 편입이 섞인 메시지.
        ws.push({"trnm": "REAL", "data": [TRADE_ITEM]})
        ws.push(
            {
                "trnm": "REAL",
                "data": [
                    {
                        "type": "02",
                        "item": "0004V0",
                        "values": {"841": "7", "843": "I", "9001": "0004V0"},
                    },
                    {
                        "type": "02",
                        "item": "005930_AL",
                        "values": {"841": "7", "843": "I", "9001": "005930"},
                    },
                    {"type": "0J", "item": "001", "values": {"20": "093000"}},
                ],
            }
        )
        ws.push({"trnm": "PING", "seq": "1"})
        ws.push({"trnm": "CNSRREQ", "seq": "7", "return_code": 0, "data": None})
        envelopes = drain(adapter, connection.session_id, 2)

        normalizer = KiwoomNormalizer()
        trade_events = normalizer.normalize(envelopes[0])
        assert [event.event_type for event in trade_events] == [
            CanonicalEventType.TRADE
        ]
        assert trade_events[0].stock_id == "KRX:000080"
        assert str(trade_events[0].data.current_price) == "15560"

        candidate_events = normalizer.normalize(envelopes[1])
        assert [event.event_type for event in candidate_events] == [
            CanonicalEventType.CANDIDATE_ENTERED
        ]
        assert candidate_events[0].stock_id == "KRX:005930"

        assert envelopes[0].source_sequence < envelopes[1].source_sequence
        # 거래소 체결시각(091902 KST)이 envelope 시각에 반영된다.
        assert envelopes[0].source_timestamp.astimezone(UTC).strftime("%H%M%S") == "001902"

        wait_until(lambda: any("seq" in m for m in ws.sent_by_trnm("PING")))
        for envelope in envelopes:
            serialized = json.dumps(dict(envelope.payload), ensure_ascii=False)
            assert TOKEN not in serialized
            assert APP_KEY not in serialized
            assert APP_SECRET not in serialized
    finally:
        adapter.close()


def test_condition_rate_limit_is_retried() -> None:
    ws = ScriptedWebSocket(rate_limited_seqs={"7"})
    adapter = make_adapter(ws)
    try:
        adapter.connect(now=NOW)
        wait_until(lambda: len(ws.sent_by_trnm("CNSRREQ")) >= 2)
        requests = ws.sent_by_trnm("CNSRREQ")
        assert [message["seq"] for message in requests] == ["7", "7"]
    finally:
        adapter.close()


def test_replace_trade_subscriptions_builds_replacing_packet() -> None:
    ws = ScriptedWebSocket()
    adapter = make_adapter(ws)
    try:
        connection = adapter.connect(now=NOW)
        stock_ids = tuple(f"KRX:{index:06d}" for index in range(1, 151))
        adapter.replace_trade_subscriptions(connection.session_id, stock_ids)
        packets = ws.sent_by_trnm("REG")
        assert len(packets) == 1
        packet = packets[0]
        assert packet["grp_no"] == "1"
        assert packet["refresh"] == "0"
        data = packet["data"]
        assert [len(entry["item"]) for entry in data] == [100, 50]
        assert all(entry["type"] == ["0B"] for entry in data)
        assert data[0]["item"][0] == "000001"

        with pytest.raises(ValueError):
            adapter.replace_trade_subscriptions(
                connection.session_id,
                tuple(f"KRX:{index:06d}" for index in range(1, 202)),
            )
        with pytest.raises(ValueError):
            adapter.replace_trade_subscriptions(
                connection.session_id, ("KRX:000001", "KRX:000001")
            )
    finally:
        adapter.close()


def test_fetch_watchlist_snapshots_filters_and_chunks() -> None:
    recorded: list[httpx.Request] = []
    ws = ScriptedWebSocket()
    adapter = make_adapter(ws, recorded)
    try:
        connection = adapter.connect(now=NOW)
        envelopes = adapter.fetch_watchlist_snapshots(
            connection.session_id,
            ("KRX:005930", "KRX:000660"),
            requested_at=NOW,
        )
        assert len(envelopes) == 1
        snapshot_requests = [
            request for request in recorded if request.url.path == "/api/dostk/stkinfo"
        ]
        assert len(snapshot_requests) == 1
        request = snapshot_requests[0]
        assert request.headers["api-id"] == "ka10095"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert json.loads(request.content) == {"stk_cd": "005930|000660"}

        events = KiwoomNormalizer().normalize(envelopes[0])
        assert sorted(event.stock_id for event in events) == [
            "KRX:000660",
            "KRX:005930",
        ]  # 요청하지 않은 999999 행은 걸러진다.
        assert all(
            event.event_type is CanonicalEventType.SNAPSHOT for event in events
        )

        adapter.fetch_watchlist_snapshots(
            connection.session_id,
            tuple(f"KRX:{index:06d}" for index in range(1, 151)),
            requested_at=NOW,
        )
        snapshot_requests = [
            request for request in recorded if request.url.path == "/api/dostk/stkinfo"
        ]
        assert len(snapshot_requests) == 3  # 150종목은 100 + 50으로 나눠 부른다.
    finally:
        adapter.close()


def test_rest_allow_list_blocks_other_apis() -> None:
    adapter = make_adapter(ScriptedWebSocket())
    with pytest.raises(KiwoomConnectionError):
        adapter._rest_post("ka10099", "/api/dostk/stkinfo", {"mrkt_tp": "0"})
    adapter.close()


def test_disconnect_drains_then_raises_lost() -> None:
    ws = ScriptedWebSocket()
    adapter = make_adapter(ws)
    connection = adapter.connect(now=NOW)
    ws.push({"trnm": "REAL", "data": [TRADE_ITEM]})
    ws.push_disconnect()
    envelopes = drain(adapter, connection.session_id, 1)
    assert envelopes[0].channel.value == "KIWOOM_WEBSOCKET"

    def read_until_lost() -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            # 큐가 빈 뒤 reader가 종료를 알아채면 Lost가 올라온다.
            adapter.read(connection.session_id)
            time.sleep(0.01)
        raise AssertionError("연결 종료가 KiwoomConnectionLost로 드러나지 않았습니다")

    with pytest.raises(KiwoomConnectionLost):
        read_until_lost()
    adapter.close_session(connection.session_id)
    adapter.close()


def test_gateway_accepts_live_adapter_events() -> None:
    ws = ScriptedWebSocket()
    adapter = make_adapter(ws)
    try:
        gateway = MarketGateway(adapter)
        gateway.connect(now=NOW)
        ws.push({"trnm": "REAL", "data": [TRADE_ITEM]})

        def poll() -> bool:
            gateway.poll_once(now=datetime.now(UTC))
            return len(gateway.accepted_events) >= 1

        wait_until(poll)
        event = gateway.accepted_events[0]
        assert event.event_type is CanonicalEventType.TRADE
        assert event.stock_id == "KRX:000080"
        assert adapter.last_message_at is not None
    finally:
        adapter.close()
