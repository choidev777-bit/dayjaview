"""Latest-by-stock hot state with deterministic stale and retry handling."""

from __future__ import annotations

from datetime import date, datetime
from threading import RLock

from .models import (
    HOT_STATE_CHECKPOINT_VERSION,
    HotApplyDisposition,
    HotApplyResult,
    HotStateCheckpoint,
    HotStockState,
    SourceCursor,
    StockRealtimeUpdate,
)


class HotStateIdempotencyConflict(ValueError):
    pass


class HotStateStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._states: dict[tuple[date, str], HotStockState] = {}
        self._processed_messages: dict[str, str] = {}
        self._source_cursors: dict[tuple[date, str, str], SourceCursor] = {}

    def apply(self, update: StockRealtimeUpdate) -> HotApplyResult:
        with self._lock:
            key = (update.market_date, update.stock_id)
            previous = self._states.get(key)
            fingerprint = update.fingerprint
            seen = self._processed_messages.get(update.message_id)
            if seen is not None:
                if seen != fingerprint:
                    raise HotStateIdempotencyConflict(
                        "같은 message_id에 서로 다른 realtime update가 있습니다"
                    )
                return HotApplyResult(
                    disposition=HotApplyDisposition.DUPLICATE,
                    previous=previous,
                    current=previous,
                )

            cursor_key = (update.market_date, update.stock_id, update.source)
            cursor = self._source_cursors.get(cursor_key)
            if cursor is not None and update.source_sequence <= cursor.source_sequence:
                self._processed_messages[update.message_id] = fingerprint
                return HotApplyResult(
                    disposition=HotApplyDisposition.STALE_SOURCE_SEQUENCE,
                    previous=previous,
                    current=previous,
                )
            if cursor is not None and update.received_at < cursor.received_at:
                self._record_cursor(cursor_key, update)
                self._processed_messages[update.message_id] = fingerprint
                return HotApplyResult(
                    disposition=HotApplyDisposition.STALE_RECEIVED_AT,
                    previous=previous,
                    current=previous,
                )

            self._record_cursor(cursor_key, update)
            self._processed_messages[update.message_id] = fingerprint
            if previous is not None:
                incoming_order = (
                    update.occurred_at,
                    update.received_at,
                    update.source,
                    update.source_sequence,
                    update.message_id,
                )
                current_order = (
                    previous.occurred_at,
                    previous.received_at,
                    previous.source,
                    previous.source_sequence,
                    previous.last_message_id,
                )
                if incoming_order <= current_order:
                    return HotApplyResult(
                        disposition=HotApplyDisposition.STALE_OBSERVATION,
                        previous=previous,
                        current=previous,
                    )

            current = HotStockState.from_update(
                update,
                version=1 if previous is None else previous.version + 1,
            )
            self._states[key] = current
            return HotApplyResult(
                disposition=HotApplyDisposition.APPLIED,
                previous=previous,
                current=current,
            )

    def _record_cursor(
        self,
        key: tuple[date, str, str],
        update: StockRealtimeUpdate,
    ) -> None:
        previous = self._source_cursors.get(key)
        self._source_cursors[key] = SourceCursor(
            market_date=update.market_date,
            stock_id=update.stock_id,
            source=update.source,
            source_sequence=update.source_sequence,
            received_at=(
                update.received_at
                if previous is None
                else max(previous.received_at, update.received_at)
            ),
        )

    def get(self, *, market_date: date, stock_id: str) -> HotStockState | None:
        with self._lock:
            return self._states.get((market_date, stock_id))

    def states_for(
        self,
        *,
        market_date: date,
        stock_ids: tuple[str, ...],
    ) -> tuple[HotStockState, ...]:
        with self._lock:
            return tuple(
                state
                for stock_id in sorted(set(stock_ids))
                if (state := self._states.get((market_date, stock_id))) is not None
            )

    def checkpoint(self, *, created_at: datetime) -> HotStateCheckpoint:
        with self._lock:
            observed_times = [state.received_at for state in self._states.values()]
            observed_times.extend(
                cursor.received_at for cursor in self._source_cursors.values()
            )
            as_of = max(observed_times, default=created_at)
            return HotStateCheckpoint(
                checkpoint_version=HOT_STATE_CHECKPOINT_VERSION,
                created_at=created_at,
                as_of=as_of,
                states=tuple(self._states[key] for key in sorted(self._states)),
                processed_messages=tuple(sorted(self._processed_messages.items())),
                source_cursors=tuple(
                    self._source_cursors[key] for key in sorted(self._source_cursors)
                ),
            )

    @classmethod
    def restore(cls, checkpoint: HotStateCheckpoint) -> HotStateStore:
        if checkpoint.checkpoint_version != HOT_STATE_CHECKPOINT_VERSION:
            raise ValueError(
                "지원하지 않는 hot state checkpoint version입니다: "
                f"{checkpoint.checkpoint_version}"
            )
        store = cls()
        for state in checkpoint.states:
            state_key = (state.market_date, state.stock_id)
            if state_key in store._states:
                raise ValueError("checkpoint에 중복 hot stock state가 있습니다")
            store._states[state_key] = state
        store._processed_messages = dict(checkpoint.processed_messages)
        if len(store._processed_messages) != len(checkpoint.processed_messages):
            raise ValueError("checkpoint에 중복 processed message가 있습니다")
        for cursor in checkpoint.source_cursors:
            cursor_key = (cursor.market_date, cursor.stock_id, cursor.source)
            if cursor_key in store._source_cursors:
                raise ValueError("checkpoint에 중복 source cursor가 있습니다")
            store._source_cursors[cursor_key] = cursor
        return store
