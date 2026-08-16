"""PostgreSQL-backed catalyst evidence revisions and supporting records."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, cast

from packages.llm import LlmCallRecord

from .matching import MATCH_MODEL_VERSION
from .models import (
    CatalystEvidence,
    EvidenceRevision,
    EvidenceStatus,
    ExtractionMethod,
    MatchBasis,
    NewsThemeMatch,
)
from .policy import EvidenceDecision
from .revisions import _unchanged


class DbCursor(Protocol):
    def execute(
        self, query: str, params: Sequence[object] | None = None
    ) -> object: ...

    def fetchone(self) -> Sequence[Any] | None: ...

    def fetchall(self) -> Sequence[Sequence[Any]]: ...

    def close(self) -> None: ...


class DbConnection(Protocol):
    def cursor(self) -> DbCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


_REVISION_COLUMNS = (
    "event_id, revision, evidence_status, summary, news_ids, catalyst_key, "
    "reason, policy_version, decided_at, evidence_confirmed_at"
)


def _revision(row: Sequence[Any]) -> EvidenceRevision:
    return EvidenceRevision(
        event_id=str(row[0]),
        revision=int(row[1]),
        evidence_status=EvidenceStatus(str(row[2])),
        summary=None if row[3] is None else str(row[3]),
        news_ids=tuple(str(value) for value in row[4]),
        catalyst_key=None if row[5] is None else str(row[5]),
        reason=str(row[6]),
        policy_version=str(row[7]),
        decided_at=cast(datetime, row[8]),
        evidence_confirmed_at=cast(datetime | None, row[9]),
    )


class PostgresEvidenceRepository:
    """Append-only revisions plus their news matches, LLM calls, and evidence."""

    def __init__(self, connection: DbConnection) -> None:
        self._connection = connection

    def current(self, event_id: str) -> EvidenceRevision | None:
        db = self._connection.cursor()
        try:
            db.execute(
                f"SELECT {_REVISION_COLUMNS} FROM news.evidence_revisions "
                "WHERE event_id = %s ORDER BY revision DESC LIMIT 1",
                (event_id,),
            )
            row = db.fetchone()
            return None if row is None else _revision(row)
        finally:
            db.close()

    def history(self, event_id: str) -> tuple[EvidenceRevision, ...]:
        db = self._connection.cursor()
        try:
            db.execute(
                f"SELECT {_REVISION_COLUMNS} FROM news.evidence_revisions "
                "WHERE event_id = %s ORDER BY revision",
                (event_id,),
            )
            return tuple(_revision(row) for row in db.fetchall())
        finally:
            db.close()

    def record(
        self,
        event_id: str,
        decision: EvidenceDecision,
        *,
        now: datetime,
    ) -> EvidenceRevision:
        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"dayjaview:evidence:{event_id}",),
            )
            db.execute(
                f"SELECT {_REVISION_COLUMNS} FROM news.evidence_revisions "
                "WHERE event_id = %s ORDER BY revision DESC LIMIT 1 FOR UPDATE",
                (event_id,),
            )
            row = db.fetchone()
            previous = None if row is None else _revision(row)
            if previous is not None and _unchanged(previous, decision):
                self._connection.commit()
                return previous
            confirmed_at = (
                now
                if decision.news_ids
                and (previous is None or previous.news_ids != decision.news_ids)
                else (previous.evidence_confirmed_at if previous else None)
            )
            revision = EvidenceRevision(
                event_id=event_id,
                revision=1 if previous is None else previous.revision + 1,
                evidence_status=decision.evidence_status,
                summary=decision.summary,
                news_ids=decision.news_ids,
                catalyst_key=decision.catalyst_key,
                reason=decision.reason,
                policy_version=decision.policy_version,
                decided_at=now,
                evidence_confirmed_at=confirmed_at,
            )
            db.execute(
                """
                INSERT INTO news.evidence_revisions (
                    event_id, revision, evidence_status, summary, news_ids,
                    catalyst_key, reason, policy_version, decided_at,
                    evidence_confirmed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    revision.event_id,
                    revision.revision,
                    revision.evidence_status.value,
                    revision.summary,
                    list(revision.news_ids),
                    revision.catalyst_key,
                    revision.reason,
                    revision.policy_version,
                    revision.decided_at,
                    revision.evidence_confirmed_at,
                ),
            )
            self._connection.commit()
            return revision
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()

    def save_supporting_records(
        self,
        *,
        matches: Sequence[NewsThemeMatch],
        evidence: Sequence[CatalystEvidence],
        llm_record: LlmCallRecord | None,
    ) -> None:
        db = self._connection.cursor()
        try:
            for match in matches:
                db.execute(
                    """
                    INSERT INTO news.theme_matches (
                        news_id, event_id, theme_id, matched_stock_ids,
                        match_basis, trigger_type, rule_score, relevance_score,
                        match_model_version, matched_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (news_id, event_id, matched_at) DO NOTHING
                    """,
                    (
                        match.news_id,
                        match.event_id,
                        match.theme_id,
                        list(match.matched_stock_ids),
                        [basis.value for basis in match.match_basis],
                        match.trigger.value,
                        match.rule_score,
                        match.relevance_score,
                        MATCH_MODEL_VERSION,
                        match.matched_at,
                    ),
                )
            llm_call_id = self._save_llm_call(db, llm_record)
            for item in evidence:
                db.execute(
                    """
                    INSERT INTO news.catalyst_evidence (
                        event_id, news_id, summary, match_basis, entities,
                        quality_flags, extraction_method, llm_call_id,
                        confidence, generated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_id, news_id) DO UPDATE
                       SET summary = EXCLUDED.summary,
                           match_basis = EXCLUDED.match_basis,
                           entities = EXCLUDED.entities,
                           quality_flags = EXCLUDED.quality_flags,
                           extraction_method = EXCLUDED.extraction_method,
                           llm_call_id = EXCLUDED.llm_call_id,
                           confidence = EXCLUDED.confidence,
                           generated_at = EXCLUDED.generated_at
                    """,
                    (
                        item.event_id,
                        item.news_id,
                        item.summary,
                        [basis.value for basis in item.match_basis],
                        list(item.entities),
                        list(item.quality_flags),
                        item.extraction_method.value,
                        llm_call_id,
                        item.confidence,
                        item.generated_at,
                    ),
                )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _save_llm_call(
        db: DbCursor, record: LlmCallRecord | None
    ) -> int | None:
        if record is None:
            return None
        db.execute(
            """
            INSERT INTO news.llm_calls (
                model_name, prompt_version, news_ids, request_fingerprint,
                raw_output, accepted, rejection, called_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING llm_call_id
            """,
            (
                record.model_name,
                record.prompt_version,
                list(record.news_ids),
                record.request_fingerprint,
                record.raw_output,
                record.accepted,
                None if record.rejection is None else record.rejection.value,
                record.called_at,
            ),
        )
        row = db.fetchone()
        if row is None:
            raise RuntimeError("LLM 호출 기록 저장 결과가 없습니다")
        return int(row[0])

    def load(
        self, event_id: str
    ) -> tuple[EvidenceRevision, tuple[CatalystEvidence, ...]] | None:
        revision = self.current(event_id)
        if revision is None:
            return None
        if not revision.news_ids:
            return revision, ()
        db = self._connection.cursor()
        try:
            db.execute(
                """
                SELECT evidence.news_id, evidence.event_id, item.publisher,
                       item.title, evidence.summary, evidence.match_basis,
                       evidence.entities, item.published_at, item.retrieved_at,
                       item.original_url, evidence.quality_flags,
                       evidence.extraction_method, calls.model_name,
                       calls.prompt_version, evidence.confidence,
                       evidence.generated_at
                  FROM news.catalyst_evidence AS evidence
                  JOIN news.items AS item ON item.news_id = evidence.news_id
             LEFT JOIN news.llm_calls AS calls
                    ON calls.llm_call_id = evidence.llm_call_id
                 WHERE evidence.event_id = %s
                   AND evidence.news_id = ANY(%s::text[])
                """,
                (event_id, list(revision.news_ids)),
            )
            by_id = {
                str(row[0]): CatalystEvidence(
                    news_id=str(row[0]),
                    event_id=str(row[1]),
                    publisher=str(row[2]),
                    title=str(row[3]),
                    summary=str(row[4]),
                    match_basis=tuple(MatchBasis(str(value)) for value in row[5]),
                    entities=tuple(str(value) for value in row[6]),
                    published_at=cast(datetime | None, row[7]),
                    received_at=cast(datetime, row[8]),
                    original_url=str(row[9]),
                    quality_flags=tuple(str(value) for value in row[10]),
                    extraction_method=ExtractionMethod(str(row[11])),
                    model_name=None if row[12] is None else str(row[12]),
                    prompt_version=None if row[13] is None else str(row[13]),
                    confidence=None if row[14] is None else float(row[14]),
                    generated_at=cast(datetime, row[15]),
                )
                for row in db.fetchall()
            }
            ordered = tuple(
                by_id[news_id]
                for news_id in revision.news_ids
                if news_id in by_id
            )
            return revision, ordered
        finally:
            db.close()
