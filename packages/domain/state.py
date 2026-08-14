from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

STATE_TRANSITION_VERSION = "event-state-2026.08.1"


class LifecycleStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    WEAKENING = "WEAKENING"
    CLOSED = "CLOSED"
    DISCARDED = "DISCARDED"


class ReconciliationStatus(StrEnum):
    PENDING = "PENDING"
    MATCHED = "MATCHED"
    UNMATCHED = "UNMATCHED"


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"


class StateAxis(StrEnum):
    LIFECYCLE = "lifecycleStatus"
    RECONCILIATION = "reconciliationStatus"
    REVIEW = "reviewStatus"


class InvalidStateTransition(ValueError):
    pass


_LIFECYCLE_TARGETS: dict[LifecycleStatus, tuple[LifecycleStatus, ...]] = {
    LifecycleStatus.CANDIDATE: (
        LifecycleStatus.ACTIVE,
        LifecycleStatus.DISCARDED,
    ),
    LifecycleStatus.ACTIVE: (
        LifecycleStatus.WEAKENING,
        LifecycleStatus.CLOSED,
    ),
    LifecycleStatus.WEAKENING: (
        LifecycleStatus.ACTIVE,
        LifecycleStatus.CLOSED,
    ),
    LifecycleStatus.CLOSED: (),
    LifecycleStatus.DISCARDED: (),
}

_RECONCILIATION_TARGETS: dict[
    ReconciliationStatus, tuple[ReconciliationStatus, ...]
] = {
    ReconciliationStatus.PENDING: (
        ReconciliationStatus.MATCHED,
        ReconciliationStatus.UNMATCHED,
    ),
    ReconciliationStatus.MATCHED: (),
    ReconciliationStatus.UNMATCHED: (ReconciliationStatus.MATCHED,),
}

_REVIEW_TARGETS: dict[ReviewStatus | None, tuple[ReviewStatus, ...]] = {
    None: (ReviewStatus.PENDING,),
    ReviewStatus.PENDING: (ReviewStatus.RESOLVED,),
    ReviewStatus.RESOLVED: (),
}

@dataclass(frozen=True, slots=True)
class StateTransition:
    axis: StateAxis
    from_status: str | None
    to_status: str
    policy_version: str

    def __post_init__(self) -> None:
        if not self.policy_version:
            raise ValueError("state transition policy version은 비어 있을 수 없습니다")


def _require_transition[StatusT: StrEnum](
    *,
    axis: StateAxis,
    current: StatusT | None,
    target: StatusT,
    allowed_targets: tuple[StatusT, ...],
    policy_version: str,
) -> StateTransition:
    if target not in allowed_targets:
        current_value = None if current is None else current.value
        raise InvalidStateTransition(
            f"허용되지 않은 {axis.value} 전이입니다: {current_value} -> {target.value}"
        )
    return StateTransition(
        axis=axis,
        from_status=None if current is None else current.value,
        to_status=target.value,
        policy_version=policy_version,
    )


def allowed_lifecycle_targets(
    current: LifecycleStatus,
) -> tuple[LifecycleStatus, ...]:
    return _LIFECYCLE_TARGETS[current]


def transition_lifecycle(
    current: LifecycleStatus,
    target: LifecycleStatus,
    *,
    policy_version: str = STATE_TRANSITION_VERSION,
) -> StateTransition:
    return _require_transition(
        axis=StateAxis.LIFECYCLE,
        current=current,
        target=target,
        allowed_targets=_LIFECYCLE_TARGETS[current],
        policy_version=policy_version,
    )


def transition_reconciliation(
    current: ReconciliationStatus,
    target: ReconciliationStatus,
    *,
    policy_version: str = STATE_TRANSITION_VERSION,
) -> StateTransition:
    return _require_transition(
        axis=StateAxis.RECONCILIATION,
        current=current,
        target=target,
        allowed_targets=_RECONCILIATION_TARGETS[current],
        policy_version=policy_version,
    )


def transition_review(
    current: ReviewStatus | None,
    target: ReviewStatus,
    *,
    policy_version: str = STATE_TRANSITION_VERSION,
) -> StateTransition:
    return _require_transition(
        axis=StateAxis.REVIEW,
        current=current,
        target=target,
        allowed_targets=_REVIEW_TARGETS[current],
        policy_version=policy_version,
    )
