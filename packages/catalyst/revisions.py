"""Event별 근거 revision. 덮어쓰지 않고 이력을 append 한다."""

from __future__ import annotations

from datetime import datetime
from threading import RLock

from .models import EvidenceRevision
from .policy import EvidenceDecision


class EvidenceRevisionStore:
    def __init__(self) -> None:
        self._revisions: dict[str, list[EvidenceRevision]] = {}
        self._lock = RLock()

    def current(self, event_id: str) -> EvidenceRevision | None:
        with self._lock:
            history = self._revisions.get(event_id)
            return history[-1] if history else None

    def history(self, event_id: str) -> tuple[EvidenceRevision, ...]:
        with self._lock:
            return tuple(self._revisions.get(event_id, ()))

    def record(
        self,
        event_id: str,
        decision: EvidenceDecision,
        *,
        now: datetime,
    ) -> EvidenceRevision:
        """상태·요약·근거 기사 집합이 바뀔 때만 새 revision을 만든다."""

        with self._lock:
            history = self._revisions.setdefault(event_id, [])
            previous = history[-1] if history else None
            if previous is not None and _unchanged(previous, decision):
                return previous
            confirmed_at = (
                now
                if decision.news_ids
                and (previous is None or previous.news_ids != decision.news_ids)
                else (previous.evidence_confirmed_at if previous else None)
            )
            revision = EvidenceRevision(
                event_id=event_id,
                revision=len(history) + 1,
                evidence_status=decision.evidence_status,
                summary=decision.summary,
                news_ids=decision.news_ids,
                catalyst_key=decision.catalyst_key,
                reason=decision.reason,
                policy_version=decision.policy_version,
                decided_at=now,
                evidence_confirmed_at=confirmed_at,
            )
            history.append(revision)
            return revision


def _unchanged(previous: EvidenceRevision, decision: EvidenceDecision) -> bool:
    return (
        previous.evidence_status is decision.evidence_status
        and previous.summary == decision.summary
        and previous.news_ids == decision.news_ids
        and previous.catalyst_key == decision.catalyst_key
    )
