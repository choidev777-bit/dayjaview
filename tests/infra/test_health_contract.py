from __future__ import annotations

import json

from infra.operations import local_stack


def test_health_payload_is_deterministic_fixture_only(monkeypatch) -> None:
    monkeypatch.setattr(
        local_stack,
        "_dependency_status",
        lambda: {"postgresql": "HEALTHY", "redis": "HEALTHY"},
    )

    first = local_stack._health_payload()
    second = local_stack._health_payload()

    assert first == second
    assert first["status"] == "HEALTHY"
    assert first["locale"] == "ko-KR"
    assert first["fixtureMode"] is True
    assert first["externalRequestsAttempted"] is False
    assert first["migrations"] == "APPLIED"
    assert first["liveBlockersPreserved"] == [
        "B-REFDATA-KEYS",
        "B-MARKET-FIXTURE",
        "B-INFOSTOCK-AUTH",
        "B-DATA-RIGHTS",
        "B-OPERATOR",
        "B-DEPLOY",
    ]
    json.dumps(first, ensure_ascii=False, sort_keys=True)


def test_redis_health_uses_only_ping(monkeypatch) -> None:
    sent: list[bytes] = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def sendall(self, payload: bytes) -> None:
            sent.append(payload)

        def recv(self, _size: int) -> bytes:
            return b"+PONG\r\n"

    monkeypatch.setattr(local_stack.socket, "create_connection", lambda *_args, **_kwargs: Connection())
    local_stack._redis_ready("redis", 6379)
    assert sent == [b"*1\r\n$4\r\nPING\r\n"]
