"""F-23 수리: 배포 env 계약과 코드가 같은 것을 가리킨다.

계약이 필수로 선언한 값은 코드가 실제로 읽어야 하고, 코드가 필요로 하는 커서
서명 키는 env에서 주입돼 인스턴스가 여러 개여도 같은 커서를 받아들여야 한다.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from apps.api.config import ApiSettings
from apps.api.production import (
    CURSOR_SIGNING_SECRET_ENV,
    POSTGRES_STORE,
    create_production_app,
)
from packages.identity import (
    FixtureGoogleOAuthProvider,
    GoogleIdentity,
    IdentityService,
    InMemoryIdentityRepository,
    InMemoryTargetCatalog,
    InvalidCursor,
    SavedType,
    TargetRecord,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "infra" / "deployment" / "environment.contract.json"
NOW = datetime(2026, 8, 14, 6, 0, tzinfo=UTC)
ORIGIN = "https://dayjaview.vercel.app"

CODE_ROOTS = ("apps", "packages", "infra")
CODE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".mjs"})
SKIPPED_DIRECTORIES = frozenset({"node_modules", "dist", "build", ".venv", "__pycache__"})

# 코드가 읽지 않아 필수 선언을 내린 값. 쓰는 코드가 생기면 다시 필수로 올린다.
UNCONSUMED = ("APPLICATION_ENCRYPTION_KEY", "INFOSTOCK_SESSION_STATE_PATH", "REDIS_URL")


class FixedClock:
    def now(self) -> datetime:
        return NOW


def _code_files() -> Iterator[Path]:
    for root in CODE_ROOTS:
        for directory, subdirectories, filenames in os.walk(ROOT / root):
            subdirectories[:] = [name for name in subdirectories if name not in SKIPPED_DIRECTORIES]
            for filename in filenames:
                if Path(filename).suffix in CODE_SUFFIXES:
                    yield Path(directory) / filename


def test_every_production_required_variable_is_read_by_code() -> None:
    """production 필수로 선언한 값은 코드 어딘가가 실제로 읽어야 한다.

    선언만 있고 읽는 곳이 없으면 운영자가 값을 넣거나 회전해도 보호되는 대상이
    없다. F-23에서 `SESSION_SIGNING_SECRET`은 커서 서명 키로 연결했고, 쓰는 코드가
    없던 나머지는 필수 선언을 내렸다.
    """

    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    declared = {variable["name"]: variable["required"] for variable in contract["variables"]}
    required = {name for name, scope in declared.items() if scope["production"]}

    sources = [path.read_text(encoding="utf-8", errors="ignore") for path in _code_files()]
    assert len(sources) > 100  # 스캔이 비어 있으면 통과가 무의미하다

    unread = sorted(name for name in required if not any(name in text for text in sources))
    assert unread == []

    # 필수에서 내린 값은 여전히 코드가 안 읽는다 — 쓰는 코드 없이 되올리면 안 된다.
    for name in UNCONSUMED:
        assert declared[name]["production"] is False
        assert declared[name]["staging"] is False
        assert not any(name in text for text in sources)


def test_saved_cursor_survives_across_replicas_with_the_shared_secret() -> None:
    """같은 서명 키를 받은 두 인스턴스는 서로의 커서를 받아들인다.

    키가 프로세스마다 무작위이던 때는 한쪽이 발급한 커서를 다른 쪽이
    `InvalidCursor`로 거부해 관심 목록 2페이지부터 실패했다.
    """

    repository = InMemoryIdentityRepository()
    targets = tuple(
        TargetRecord(SavedType.THEME, f"thm_{index}", f"테마 {index}")
        for index in range(3)
    )
    catalog = InMemoryTargetCatalog(targets)

    def replica(cursor_secret: bytes | None) -> IdentityService:
        return IdentityService(
            repository=repository,
            oauth_provider=FixtureGoogleOAuthProvider(),
            target_catalog=catalog,
            clock=FixedClock(),
            cursor_secret=cursor_secret,
        )

    shared = b"shared-cursor-signing-secret-0123456789"
    first, second, unrelated = replica(shared), replica(shared), replica(None)

    started = first.begin_google_login("/today")
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    first._oauth_provider.register_code(  # type: ignore[attr-defined]
        "code-cursor",
        GoogleIdentity("sub-cursor", "커서 사용자"),
    )
    completion = first.complete_google_login(
        code="code-cursor",
        state=state,
        browser_nonce=started.browser_nonce,
    )
    for target in targets:
        first.save_item(
            session_token=completion.session_token,
            origin=ORIGIN,
            csrf_token=completion.csrf_token,
            csrf_cookie=completion.csrf_token,
            saved_type=SavedType.THEME,
            target_id=target.target_id,
        )

    page = first.list_saved_items(
        session_token=completion.session_token,
        saved_type=None,
        cursor=None,
        limit=2,
    )
    assert page.next_cursor is not None

    # 다른 인스턴스가 같은 커서로 다음 페이지를 읽는다.
    assert second.list_saved_items(
        session_token=completion.session_token,
        saved_type=None,
        cursor=page.next_cursor,
        limit=2,
    ).items

    # 키를 공유하지 않으면 여전히 거부된다 — 서명이 살아 있다는 뜻.
    with pytest.raises(InvalidCursor):
        unrelated.list_saved_items(
            session_token=completion.session_token,
            saved_type=None,
            cursor=page.next_cursor,
            limit=2,
        )


def test_shared_storage_assembly_refuses_to_start_without_the_secret() -> None:
    """공유 저장소를 쓰는데 서명 키가 없으면 조용히 넘어가지 않는다."""

    settings = ApiSettings(app_base_url=ORIGIN)
    environment = {"DATABASE_URL": "postgresql://localhost/dayjaview"}

    with pytest.raises(ValueError) as caught:
        create_production_app(
            environment,
            settings=settings,
            clock=FixedClock(),
            connect=lambda dsn: _FakeConnection(),
        )
    assert CURSOR_SIGNING_SECRET_ENV in str(caught.value)

    # 키를 넣으면 같은 조립이 통과한다.
    healthy = create_production_app(
        {**environment, CURSOR_SIGNING_SECRET_ENV: "shared-cursor-signing-secret-0123456789"},
        settings=settings,
        clock=FixedClock(),
        connect=lambda dsn: _FakeConnection(),
    )
    try:
        assert healthy.identity_store == POSTGRES_STORE
    finally:
        healthy.close()


class _FakeConnection:
    """Postgres 저장소 조립만 확인하면 되므로 연결은 열지 않는다."""

    def close(self) -> None:
        return None
