"""테마 history 라벨의 PostgreSQL 적재 (E-17).

`label_theme_history.py`는 파일 산출물만 만든다. 이 모듈은 같은 분류 결과를
`ontology` 스키마에 넣어 소재 유형을 조건으로 거는 질의를 가능하게 한다.

적재는 덮어쓰기가 아니라 (history_id, 어휘 버전, 변환 버전) append다. 같은
입력으로 다시 실행하면 아무 행도 늘지 않는다. DB 원문과 라벨 대상 원문이
다르면 span 오프셋이 어긋나므로 그 기록은 넣지 않고 세기만 한다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .labeling import HistoryRecord
from .transform import TRANSFORM_VERSION, classify_catalyst
from .vocabulary import VOCABULARY, VOCABULARY_VERSION, vocabulary_content_hash


class DbCursor(Protocol):
    rowcount: int

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


class VocabularyConflictError(RuntimeError):
    """같은 어휘 버전이 다른 내용으로 이미 등록돼 있다."""


@dataclass(frozen=True, slots=True)
class LoadCounts:
    """적재 결과 집계. total은 입력 기록 수다."""

    total: int
    inserted: int
    existing: int
    unresolved: int
    mismatched: int


class PostgresCatalystLabelStore:
    """`ontology` 스키마에 통제어휘와 history 라벨을 적재한다."""

    def __init__(self, connection: DbConnection) -> None:
        self._connection = connection

    def sync_vocabulary(self, *, registered_at: datetime) -> bool:
        """현재 어휘 버전을 등록한다. 이미 있으면 content hash 일치를 확인한다."""

        content_hash = vocabulary_content_hash()
        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT content_hash FROM ontology.catalyst_vocabularies"
                " WHERE vocabulary_version = %s",
                (VOCABULARY_VERSION,),
            )
            row = db.fetchone()
            if row is not None:
                if str(row[0]) != content_hash:
                    raise VocabularyConflictError(
                        f"어휘 버전 {VOCABULARY_VERSION}이(가) 다른 내용으로 이미 "
                        "등록돼 있습니다. 어휘를 고쳤다면 VOCABULARY_VERSION을 "
                        "올리십시오."
                    )
                return False
            db.execute(
                "INSERT INTO ontology.catalyst_vocabularies"
                " (vocabulary_version, content_hash, registered_at)"
                " VALUES (%s, %s, %s)",
                (VOCABULARY_VERSION, content_hash, registered_at),
            )
            for source_order, definition in enumerate(VOCABULARY):
                db.execute(
                    "INSERT INTO ontology.catalyst_types"
                    " (vocabulary_version, type_id, name_ko, description_ko,"
                    " source_order) VALUES (%s, %s, %s, %s, %s)",
                    (
                        VOCABULARY_VERSION,
                        definition.type_id,
                        definition.name_ko,
                        definition.description_ko,
                        source_order,
                    ),
                )
            self._connection.commit()
            return True
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()

    def current_history(self) -> dict[tuple[str, str], tuple[int, str]]:
        """(테마 원천 번호, history key) → (history_id, 원문) 현재 revision 지도."""

        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT t.source_theme_id, h.source_history_key, h.history_id,"
                " h.raw_text"
                " FROM core.infostock_theme_history h"
                " JOIN core.infostock_themes t ON t.theme_id = h.theme_id"
                " WHERE h.observed_to IS NULL"
            )
            return {
                (str(row[0]), str(row[1])): (int(row[2]), str(row[3]))
                for row in db.fetchall()
            }
        finally:
            db.close()

    def load(
        self, records: Iterable[HistoryRecord], *, labeled_at: datetime
    ) -> LoadCounts:
        """기록마다 분류를 붙여 적재하고 건수를 돌려준다."""

        history = self.current_history()
        total = 0
        inserted = 0
        existing = 0
        unresolved = 0
        mismatched = 0
        db = self._connection.cursor()
        try:
            for record in records:
                total += 1
                found = history.get((record.theme_id, record.source_history_key))
                if found is None:
                    unresolved += 1
                    continue
                history_id, stored_text = found
                if stored_text != record.raw_text:
                    mismatched += 1
                    continue
                classification = classify_catalyst(record.raw_text)
                db.execute(
                    "INSERT INTO ontology.theme_history_labels"
                    " (history_id, vocabulary_version, transform_version, type_ids,"
                    " primary_type_id, direction, certainty, continuation, labeled_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (history_id, vocabulary_version, transform_version)"
                    " DO NOTHING RETURNING label_id",
                    (
                        history_id,
                        VOCABULARY_VERSION,
                        TRANSFORM_VERSION,
                        list(classification.type_ids),
                        classification.primary_type_id,
                        classification.direction,
                        classification.certainty,
                        classification.continuation,
                        labeled_at,
                    ),
                )
                row = db.fetchone()
                if row is None:
                    existing += 1
                    continue
                label_id = int(row[0])
                for source_order, span in enumerate(classification.evidence_spans):
                    db.execute(
                        "INSERT INTO ontology.theme_history_label_spans"
                        " (label_id, source_order, field, value, keyword,"
                        " start_offset, end_offset) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (
                            label_id,
                            source_order,
                            span.field,
                            span.value,
                            span.keyword,
                            span.start,
                            span.end,
                        ),
                    )
                inserted += 1
            self._connection.commit()
            return LoadCounts(
                total=total,
                inserted=inserted,
                existing=existing,
                unresolved=unresolved,
                mismatched=mismatched,
            )
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()
