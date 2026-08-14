"""Idempotent, versioned full read snapshots with scoped monotonic sequence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from threading import RLock
from typing import Protocol

from packages.domain import DataStatus


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name}은 비어 있을 수 없습니다")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}에는 timezone 정보가 필요합니다")


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("snapshot 값은 유효한 JSON이어야 합니다") from error


def _copy_json_object(value: dict[str, object]) -> dict[str, object]:
    copied = json.loads(_canonical_json(value))
    if not isinstance(copied, dict):
        raise TypeError("snapshot payload는 JSON object여야 합니다")
    return copied


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _opaque(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]}"


class SnapshotTopic(StrEnum):
    THEME_RANK = "theme_rank_snapshot"
    THEME_TREEMAP = "theme_treemap_snapshot"
    EVENT_STATE_CHANGED = "event_state_changed"


@dataclass(frozen=True, slots=True)
class SnapshotVersions:
    schema_version: str
    calculation_version: str
    ranking_model_version: str
    membership_version: str
    baseline_version: str | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("schema_version", self.schema_version),
            ("calculation_version", self.calculation_version),
            ("ranking_model_version", self.ranking_model_version),
            ("membership_version", self.membership_version),
        ):
            _require_text(value, field_name)
        if self.baseline_version is not None:
            _require_text(self.baseline_version, "baseline_version")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "schemaVersion": self.schema_version,
            "calculationVersion": self.calculation_version,
            "rankingModelVersion": self.ranking_model_version,
            "membershipVersion": self.membership_version,
            "baselineVersion": self.baseline_version,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> SnapshotVersions:
        return cls(
            schema_version=str(value["schemaVersion"]),
            calculation_version=str(value["calculationVersion"]),
            ranking_model_version=str(value["rankingModelVersion"]),
            membership_version=str(value["membershipVersion"]),
            baseline_version=(
                None
                if value.get("baselineVersion") is None
                else str(value["baselineVersion"])
            ),
        )


@dataclass(frozen=True, slots=True)
class SnapshotPublication:
    publication_id: str
    stream_id: str
    topic: SnapshotTopic
    params: dict[str, object]
    market_date: date
    generated_at: datetime
    as_of: datetime
    data_status: DataStatus
    quality_flags: tuple[str, ...]
    payload: dict[str, object]
    versions: SnapshotVersions

    def __post_init__(self) -> None:
        _require_text(self.publication_id, "publication_id")
        _require_text(self.stream_id, "stream_id")
        _require_aware(self.generated_at, "generated_at")
        _require_aware(self.as_of, "as_of")
        if self.as_of > self.generated_at:
            raise ValueError("snapshot as_of는 generated_at 이후일 수 없습니다")
        if len(self.quality_flags) != len(set(self.quality_flags)):
            raise ValueError("snapshot quality_flags에 중복 값이 있습니다")
        object.__setattr__(self, "params", _copy_json_object(self.params))
        object.__setattr__(self, "payload", _copy_json_object(self.payload))
        object.__setattr__(self, "quality_flags", tuple(sorted(self.quality_flags)))

    @property
    def params_key(self) -> str:
        return _opaque("params", _canonical_json(self.params))

    @property
    def fingerprint(self) -> str:
        value = {
            "streamId": self.stream_id,
            "topic": self.topic.value,
            "params": self.params,
            "marketDate": self.market_date.isoformat(),
            "generatedAt": _utc_iso(self.generated_at),
            "asOf": _utc_iso(self.as_of),
            "dataStatus": self.data_status.value,
            "qualityFlags": sorted(self.quality_flags),
            "payload": self.payload,
            "versions": self.versions.to_dict(),
        }
        return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReadSnapshot:
    snapshot_id: str
    publication_id: str
    stream_id: str
    topic: SnapshotTopic
    params_key: str
    sequence: int
    market_date: date
    generated_at: datetime
    as_of: datetime
    data_status: DataStatus
    quality_flags: tuple[str, ...]
    payload: dict[str, object]
    versions: SnapshotVersions
    content_hash: str

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("snapshot sequence는 1 이상이어야 합니다")
        if self.as_of > self.generated_at:
            raise ValueError("snapshot as_of는 generated_at 이후일 수 없습니다")
        object.__setattr__(self, "payload", _copy_json_object(self.payload))
        object.__setattr__(self, "quality_flags", tuple(sorted(self.quality_flags)))

    def to_ws_message(self, *, subscription_id: str) -> dict[str, object]:
        _require_text(subscription_id, "subscription_id")
        if self.topic in (SnapshotTopic.THEME_RANK, SnapshotTopic.THEME_TREEMAP):
            if "snapshotId" in self.payload:
                raise ValueError("payload에 snapshotId를 직접 넣을 수 없습니다")
            wire_payload: dict[str, object] = {
                "snapshotId": self.snapshot_id,
                **_copy_json_object(self.payload),
            }
        else:
            wire_payload = _copy_json_object(self.payload)
        return {
            "type": self.topic.value,
            "schemaVersion": self.versions.schema_version,
            "subscriptionId": subscription_id,
            "streamId": self.stream_id,
            "topic": self.topic.value,
            "sequence": self.sequence,
            "generatedAt": _utc_iso(self.generated_at),
            "asOf": _utc_iso(self.as_of),
            "marketDate": self.market_date.isoformat(),
            "dataStatus": self.data_status.value,
            "qualityFlags": list(self.quality_flags),
            "payload": wire_payload,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshotId": self.snapshot_id,
            "publicationId": self.publication_id,
            "streamId": self.stream_id,
            "topic": self.topic.value,
            "paramsKey": self.params_key,
            "sequence": self.sequence,
            "marketDate": self.market_date.isoformat(),
            "generatedAt": _utc_iso(self.generated_at),
            "asOf": _utc_iso(self.as_of),
            "dataStatus": self.data_status.value,
            "qualityFlags": list(self.quality_flags),
            "payload": self.payload,
            "versions": self.versions.to_dict(),
            "contentHash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ReadSnapshot:
        payload = value["payload"]
        versions = value["versions"]
        flags = value["qualityFlags"]
        if not isinstance(payload, dict) or not isinstance(versions, dict):
            raise TypeError("저장된 snapshot payload 또는 versions가 잘못되었습니다")
        if not isinstance(flags, list):
            raise TypeError("저장된 snapshot qualityFlags가 잘못되었습니다")
        return cls(
            snapshot_id=str(value["snapshotId"]),
            publication_id=str(value["publicationId"]),
            stream_id=str(value["streamId"]),
            topic=SnapshotTopic(str(value["topic"])),
            params_key=str(value["paramsKey"]),
            sequence=int(str(value["sequence"])),
            market_date=date.fromisoformat(str(value["marketDate"])),
            generated_at=datetime.fromisoformat(str(value["generatedAt"])),
            as_of=datetime.fromisoformat(str(value["asOf"])),
            data_status=DataStatus(str(value["dataStatus"])),
            quality_flags=tuple(str(item) for item in flags),
            payload=payload,
            versions=SnapshotVersions.from_dict(versions),
            content_hash=str(value["contentHash"]),
        )


class SnapshotIdempotencyConflict(ValueError):
    pass


class StaleSnapshotPublication(ValueError):
    pass


class SnapshotCommitFailure(RuntimeError):
    pass


class SnapshotRepository(Protocol):
    def publish(self, publication: SnapshotPublication) -> ReadSnapshot: ...

    def latest(
        self,
        *,
        stream_id: str,
        topic: SnapshotTopic,
        params: dict[str, object],
    ) -> ReadSnapshot | None: ...


class InMemorySnapshotRepository:
    """Atomic durable-contract double that survives writer reconstruction."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._sequences: dict[tuple[str, SnapshotTopic, str], int] = {}
        self._snapshots: dict[str, ReadSnapshot] = {}
        self._latest: dict[tuple[str, SnapshotTopic, str], str] = {}
        self._receipts: dict[str, tuple[str, str]] = {}
        self._fail_next_commit = False

    def fail_next_commit(self) -> None:
        with self._lock:
            self._fail_next_commit = True

    def publish(self, publication: SnapshotPublication) -> ReadSnapshot:
        with self._lock:
            receipt = self._receipts.get(publication.publication_id)
            if receipt is not None:
                fingerprint, snapshot_id = receipt
                if fingerprint != publication.fingerprint:
                    raise SnapshotIdempotencyConflict(
                        "같은 publication_id에 서로 다른 snapshot이 있습니다"
                    )
                return self._snapshots[snapshot_id]

            backup = (
                self._sequences.copy(),
                self._snapshots.copy(),
                self._latest.copy(),
                self._receipts.copy(),
            )
            try:
                scope = (
                    publication.stream_id,
                    publication.topic,
                    publication.params_key,
                )
                latest_id = self._latest.get(scope)
                if (
                    latest_id is not None
                    and publication.as_of < self._snapshots[latest_id].as_of
                ):
                    raise StaleSnapshotPublication(
                        "snapshot as_of가 현재 full snapshot보다 과거입니다"
                    )
                sequence = self._sequences.get(scope, 0) + 1
                snapshot_id = _opaque(
                    "snap",
                    f"{publication.publication_id}:{publication.fingerprint}",
                )
                payload = _copy_json_object(publication.payload)
                content_hash = hashlib.sha256(
                    _canonical_json(
                        {
                            "scope": [
                                publication.stream_id,
                                publication.topic.value,
                                publication.params_key,
                            ],
                            "sequence": sequence,
                            "payload": payload,
                            "versions": publication.versions.to_dict(),
                        }
                    ).encode("utf-8")
                ).hexdigest()
                snapshot = ReadSnapshot(
                    snapshot_id=snapshot_id,
                    publication_id=publication.publication_id,
                    stream_id=publication.stream_id,
                    topic=publication.topic,
                    params_key=publication.params_key,
                    sequence=sequence,
                    market_date=publication.market_date,
                    generated_at=publication.generated_at,
                    as_of=publication.as_of,
                    data_status=publication.data_status,
                    quality_flags=tuple(sorted(publication.quality_flags)),
                    payload=payload,
                    versions=publication.versions,
                    content_hash=content_hash,
                )
                self._sequences[scope] = sequence
                self._snapshots[snapshot_id] = snapshot
                self._latest[scope] = snapshot_id
                self._receipts[publication.publication_id] = (
                    publication.fingerprint,
                    snapshot_id,
                )
                if self._fail_next_commit:
                    self._fail_next_commit = False
                    raise SnapshotCommitFailure("snapshot commit 실패를 모의했습니다")
            except BaseException:
                (
                    self._sequences,
                    self._snapshots,
                    self._latest,
                    self._receipts,
                ) = backup
                raise
            return snapshot

    def latest(
        self,
        *,
        stream_id: str,
        topic: SnapshotTopic,
        params: dict[str, object],
    ) -> ReadSnapshot | None:
        params_key = _opaque("params", _canonical_json(params))
        with self._lock:
            snapshot_id = self._latest.get((stream_id, topic, params_key))
            return None if snapshot_id is None else self._snapshots[snapshot_id]


class VersionedSnapshotWriter:
    def __init__(self, repository: SnapshotRepository) -> None:
        self._repository = repository

    def publish(self, publication: SnapshotPublication) -> ReadSnapshot:
        return self._repository.publish(publication)
