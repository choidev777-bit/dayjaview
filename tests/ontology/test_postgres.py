"""테마 history 라벨 PostgreSQL 적재 (E-17 선행 단계)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from packages.ontology import (
    TRANSFORM_VERSION,
    VOCABULARY,
    VOCABULARY_VERSION,
    HistoryRecord,
    PostgresCatalystLabelStore,
    VocabularyConflictError,
    vocabulary_content_hash,
)

NOW = datetime(2026, 8, 17, 1, 0, tzinfo=UTC)
FIRST = "정부 원전 수출 지원 방안 발표 소식 등에 상승(주도주 : 두산에너빌리티)"
SECOND = "체코 원전 수주 기대감 지속 등에 상승"


class FakeDatabase:
    """적재에 쓰이는 질의만 흉내내는 최소 저장소."""

    def __init__(
        self,
        history: dict[tuple[str, str], tuple[int, str]],
        *,
        vocabulary_hash: str | None = None,
    ) -> None:
        self.history = history
        self.vocabularies: dict[str, str] = (
            {} if vocabulary_hash is None else {VOCABULARY_VERSION: vocabulary_hash}
        )
        self.types: list[tuple[Any, ...]] = []
        self.labels: dict[tuple[int, str, str], dict[str, Any]] = {}
        self.spans: list[dict[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self._next_label_id = 1

    def insert_label(self, params: tuple[Any, ...]) -> int | None:
        key = (int(params[0]), str(params[1]), str(params[2]))
        if key in self.labels:
            return None
        label_id = self._next_label_id
        self._next_label_id += 1
        self.labels[key] = {
            "label_id": label_id,
            "history_id": key[0],
            "type_ids": tuple(params[3]),
            "primary_type_id": params[4],
            "direction": params[5],
            "certainty": params[6],
            "continuation": params[7],
            "labeled_at": params[8],
        }
        return label_id


class FakeCursor:
    def __init__(self, database: FakeDatabase) -> None:
        self._database = database
        self._result: list[Any] = []
        self.rowcount = 0
        self.closed = False

    def execute(self, query: str, params: Any = None) -> None:
        self._result = []
        if "SELECT content_hash FROM ontology.catalyst_vocabularies" in query:
            stored = self._database.vocabularies.get(str(params[0]))
            self._result = [] if stored is None else [(stored,)]
        elif "INSERT INTO ontology.catalyst_vocabularies" in query:
            self._database.vocabularies[str(params[0])] = str(params[1])
        elif "INSERT INTO ontology.catalyst_types" in query:
            self._database.types.append(tuple(params))
        elif "FROM core.infostock_theme_history" in query:
            self._result = [
                (theme_id, history_key, history_id, raw_text)
                for (theme_id, history_key), (
                    history_id,
                    raw_text,
                ) in self._database.history.items()
            ]
        elif "INSERT INTO ontology.theme_history_labels" in query:
            label_id = self._database.insert_label(tuple(params))
            self._result = [] if label_id is None else [(label_id,)]
        elif "INSERT INTO ontology.theme_history_label_spans" in query:
            self._database.spans.append(
                {
                    "label_id": int(params[0]),
                    "source_order": int(params[1]),
                    "field": str(params[2]),
                    "value": str(params[3]),
                    "keyword": str(params[4]),
                    "start_offset": int(params[5]),
                    "end_offset": int(params[6]),
                }
            )
        else:  # pragma: no cover - 예상하지 못한 질의는 테스트 실패로 드러난다
            raise AssertionError(f"흉내내지 않는 질의입니다: {query}")

    def fetchone(self) -> Any:
        return self._result[0] if self._result else None

    def fetchall(self) -> list[Any]:
        return list(self._result)

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, database: FakeDatabase) -> None:
        self._database = database

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._database)

    def commit(self) -> None:
        self._database.commits += 1

    def rollback(self) -> None:
        self._database.rollbacks += 1


def _record(theme_id: str, key: str, text: str) -> HistoryRecord:
    return HistoryRecord(
        theme_id=theme_id,
        theme_name="원자력발전",
        source_history_key=key,
        event_date=None,
        raw_text=text,
    )


def _store(database: FakeDatabase) -> PostgresCatalystLabelStore:
    return PostgresCatalystLabelStore(FakeConnection(database))  # type: ignore[arg-type]


def test_sync_vocabulary_registers_every_type_once() -> None:
    database = FakeDatabase({})
    store = _store(database)

    assert store.sync_vocabulary(registered_at=NOW) is True
    assert database.vocabularies[VOCABULARY_VERSION] == vocabulary_content_hash()
    assert len(database.types) == len(VOCABULARY)
    assert [row[1] for row in database.types] == [
        definition.type_id for definition in VOCABULARY
    ]
    assert [row[4] for row in database.types] == list(range(len(VOCABULARY)))

    assert store.sync_vocabulary(registered_at=NOW) is False
    assert len(database.types) == len(VOCABULARY)


def test_sync_vocabulary_rejects_same_version_with_different_content() -> None:
    database = FakeDatabase({}, vocabulary_hash="0" * 64)
    store = _store(database)

    with pytest.raises(VocabularyConflictError):
        store.sync_vocabulary(registered_at=NOW)

    assert database.rollbacks == 1


def test_load_inserts_labels_with_spans_and_counts() -> None:
    database = FakeDatabase(
        {("7", "source:1"): (11, FIRST), ("7", "source:2"): (12, SECOND)}
    )
    counts = _store(database).load(
        (_record("7", "source:1", FIRST), _record("7", "source:2", SECOND)),
        labeled_at=NOW,
    )

    assert (counts.total, counts.inserted, counts.existing) == (2, 2, 0)
    assert (counts.unresolved, counts.mismatched) == (0, 0)
    assert set(database.labels) == {
        (11, VOCABULARY_VERSION, TRANSFORM_VERSION),
        (12, VOCABULARY_VERSION, TRANSFORM_VERSION),
    }
    first = database.labels[(11, VOCABULARY_VERSION, TRANSFORM_VERSION)]
    assert first["primary_type_id"] == first["type_ids"][0]
    assert first["direction"] == "UP"
    assert first["labeled_at"] == NOW
    second = database.labels[(12, VOCABULARY_VERSION, TRANSFORM_VERSION)]
    assert second["certainty"] == "ANTICIPATION"
    assert second["continuation"] is True
    assert database.spans
    assert database.commits == 1


def test_load_spans_stay_inside_stored_raw_text() -> None:
    database = FakeDatabase({("7", "source:1"): (11, FIRST)})
    _store(database).load((_record("7", "source:1", FIRST),), labeled_at=NOW)

    for span in database.spans:
        assert 0 <= span["start_offset"] < span["end_offset"] <= len(FIRST)
        assert FIRST[span["start_offset"] : span["end_offset"]] == span["keyword"]
    orders = [span["source_order"] for span in database.spans]
    assert orders == list(range(len(orders)))


def test_load_is_idempotent_on_rerun() -> None:
    database = FakeDatabase({("7", "source:1"): (11, FIRST)})
    store = _store(database)
    records = (_record("7", "source:1", FIRST),)

    first = store.load(records, labeled_at=NOW)
    span_count = len(database.spans)
    second = store.load(records, labeled_at=NOW)

    assert (first.inserted, first.existing) == (1, 0)
    assert (second.inserted, second.existing) == (0, 1)
    assert len(database.labels) == 1
    assert len(database.spans) == span_count


def test_load_skips_history_missing_or_with_different_raw_text() -> None:
    database = FakeDatabase({("7", "source:2"): (12, "다른 원문에 상승")})
    counts = _store(database).load(
        (_record("7", "source:1", FIRST), _record("7", "source:2", SECOND)),
        labeled_at=NOW,
    )

    assert (counts.total, counts.inserted) == (2, 0)
    assert (counts.unresolved, counts.mismatched) == (1, 1)
    assert database.labels == {}
    assert database.spans == []
