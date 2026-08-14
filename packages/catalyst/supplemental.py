"""로컬 근거가 없을 때만 실행하는 서버 보완 검색 게이트."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from threading import RLock

from .models import ThemeContext

SUPPLEMENTAL_COOLDOWN = timedelta(minutes=10)


class SupplementalDenial(StrEnum):
    LOCAL_EVIDENCE_EXISTS = "LOCAL_EVIDENCE_EXISTS"
    COOLDOWN = "COOLDOWN"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"


@dataclass(frozen=True, slots=True)
class SupplementalSearchRequest:
    event_id: str
    theme_id: str
    query_terms: tuple[str, ...]
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class SupplementalDecision:
    request: SupplementalSearchRequest | None
    denial: SupplementalDenial | None


class SupplementalSearchGate:
    """LLM이 아니라 서버가 고정 규칙으로 검색어를 만든다."""

    def __init__(
        self,
        *,
        cooldown: timedelta = SUPPLEMENTAL_COOLDOWN,
        quota_per_window: int = 30,
        window: timedelta = timedelta(hours=1),
    ) -> None:
        self._cooldown = cooldown
        self._quota = quota_per_window
        self._window = window
        self._last_request: dict[str, datetime] = {}
        self._calls: list[datetime] = []
        self._lock = RLock()

    def request(
        self,
        context: ThemeContext,
        *,
        now: datetime,
        has_local_evidence: bool,
    ) -> SupplementalDecision:
        if has_local_evidence:
            return SupplementalDecision(None, SupplementalDenial.LOCAL_EVIDENCE_EXISTS)
        with self._lock:
            previous = self._last_request.get(context.event_id)
            if previous is not None and now - previous < self._cooldown:
                return SupplementalDecision(None, SupplementalDenial.COOLDOWN)
            self._calls = [called for called in self._calls if now - called < self._window]
            if len(self._calls) >= self._quota:
                return SupplementalDecision(None, SupplementalDenial.QUOTA_EXHAUSTED)
            self._last_request[context.event_id] = now
            self._calls.append(now)
        return SupplementalDecision(
            SupplementalSearchRequest(
                event_id=context.event_id,
                theme_id=context.theme_id,
                query_terms=query_terms(context),
                requested_at=now,
            ),
            None,
        )


def query_terms(context: ThemeContext) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            term.strip()
            for term in (context.display_name, *context.synonyms, *context.leader_names)
            if term.strip()
        )
    )
