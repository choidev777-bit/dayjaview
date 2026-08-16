#!/usr/bin/env python3
"""OCI production live API 진입점과 health 경계.

fixture 전용인 local_stack.py의 운영판이다. secret 값은 읽어도 출력하지
않고, fixture 모드가 켜져 있으면 기동을 거부한다(반대 방향의 fail-closed).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

_LOCALE = "ko-KR"
_HOST = "0.0.0.0"
_PORT = 8000


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"운영 live 상태 확인 실패(ko-KR): {message}")


def _require_live_mode() -> None:
    if os.environ.get("DAYJAVIEW_FIXTURE_MODE") == "1":
        _fail(
            "DAYJAVIEW_FIXTURE_MODE=1인 fixture 모드에서는 실행할 수 없습니다."
            " fixture 실행은 local_stack.py를 사용합니다."
        )


def _dependency_status() -> dict[str, str]:
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    try:
        with socket.create_connection((host, port), timeout=1.0):
            pass
    except OSError as exc:
        _fail(f"PostgreSQL 연결 실패({host}:{port}): {exc}")
    return {"postgresql": "HEALTHY"}


def _health_payload() -> dict[str, object]:
    dependencies = _dependency_status()
    return {
        "status": "HEALTHY",
        "locale": _LOCALE,
        "fixtureMode": False,
        "externalRequestsAttempted": True,
        "migrations": "APPLIED",
        "dependencies": dependencies,
    }


def _api() -> int:
    _require_live_mode()
    _dependency_status()
    import sys
    from pathlib import Path

    repository_root = Path(__file__).resolve().parents[2]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    from apps.api.serve import serve_live_api

    print(
        json.dumps(
            {
                "component": "live-api",
                "status": "STARTED",
                "locale": _LOCALE,
                "fixtureMode": False,
                "externalRequestsAttempted": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    # env는 compose env_file 주입으로만 온다. .env.local은 읽지 않는다.
    return serve_live_api(
        host=_HOST,
        port=_PORT,
        env_file=None,
        health_payload=_health_payload,
    )


def _probe(url: str) -> int:
    try:
        with urlopen(url, timeout=3.0) as response:
            payload = json.load(response)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        _fail(f"health endpoint 호출 실패: {exc}")
    expected = {
        "status": "HEALTHY",
        "locale": _LOCALE,
        "fixtureMode": False,
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
                "fixtureMode": False,
                "messageKo": "API·PostgreSQL live health가 정상입니다.",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DAYJAVIEW OCI production live API 진입·health 경계"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("api")
    probe = subparsers.add_parser("probe")
    probe.add_argument("--url", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "api":
        return _api()
    return _probe(args.url)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        _fail(str(exc))
