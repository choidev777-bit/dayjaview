"""Daily source mention PostgreSQL 적재 (E-22 단계 1)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from packages.infostock.hashing import sha256_text
from packages.infostock.models import DailyPost, DailyRelation
from packages.ontology import (
    DailyMentionTransformConflictError,
    PostgresDailyMentionStore,
    mentions_from_daily_post,
)


def _relation(order: int, relation_type: str, raw_text: str) -> DailyRelation:
    return DailyRelation(  # type: ignore[arg-type]
        source_order=order,
        relation_type=relation_type,
        source_theme_name="방산",
        source_stock_name=None,
        source_stock_code=None,
        description=raw_text,
        raw_text=raw_text,
        quality_status="OK",
        paragraph_no=0,
    )


def _post(relation: DailyRelation) -> DailyPost:
    return DailyPost(
        source_post_key="post-1",
        source_post_id="1",
        source_url="https://example.test/1",
        title="[5/2] 특징테마",
        published_date=date(2024, 5, 3),
        source_date="20240503",
        raw_body=relation.raw_text,
        body_hash=sha256_text(relation.raw_text),
        normalized_hash="a" * 64,
        body_status="OK",
        visibility_status="VISIBLE",
        relations=(relation,),
        detail_snapshot=None,
    )


class _Database:
    def __init__(self) -> None:
        self.relations: dict[tuple[str, int], tuple[int, str, date, str, str]] = {}
        self.mentions: dict[int, dict[str, Any]] = {}
        self.links: dict[tuple[int, str, str], int] = {}
        self.next_id = 1
        self.commits = 0
        self.rollbacks = 0


class _Cursor:
    def __init__(self, database: _Database) -> None:
        self.database = database
        self.result: list[Any] = []

    @property
    def rowcount(self) -> int:
        return len(self.result)

    def execute(self, query: str, params: Any = None) -> None:
        self.result = []
        if query.startswith("SELECT post.source_post_key"):
            self.result = [
                (key[0], key[1], *value)
                for key, value in self.database.relations.items()
            ]
        elif query.startswith("SELECT mention.source_mention_id"):
            key = (int(params[0]), str(params[1]), str(params[2]))
            mention_id = self.database.links.get(key)
            if mention_id is not None:
                self.result = [
                    (mention_id, self.database.mentions[mention_id]["output_hash"])
                ]
        elif query.startswith("INSERT INTO ontology.source_mentions"):
            mention_id = self.database.next_id
            self.database.next_id += 1
            self.database.mentions[mention_id] = {
                "source_kind": params[0],
                "source_revision_hash": params[1],
                "source_text_hash": params[2],
                "output_hash": params[6],
            }
            self.result = [(mention_id,)]
        elif query.startswith("INSERT INTO ontology.source_mention_daily"):
            mention_id = int(params[0])
            key = (int(params[1]), str(params[2]), str(params[3]))
            self.database.links[key] = mention_id
        else:  # pragma: no cover - 새 SQL은 fake에 명시한다.
            raise AssertionError(f"흉내내지 않는 질의입니다: {query}")

    def fetchone(self) -> Any:
        return self.result[0] if self.result else None

    def fetchall(self) -> list[Any]:
        return list(self.result)

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, database: _Database) -> None:
        self.database = database

    def cursor(self) -> _Cursor:
        return _Cursor(self.database)

    def commit(self) -> None:
        self.database.commits += 1

    def rollback(self) -> None:
        self.database.rollbacks += 1


def _mention_and_database():
    relation = _relation(0, "DESCRIPTION", "방산 수출 기대감 등에 상승")
    post = _post(relation)
    mention = mentions_from_daily_post(post)[0]
    database = _Database()
    database.relations[(post.source_post_key, relation.source_order)] = (
        101,
        post.normalized_hash,
        post.published_date,
        relation.relation_type,
        relation.raw_text,
    )
    return mention, database


def _store(database: _Database) -> PostgresDailyMentionStore:
    return PostgresDailyMentionStore(_Connection(database))  # type: ignore[arg-type]


def test_daily_mention_load_is_typed_and_idempotent() -> None:
    mention, database = _mention_and_database()

    first = _store(database).load((mention,))
    second = _store(database).load((mention,))

    assert (first.inserted, first.existing) == (1, 0)
    assert (second.inserted, second.existing) == (0, 1)
    assert len(database.mentions) == 1
    assert next(iter(database.mentions.values()))["source_text_hash"] == sha256_text(
        mention.raw_text
    )
    assert database.commits == 2


def test_daily_mention_same_transform_version_rejects_output_drift() -> None:
    mention, database = _mention_and_database()
    store = _store(database)
    store.load((mention,))

    changed = replace(mention, trading_date=date(2024, 5, 1))
    with pytest.raises(DailyMentionTransformConflictError):
        store.load((changed,))

    assert database.rollbacks == 1


def test_daily_mention_load_skips_missing_or_changed_source_rows() -> None:
    mention, database = _mention_and_database()
    changed = replace(mention, source_post_key="missing")

    missing = _store(database).load((changed,))
    assert missing.missing_relations == 1

    database.relations[(mention.source_post_key, mention.source_relation_order)] = (
        101,
        mention.source_revision_hash,
        mention.published_date,
        mention.relation_type,
        "다른 원문",
    )
    mismatch = _store(database).load((mention,))
    assert mismatch.mismatched_relations == 1
