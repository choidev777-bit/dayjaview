"""Kiwoom source boundary and versioned canonical market event contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

CANONICAL_EVENT_SCHEMA_VERSION = "market-event.v1"
FIXTURE_SCHEMA_VERSION = "kiwoom-market-gateway.fixture.v1"
ADAPTER_VERSION = "kiwoom-read-only.v1"


def require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}에는 timezone 정보가 필요합니다")


def require_stock_id(value: str) -> None:
    prefix, separator, code = value.partition(":")
    if prefix != "KRX" or separator != ":" or len(code) != 6 or not code.isdigit():
        raise ValueError("stock_id는 KRX: 뒤에 6자리 종목코드가 와야 합니다")


class SourceChannel(StrEnum):
    WEBSOCKET = "KIWOOM_WEBSOCKET"
    REST_SNAPSHOT = "KIWOOM_REST_SNAPSHOT"


class CanonicalEventType(StrEnum):
    CANDIDATE_ENTERED = "market.candidate.entered"
    CANDIDATE_EXITED = "market.candidate.exited"
    TRADE = "market.trade"
    SNAPSHOT = "market.snapshot"


class CandidateAction(StrEnum):
    ENTER = "ENTER"
    EXIT = "EXIT"


class ObservationSource(StrEnum):
    REALTIME_0B = "REALTIME_0B"
    REST_KA10095 = "REST_KA10095"


class LiveValidationStatus(StrEnum):
    PENDING_EXTERNAL = "PENDING_EXTERNAL"


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    read_only: bool = True
    market_data: bool = True
    condition_search: bool = True
    snapshot_supplement: bool = True
    orders: bool = False
    accounts: bool = False
    live_validation: LiveValidationStatus = LiveValidationStatus.PENDING_EXTERNAL


@dataclass(frozen=True, slots=True)
class KiwoomConnection:
    session_id: str
    connected_at: datetime

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id는 비어 있을 수 없습니다")
        require_aware(self.connected_at, "connected_at")


@dataclass(frozen=True, slots=True)
class KiwoomSourceEnvelope:
    """Raw provider message plus adapter-assigned ordering metadata."""

    source_schema_version: str
    channel: SourceChannel
    session_id: str
    source_message_id: str
    source_sequence: int
    source_timestamp: datetime
    received_at: datetime
    market_date: date
    payload: Mapping[str, object]
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not self.source_schema_version:
            raise ValueError("source_schema_version은 비어 있을 수 없습니다")
        if not self.session_id or not self.source_message_id:
            raise ValueError("source session/message ID는 비어 있을 수 없습니다")
        if self.source_sequence < 0:
            raise ValueError("source_sequence는 음수일 수 없습니다")
        require_aware(self.source_timestamp, "source_timestamp")
        require_aware(self.received_at, "received_at")


@dataclass(frozen=True, slots=True)
class EventLineage:
    provider: str
    adapter_version: str
    source_schema_version: str
    source_channel: SourceChannel
    session_id: str
    source_message_id: str
    source_item_index: int
    request_id: str | None
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        if self.provider != "KIWOOM":
            raise ValueError("provider는 KIWOOM이어야 합니다")
        if self.source_item_index < 0:
            raise ValueError("source_item_index는 음수일 수 없습니다")
        if len(self.raw_payload_sha256) != 64:
            raise ValueError("raw_payload_sha256는 SHA-256 hex여야 합니다")


@dataclass(frozen=True, slots=True)
class CandidateData:
    action: CandidateAction
    condition_id: str
    source_stock_code: str

    def __post_init__(self) -> None:
        if not self.condition_id:
            raise ValueError("condition_id는 비어 있을 수 없습니다")
        if len(self.source_stock_code) != 6 or not self.source_stock_code.isdigit():
            raise ValueError("source_stock_code는 6자리 숫자여야 합니다")


@dataclass(frozen=True, slots=True)
class MarketObservation:
    observation_source: ObservationSource
    source_stock_code: str
    current_price: Decimal | None
    change_rate: Decimal | None
    # 그날의 기준가. 권리락·액면분할이 있으면 키움이 조정된 값을 준다.
    # KRX 일별매매는 장 마감 후에야 나오므로 장중 전일종가는 이 값이 유일한
    # 원천이다.
    base_price: Decimal | None
    trade_volume: int | None
    cumulative_volume: int | None
    cumulative_trading_value: Decimal | None
    open_price: Decimal | None
    high_price: Decimal | None
    low_price: Decimal | None
    execution_strength: Decimal | None
    market_cap: Decimal | None
    missing_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.source_stock_code) != 6 or not self.source_stock_code.isdigit():
            raise ValueError("source_stock_code는 6자리 숫자여야 합니다")
        for name in (
            "current_price",
            "base_price",
            "cumulative_trading_value",
            "open_price",
            "high_price",
            "low_price",
            "market_cap",
        ):
            value = getattr(self, name)
            if value is not None and (not value.is_finite() or value < 0):
                raise ValueError(f"{name}은 0 이상의 유한한 Decimal이어야 합니다")
        for name in ("change_rate", "execution_strength"):
            value = getattr(self, name)
            if value is not None and not value.is_finite():
                raise ValueError(f"{name}은 유한한 Decimal이어야 합니다")
        for name in ("trade_volume", "cumulative_volume"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name}은 음수일 수 없습니다")
        if tuple(sorted(set(self.missing_fields))) != self.missing_fields:
            raise ValueError("missing_fields는 중복 없이 정렬돼야 합니다")


@dataclass(frozen=True, slots=True)
class CanonicalMarketEvent:
    schema_version: str
    event_id: str
    idempotency_key: str
    event_type: CanonicalEventType
    stock_id: str
    source_sequence: int
    source_timestamp: datetime
    received_at: datetime
    lineage: EventLineage
    data: CandidateData | MarketObservation

    def __post_init__(self) -> None:
        if self.schema_version != CANONICAL_EVENT_SCHEMA_VERSION:
            raise ValueError("지원하지 않는 canonical event schema version입니다")
        if not self.event_id.startswith("mkt_"):
            raise ValueError("event_id는 mkt_ prefix를 사용해야 합니다")
        if not self.idempotency_key.startswith("kiwoom:"):
            raise ValueError("idempotency_key는 kiwoom: prefix를 사용해야 합니다")
        require_stock_id(self.stock_id)
        if self.source_sequence < 0:
            raise ValueError("source_sequence는 음수일 수 없습니다")
        require_aware(self.source_timestamp, "source_timestamp")
        require_aware(self.received_at, "received_at")
        if self.event_type in {
            CanonicalEventType.CANDIDATE_ENTERED,
            CanonicalEventType.CANDIDATE_EXITED,
        } and not isinstance(self.data, CandidateData):
            raise ValueError("candidate event에는 CandidateData가 필요합니다")
        if self.event_type in {
            CanonicalEventType.TRADE,
            CanonicalEventType.SNAPSHOT,
        } and not isinstance(self.data, MarketObservation):
            raise ValueError("market observation event에는 MarketObservation이 필요합니다")

    @property
    def stock_code(self) -> str:
        return self.stock_id.removeprefix("KRX:")

    def to_dict(self) -> dict[str, object]:
        if isinstance(self.data, CandidateData):
            payload: dict[str, object] = {
                "action": self.data.action.value,
                "conditionId": self.data.condition_id,
                "sourceStockCode": self.data.source_stock_code,
            }
        else:
            payload = {
                "observationSource": self.data.observation_source.value,
                "sourceStockCode": self.data.source_stock_code,
                "currentPrice": _decimal_text(self.data.current_price),
                "changeRate": _decimal_text(self.data.change_rate),
                "basePrice": _decimal_text(self.data.base_price),
                "tradeVolume": self.data.trade_volume,
                "cumulativeVolume": self.data.cumulative_volume,
                "cumulativeTradingValue": _decimal_text(
                    self.data.cumulative_trading_value
                ),
                "openPrice": _decimal_text(self.data.open_price),
                "highPrice": _decimal_text(self.data.high_price),
                "lowPrice": _decimal_text(self.data.low_price),
                "executionStrength": _decimal_text(self.data.execution_strength),
                "marketCap": _decimal_text(self.data.market_cap),
                "missingFields": list(self.data.missing_fields),
            }
        return {
            "schemaVersion": self.schema_version,
            "eventId": self.event_id,
            "idempotencyKey": self.idempotency_key,
            "type": self.event_type.value,
            "stockId": self.stock_id,
            "sourceSequence": self.source_sequence,
            "sourceTimestamp": self.source_timestamp.isoformat(),
            "receivedAt": self.received_at.isoformat(),
            "lineage": {
                "provider": self.lineage.provider,
                "adapterVersion": self.lineage.adapter_version,
                "sourceSchemaVersion": self.lineage.source_schema_version,
                "sourceChannel": self.lineage.source_channel.value,
                "sessionId": self.lineage.session_id,
                "sourceMessageId": self.lineage.source_message_id,
                "sourceItemIndex": self.lineage.source_item_index,
                "requestId": self.lineage.request_id,
                "rawPayloadSha256": self.lineage.raw_payload_sha256,
            },
            "data": payload,
        }


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


class ReadOnlyKiwoomPort(Protocol):
    """The only allowed external boundary; intentionally exposes no trading API."""

    @property
    def capabilities(self) -> AdapterCapabilities: ...

    def connect(self, *, now: datetime) -> KiwoomConnection: ...

    def read(self, session_id: str) -> KiwoomSourceEnvelope | None: ...

    def replace_trade_subscriptions(
        self,
        session_id: str,
        stock_ids: Sequence[str],
    ) -> None: ...

    def fetch_watchlist_snapshots(
        self,
        session_id: str,
        stock_ids: Sequence[str],
        *,
        requested_at: datetime,
    ) -> tuple[KiwoomSourceEnvelope, ...]: ...

    def close_session(self, session_id: str) -> None: ...
