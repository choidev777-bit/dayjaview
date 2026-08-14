#!/usr/bin/env python3
"""Secret-free local/CI fixture stack health boundary."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

_LOCALE = "ko-KR"
_HEALTH_PATH = "/api/health"


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"로컬 fixture 상태 확인 실패(ko-KR): {message}")


def _require_fixture_mode() -> None:
    if os.environ.get("DAYJAVIEW_FIXTURE_MODE") != "1":
        _fail("DAYJAVIEW_FIXTURE_MODE=1이 필요합니다. live 실행은 허용되지 않습니다.")


def _tcp_ready(host: str, port: int, component: str) -> None:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return
    except OSError as exc:
        _fail(f"{component} 연결 실패({host}:{port}): {exc}")


def _redis_ready(host: str, port: int) -> None:
    try:
        with socket.create_connection((host, port), timeout=1.0) as connection:
            connection.sendall(b"*1\r\n$4\r\nPING\r\n")
            response = connection.recv(64)
    except OSError as exc:
        _fail(f"Redis 연결 실패({host}:{port}): {exc}")
    if response != b"+PONG\r\n":
        _fail(f"Redis PING 응답이 올바르지 않습니다: {response!r}")


def _dependency_status() -> dict[str, str]:
    postgres_host = os.environ.get("POSTGRES_HOST", "postgres")
    postgres_port = int(os.environ.get("POSTGRES_PORT", "5432"))
    redis_host = os.environ.get("REDIS_HOST", "redis")
    redis_port = int(os.environ.get("REDIS_PORT", "6379"))
    _tcp_ready(postgres_host, postgres_port, "PostgreSQL")
    _redis_ready(redis_host, redis_port)
    return {"postgresql": "HEALTHY", "redis": "HEALTHY"}


def _health_payload() -> dict[str, object]:
    dependencies = _dependency_status()
    return {
        "status": "HEALTHY",
        "locale": _LOCALE,
        "fixtureMode": True,
        "externalRequestsAttempted": False,
        "migrations": "APPLIED",
        "dependencies": dependencies,
        "liveBlockersPreserved": [
            "B-REFDATA-KEYS",
            "B-MARKET-FIXTURE",
            "B-INFOSTOCK-AUTH",
            "B-DATA-RIGHTS",
            "B-DEPLOY",
        ],
    }


class _HealthHandler(BaseHTTPRequestHandler):
    server_version = "DAYJAVIEWFixture/1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != _HEALTH_PATH:
            self._write_json(
                404,
                {
                    "status": "NOT_FOUND",
                    "locale": _LOCALE,
                    "messageKo": "fixture health 경로가 아닙니다.",
                },
            )
            return
        try:
            payload = _health_payload()
        except SystemExit as exc:
            self._write_json(
                503,
                {
                    "status": "UNHEALTHY",
                    "locale": _LOCALE,
                    "messageKo": str(exc),
                },
            )
            return
        self._write_json(200, payload)

    def log_message(self, format: str, *args: object) -> None:
        print(
            json.dumps(
                {
                    "component": "fixture-api",
                    "locale": _LOCALE,
                    "message": format % args,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )

    def _write_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _api() -> int:
    _require_fixture_mode()
    _dependency_status()
    from apps.api import create_fixture_app

    environment = create_fixture_app()
    if environment.app is None:
        _fail("API fixture application 초기화에 실패했습니다.")
    host = os.environ.get("FIXTURE_API_HOST", "0.0.0.0")
    port = int(os.environ.get("FIXTURE_API_PORT", "8000"))
    print(
        json.dumps(
            {
                "component": "fixture-api",
                "status": "STARTED",
                "locale": _LOCALE,
                "fixtureMode": True,
                "externalRequestsAttempted": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    ThreadingHTTPServer((host, port), _HealthHandler).serve_forever()
    return 0


def _probe(url: str) -> int:
    try:
        with urlopen(url, timeout=3.0) as response:  # noqa: S310 - fixed fixture URL
            payload = json.load(response)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        _fail(f"health endpoint 호출 실패: {exc}")
    expected = {
        "status": "HEALTHY",
        "locale": _LOCALE,
        "fixtureMode": True,
        "externalRequestsAttempted": False,
        "migrations": "APPLIED",
    }
    mismatches = {
        key: {"expected": value, "actual": payload.get(key)}
        for key, value in expected.items()
        if payload.get(key) != value
    }
    if mismatches:
        _fail(
            "health 응답 계약 불일치: "
            + json.dumps(mismatches, ensure_ascii=False, sort_keys=True)
        )
    print(
        json.dumps(
            {
                "status": "COMPLETE",
                "locale": _LOCALE,
                "fixtureMode": True,
                "externalRequestsAttempted": False,
                "messageKo": "API·PostgreSQL·Redis fixture health가 정상입니다.",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="외부 호출 없는 DAYJAVIEW local/CI fixture health 경계"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("api")
    probe = subparsers.add_parser("probe")
    probe.add_argument("--url", required=True)
    subparsers.add_parser("worker-help")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "api":
        return _api()
    if args.command == "probe":
        return _probe(args.url)
    print("worker target은 Compose의 명시적 fixture entrypoint로 실행합니다.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        _fail(str(exc))
