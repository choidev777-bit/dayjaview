from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.validate_contracts import (
    FIXTURES,
    REQUIRED_ASYNC_MESSAGES,
    REQUIRED_HTTP_SURFACE,
    SHARED_SCHEMA_PATH,
    ContractValidationError,
    load_contracts,
    run_validation,
    validate_instance,
    validate_invariants,
)


def read_fixture(relative_path: str) -> dict:
    return json.loads((FIXTURES / relative_path).read_text(encoding="utf-8"))


def test_complete_contract_suite_passes() -> None:
    counts = run_validation()

    assert counts["http_operations"] == 31
    assert counts["websocket_messages"] == 9
    assert counts["fixtures"] == 45
    assert counts["prose_json_examples"] >= 20


def test_http_and_websocket_surface_is_exact() -> None:
    _, _, openapi, asyncapi = load_contracts()
    actual_paths = set(openapi["paths"])
    actual_messages = set(asyncapi["components"]["messages"])

    assert actual_paths == set(REQUIRED_HTTP_SURFACE)
    assert actual_messages == REQUIRED_ASYNC_MESSAGES


def test_public_theme_detail_rejects_operator_review_status() -> None:
    shared, _, _, _ = load_contracts()
    public_detail = read_fixture("event/unmatched.json")
    public_detail["data"]["reviewStatus"] = "PENDING"

    with pytest.raises(ContractValidationError, match="reviewStatus"):
        validate_instance(
            public_detail,
            "ThemeDetailResponse",
            shared=shared,
            label="drifted-public-detail",
        )


def test_operator_review_projection_retains_review_status() -> None:
    shared, _, _, _ = load_contracts()
    operator_reviews = read_fixture("operator/reviews.pending.json")
    validate_instance(
        operator_reviews,
        "OperatorReviewListResponse",
        shared=shared,
        label="operator-review",
    )
    assert operator_reviews["data"]["items"][0]["reviewStatus"] == "PENDING"


def test_saved_return_uses_decimal_convention() -> None:
    saved = read_fixture("saved/library.json")
    weighted_return = saved["data"]["items"][0]["currentState"]["weightedReturn"]

    assert weighted_return == pytest.approx(0.0342)


def test_schema_drift_fails_deterministically() -> None:
    shared = json.loads(SHARED_SCHEMA_PATH.read_text(encoding="utf-8"))
    drifted = read_fixture("rankings/live.json")
    drifted["data"]["items"][0]["unexpectedScore"] = 92

    with pytest.raises(ContractValidationError, match="unexpectedScore"):
        validate_instance(
            drifted,
            "RankingResponse",
            shared=shared,
            label="drifted-ranking",
        )


def test_time_invariant_drift_fails_deterministically() -> None:
    drifted = copy.deepcopy(read_fixture("rankings/live.json"))
    drifted["meta"]["marketContext"]["asOf"] = "2026-08-14T01:18:24.000Z"

    with pytest.raises(ContractValidationError, match="asOf"):
        validate_invariants(drifted, label="drifted-time")


def test_coverage_invariant_drift_fails_deterministically() -> None:
    drifted = copy.deepcopy(read_fixture("rankings/live.json"))
    drifted["data"]["items"][0]["coverage"]["core"]["observedCount"] = 22

    with pytest.raises(ContractValidationError, match="observedCount"):
        validate_invariants(drifted, label="drifted-coverage")


def test_reconnect_accepts_sequence_reset_only_for_new_stream() -> None:
    previous = read_fixture("realtime/ranking-snapshot.json")
    reconnect = read_fixture("realtime/reconnect-full-snapshot.json")
    stale_same_stream = copy.deepcopy(reconnect)
    stale_same_stream["streamId"] = previous["streamId"]

    def should_apply(old: dict, new: dict) -> bool:
        return new["streamId"] != old["streamId"] or new["sequence"] > old["sequence"]

    assert should_apply(previous, reconnect)
    assert not should_apply(previous, stale_same_stream)


def test_historical_sort_cannot_use_future_outcome() -> None:
    _, _, openapi, _ = load_contracts()
    parameters = openapi["paths"]["/v1/events/{eventId}/similar-events"]["get"][
        "parameters"
    ]
    sort_parameter = next(item for item in parameters if item.get("name") == "sort")

    assert sort_parameter["schema"]["enum"] == ["relevance", "eventDate"]


def test_contract_artifacts_contain_no_secret_files() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    forbidden_names = {".env", ".env.local", "cookies.json", "storage-state.json"}

    assert not any(
        path.name in forbidden_names
        for path in (repository_root / "contracts").rglob("*")
    )
