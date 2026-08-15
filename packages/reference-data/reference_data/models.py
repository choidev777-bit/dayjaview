"""KRX·OpenDART 기준정보의 source-preserving/PIT domain 값."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum

from .hashing import sha256_text

STOCK_CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}에는 timezone 정보가 필요합니다.")


def require_stock_code(value: str) -> None:
    if not STOCK_CODE_RE.fullmatch(value):
        raise ValueError("stock_code는 6자리 영문 대문자 또는 숫자여야 합니다.")


def require_positive_decimal(value: Decimal, field_name: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field_name}은 0보다 큰 유한 Decimal이어야 합니다.")


class SourceProvider(StrEnum):
    KRX_OPEN_API = "KRX_OPEN_API"
    OPENDART = "OPENDART"


class SourceDataset(StrEnum):
    KRX_STOCK_DAILY = "KRX_STOCK_DAILY"
    KRX_CALENDAR_DERIVED = "KRX_CALENDAR_DERIVED"
    KRX_CORPORATE_ACTION_REFERENCE = "KRX_CORPORATE_ACTION_REFERENCE"
    OPENDART_CORP_CODE = "OPENDART_CORP_CODE"
    OPENDART_STOCK_TOTAL = "OPENDART_STOCK_TOTAL"
    OPENDART_LARGEST_SHAREHOLDER = "OPENDART_LARGEST_SHAREHOLDER"
    OPENDART_TREASURY_STATUS = "OPENDART_TREASURY_STATUS"
    OPENDART_DISCLOSURE_CLASSIFICATION = "OPENDART_DISCLOSURE_CLASSIFICATION"


class LiveValidationStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"


class QualityState(StrEnum):
    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"
    STALE = "STALE"
    CORPORATE_ACTION_UNRESOLVED = "CORPORATE_ACTION_UNRESOLVED"
    POINT_IN_TIME_UNAVAILABLE = "POINT_IN_TIME_UNAVAILABLE"


class QualityFlag(StrEnum):
    FREE_FLOAT_UNAVAILABLE = "FREE_FLOAT_UNAVAILABLE"
    FREE_FLOAT_STALE = "FREE_FLOAT_STALE"
    FREE_FLOAT_SOURCE_CONFLICT = "FREE_FLOAT_SOURCE_CONFLICT"
    SOURCE_MISSING = "SOURCE_MISSING"
    SOURCE_STALE = "SOURCE_STALE"
    DUPLICATE_DEDUCTION_PREVENTED = "DUPLICATE_DEDUCTION_PREVENTED"
    CORPORATE_ACTION_UNRESOLVED = "CORPORATE_ACTION_UNRESOLVED"
    REFERENCE_PRICE_UNAVAILABLE = "REFERENCE_PRICE_UNAVAILABLE"
    CALENDAR_UNAVAILABLE = "CALENDAR_UNAVAILABLE"
    POINT_IN_TIME_FILTERED = "POINT_IN_TIME_FILTERED"
    LIVE_UNVERIFIED = "LIVE_UNVERIFIED"


class CoverageStatus(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class EconomicField(StrEnum):
    LISTED_COMMON_SHARES = "LISTED_COMMON_SHARES"


class ShareClass(StrEnum):
    COMMON = "COMMON"


class NonFloatCategory(StrEnum):
    TREASURY = "TREASURY"
    CONTROLLING_HOLDER = "CONTROLLING_HOLDER"
    STRATEGIC_LOCKUP = "STRATEGIC_LOCKUP"


class CoverageDeclarationStatus(StrEnum):
    COMPLETE = "COMPLETE"
    COMPLETE_ZERO = "COMPLETE_ZERO"
    INCOMPLETE = "INCOMPLETE"


class CorporateActionStatus(StrEnum):
    CLEAR = "CLEAR"
    ADJUSTED = "ADJUSTED"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    provider: SourceProvider
    dataset: SourceDataset
    endpoint: str
    source_key: str
    as_of: datetime
    collected_at: datetime
    parser_version: str
    revision: int
    lineage: tuple[str, ...]
    source_document_ids: tuple[str, ...] = ()
    live_validation_status: LiveValidationStatus = LiveValidationStatus.UNVERIFIED

    def __post_init__(self) -> None:
        require_aware(self.as_of, "as_of")
        require_aware(self.collected_at, "collected_at")
        if self.as_of > self.collected_at:
            raise ValueError("as_of는 collected_at보다 늦을 수 없습니다.")
        if not self.endpoint.startswith("https://"):
            raise ValueError("endpoint는 HTTPS URI여야 합니다.")
        if not self.source_key or not self.parser_version:
            raise ValueError("source_key와 parser_version은 비어 있을 수 없습니다.")
        if self.revision <= 0:
            raise ValueError("revision은 1 이상이어야 합니다.")
        if not self.lineage or any(not item for item in self.lineage):
            raise ValueError("lineage에는 하나 이상의 비어 있지 않은 항목이 필요합니다.")
        if len(set(self.lineage)) != len(self.lineage):
            raise ValueError("lineage 항목은 중복될 수 없습니다.")
        if any(not item for item in self.source_document_ids):
            raise ValueError("source_document_ids에는 빈 항목을 둘 수 없습니다.")
        if len(set(self.source_document_ids)) != len(self.source_document_ids):
            raise ValueError("source_document_ids 항목은 중복될 수 없습니다.")

    @property
    def known_at(self) -> datetime:
        return self.collected_at


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    metadata: SourceMetadata
    raw_payload_text: str
    raw_hash: str

    def __post_init__(self) -> None:
        if not SHA256_RE.fullmatch(self.raw_hash):
            raise ValueError("raw_hash는 소문자 SHA-256 hex여야 합니다.")
        if sha256_text(self.raw_payload_text) != self.raw_hash:
            raise ValueError("raw_payload_text와 raw_hash가 일치하지 않습니다.")


@dataclass(frozen=True, slots=True)
class FieldObservation:
    stock_code: str
    field: EconomicField
    value: int
    effective_on: date
    share_class: ShareClass
    metadata: SourceMetadata

    def __post_init__(self) -> None:
        require_stock_code(self.stock_code)
        if self.value <= 0:
            raise ValueError("주식수 관측값은 0보다 커야 합니다.")


@dataclass(frozen=True, slots=True)
class DailyPriceObservation:
    stock_code: str
    market: str
    market_date: date
    close: Decimal
    change_from_previous: Decimal | None
    listed_shares: int
    metadata: SourceMetadata

    def __post_init__(self) -> None:
        require_stock_code(self.stock_code)
        require_positive_decimal(self.close, "close")
        if self.change_from_previous is not None and not self.change_from_previous.is_finite():
            raise ValueError("change_from_previous는 유한 Decimal이어야 합니다.")
        if self.listed_shares <= 0:
            raise ValueError("listed_shares는 0보다 커야 합니다.")

    @property
    def implied_previous_adjusted_close(self) -> Decimal | None:
        if self.change_from_previous is None:
            return None
        result = self.close - self.change_from_previous
        return result if result > 0 else None

    def listed_share_observation(self) -> FieldObservation:
        return FieldObservation(
            stock_code=self.stock_code,
            field=EconomicField.LISTED_COMMON_SHARES,
            value=self.listed_shares,
            effective_on=self.market_date,
            share_class=ShareClass.COMMON,
            metadata=self.metadata,
        )


@dataclass(frozen=True, slots=True)
class NonFloatHolding:
    stock_code: str
    holder_id: str
    holder_name: str
    category: NonFloatCategory
    share_class: ShareClass
    shares: int
    effective_on: date
    metadata: SourceMetadata

    def __post_init__(self) -> None:
        require_stock_code(self.stock_code)
        if not self.holder_id or not self.holder_name:
            raise ValueError("holder_id와 holder_name은 비어 있을 수 없습니다.")
        if self.shares < 0:
            raise ValueError("비유동 주식수는 음수일 수 없습니다.")

    @property
    def economic_key(self) -> tuple[str, str, ShareClass]:
        return self.stock_code, self.holder_id, self.share_class


@dataclass(frozen=True, slots=True)
class HoldingCoverageDeclaration:
    stock_code: str
    category: NonFloatCategory
    status: CoverageDeclarationStatus
    effective_on: date
    metadata: SourceMetadata

    def __post_init__(self) -> None:
        require_stock_code(self.stock_code)


@dataclass(frozen=True, slots=True)
class TradingDayObservation:
    market_date: date
    is_trading_day: bool
    session_open: time | None
    session_close: time | None
    version: str
    metadata: SourceMetadata

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("calendar version은 비어 있을 수 없습니다.")
        if self.is_trading_day:
            if self.session_open is None or self.session_close is None:
                raise ValueError("거래일에는 개장·마감 시각이 필요합니다.")
            if self.session_open >= self.session_close:
                raise ValueError("개장 시각은 마감 시각보다 빨라야 합니다.")
        elif self.session_open is not None or self.session_close is not None:
            raise ValueError("비거래일에는 session 시각을 둘 수 없습니다.")


@dataclass(frozen=True, slots=True)
class CorporateActionReference:
    stock_code: str
    effective_on: date
    status: CorporateActionStatus
    adjustment_factor: Decimal | None
    metadata: SourceMetadata

    def __post_init__(self) -> None:
        require_stock_code(self.stock_code)
        if self.status is CorporateActionStatus.CLEAR:
            if self.adjustment_factor != Decimal(1):
                raise ValueError("CLEAR 기업행위의 adjustment_factor는 1이어야 합니다.")
        elif self.status is CorporateActionStatus.ADJUSTED:
            if self.adjustment_factor is None:
                raise ValueError("ADJUSTED 기업행위에는 adjustment_factor가 필요합니다.")
            require_positive_decimal(self.adjustment_factor, "adjustment_factor")
        elif self.adjustment_factor is not None:
            raise ValueError("UNRESOLVED 기업행위에는 factor를 둘 수 없습니다.")


@dataclass(frozen=True, slots=True)
class ReferencePolicy:
    version: str
    free_float_stale_after: timedelta
    sufficient_coverage_ratio: Decimal
    required_non_float_categories: tuple[NonFloatCategory, ...]

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("policy version은 비어 있을 수 없습니다.")
        if self.free_float_stale_after <= timedelta(0):
            raise ValueError("free_float_stale_after는 양수여야 합니다.")
        if (
            not self.sufficient_coverage_ratio.is_finite()
            or not Decimal(0) < self.sufficient_coverage_ratio <= Decimal(1)
        ):
            raise ValueError("sufficient_coverage_ratio는 0 초과 1 이하여야 합니다.")
        if not self.required_non_float_categories:
            raise ValueError("하나 이상의 비유동 범주가 필요합니다.")
        if len(set(self.required_non_float_categories)) != len(
            self.required_non_float_categories
        ):
            raise ValueError("required_non_float_categories가 중복되었습니다.")


@dataclass(frozen=True, slots=True)
class FieldResolution:
    value: int | None
    state: QualityState
    quality_flags: tuple[QualityFlag, ...]
    selected: tuple[FieldObservation, ...]
    lineage: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FreeFloatResult:
    stock_code: str
    effective_on: date
    ratio: Decimal | None
    issued_common_shares: int | None
    deducted_non_float_shares: int | None
    free_float_shares: int | None
    state: QualityState
    quality_flags: tuple[QualityFlag, ...]
    duplicate_deductions_prevented: int
    calculation_version: str
    lineage: tuple[str, ...]

    @property
    def available(self) -> bool:
        return self.state is QualityState.VERIFIED and self.ratio is not None


@dataclass(frozen=True, slots=True)
class ReferenceCoverage:
    status: CoverageStatus
    observed_count: int
    total_count: int
    count_ratio: Decimal | None
    missing_stock_codes: tuple[str, ...]
    conflict_stock_codes: tuple[str, ...]
    stale_stock_codes: tuple[str, ...]
    quality_flags: tuple[QualityFlag, ...]
    policy_version: str

    def __post_init__(self) -> None:
        if self.observed_count < 0 or self.total_count < 0:
            raise ValueError("Coverage count는 음수일 수 없습니다.")
        if self.observed_count > self.total_count:
            raise ValueError("observed_count는 total_count를 초과할 수 없습니다.")
        expected = (
            None
            if self.total_count == 0
            else Decimal(self.observed_count) / Decimal(self.total_count)
        )
        if self.count_ratio != expected:
            raise ValueError("count_ratio가 count와 일치하지 않습니다.")


@dataclass(frozen=True, slots=True)
class AdjustedPriceResolution:
    stock_code: str
    effective_for: date
    previous_trading_day: date | None
    previous_adjusted_close: Decimal | None
    state: QualityState
    quality_flags: tuple[QualityFlag, ...]
    version: str
    lineage: tuple[str, ...]

    @property
    def available(self) -> bool:
        return self.state is QualityState.VERIFIED and self.previous_adjusted_close is not None


@dataclass(frozen=True, slots=True)
class CalendarResolution:
    market_date: date
    is_trading_day: bool | None
    state: QualityState
    quality_flags: tuple[QualityFlag, ...]
    version: str | None
    lineage: tuple[str, ...]
