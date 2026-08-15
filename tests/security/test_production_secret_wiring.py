"""F-23 finding: 배포 env 계약과 코드가 어긋나 있다.

`infra/deployment/environment.contract.json`이 staging·production 필수로
선언한 값 중 코드가 읽지 않는 것이 있고, 반대로 코드가 필요로 하는 커서
서명 키는 계약에 없어 프로세스마다 무작위로 생긴다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from apps.api.config import ApiSettings
from apps.api.production import FIXTURE_MODE, MEMORY_STORE, create_production_app
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

# 계약이 production 필수로 선언했지만 코드가 한 번도 읽지 않는 값.
UNCONSUMED_SECRETS = ("SESSION_SIGNING_SECRET", "APPLICATION_ENCRYPTION_KEY")


class FixedClock:
    def now(self) -> datetime:
        return NOW


def test_contract_declares_secrets_the_assembly_never_reads() -> None:
    """선언은 production 필수인데, 값을 바꿔도 조립 결과가 달라지지 않는다.

    운영자가 이 값을 넣거나 회전해도 보호되는 대상이 없다는 뜻이다.
    """

    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    declared = {variable["name"]: variable["required"] for variable in contract["variables"]}
    for name in UNCONSUMED_SECRETS:
        assert declared[name]["production"] is True

    settings = ApiSettings(app_base_url="https://dayjaview.vercel.app")
    without = create_production_app({}, settings=settings, clock=FixedClock())
    with_secrets = create_production_app(
        {name: "rotated-secret-value" for name in UNCONSUMED_SECRETS},
        settings=settings,
        clock=FixedClock(),
    )
    try:
        # 두 조립이 완전히 같다 = 선언된 secret이 어디에도 연결되어 있지 않다.
        assert without.google_mode == with_secrets.google_mode == FIXTURE_MODE
        assert without.identity_store == with_secrets.identity_store == MEMORY_STORE
    finally:
        without.close()
        with_secrets.close()


def test_saved_cursor_secret_is_per_process_and_breaks_across_replicas() -> None:
    """커서 서명 키가 프로세스마다 무작위라 replica가 2개면 페이지 넘김이 깨진다.

    같은 저장소를 보는 두 IdentityService(=API 인스턴스 2개)를 만들면, 한쪽이
    발급한 cursor를 다른 쪽이 거부한다. fail-closed라 정보가 새지는 않지만
    F-25에서 API를 2개 이상 띄우면 관심 목록 2페이지부터 실패한다.
    """

    repository = InMemoryIdentityRepository()
    targets = tuple(
        TargetRecord(SavedType.THEME, f"thm_{index}", f"테마 {index}")
        for index in range(3)
    )
    catalog = InMemoryTargetCatalog(targets)

    def replica() -> IdentityService:
        return IdentityService(
            repository=repository,
            oauth_provider=FixtureGoogleOAuthProvider(),
            target_catalog=catalog,
            clock=FixedClock(),
        )

    first, second = replica(), replica()

    started = first.begin_google_login("/today")
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    # provider는 replica마다 다르므로 code 등록은 first 것에 한다.
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
            origin="https://dayjaview.vercel.app",
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

    # 같은 세션·같은 저장소인데 다른 인스턴스에서는 커서가 죽는다.
    with pytest.raises(InvalidCursor):
        second.list_saved_items(
            session_token=completion.session_token,
            saved_type=None,
            cursor=page.next_cursor,
            limit=2,
        )

    # 발급한 인스턴스에서는 정상 동작한다 — 키가 인스턴스에 묶여 있다는 증거.
    assert first.list_saved_items(
        session_token=completion.session_token,
        saved_type=None,
        cursor=page.next_cursor,
        limit=2,
    ).items
