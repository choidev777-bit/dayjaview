from __future__ import annotations

import itertools

import pytest

from packages.domain import (
    STATE_TRANSITION_VERSION,
    InvalidStateTransition,
    LifecycleStatus,
    ReconciliationStatus,
    ReviewStatus,
    allowed_lifecycle_targets,
    transition_lifecycle,
    transition_reconciliation,
    transition_review,
)

ALLOWED_LIFECYCLE = {
    (LifecycleStatus.CANDIDATE, LifecycleStatus.ACTIVE),
    (LifecycleStatus.CANDIDATE, LifecycleStatus.DISCARDED),
    (LifecycleStatus.ACTIVE, LifecycleStatus.WEAKENING),
    (LifecycleStatus.ACTIVE, LifecycleStatus.CLOSED),
    (LifecycleStatus.WEAKENING, LifecycleStatus.ACTIVE),
    (LifecycleStatus.WEAKENING, LifecycleStatus.CLOSED),
}


def test_lifecycle_transition_property_matches_contract_exactly() -> None:
    for current, target in itertools.product(LifecycleStatus, repeat=2):
        if (current, target) in ALLOWED_LIFECYCLE:
            transition = transition_lifecycle(current, target)
            assert transition.from_status == current.value
            assert transition.to_status == target.value
            assert transition.policy_version == STATE_TRANSITION_VERSION
        else:
            with pytest.raises(InvalidStateTransition):
                transition_lifecycle(current, target)


def test_terminal_lifecycle_states_have_no_targets() -> None:
    assert allowed_lifecycle_targets(LifecycleStatus.CLOSED) == ()
    assert allowed_lifecycle_targets(LifecycleStatus.DISCARDED) == ()


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ReconciliationStatus.PENDING, ReconciliationStatus.MATCHED),
        (ReconciliationStatus.PENDING, ReconciliationStatus.UNMATCHED),
        (ReconciliationStatus.UNMATCHED, ReconciliationStatus.MATCHED),
    ],
)
def test_reconciliation_allows_only_forward_contract_transitions(
    current: ReconciliationStatus,
    target: ReconciliationStatus,
) -> None:
    assert transition_reconciliation(current, target).to_status == target.value


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ReconciliationStatus.MATCHED, ReconciliationStatus.PENDING),
        (ReconciliationStatus.MATCHED, ReconciliationStatus.UNMATCHED),
        (ReconciliationStatus.UNMATCHED, ReconciliationStatus.PENDING),
    ],
)
def test_reconciliation_reversal_is_rejected(
    current: ReconciliationStatus,
    target: ReconciliationStatus,
) -> None:
    with pytest.raises(InvalidStateTransition):
        transition_reconciliation(current, target)


def test_review_null_is_distinct_from_pending_and_resolved() -> None:
    created = transition_review(None, ReviewStatus.PENDING)
    resolved = transition_review(ReviewStatus.PENDING, ReviewStatus.RESOLVED)

    assert created.from_status is None
    assert resolved.from_status == "PENDING"
    with pytest.raises(InvalidStateTransition):
        transition_review(None, ReviewStatus.RESOLVED)
    with pytest.raises(InvalidStateTransition):
        transition_review(ReviewStatus.RESOLVED, ReviewStatus.PENDING)
