"""Deterministic reconnect backoff without live network side effects."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from .contract import require_aware


@dataclass(frozen=True, slots=True)
class ReconnectPolicy:
    base_delay: timedelta = timedelta(seconds=1)
    maximum_delay: timedelta = timedelta(seconds=30)
    multiplier: float = 2.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.base_delay.total_seconds() <= 0:
            raise ValueError("base_delay는 0보다 커야 합니다")
        if self.maximum_delay < self.base_delay:
            raise ValueError("maximum_delay는 base_delay 이상이어야 합니다")
        if self.multiplier < 1:
            raise ValueError("multiplier는 1 이상이어야 합니다")
        if not 0 <= self.jitter_ratio < 1:
            raise ValueError("jitter_ratio는 0 이상 1 미만이어야 합니다")

    def delay_for(self, attempt: int, *, jitter_key: str) -> timedelta:
        if attempt < 1:
            raise ValueError("attempt는 1 이상이어야 합니다")
        raw_seconds = self.base_delay.total_seconds() * self.multiplier ** (attempt - 1)
        capped_seconds = min(raw_seconds, self.maximum_delay.total_seconds())
        digest = hashlib.sha256(f"{jitter_key}:{attempt}".encode()).digest()
        fraction = int.from_bytes(digest[:8], "big") / ((1 << 64) - 1)
        factor = 1 - self.jitter_ratio + self.jitter_ratio * fraction
        return timedelta(seconds=capped_seconds * factor)


@dataclass(frozen=True, slots=True)
class ReconnectSchedule:
    attempt: int
    reason: str
    scheduled_at: datetime
    due_at: datetime
    delay: timedelta


class ReconnectController:
    def __init__(self, policy: ReconnectPolicy | None = None) -> None:
        self.policy = policy or ReconnectPolicy()
        self._attempt = 0
        self._schedule: ReconnectSchedule | None = None

    @property
    def schedule(self) -> ReconnectSchedule | None:
        return self._schedule

    def schedule_failure(
        self,
        *,
        now: datetime,
        reason: str,
        jitter_key: str,
    ) -> ReconnectSchedule:
        require_aware(now, "now")
        if not reason:
            raise ValueError("reconnect reason은 비어 있을 수 없습니다")
        self._attempt += 1
        delay = self.policy.delay_for(self._attempt, jitter_key=jitter_key)
        self._schedule = ReconnectSchedule(
            attempt=self._attempt,
            reason=reason,
            scheduled_at=now,
            due_at=now + delay,
            delay=delay,
        )
        return self._schedule

    def is_due(self, now: datetime) -> bool:
        require_aware(now, "now")
        return self._schedule is not None and now >= self._schedule.due_at

    def mark_connected(self) -> None:
        self._attempt = 0
        self._schedule = None
