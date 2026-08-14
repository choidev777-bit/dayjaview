"""허용된 공급원 polling: cursor, rate limit, retry, 공급원별 장애 격리."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from .models import NewsSourceType, RawNewsItem


class SourceStatus(StrEnum):
    HEALTHY = "HEALTHY"
    RETRYING = "RETRYING"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class SourceCursor:
    source_id: str
    source_type: NewsSourceType
    last_source_item_id: str | None = None
    last_published_at: datetime | None = None
    last_polled_at: datetime | None = None
    next_poll_at: datetime | None = None
    status: SourceStatus = SourceStatus.HEALTHY
    last_error: str | None = None
    consecutive_failures: int = 0

    @property
    def degraded(self) -> bool:
        return self.status is not SourceStatus.HEALTHY


class NewsSource(Protocol):
    source_id: str
    source_type: NewsSourceType

    def fetch(self, cursor: SourceCursor) -> Sequence[RawNewsItem]:
        """공급원이 준 새 항목만 돌려준다. 실패는 예외로 알린다."""


@dataclass(frozen=True, slots=True)
class SourceFailure:
    source_id: str
    message: str
    status: SourceStatus


@dataclass(frozen=True, slots=True)
class PollResult:
    items: tuple[RawNewsItem, ...]
    cursors: tuple[SourceCursor, ...]
    failures: tuple[SourceFailure, ...]
    skipped: tuple[str, ...]

    @property
    def degraded_source_ids(self) -> tuple[str, ...]:
        return tuple(cursor.source_id for cursor in self.cursors if cursor.degraded)


class SourcePoller:
    """공급원 하나가 실패해도 나머지 수집을 계속한다."""

    def __init__(
        self,
        sources: Sequence[NewsSource],
        *,
        poll_interval: timedelta = timedelta(seconds=45),
        retry_backoff: timedelta = timedelta(seconds=60),
        failure_threshold: int = 3,
    ) -> None:
        self._sources = tuple(sources)
        self._poll_interval = poll_interval
        self._retry_backoff = retry_backoff
        self._failure_threshold = failure_threshold

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(source.source_id for source in self._sources)

    def poll(
        self,
        cursors: dict[str, SourceCursor],
        *,
        now: datetime,
    ) -> PollResult:
        items: list[RawNewsItem] = []
        updated: list[SourceCursor] = []
        failures: list[SourceFailure] = []
        skipped: list[str] = []
        for source in self._sources:
            cursor = cursors.get(source.source_id) or SourceCursor(
                source_id=source.source_id,
                source_type=source.source_type,
            )
            if cursor.next_poll_at is not None and now < cursor.next_poll_at:
                skipped.append(source.source_id)
                updated.append(cursor)
                continue
            try:
                fetched = tuple(source.fetch(cursor))
            except Exception as error:  # 공급원 장애는 다른 공급원으로 번지지 않는다
                failure_count = cursor.consecutive_failures + 1
                status = (
                    SourceStatus.FAILED
                    if failure_count >= self._failure_threshold
                    else SourceStatus.RETRYING
                )
                failures.append(SourceFailure(source.source_id, str(error), status))
                updated.append(
                    replace(
                        cursor,
                        last_polled_at=now,
                        next_poll_at=now + self._retry_backoff * failure_count,
                        status=status,
                        last_error=str(error),
                        consecutive_failures=failure_count,
                    )
                )
                continue
            items.extend(fetched)
            published = [item.published_at for item in fetched if item.published_at is not None]
            updated.append(
                replace(
                    cursor,
                    last_source_item_id=fetched[-1].source_item_id
                    if fetched
                    else cursor.last_source_item_id,
                    last_published_at=max(published) if published else cursor.last_published_at,
                    last_polled_at=now,
                    next_poll_at=now + self._poll_interval,
                    status=SourceStatus.HEALTHY,
                    last_error=None,
                    consecutive_failures=0,
                )
            )
        return PollResult(
            items=tuple(items),
            cursors=tuple(updated),
            failures=tuple(failures),
            skipped=tuple(skipped),
        )
