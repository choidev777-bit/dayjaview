"""Versioned lifecycle hysteresis reducer for duplicate and partial inputs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from packages.domain import CoverageStatus, LifecycleStatus, transition_lifecycle

HYSTERESIS_POLICY_VERSION = "theme-hysteresis-2026.08.1"


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}에는 timezone 정보가 필요합니다")


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


@dataclass(frozen=True, slots=True)
class HysteresisPolicy:
    version: str
    maturity: str
    activate_after: timedelta
    weaken_after: timedelta
    recover_within: timedelta
    close_after: timedelta

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("hysteresis policy version은 비어 있을 수 없습니다")
        if not self.maturity.strip():
            raise ValueError("hysteresis policy maturity는 비어 있을 수 없습니다")
        for field_name, value in (
            ("activate_after", self.activate_after),
            ("weaken_after", self.weaken_after),
            ("recover_within", self.recover_within),
            ("close_after", self.close_after),
        ):
            if value <= timedelta(0):
                raise ValueError(f"{field_name}는 0보다 커야 합니다")


HYSTERESIS_POLICY_V1 = HysteresisPolicy(
    version=HYSTERESIS_POLICY_VERSION,
    maturity="BACKTEST_PENDING",
    activate_after=timedelta(seconds=10),
    weaken_after=timedelta(seconds=60),
    recover_within=timedelta(minutes=10),
    close_after=timedelta(minutes=10),
)


@dataclass(frozen=True, slots=True)
class ActivationEvaluation:
    input_id: str
    sequence: int
    evaluated_at: datetime
    policy_version: str
    coverage_status: CoverageStatus
    qualifies: bool | None
    market_closed: bool = False
    candidate_expired: bool = False

    def __post_init__(self) -> None:
        if not self.input_id.strip():
            raise ValueError("input_id는 비어 있을 수 없습니다")
        if self.sequence < 1:
            raise ValueError("activation sequence는 1 이상이어야 합니다")
        if not self.policy_version.strip():
            raise ValueError("policy_version은 비어 있을 수 없습니다")
        _require_aware(self.evaluated_at, "evaluated_at")
        if self.market_closed and self.candidate_expired:
            raise ValueError(
                "market_closed와 candidate_expired를 함께 지정할 수 없습니다"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "inputId": self.input_id,
            "sequence": self.sequence,
            "evaluatedAt": self.evaluated_at.isoformat(),
            "policyVersion": self.policy_version,
            "coverageStatus": self.coverage_status.value,
            "qualifies": self.qualifies,
            "marketClosed": self.market_closed,
            "candidateExpired": self.candidate_expired,
        }

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class HysteresisState:
    theme_id: str
    lifecycle_status: LifecycleStatus
    policy_version: str
    last_sequence: int
    last_evaluated_at: datetime | None = None
    last_input_id: str | None = None
    last_input_fingerprint: str | None = None
    candidate_since: datetime | None = None
    below_since: datetime | None = None
    weakening_since: datetime | None = None
    weakening_below_since: datetime | None = None

    def __post_init__(self) -> None:
        if not self.theme_id.strip():
            raise ValueError("theme_id는 비어 있을 수 없습니다")
        if not self.policy_version.strip():
            raise ValueError("policy_version은 비어 있을 수 없습니다")
        if self.last_sequence < 0:
            raise ValueError("last_sequence는 음수일 수 없습니다")
        for field_name, value in (
            ("last_evaluated_at", self.last_evaluated_at),
            ("candidate_since", self.candidate_since),
            ("below_since", self.below_since),
            ("weakening_since", self.weakening_since),
            ("weakening_below_since", self.weakening_below_since),
        ):
            if value is not None:
                _require_aware(value, field_name)

    @classmethod
    def candidate(
        cls,
        *,
        theme_id: str,
        policy: HysteresisPolicy = HYSTERESIS_POLICY_V1,
    ) -> HysteresisState:
        return cls(
            theme_id=theme_id,
            lifecycle_status=LifecycleStatus.CANDIDATE,
            policy_version=policy.version,
            last_sequence=0,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "themeId": self.theme_id,
            "lifecycleStatus": self.lifecycle_status.value,
            "policyVersion": self.policy_version,
            "lastSequence": self.last_sequence,
            "lastEvaluatedAt": _time(self.last_evaluated_at),
            "lastInputId": self.last_input_id,
            "lastInputFingerprint": self.last_input_fingerprint,
            "candidateSince": _time(self.candidate_since),
            "belowSince": _time(self.below_since),
            "weakeningSince": _time(self.weakening_since),
            "weakeningBelowSince": _time(self.weakening_below_since),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> HysteresisState:
        def parse(field: str) -> datetime | None:
            raw = value.get(field)
            return None if raw is None else datetime.fromisoformat(str(raw))

        return cls(
            theme_id=str(value["themeId"]),
            lifecycle_status=LifecycleStatus(str(value["lifecycleStatus"])),
            policy_version=str(value["policyVersion"]),
            last_sequence=int(str(value["lastSequence"])),
            last_evaluated_at=parse("lastEvaluatedAt"),
            last_input_id=(
                None if value.get("lastInputId") is None else str(value["lastInputId"])
            ),
            last_input_fingerprint=(
                None
                if value.get("lastInputFingerprint") is None
                else str(value["lastInputFingerprint"])
            ),
            candidate_since=parse("candidateSince"),
            below_since=parse("belowSince"),
            weakening_since=parse("weakeningSince"),
            weakening_below_since=parse("weakeningBelowSince"),
        )


@dataclass(frozen=True, slots=True)
class HysteresisTransition:
    from_status: LifecycleStatus
    to_status: LifecycleStatus
    policy_version: str
    input_id: str
    reason: str


class HysteresisDisposition(StrEnum):
    TRANSITIONED = "TRANSITIONED"
    NO_TRANSITION = "NO_TRANSITION"
    DUPLICATE = "DUPLICATE"
    STALE_SEQUENCE = "STALE_SEQUENCE"
    STALE_TIME = "STALE_TIME"
    TERMINAL = "TERMINAL"


@dataclass(frozen=True, slots=True)
class HysteresisDecision:
    state: HysteresisState
    disposition: HysteresisDisposition
    transition: HysteresisTransition | None


class HysteresisInputConflict(ValueError):
    pass


def evaluate_hysteresis(
    state: HysteresisState,
    evaluation: ActivationEvaluation,
    *,
    policy: HysteresisPolicy = HYSTERESIS_POLICY_V1,
) -> HysteresisDecision:
    """Reduce one ordered input without treating missing Coverage as false."""

    if state.policy_version != policy.version:
        raise ValueError("hysteresis state와 policy version이 일치하지 않습니다")
    if evaluation.policy_version != policy.version:
        raise ValueError("activation input과 policy version이 일치하지 않습니다")
    if evaluation.input_id == state.last_input_id:
        if evaluation.fingerprint != state.last_input_fingerprint:
            raise HysteresisInputConflict(
                "같은 input_id에 서로 다른 hysteresis input이 있습니다"
            )
        return HysteresisDecision(
            state=state,
            disposition=HysteresisDisposition.DUPLICATE,
            transition=None,
        )
    if evaluation.sequence <= state.last_sequence:
        return HysteresisDecision(
            state=state,
            disposition=HysteresisDisposition.STALE_SEQUENCE,
            transition=None,
        )
    if (
        state.last_evaluated_at is not None
        and evaluation.evaluated_at < state.last_evaluated_at
    ):
        return HysteresisDecision(
            state=state,
            disposition=HysteresisDisposition.STALE_TIME,
            transition=None,
        )

    advanced = replace(
        state,
        last_sequence=evaluation.sequence,
        last_evaluated_at=evaluation.evaluated_at,
        last_input_id=evaluation.input_id,
        last_input_fingerprint=evaluation.fingerprint,
    )
    if state.lifecycle_status in (LifecycleStatus.CLOSED, LifecycleStatus.DISCARDED):
        return HysteresisDecision(
            state=advanced,
            disposition=HysteresisDisposition.TERMINAL,
            transition=None,
        )
    if evaluation.market_closed:
        target = (
            LifecycleStatus.DISCARDED
            if state.lifecycle_status is LifecycleStatus.CANDIDATE
            else LifecycleStatus.CLOSED
        )
        return _transition(advanced, target, evaluation, "장 마감")
    if (
        evaluation.candidate_expired
        and state.lifecycle_status is LifecycleStatus.CANDIDATE
    ):
        return _transition(
            advanced,
            LifecycleStatus.DISCARDED,
            evaluation,
            "후보 신호 소멸",
        )

    qualifies = (
        evaluation.qualifies
        if evaluation.coverage_status is CoverageStatus.SUFFICIENT
        else None
    )
    if qualifies is None:
        if state.lifecycle_status is LifecycleStatus.CANDIDATE:
            advanced = replace(advanced, candidate_since=None)
        elif state.lifecycle_status is LifecycleStatus.ACTIVE:
            advanced = replace(advanced, below_since=None)
        elif state.lifecycle_status is LifecycleStatus.WEAKENING:
            advanced = replace(advanced, weakening_below_since=None)
        return HysteresisDecision(
            state=advanced,
            disposition=HysteresisDisposition.NO_TRANSITION,
            transition=None,
        )

    now = evaluation.evaluated_at
    if state.lifecycle_status is LifecycleStatus.CANDIDATE:
        if not qualifies:
            advanced = replace(advanced, candidate_since=None)
        else:
            candidate_since = advanced.candidate_since or now
            advanced = replace(advanced, candidate_since=candidate_since)
            if now - candidate_since >= policy.activate_after:
                return _transition(
                    advanced,
                    LifecycleStatus.ACTIVE,
                    evaluation,
                    "활성 기준 지속 충족",
                )
    elif state.lifecycle_status is LifecycleStatus.ACTIVE:
        if qualifies:
            advanced = replace(advanced, below_since=None)
        else:
            below_since = advanced.below_since or now
            advanced = replace(advanced, below_since=below_since)
            if now - below_since >= policy.weaken_after:
                transitioned = _transition(
                    advanced,
                    LifecycleStatus.WEAKENING,
                    evaluation,
                    "활성 기준 미달 지속",
                )
                weakened = replace(
                    transitioned.state,
                    weakening_since=now,
                    weakening_below_since=now,
                    below_since=None,
                )
                return replace(transitioned, state=weakened)
    elif state.lifecycle_status is LifecycleStatus.WEAKENING:
        weakening_since = advanced.weakening_since
        if weakening_since is None:
            raise ValueError("WEAKENING state에 weakening_since가 없습니다")
        if qualifies:
            if now - weakening_since <= policy.recover_within:
                return _transition(
                    advanced,
                    LifecycleStatus.ACTIVE,
                    evaluation,
                    "같은 Event 재강화",
                )
            return _transition(
                advanced,
                LifecycleStatus.CLOSED,
                evaluation,
                "재강화 허용 시간 경과",
            )
        weakening_below_since = advanced.weakening_below_since or now
        advanced = replace(
            advanced,
            weakening_below_since=weakening_below_since,
        )
        if now - weakening_below_since >= policy.close_after:
            return _transition(
                advanced,
                LifecycleStatus.CLOSED,
                evaluation,
                "약화 지속",
            )

    return HysteresisDecision(
        state=advanced,
        disposition=HysteresisDisposition.NO_TRANSITION,
        transition=None,
    )


def _transition(
    state: HysteresisState,
    target: LifecycleStatus,
    evaluation: ActivationEvaluation,
    reason: str,
) -> HysteresisDecision:
    transition_lifecycle(
        state.lifecycle_status,
        target,
        policy_version=state.policy_version,
    )
    transition = HysteresisTransition(
        from_status=state.lifecycle_status,
        to_status=target,
        policy_version=state.policy_version,
        input_id=evaluation.input_id,
        reason=reason,
    )
    cleared = replace(
        state,
        lifecycle_status=target,
        candidate_since=None,
        below_since=None,
        weakening_since=None,
        weakening_below_since=None,
    )
    return HysteresisDecision(
        state=cleared,
        disposition=HysteresisDisposition.TRANSITIONED,
        transition=transition,
    )
