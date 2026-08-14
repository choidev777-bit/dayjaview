"""DB driver와 독립된 idempotent/PIT revision store 계약과 메모리 구현."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime

from .errors import TemporalConflictError
from .hashing import sha256_json
from .models import SourceSnapshot, require_aware


@dataclass(frozen=True, slots=True)
class ReferenceRevision:
    record_type: str
    entity_key: str
    effective_on: date
    known_from: datetime
    known_to: datetime | None
    revision: int
    content_hash: str
    value: object

    def contains(self, decision_at: datetime) -> bool:
        return self.known_from <= decision_at and (
            self.known_to is None or decision_at < self.known_to
        )


@dataclass(frozen=True, slots=True)
class ApplyRevisionResult:
    revision: ReferenceRevision
    created: bool


class InMemoryReferenceStore:
    """동일 입력은 no-op, 변경 입력은 새 known-time revision으로 보존한다."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str, date], list[ReferenceRevision]] = {}

    def apply(
        self,
        *,
        record_type: str,
        entity_key: str,
        effective_on: date,
        known_at: datetime,
        value: object,
        content_hash: str | None = None,
    ) -> ApplyRevisionResult:
        require_aware(known_at, "known_at")
        if not record_type or not entity_key:
            raise ValueError("record_type과 entity_key는 비어 있을 수 없습니다.")
        value_hash = content_hash or sha256_json(value)
        key = record_type, entity_key, effective_on
        versions = self._values.setdefault(key, [])
        if not versions:
            revision = ReferenceRevision(
                record_type=record_type,
                entity_key=entity_key,
                effective_on=effective_on,
                known_from=known_at,
                known_to=None,
                revision=1,
                content_hash=value_hash,
                value=value,
            )
            versions.append(revision)
            return ApplyRevisionResult(revision=revision, created=True)

        current = versions[-1]
        if current.content_hash == value_hash:
            return ApplyRevisionResult(revision=current, created=False)
        if known_at <= current.known_from:
            raise TemporalConflictError(
                "변경 revision의 known_at은 현재 revision보다 늦어야 합니다."
            )
        versions[-1] = replace(current, known_to=known_at)
        revision = ReferenceRevision(
            record_type=record_type,
            entity_key=entity_key,
            effective_on=effective_on,
            known_from=known_at,
            known_to=None,
            revision=current.revision + 1,
            content_hash=value_hash,
            value=value,
        )
        versions.append(revision)
        return ApplyRevisionResult(revision=revision, created=True)

    def apply_snapshot(self, snapshot: SourceSnapshot) -> ApplyRevisionResult:
        metadata = snapshot.metadata
        entity_key = (
            f"{metadata.provider.value}:{metadata.dataset.value}:{metadata.source_key}"
        )
        existing = self.versions(
            record_type="SOURCE_SNAPSHOT",
            entity_key=entity_key,
            effective_on=metadata.as_of.date(),
        )
        if existing:
            expected_revision = (
                existing[-1].revision
                if existing[-1].content_hash == snapshot.raw_hash
                else existing[-1].revision + 1
            )
        else:
            expected_revision = 1
        if metadata.revision != expected_revision:
            raise TemporalConflictError(
                "source metadata revision과 예상 저장 revision이 일치하지 않습니다."
            )
        result = self.apply(
            record_type="SOURCE_SNAPSHOT",
            entity_key=entity_key,
            effective_on=metadata.as_of.date(),
            known_at=metadata.collected_at,
            value=snapshot,
            content_hash=snapshot.raw_hash,
        )
        return result

    def point_in_time(
        self,
        *,
        record_type: str,
        entity_key: str,
        effective_on_or_before: date,
        decision_at: datetime,
    ) -> ReferenceRevision | None:
        require_aware(decision_at, "decision_at")
        candidates = [
            revision
            for (stored_type, stored_key, effective_on), versions in self._values.items()
            if stored_type == record_type
            and stored_key == entity_key
            and effective_on <= effective_on_or_before
            for revision in versions
            if revision.contains(decision_at)
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda revision: (
                revision.effective_on,
                revision.known_from,
                revision.revision,
            ),
        )

    def versions(
        self, *, record_type: str, entity_key: str, effective_on: date
    ) -> tuple[ReferenceRevision, ...]:
        return tuple(self._values.get((record_type, entity_key, effective_on), ()))
