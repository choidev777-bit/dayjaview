"""Canonical realtime stock inputs and hot-state records."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain import StockMarketObservation
    from events.models import LineageRef
else:
    StockMarketObservation = import_module(
        "packages." + "domain"
    ).StockMarketObservation
    LineageRef = import_module("packages." + "events.models").LineageRef

HOT_STATE_CHECKPOINT_VERSION = "hot-state-2026.08.1"


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name}은 비어 있을 수 없습니다")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}에는 timezone 정보가 필요합니다")


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class StockRealtimeUpdate:
    message_id: str
    stock_id: str
    market_date: date
    source: str
    source_sequence: int
    occurred_at: datetime
    received_at: datetime
    current_price: Decimal | None
    cumulative_trading_value: Decimal | None
    fresh: bool = True
    trading_halted: bool = False
    corporate_action_unresolved: bool = False
    lineage: tuple[LineageRef, ...] = ()

    def __post_init__(self) -> None:
        for field_name, value in (
            ("message_id", self.message_id),
            ("stock_id", self.stock_id),
            ("source", self.source),
        ):
            _require_text(value, field_name)
        if self.source_sequence < 0:
            raise ValueError("source_sequence는 음수일 수 없습니다")
        _require_aware(self.occurred_at, "occurred_at")
        _require_aware(self.received_at, "received_at")
        if self.occurred_at > self.received_at:
            raise ValueError("occurred_at은 received_at 이후일 수 없습니다")
        # Reuse the S1 domain validator so null, zero, and invalid values have
        # exactly the same semantics on the hot and calculation paths.
        self.to_observation()

    def to_observation(self) -> StockMarketObservation:
        return StockMarketObservation(
            stock_id=self.stock_id,
            market_date=self.market_date,
            observed_at=self.occurred_at,
            current_price=self.current_price,
            cumulative_trading_value=self.cumulative_trading_value,
            fresh=self.fresh,
            trading_halted=self.trading_halted,
            corporate_action_unresolved=self.corporate_action_unresolved,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "messageId": self.message_id,
            "stockId": self.stock_id,
            "marketDate": self.market_date.isoformat(),
            "source": self.source,
            "sourceSequence": self.source_sequence,
            "occurredAt": self.occurred_at.isoformat(),
            "receivedAt": self.received_at.isoformat(),
            "currentPrice": _decimal(self.current_price),
            "cumulativeTradingValue": _decimal(self.cumulative_trading_value),
            "fresh": self.fresh,
            "tradingHalted": self.trading_halted,
            "corporateActionUnresolved": self.corporate_action_unresolved,
            "lineage": [item.to_dict() for item in self.lineage],
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class HotStockState:
    stock_id: str
    market_date: date
    version: int
    source: str
    source_sequence: int
    occurred_at: datetime
    received_at: datetime
    current_price: Decimal | None
    cumulative_trading_value: Decimal | None
    fresh: bool
    trading_halted: bool
    corporate_action_unresolved: bool
    last_message_id: str
    lineage: tuple[LineageRef, ...]

    def __post_init__(self) -> None:
        for field_name, value in (
            ("stock_id", self.stock_id),
            ("source", self.source),
            ("last_message_id", self.last_message_id),
        ):
            _require_text(value, field_name)
        if self.version < 1:
            raise ValueError("hot state version은 1 이상이어야 합니다")
        if self.source_sequence < 0:
            raise ValueError("source_sequence는 음수일 수 없습니다")
        _require_aware(self.occurred_at, "occurred_at")
        _require_aware(self.received_at, "received_at")
        if self.occurred_at > self.received_at:
            raise ValueError("occurred_at은 received_at 이후일 수 없습니다")
        self.to_observation()

    @classmethod
    def from_update(
        cls,
        update: StockRealtimeUpdate,
        *,
        version: int,
    ) -> HotStockState:
        return cls(
            stock_id=update.stock_id,
            market_date=update.market_date,
            version=version,
            source=update.source,
            source_sequence=update.source_sequence,
            occurred_at=update.occurred_at,
            received_at=update.received_at,
            current_price=update.current_price,
            cumulative_trading_value=update.cumulative_trading_value,
            fresh=update.fresh,
            trading_halted=update.trading_halted,
            corporate_action_unresolved=update.corporate_action_unresolved,
            last_message_id=update.message_id,
            lineage=update.lineage,
        )

    def to_observation(self) -> StockMarketObservation:
        return StockMarketObservation(
            stock_id=self.stock_id,
            market_date=self.market_date,
            observed_at=self.occurred_at,
            current_price=self.current_price,
            cumulative_trading_value=self.cumulative_trading_value,
            fresh=self.fresh,
            trading_halted=self.trading_halted,
            corporate_action_unresolved=self.corporate_action_unresolved,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "stockId": self.stock_id,
            "marketDate": self.market_date.isoformat(),
            "version": self.version,
            "source": self.source,
            "sourceSequence": self.source_sequence,
            "occurredAt": self.occurred_at.isoformat(),
            "receivedAt": self.received_at.isoformat(),
            "currentPrice": _decimal(self.current_price),
            "cumulativeTradingValue": _decimal(self.cumulative_trading_value),
            "fresh": self.fresh,
            "tradingHalted": self.trading_halted,
            "corporateActionUnresolved": self.corporate_action_unresolved,
            "lastMessageId": self.last_message_id,
            "lineage": [item.to_dict() for item in self.lineage],
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> HotStockState:
        lineage = value["lineage"]
        if not isinstance(lineage, list):
            raise TypeError("checkpoint의 hot state lineage가 잘못되었습니다")
        current_price = value.get("currentPrice")
        cumulative = value.get("cumulativeTradingValue")
        return cls(
            stock_id=str(value["stockId"]),
            market_date=date.fromisoformat(str(value["marketDate"])),
            version=int(str(value["version"])),
            source=str(value["source"]),
            source_sequence=int(str(value["sourceSequence"])),
            occurred_at=datetime.fromisoformat(str(value["occurredAt"])),
            received_at=datetime.fromisoformat(str(value["receivedAt"])),
            current_price=(
                None if current_price is None else Decimal(str(current_price))
            ),
            cumulative_trading_value=(
                None if cumulative is None else Decimal(str(cumulative))
            ),
            fresh=bool(value["fresh"]),
            trading_halted=bool(value["tradingHalted"]),
            corporate_action_unresolved=bool(value["corporateActionUnresolved"]),
            last_message_id=str(value["lastMessageId"]),
            lineage=tuple(LineageRef.from_dict(item) for item in lineage),
        )


class HotApplyDisposition(StrEnum):
    APPLIED = "APPLIED"
    DUPLICATE = "DUPLICATE"
    STALE_SOURCE_SEQUENCE = "STALE_SOURCE_SEQUENCE"
    STALE_RECEIVED_AT = "STALE_RECEIVED_AT"
    STALE_OBSERVATION = "STALE_OBSERVATION"


@dataclass(frozen=True, slots=True)
class HotApplyResult:
    disposition: HotApplyDisposition
    previous: HotStockState | None
    current: HotStockState | None

    @property
    def changed(self) -> bool:
        return self.disposition is HotApplyDisposition.APPLIED


@dataclass(frozen=True, slots=True)
class SourceCursor:
    market_date: date
    stock_id: str
    source: str
    source_sequence: int
    received_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.stock_id, "stock_id")
        _require_text(self.source, "source")
        if self.source_sequence < 0:
            raise ValueError("source_sequence는 음수일 수 없습니다")
        _require_aware(self.received_at, "received_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "marketDate": self.market_date.isoformat(),
            "stockId": self.stock_id,
            "source": self.source,
            "sourceSequence": self.source_sequence,
            "receivedAt": self.received_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> SourceCursor:
        return cls(
            market_date=date.fromisoformat(str(value["marketDate"])),
            stock_id=str(value["stockId"]),
            source=str(value["source"]),
            source_sequence=int(str(value["sourceSequence"])),
            received_at=datetime.fromisoformat(str(value["receivedAt"])),
        )


@dataclass(frozen=True, slots=True)
class HotStateCheckpoint:
    checkpoint_version: str
    created_at: datetime
    as_of: datetime
    states: tuple[HotStockState, ...]
    processed_messages: tuple[tuple[str, str], ...]
    source_cursors: tuple[SourceCursor, ...]

    def __post_init__(self) -> None:
        _require_text(self.checkpoint_version, "checkpoint_version")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.as_of, "as_of")
        if self.as_of > self.created_at:
            raise ValueError("checkpoint as_of는 created_at 이후일 수 없습니다")

    def to_dict(self) -> dict[str, object]:
        return {
            "checkpointVersion": self.checkpoint_version,
            "createdAt": self.created_at.isoformat(),
            "asOf": self.as_of.isoformat(),
            "states": [item.to_dict() for item in self.states],
            "processedMessages": [
                {"messageId": message_id, "fingerprint": fingerprint}
                for message_id, fingerprint in self.processed_messages
            ],
            "sourceCursors": [item.to_dict() for item in self.source_cursors],
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> HotStateCheckpoint:
        states = value["states"]
        processed = value["processedMessages"]
        cursors = value["sourceCursors"]
        if not isinstance(states, list) or not isinstance(processed, list):
            raise TypeError("hot state checkpoint 목록 형식이 잘못되었습니다")
        if not isinstance(cursors, list):
            raise TypeError("hot state checkpoint cursor 형식이 잘못되었습니다")
        processed_pairs: list[tuple[str, str]] = []
        for item in processed:
            if not isinstance(item, dict):
                raise TypeError("processed message 형식이 잘못되었습니다")
            processed_pairs.append((str(item["messageId"]), str(item["fingerprint"])))
        return cls(
            checkpoint_version=str(value["checkpointVersion"]),
            created_at=datetime.fromisoformat(str(value["createdAt"])),
            as_of=datetime.fromisoformat(str(value["asOf"])),
            states=tuple(HotStockState.from_dict(item) for item in states),
            processed_messages=tuple(processed_pairs),
            source_cursors=tuple(SourceCursor.from_dict(item) for item in cursors),
        )

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.to_dict()).encode("utf-8")
        ).hexdigest()

    @property
    def checkpoint_id(self) -> str:
        return f"checkpoint_{self.content_hash[:32]}"
