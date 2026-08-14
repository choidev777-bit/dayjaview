"""PIT-safe stock-to-theme mapping and dirty-theme recomputation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from threading import RLock

from packages.calculations import (
    THEME_CALCULATION_POLICY_V1,
    ThemeCalculationPolicy,
    ThemeMetrics,
    calculate_theme_metrics,
)
from packages.domain import (
    StockReference,
    ThemeMembershipSnapshot,
    select_membership_snapshot,
)

from .hot_state import HotStateStore

DIRTY_THEME_CHECKPOINT_VERSION = "dirty-theme-2026.08.1"


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}에는 timezone 정보가 필요합니다")


class VersionedThemeCatalog:
    """Selects only membership effective and known at each decision time."""

    def __init__(self, snapshots: tuple[ThemeMembershipSnapshot, ...]) -> None:
        self._snapshots = snapshots
        self._theme_ids = tuple(sorted({item.theme_id for item in snapshots}))
        keys = [
            (
                item.theme_id,
                item.effective_from,
                item.known_at,
                item.version,
            )
            for item in snapshots
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("membership catalog에 중복 snapshot이 있습니다")

    @property
    def theme_ids(self) -> tuple[str, ...]:
        return self._theme_ids

    def select(
        self,
        *,
        theme_id: str,
        market_date: date,
        decision_at: datetime,
    ) -> ThemeMembershipSnapshot | None:
        return select_membership_snapshot(
            self._snapshots,
            theme_id=theme_id,
            market_date=market_date,
            decision_at=decision_at,
        )

    def affected_themes(
        self,
        *,
        stock_id: str,
        market_date: date,
        decision_at: datetime,
    ) -> tuple[str, ...]:
        affected: list[str] = []
        for theme_id in self._theme_ids:
            membership = self.select(
                theme_id=theme_id,
                market_date=market_date,
                decision_at=decision_at,
            )
            if membership is not None and membership.contains(stock_id):
                affected.append(theme_id)
        return tuple(affected)


@dataclass(frozen=True, slots=True)
class DirtyTheme:
    theme_id: str
    market_date: date
    as_of: datetime

    def __post_init__(self) -> None:
        if not self.theme_id.strip():
            raise ValueError("theme_id는 비어 있을 수 없습니다")
        _require_aware(self.as_of, "as_of")

    def to_dict(self) -> dict[str, str]:
        return {
            "themeId": self.theme_id,
            "marketDate": self.market_date.isoformat(),
            "asOf": self.as_of.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> DirtyTheme:
        return cls(
            theme_id=str(value["themeId"]),
            market_date=date.fromisoformat(str(value["marketDate"])),
            as_of=datetime.fromisoformat(str(value["asOf"])),
        )


@dataclass(frozen=True, slots=True)
class DirtyThemeCheckpoint:
    checkpoint_version: str
    created_at: datetime
    entries: tuple[DirtyTheme, ...]

    def __post_init__(self) -> None:
        if not self.checkpoint_version.strip():
            raise ValueError("checkpoint_version은 비어 있을 수 없습니다")
        _require_aware(self.created_at, "created_at")
        if self.entries and max(item.as_of for item in self.entries) > self.created_at:
            raise ValueError("dirty checkpoint entry는 created_at 이후일 수 없습니다")

    def to_dict(self) -> dict[str, object]:
        return {
            "checkpointVersion": self.checkpoint_version,
            "createdAt": self.created_at.isoformat(),
            "entries": [item.to_dict() for item in self.entries],
        }

    @property
    def content_hash(self) -> str:
        raw = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> DirtyThemeCheckpoint:
        entries = value["entries"]
        if not isinstance(entries, list):
            raise TypeError("dirty theme checkpoint entries가 list가 아닙니다")
        return cls(
            checkpoint_version=str(value["checkpointVersion"]),
            created_at=datetime.fromisoformat(str(value["createdAt"])),
            entries=tuple(DirtyTheme.from_dict(item) for item in entries),
        )


@dataclass(frozen=True, slots=True)
class ThemeMetricUpdate:
    theme_id: str
    market_date: date
    as_of: datetime
    membership_version: str
    metrics: ThemeMetrics


class DirtyThemeAggregator:
    """Recomputes only affected themes and clears the batch only on success."""

    def __init__(
        self,
        *,
        catalog: VersionedThemeCatalog,
        references: tuple[StockReference, ...],
        policy: ThemeCalculationPolicy = THEME_CALCULATION_POLICY_V1,
    ) -> None:
        self._catalog = catalog
        self._references = references
        self._policy = policy
        self._dirty: dict[tuple[str, date], DirtyTheme] = {}
        self._lock = RLock()

    def mark_stock(
        self,
        *,
        stock_id: str,
        market_date: date,
        decision_at: datetime,
    ) -> tuple[str, ...]:
        _require_aware(decision_at, "decision_at")
        affected = self._catalog.affected_themes(
            stock_id=stock_id,
            market_date=market_date,
            decision_at=decision_at,
        )
        with self._lock:
            for theme_id in affected:
                key = (theme_id, market_date)
                previous = self._dirty.get(key)
                as_of = (
                    decision_at
                    if previous is None
                    else max(previous.as_of, decision_at)
                )
                self._dirty[key] = DirtyTheme(
                    theme_id=theme_id,
                    market_date=market_date,
                    as_of=as_of,
                )
        return affected

    def pending(self) -> tuple[DirtyTheme, ...]:
        with self._lock:
            return tuple(self._dirty[key] for key in sorted(self._dirty))

    def drain(self, hot_state: HotStateStore) -> tuple[ThemeMetricUpdate, ...]:
        with self._lock:
            batch = tuple(self._dirty[key] for key in sorted(self._dirty))
            updates: list[ThemeMetricUpdate] = []
            for dirty in batch:
                membership = self._catalog.select(
                    theme_id=dirty.theme_id,
                    market_date=dirty.market_date,
                    decision_at=dirty.as_of,
                )
                if membership is None:
                    raise ValueError(
                        "dirty theme 계산 시점에 유효한 membership이 없습니다: "
                        f"{dirty.theme_id}"
                    )
                stock_ids = tuple(member.stock_id for member in membership.members)
                states = hot_state.states_for(
                    market_date=dirty.market_date,
                    stock_ids=stock_ids,
                )
                references = self._select_references(
                    stock_ids=stock_ids,
                    market_date=dirty.market_date,
                    as_of=dirty.as_of,
                )
                metrics = calculate_theme_metrics(
                    market_date=dirty.market_date,
                    as_of=dirty.as_of,
                    membership=membership,
                    references=references,
                    observations=(state.to_observation() for state in states),
                    policy=self._policy,
                )
                updates.append(
                    ThemeMetricUpdate(
                        theme_id=dirty.theme_id,
                        market_date=dirty.market_date,
                        as_of=dirty.as_of,
                        membership_version=membership.version,
                        metrics=metrics,
                    )
                )
            # The lock makes this an atomic drain. If any calculation raises,
            # no dirty entry is removed and a retry sees the same batch.
            for dirty in batch:
                self._dirty.pop((dirty.theme_id, dirty.market_date), None)
            return tuple(updates)

    def _select_references(
        self,
        *,
        stock_ids: tuple[str, ...],
        market_date: date,
        as_of: datetime,
    ) -> tuple[StockReference, ...]:
        selected: list[StockReference] = []
        for stock_id in sorted(set(stock_ids)):
            eligible = [
                item
                for item in self._references
                if item.stock_id == stock_id
                and item.effective_for == market_date
                and item.known_at <= as_of
            ]
            eligible.sort(key=lambda item: (item.known_at, item.version))
            if not eligible:
                continue
            latest = eligible[-1]
            if sum(item.known_at == latest.known_at for item in eligible) > 1:
                raise ValueError(
                    f"같은 시점에 둘 이상의 reference version이 유효합니다: {stock_id}"
                )
            selected.append(latest)
        return tuple(selected)

    def checkpoint(self, *, created_at: datetime) -> DirtyThemeCheckpoint:
        return DirtyThemeCheckpoint(
            checkpoint_version=DIRTY_THEME_CHECKPOINT_VERSION,
            created_at=created_at,
            entries=self.pending(),
        )

    def restore(self, checkpoint: DirtyThemeCheckpoint) -> None:
        if checkpoint.checkpoint_version != DIRTY_THEME_CHECKPOINT_VERSION:
            raise ValueError(
                "지원하지 않는 dirty theme checkpoint version입니다: "
                f"{checkpoint.checkpoint_version}"
            )
        with self._lock:
            restored: dict[tuple[str, date], DirtyTheme] = {}
            for item in checkpoint.entries:
                key = (item.theme_id, item.market_date)
                if key in restored:
                    raise ValueError("checkpoint에 중복 dirty theme가 있습니다")
                restored[key] = item
            self._dirty = restored
