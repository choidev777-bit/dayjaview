"""Deterministic 180-target/200-hard-limit subscription admission policy."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum

from .contract import require_aware, require_stock_id


class DemandPriority(IntEnum):
    ACTIVE_LEADER = 1
    ACTIVE_CORE = 2
    MULTI_SIGNAL_CANDIDATE = 3
    ACTIVE_RELATED = 4
    SINGLE_SIGNAL_CANDIDATE = 5


@dataclass(frozen=True, slots=True)
class SubscriptionDemand:
    stock_id: str
    priority: DemandPriority
    observed_at: datetime
    signal_count: int = 1
    theme_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_stock_id(self.stock_id)
        require_aware(self.observed_at, "observed_at")
        if self.signal_count < 1:
            raise ValueError("signal_count는 1 이상이어야 합니다")
        if tuple(sorted(set(self.theme_ids))) != self.theme_ids:
            raise ValueError("theme_ids는 중복 없이 정렬돼야 합니다")


@dataclass(frozen=True, slots=True)
class SubscriptionPlan:
    generated_at: datetime
    subscriptions: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    retained_for_cooldown: tuple[str, ...]
    snapshot_supplement: tuple[str, ...]
    target_limit: int
    hard_limit: int
    coalesced: bool = False

    def __post_init__(self) -> None:
        require_aware(self.generated_at, "generated_at")
        if len(self.subscriptions) > self.hard_limit:
            raise ValueError("구독 계획이 hard limit를 초과했습니다")
        if len(set(self.subscriptions)) != len(self.subscriptions):
            raise ValueError("구독 계획에 중복 stock_id가 있습니다")


class SubscriptionManager:
    """Keep stable registrations while admitting higher-priority bursts safely."""

    def __init__(
        self,
        *,
        target_limit: int = 180,
        hard_limit: int = 200,
        cooldown: timedelta = timedelta(seconds=60),
        coalesce_interval: timedelta = timedelta(seconds=1),
    ) -> None:
        if target_limit < 1 or hard_limit < target_limit:
            raise ValueError("구독 limit 설정이 올바르지 않습니다")
        if cooldown.total_seconds() < 0 or coalesce_interval.total_seconds() < 0:
            raise ValueError("cooldown/coalesce interval은 음수일 수 없습니다")
        self.target_limit = target_limit
        self.hard_limit = hard_limit
        self.cooldown = cooldown
        self.coalesce_interval = coalesce_interval
        self._current: tuple[str, ...] = ()
        self._last_applied_at: datetime | None = None
        self._retained_until: dict[str, datetime] = {}
        self._last_demands: dict[str, SubscriptionDemand] = {}
        self._pending_demands: dict[str, SubscriptionDemand] = {}

    @property
    def current(self) -> tuple[str, ...]:
        return self._current

    def reconcile(
        self,
        demands: Iterable[SubscriptionDemand],
        *,
        now: datetime,
        force: bool = False,
    ) -> SubscriptionPlan:
        require_aware(now, "now")
        normalized = _aggregate_demands(demands)
        self._pending_demands = normalized
        if (
            not force
            and self._last_applied_at is not None
            and now - self._last_applied_at < self.coalesce_interval
        ):
            return self._coalesced_plan(normalized, now)
        return self._apply(normalized, now)

    def flush(self, *, now: datetime, force: bool = False) -> SubscriptionPlan:
        return self.reconcile(self._pending_demands.values(), now=now, force=force)

    def _coalesced_plan(
        self,
        demands: Mapping[str, SubscriptionDemand],
        now: datetime,
    ) -> SubscriptionPlan:
        ranked = sorted(demands, key=lambda stock_id: _rank_key(demands[stock_id]))
        supplemental = tuple(stock_id for stock_id in ranked if stock_id not in self._current)
        return SubscriptionPlan(
            generated_at=now,
            subscriptions=self._current,
            added=(),
            removed=(),
            retained_for_cooldown=tuple(
                stock_id for stock_id in self._current if stock_id in self._retained_until
            ),
            snapshot_supplement=supplemental,
            target_limit=self.target_limit,
            hard_limit=self.hard_limit,
            coalesced=True,
        )

    def _apply(
        self,
        demands: dict[str, SubscriptionDemand],
        now: datetime,
    ) -> SubscriptionPlan:
        ranked = sorted(demands, key=lambda stock_id: _rank_key(demands[stock_id]))
        desired = ranked[: self.target_limit]
        desired_set = set(desired)

        expired_cooldown: set[str] = set()
        for stock_id in tuple(self._retained_until):
            if stock_id in desired_set:
                del self._retained_until[stock_id]
            elif self._retained_until[stock_id] <= now:
                expired_cooldown.add(stock_id)
                del self._retained_until[stock_id]
        for stock_id in self._current:
            if (
                stock_id not in desired_set
                and stock_id not in self._retained_until
                and stock_id not in expired_cooldown
            ):
                self._retained_until[stock_id] = now + self.cooldown

        cooling = [
            stock_id
            for stock_id in self._current
            if stock_id not in desired_set and self._retained_until.get(stock_id, now) > now
        ]
        cooling.sort(key=lambda stock_id: self._historical_rank(stock_id))
        cooling = cooling[: self.hard_limit - len(desired)]
        selected_set = desired_set | set(cooling)

        stable = [stock_id for stock_id in self._current if stock_id in selected_set]
        stable_set = set(stable)
        stable.extend(stock_id for stock_id in desired if stock_id not in stable_set)
        subscriptions = tuple(stable)

        old_set = set(self._current)
        new_set = set(subscriptions)
        added = tuple(sorted(new_set - old_set))
        removed = tuple(sorted(old_set - new_set))
        for stock_id in removed:
            self._retained_until.pop(stock_id, None)

        supplemental = tuple(stock_id for stock_id in ranked if stock_id not in new_set)
        retained = tuple(stock_id for stock_id in subscriptions if stock_id in set(cooling))
        self._last_demands.update(demands)
        self._current = subscriptions
        self._last_applied_at = now
        return SubscriptionPlan(
            generated_at=now,
            subscriptions=subscriptions,
            added=added,
            removed=removed,
            retained_for_cooldown=retained,
            snapshot_supplement=supplemental,
            target_limit=self.target_limit,
            hard_limit=self.hard_limit,
        )

    def _historical_rank(self, stock_id: str) -> tuple[int, int, float, str]:
        demand = self._pending_demands.get(stock_id) or self._last_demands.get(stock_id)
        if demand is None:
            return (len(DemandPriority) + 1, 0, 0.0, stock_id)
        return _rank_key(demand)


def _aggregate_demands(
    demands: Iterable[SubscriptionDemand],
) -> dict[str, SubscriptionDemand]:
    grouped: dict[str, list[SubscriptionDemand]] = {}
    for demand in demands:
        grouped.setdefault(demand.stock_id, []).append(demand)
    normalized: dict[str, SubscriptionDemand] = {}
    for stock_id, rows in grouped.items():
        normalized[stock_id] = SubscriptionDemand(
            stock_id=stock_id,
            priority=min(row.priority for row in rows),
            observed_at=max(row.observed_at for row in rows),
            signal_count=sum(row.signal_count for row in rows),
            theme_ids=tuple(sorted({theme_id for row in rows for theme_id in row.theme_ids})),
        )
    return normalized


def _rank_key(demand: SubscriptionDemand) -> tuple[int, int, float, str]:
    return (
        int(demand.priority),
        -demand.signal_count,
        -demand.observed_at.timestamp(),
        demand.stock_id,
    )
