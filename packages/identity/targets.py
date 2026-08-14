from __future__ import annotations

from threading import RLock
from typing import Protocol

from .models import SavedType, TargetRecord


class TargetCatalog(Protocol):
    def get_target(self, saved_type: SavedType, target_id: str) -> TargetRecord | None: ...


class InMemoryTargetCatalog:
    """Fixture read model; saved state never mutates this shared catalog."""

    def __init__(self, targets: tuple[TargetRecord, ...] = ()) -> None:
        self._targets = {(item.saved_type, item.target_id): item for item in targets}
        self._lock = RLock()

    def put(self, target: TargetRecord) -> None:
        with self._lock:
            self._targets[(target.saved_type, target.target_id)] = target

    def remove(self, saved_type: SavedType, target_id: str) -> None:
        with self._lock:
            self._targets.pop((saved_type, target_id), None)

    def get_target(self, saved_type: SavedType, target_id: str) -> TargetRecord | None:
        with self._lock:
            return self._targets.get((saved_type, target_id))
