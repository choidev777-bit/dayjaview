"""ADR-008을 실행하는 versioned source-rights fail-closed registry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .errors import SourceRightsDeniedError
from .models import (
    CollectionEnvironment,
    ContentClass,
    RightsOperation,
    require_aware,
    require_text,
)


@dataclass(frozen=True, slots=True)
class SourceRightsRecord:
    source_id: str
    rights_version: str
    terms_version: str
    verified_by: str
    verified_at: datetime
    valid_from: datetime
    valid_until: datetime | None
    environments: frozenset[CollectionEnvironment]
    operations: frozenset[RightsOperation]
    allowed_fields: frozenset[str]
    content_classes: frozenset[ContentClass]
    retention_days: int
    attribution_required: bool
    original_link_required: bool

    def __post_init__(self) -> None:
        for field_name, value in (
            ("source_id", self.source_id),
            ("rights_version", self.rights_version),
            ("terms_version", self.terms_version),
            ("verified_by", self.verified_by),
        ):
            require_text(value, field_name)
        require_aware(self.verified_at, "verified_at")
        require_aware(self.valid_from, "valid_from")
        if self.valid_until is not None:
            require_aware(self.valid_until, "valid_until")
            if self.valid_until <= self.valid_from:
                raise ValueError("valid_until은 valid_from보다 늦어야 합니다.")
        if self.verified_at > self.valid_from:
            raise ValueError("권리 record는 유효 시작 전에 검증돼야 합니다.")
        if not self.environments or not self.operations:
            raise ValueError("권리 record에는 환경과 operation이 필요합니다.")
        if not self.allowed_fields or any(
            not field.strip() for field in self.allowed_fields
        ):
            raise ValueError("권리 record에는 허용 field가 필요합니다.")
        if not self.content_classes:
            raise ValueError("권리 record에는 content class가 필요합니다.")
        if self.retention_days < 0:
            raise ValueError("retention_days는 음수일 수 없습니다.")

    def active_at(self, checked_at: datetime) -> bool:
        require_aware(checked_at, "checked_at")
        return self.valid_from <= checked_at and (
            self.valid_until is None or checked_at < self.valid_until
        )


class RightsRegistry:
    """명시적으로 등록된 현재 유효 record 외에는 어떤 권리도 추정하지 않는다."""

    def __init__(self, records: tuple[SourceRightsRecord, ...] = ()) -> None:
        self._records: dict[str, list[SourceRightsRecord]] = {}
        for record in records:
            self.register(record)

    def register(self, record: SourceRightsRecord) -> None:
        versions = self._records.setdefault(record.source_id, [])
        if any(item.rights_version == record.rights_version for item in versions):
            raise ValueError("같은 source_id와 rights_version을 중복 등록할 수 없습니다.")
        versions.append(record)
        versions.sort(key=lambda item: (item.valid_from, item.verified_at))

    def authorize(
        self,
        *,
        source_id: str,
        environment: CollectionEnvironment,
        operations: frozenset[RightsOperation],
        fields: frozenset[str],
        content_classes: frozenset[ContentClass],
        checked_at: datetime,
    ) -> SourceRightsRecord:
        require_aware(checked_at, "checked_at")
        records = self._records.get(source_id, ())
        active = [record for record in records if record.active_at(checked_at)]
        if not active:
            raise SourceRightsDeniedError(
                "RIGHTS_RECORD_MISSING_OR_EXPIRED",
                source_id,
                "현재 시점에 유효한 source-rights record가 없습니다.",
            )
        record = active[-1]
        if environment not in record.environments:
            raise SourceRightsDeniedError(
                "ENVIRONMENT_NOT_ALLOWED",
                source_id,
                f"{environment.value} 환경은 허용되지 않았습니다.",
            )
        missing_operations = sorted(
            operation.value for operation in operations - record.operations
        )
        if missing_operations:
            raise SourceRightsDeniedError(
                "OPERATION_NOT_ALLOWED",
                source_id,
                "허용되지 않은 operation: " + ", ".join(missing_operations),
            )
        missing_fields = sorted(fields - record.allowed_fields)
        if missing_fields:
            raise SourceRightsDeniedError(
                "FIELD_NOT_ALLOWED",
                source_id,
                "허용되지 않은 field: " + ", ".join(missing_fields),
            )
        missing_classes = sorted(
            item.value for item in content_classes - record.content_classes
        )
        if missing_classes:
            raise SourceRightsDeniedError(
                "CONTENT_CLASS_NOT_ALLOWED",
                source_id,
                "허용되지 않은 content class: " + ", ".join(missing_classes),
            )
        if not record.attribution_required or not record.original_link_required:
            raise SourceRightsDeniedError(
                "ATTRIBUTION_POLICY_INCOMPLETE",
                source_id,
                "뉴스 evidence에는 매체 attribution과 원문 link 요구가 모두 필요합니다.",
            )
        return record
