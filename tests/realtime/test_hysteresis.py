from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from packages.domain import CoverageStatus, LifecycleStatus
from packages.realtime import (
    HYSTERESIS_POLICY_V1,
    ActivationEvaluation,
    HysteresisDisposition,
    HysteresisInputConflict,
    HysteresisState,
    evaluate_hysteresis,
)

from ._factories import START


def evaluation(
    sequence: int,
    seconds: int,
    *,
    qualifies: bool | None,
    coverage: CoverageStatus = CoverageStatus.SUFFICIENT,
    input_id: str | None = None,
    market_closed: bool = False,
    candidate_expired: bool = False,
) -> ActivationEvaluation:
    return ActivationEvaluation(
        input_id=input_id or f"activation-{sequence}",
        sequence=sequence,
        evaluated_at=START + timedelta(seconds=seconds),
        policy_version=HYSTERESIS_POLICY_V1.version,
        coverage_status=coverage,
        qualifies=qualifies,
        market_closed=market_closed,
        candidate_expired=candidate_expired,
    )


def active_state() -> HysteresisState:
    state = HysteresisState.candidate(theme_id="thm_a")
    state = evaluate_hysteresis(state, evaluation(1, 0, qualifies=True)).state
    decision = evaluate_hysteresis(state, evaluation(2, 10, qualifies=True))
    assert decision.transition is not None
    assert decision.transition.to_status is LifecycleStatus.ACTIVE
    return decision.state


def weakening_state() -> HysteresisState:
    state = active_state()
    state = evaluate_hysteresis(state, evaluation(3, 11, qualifies=False)).state
    decision = evaluate_hysteresis(state, evaluation(4, 71, qualifies=False))
    assert decision.transition is not None
    assert decision.transition.to_status is LifecycleStatus.WEAKENING
    return decision.state


def test_candidate_must_hold_for_ten_seconds_before_activation() -> None:
    state = HysteresisState.candidate(theme_id="thm_a")
    first = evaluate_hysteresis(state, evaluation(1, 0, qualifies=True))
    almost = evaluate_hysteresis(first.state, evaluation(2, 9, qualifies=True))
    active = evaluate_hysteresis(almost.state, evaluation(3, 10, qualifies=True))

    assert first.transition is None and almost.transition is None
    assert active.disposition is HysteresisDisposition.TRANSITIONED
    assert active.transition is not None
    assert active.transition.to_status is LifecycleStatus.ACTIVE
    assert active.transition.policy_version == HYSTERESIS_POLICY_V1.version


def test_partial_coverage_never_counts_as_a_negative_continuity_sample() -> None:
    state = active_state()
    state = evaluate_hysteresis(state, evaluation(3, 11, qualifies=False)).state
    partial = evaluate_hysteresis(
        state,
        evaluation(
            4,
            60,
            qualifies=False,
            coverage=CoverageStatus.PARTIAL,
        ),
    )
    restarted = evaluate_hysteresis(
        partial.state,
        evaluation(5, 70, qualifies=False),
    )
    weakened = evaluate_hysteresis(
        restarted.state,
        evaluation(6, 130, qualifies=False),
    )

    assert partial.transition is None
    assert partial.state.below_since is None
    assert restarted.transition is None
    assert weakened.transition is not None
    assert weakened.transition.to_status is LifecycleStatus.WEAKENING


def test_weakening_recovers_with_same_lifecycle_within_ten_minutes() -> None:
    weakened = weakening_state()
    recovered = evaluate_hysteresis(
        weakened,
        evaluation(5, 670, qualifies=True),
    )

    assert recovered.transition is not None
    assert recovered.transition.from_status is LifecycleStatus.WEAKENING
    assert recovered.transition.to_status is LifecycleStatus.ACTIVE


def test_continuous_weakening_closes_after_ten_minutes() -> None:
    weakened = weakening_state()
    closed = evaluate_hysteresis(
        weakened,
        evaluation(5, 671, qualifies=False),
    )

    assert closed.transition is not None
    assert closed.transition.to_status is LifecycleStatus.CLOSED
    terminal = evaluate_hysteresis(
        closed.state,
        evaluation(6, 700, qualifies=True),
    )
    assert terminal.disposition is HysteresisDisposition.TERMINAL
    assert terminal.state.lifecycle_status is LifecycleStatus.CLOSED


def test_market_close_closes_active_and_discards_candidate() -> None:
    active = evaluate_hysteresis(
        active_state(),
        evaluation(3, 20, qualifies=None, market_closed=True),
    )
    candidate = evaluate_hysteresis(
        HysteresisState.candidate(theme_id="thm_b"),
        evaluation(1, 20, qualifies=None, market_closed=True),
    )

    assert active.transition is not None
    assert active.transition.to_status is LifecycleStatus.CLOSED
    assert candidate.transition is not None
    assert candidate.transition.to_status is LifecycleStatus.DISCARDED


def test_duplicate_stale_sequence_and_stale_time_are_deterministic_noops() -> None:
    state = HysteresisState.candidate(theme_id="thm_a")
    first_input = evaluation(1, 10, qualifies=True)
    first = evaluate_hysteresis(state, first_input)
    duplicate = evaluate_hysteresis(first.state, first_input)
    stale_sequence = evaluate_hysteresis(
        first.state,
        evaluation(
            1,
            11,
            qualifies=False,
            input_id="different-stale-sequence",
        ),
    )
    stale_time = evaluate_hysteresis(
        first.state,
        evaluation(2, 9, qualifies=False),
    )

    assert duplicate.disposition is HysteresisDisposition.DUPLICATE
    assert stale_sequence.disposition is HysteresisDisposition.STALE_SEQUENCE
    assert stale_time.disposition is HysteresisDisposition.STALE_TIME
    assert duplicate.state == stale_sequence.state == stale_time.state == first.state


def test_same_input_id_with_changed_payload_is_rejected() -> None:
    state = HysteresisState.candidate(theme_id="thm_a")
    original = evaluation(1, 0, qualifies=True, input_id="same-input")
    state = evaluate_hysteresis(state, original).state

    with pytest.raises(HysteresisInputConflict):
        evaluate_hysteresis(state, replace(original, qualifies=False))


def test_state_and_input_must_use_exact_versioned_policy() -> None:
    state = HysteresisState.candidate(theme_id="thm_a")
    mismatched = replace(evaluation(1, 0, qualifies=True), policy_version="future")

    with pytest.raises(ValueError, match="policy version"):
        evaluate_hysteresis(state, mismatched)


def test_hysteresis_state_round_trip_preserves_recovery_timers() -> None:
    state = weakening_state()
    restored = HysteresisState.from_dict(state.to_dict())

    assert restored == state
    recovered = evaluate_hysteresis(
        restored,
        evaluation(5, 670, qualifies=True),
    )
    assert recovered.transition is not None
    assert recovered.transition.to_status is LifecycleStatus.ACTIVE


def test_explicit_candidate_expiry_uses_allowed_discard_transition() -> None:
    state = HysteresisState.candidate(theme_id="thm_a")
    decision = evaluate_hysteresis(
        state,
        evaluation(1, 5, qualifies=False, candidate_expired=True),
    )

    assert decision.transition is not None
    assert decision.transition.to_status is LifecycleStatus.DISCARDED
