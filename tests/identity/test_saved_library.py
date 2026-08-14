from __future__ import annotations

import base64
from dataclasses import replace
from datetime import timedelta

import pytest

from apps.api import create_fixture_app
from packages.identity import (
    Availability,
    FeatureNotEntitled,
    GoogleIdentity,
    InvalidCursor,
    Role,
    SavedCurrentState,
    SavedType,
    TargetRecord,
)

from .helpers import MutableClock, mutation_arguments, service_login


def _theme(target_id: str, display_name: str) -> TargetRecord:
    return TargetRecord(SavedType.THEME, target_id, display_name)


def test_save_and_unsave_are_idempotent() -> None:
    clock = MutableClock()
    environment = create_fixture_app(
        clock=clock,
        targets=(_theme("thm_1", "반도체"),),
    )
    completion = service_login(
        environment,
        code="saved-idempotent",
        identity=GoogleIdentity("google-sub-saved", "저장 사용자"),
    )
    arguments = mutation_arguments(completion)

    first = environment.service.save_item(
        **arguments,
        saved_type=SavedType.THEME,
        target_id="thm_1",
    )
    clock.advance(timedelta(minutes=5))
    retried = environment.service.save_item(
        **arguments,
        saved_type=SavedType.THEME,
        target_id="thm_1",
    )
    assert retried.saved is True
    assert retried.saved_at == first.saved_at

    removed = environment.service.unsave_item(
        **arguments,
        saved_type=SavedType.THEME,
        target_id="thm_1",
    )
    retried_remove = environment.service.unsave_item(
        **arguments,
        saved_type=SavedType.THEME,
        target_id="thm_1",
    )
    assert removed.saved is False and removed.saved_at is None
    assert retried_remove.saved is False and retried_remove.saved_at is None


def test_owner_scope_blocks_cross_user_read_delete_and_cursor_reuse() -> None:
    environment = create_fixture_app(
        clock=MutableClock(),
        targets=(
            _theme("thm_a", "테마 A"),
            _theme("thm_b", "테마 B"),
        ),
    )
    first = service_login(
        environment,
        code="owner-1",
        identity=GoogleIdentity("google-owner-1", "사용자 1"),
    )
    second = service_login(
        environment,
        code="owner-2",
        identity=GoogleIdentity("google-owner-2", "사용자 2"),
    )
    for target_id in ("thm_a", "thm_b"):
        environment.service.save_item(
            **mutation_arguments(first),
            saved_type=SavedType.THEME,
            target_id=target_id,
        )

    first_page = environment.service.list_saved_items(
        session_token=first.session_token,
        saved_type=None,
        cursor=None,
        limit=1,
    )
    assert first_page.next_cursor is not None
    assert [item.target_id for item in first_page.items] == ["thm_a"]
    principal = environment.service.require_authenticated(first.session_token)
    encoded_payload = first_page.next_cursor.split(".", 1)[0]
    decoded_payload = base64.urlsafe_b64decode(
        encoded_payload + "=" * (-len(encoded_payload) % 4)
    ).decode("utf-8")
    assert principal.user.user_id not in decoded_payload

    second_page = environment.service.list_saved_items(
        session_token=first.session_token,
        saved_type=None,
        cursor=first_page.next_cursor,
        limit=1,
    )
    assert [item.target_id for item in second_page.items] == ["thm_b"]

    with pytest.raises(InvalidCursor):
        environment.service.list_saved_items(
            session_token=second.session_token,
            saved_type=None,
            cursor=first_page.next_cursor,
            limit=1,
        )

    environment.service.unsave_item(
        **mutation_arguments(second),
        saved_type=SavedType.THEME,
        target_id="thm_a",
    )
    owner_items = environment.service.list_saved_items(
        session_token=first.session_token,
        saved_type=None,
        cursor=None,
        limit=20,
    )
    other_items = environment.service.list_saved_items(
        session_token=second.session_token,
        saved_type=None,
        cursor=None,
        limit=20,
    )
    assert {item.target_id for item in owner_items.items} == {"thm_a", "thm_b"}
    assert other_items.items == ()


def test_unavailable_target_keeps_original_identity_without_silent_replacement() -> None:
    clock = MutableClock()
    state = SavedCurrentState(
        event_id="evt_current",
        event_state="ACTIVE",
        weighted_return=0.0342,
        data_status="LIVE",
        as_of=clock.now(),
    )
    target = TargetRecord(
        SavedType.THEME,
        "thm_584",
        "스페이스X(SpaceX)",
        current_state=state,
    )
    environment = create_fixture_app(clock=clock, targets=(target,))
    completion = service_login(
        environment,
        code="unavailable-code",
        identity=GoogleIdentity("google-unavailable", "저장 사용자"),
    )
    environment.service.save_item(
        **mutation_arguments(completion),
        saved_type=SavedType.THEME,
        target_id="thm_584",
    )
    environment.target_catalog.put(
        replace(
            target,
            availability=Availability.UNAVAILABLE,
            unavailable_reason="RESOURCE_NOT_FOUND",
            current_state=None,
        )
    )

    page = environment.service.list_saved_items(
        session_token=completion.session_token,
        saved_type=SavedType.THEME,
        cursor=None,
        limit=20,
    )
    assert len(page.items) == 1
    item = page.items[0]
    assert item.target_id == "thm_584"
    assert item.display_name == "스페이스X(SpaceX)"
    assert item.availability is Availability.UNAVAILABLE
    assert item.unavailable_reason == "RESOURCE_NOT_FOUND"
    assert item.current_state is None


def test_restricted_event_save_requires_entitlement_and_listing_rechecks_it() -> None:
    event = TargetRecord(
        SavedType.EVENT,
        "evt_pilot",
        "파일럿 과거 이벤트",
        required_role=Role.HISTORICAL_PILOT,
    )
    environment = create_fixture_app(clock=MutableClock(), targets=(event,))
    completion = service_login(
        environment,
        code="pilot-code",
        identity=GoogleIdentity("google-pilot", "파일럿 사용자"),
    )
    arguments = mutation_arguments(completion)
    with pytest.raises(FeatureNotEntitled):
        environment.service.save_item(
            **arguments,
            saved_type=SavedType.EVENT,
            target_id="evt_pilot",
        )

    principal = environment.service.require_authenticated(completion.session_token)
    environment.repository.add_role(principal.user.user_id, Role.HISTORICAL_PILOT)
    saved = environment.service.save_item(
        **arguments,
        saved_type=SavedType.EVENT,
        target_id="evt_pilot",
    )
    assert saved.saved is True


def test_saved_type_filter_does_not_change_shared_target_state() -> None:
    environment = create_fixture_app(
        clock=MutableClock(),
        targets=(
            _theme("thm_filter", "필터 테마"),
            TargetRecord(SavedType.STOCK, "stk_filter", "필터 종목"),
        ),
    )
    completion = service_login(
        environment,
        code="filter-code",
        identity=GoogleIdentity("google-filter", "필터 사용자"),
    )
    for saved_type, target_id in (
        (SavedType.THEME, "thm_filter"),
        (SavedType.STOCK, "stk_filter"),
    ):
        environment.service.save_item(
            **mutation_arguments(completion),
            saved_type=saved_type,
            target_id=target_id,
        )
    themes = environment.service.list_saved_items(
        session_token=completion.session_token,
        saved_type=SavedType.THEME,
        cursor=None,
        limit=20,
    )
    assert [item.saved_type for item in themes.items] == [SavedType.THEME]
    assert environment.target_catalog.get_target(SavedType.STOCK, "stk_filter") is not None
