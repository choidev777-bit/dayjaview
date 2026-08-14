from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from packages.infostock import (
    CommittedFixturePolicy,
    DataRightsBlockedError,
    FixtureValidationError,
    InfostockAccessPolicy,
    parse_fixture_payload,
)
from packages.infostock.hashing import fixture_bundle_hash, sha256_json

from .generate_fixture import build_fixture_payload
from .support import rehash_fixture

Mutation = Callable[[dict[str, Any]], None]


def _malformed(payload: dict[str, Any]) -> None:
    payload["detailSnapshots"][0]["rawPayload"]["data"]["stockItems"][0][
        "code"
    ] = "123"
    rehash_fixture(payload, 0)


def _partial(payload: dict[str, Any]) -> None:
    payload["detailSnapshots"].pop()
    payload["bundleHash"] = fixture_bundle_hash(payload)


def _duplicate(payload: dict[str, Any]) -> None:
    index_snapshot = payload["indexSnapshot"]
    items = index_snapshot["rawPayload"]["data"]["items"]
    items.append(copy.deepcopy(items[0]))
    index_snapshot["rawHash"] = sha256_json(index_snapshot["rawPayload"])
    payload["bundleHash"] = fixture_bundle_hash(payload)


def _conflicting(payload: dict[str, Any]) -> None:
    payload["detailSnapshots"][1]["rawPayload"]["data"]["theme"][
        "name"
    ] = "index와 다른 이름"
    rehash_fixture(payload, 1)


def _incomplete(payload: dict[str, Any]) -> None:
    payload["detailSnapshots"][0]["isComplete"] = False
    payload["bundleHash"] = fixture_bundle_hash(payload)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (_malformed, "MALFORMED_FIXTURE"),
        (_partial, "PARTIAL_FIXTURE"),
        (_duplicate, "DUPLICATE_THEME"),
        (_conflicting, "CONFLICTING_THEME"),
        (_incomplete, "INCOMPLETE_SNAPSHOT"),
    ],
)
def test_invalid_fixture_has_explicit_failure(
    mutation: Mutation, expected_code: str
) -> None:
    payload = build_fixture_payload()
    mutation(payload)

    with pytest.raises(FixtureValidationError) as raised:
        parse_fixture_payload(payload)

    assert raised.value.code == expected_code


def test_source_duplicate_history_is_preserved_and_reported() -> None:
    payload = build_fixture_payload()
    history = payload["detailSnapshots"][0]["rawPayload"]["data"]["items"]
    duplicate = copy.deepcopy(history[0])
    duplicate["B2Bseq"] = None
    history[0]["B2Bseq"] = None
    history.append(duplicate)
    rehash_fixture(payload, 0)

    bundle = parse_fixture_payload(payload)
    first_theme = bundle.details[0]

    assert len(first_theme.history) == 2
    assert first_theme.history[0].quality_status == "DUPLICATE_GROUP_HEAD"
    assert first_theme.history[1].quality_status == "SOURCE_DUPLICATE"
    assert bundle.quality_summary.duplicate_history_count == 1


def test_stock_name_variant_is_not_rejected_or_retroactively_normalized() -> None:
    payload = build_fixture_payload()
    payload["detailSnapshots"][1]["rawPayload"]["data"]["stockItems"][0][
        "code"
    ] = "100001"
    payload["detailSnapshots"][1]["rawPayload"]["data"]["stockItems"][0][
        "name"
    ] = "과거·현재 이름 변형"
    rehash_fixture(payload, 1)

    bundle = parse_fixture_payload(payload)

    assert bundle.quality_summary.stock_name_variant_count == 1
    assert any(issue.issue_code == "STOCK_NAME_VARIANT" for issue in bundle.quality_issues)


def test_bundle_hash_detects_untracked_mutation() -> None:
    payload = build_fixture_payload()
    payload["dataset"] = "quietly-mutated"

    with pytest.raises(FixtureValidationError) as raised:
        parse_fixture_payload(payload)

    assert raised.value.code == "HASH_MISMATCH"


def test_ignored_local_import_path_is_not_a_committed_fixture() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    ignored_manifest = repository_root / "data/infostock/import/manifest.json"
    policy = CommittedFixturePolicy(repository_root)

    with pytest.raises(FixtureValidationError) as raised:
        policy.validate(ignored_manifest)

    assert raised.value.code == "UNAPPROVED_FIXTURE_PATH"


def test_production_collection_serving_and_daily_auth_are_fail_closed() -> None:
    payload = build_fixture_payload()
    payload["rightsScope"] = "PRODUCTION_APPROVED"
    payload["bundleHash"] = fixture_bundle_hash(payload)

    with pytest.raises(DataRightsBlockedError) as import_error:
        parse_fixture_payload(payload)
    assert import_error.value.blocker == "B-DATA-RIGHTS"

    with pytest.raises(DataRightsBlockedError) as collection_error:
        InfostockAccessPolicy.require_production_collection()
    assert collection_error.value.blocker == "B-DATA-RIGHTS"

    with pytest.raises(DataRightsBlockedError) as serving_error:
        InfostockAccessPolicy.require_production_serving()
    assert serving_error.value.blocker == "B-DATA-RIGHTS"

    with pytest.raises(DataRightsBlockedError) as auth_error:
        InfostockAccessPolicy.require_daily_browser_collection()
    assert auth_error.value.blocker == "B-INFOSTOCK-AUTH"

    with pytest.raises(DataRightsBlockedError) as rights_error:
        InfostockAccessPolicy.require_daily_browser_collection(
            auth_verified=True, rights_verified=False
        )
    assert rights_error.value.blocker == "B-DATA-RIGHTS"
