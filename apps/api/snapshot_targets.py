"""저장(관심) 대상을 파이프라인이 아는 실테마·실이벤트·실종목으로 해석한다.

fixture 타깃 목록 대신 이 catalog가 그날 파이프라인을 직접 조회한다. 명단에
없는 대상은 저장을 받지 않고(404), 저장된 항목의 현재 상태(오늘 Event·상태·
가중수익률)는 rankings가 쓰는 것과 같은 스냅샷에서 읽는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, cast

from packages.identity import SavedCurrentState, SavedType, TargetRecord
from packages.realtime import ReadSnapshot


class TargetSource(Protocol):
    """MarketDataPipeline이 이미 만족하는 읽기 전용 표면."""

    @property
    def latest_rankings(self) -> ReadSnapshot | None: ...

    @property
    def theme_names(self) -> Mapping[str, str]: ...

    @property
    def stock_names(self) -> Mapping[str, str]: ...

    def event_id_for_theme(self, theme_id: str) -> str | None: ...

    def theme_detail(self, event_id: str) -> dict[str, object] | None: ...


class SnapshotTargetCatalog:
    def __init__(self, source: TargetSource) -> None:
        self._source = source

    def get_target(
        self,
        saved_type: SavedType,
        target_id: str,
    ) -> TargetRecord | None:
        if saved_type is SavedType.THEME:
            return self._theme(target_id)
        if saved_type is SavedType.EVENT:
            return self._event(target_id)
        return self._stock(target_id)

    def _theme(self, theme_id: str) -> TargetRecord | None:
        display_name = self._source.theme_names.get(theme_id)
        if display_name is None:
            return None
        event_id = self._source.event_id_for_theme(theme_id)
        detail = None if event_id is None else self._source.theme_detail(event_id)
        return TargetRecord(
            saved_type=SavedType.THEME,
            target_id=theme_id,
            display_name=display_name,
            current_state=self._current_state(detail),
        )

    def _event(self, event_id: str) -> TargetRecord | None:
        """상세를 공개하지 않는 Event는 저장 대상으로도 알려 주지 않는다."""

        detail = self._source.theme_detail(event_id)
        if detail is None:
            return None
        classification = cast("dict[str, object]", detail["classification"])
        return TargetRecord(
            saved_type=SavedType.EVENT,
            target_id=event_id,
            display_name=str(classification["displayName"]),
            current_state=self._current_state(detail),
        )

    def _stock(self, stock_id: str) -> TargetRecord | None:
        display_name = self._source.stock_names.get(stock_id)
        if display_name is None:
            return None
        # SavedCurrentState는 Event 상태라 종목 저장에는 채울 값이 없다.
        return TargetRecord(SavedType.STOCK, stock_id, display_name)

    def _current_state(
        self,
        detail: dict[str, object] | None,
    ) -> SavedCurrentState | None:
        """아직 공개 상태가 아닌 테마·발행 전에는 현재 상태가 없다."""

        snapshot = self._source.latest_rankings
        if detail is None or snapshot is None:
            return None
        reaction = cast("dict[str, object]", detail["currentReaction"])
        return SavedCurrentState(
            event_id=str(detail["eventId"]),
            event_state=str(detail["lifecycleStatus"]),
            weighted_return=cast("float | None", reaction["weightedReturn"]),
            data_status=snapshot.data_status.value,
            as_of=snapshot.as_of,
        )
