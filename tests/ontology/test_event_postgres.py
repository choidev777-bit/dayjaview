"""고유 사건 PostgreSQL append 적재의 typed link·재실행 검증."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest

from packages.infostock.hashing import sha256_text
from packages.ontology.company_roles import (
    CompanyRoleEvidence,
    HistoryCompanyMention,
    HistoryCompanyRoleLabel,
)
from packages.ontology.event_dedup import deduplicate_catalysts
from packages.ontology.event_postgres import (
    CatalystTransformConflictError,
    PostgresCatalystEventStore,
)
from packages.ontology.event_structure import Officiality, structure_history_catalysts

_RAW_TEXT = (
    "한화에어로스페이스, 폴란드와 K9 3억원 공급계약 체결 등에 상승"
)
_GENERATED_AT = datetime(2026, 8, 17, tzinfo=UTC)


def _result(*, officiality: Officiality | None = None):
    company = "한화에어로스페이스"
    start = _RAW_TEXT.index(company)
    evidence_end = _RAW_TEXT.index(" 등에")
    mention = HistoryCompanyMention(
        source_order=0,
        mention_kind="BODY",
        source_reference_order=None,
        mention_text=company,
        start=start,
        end=start + len(company),
        resolution_status="RESOLVED",
        resolution_basis="EXACT_ALIAS",
        seed_stock_code="012450",
        suggested_role=None,
        roles=(
            CompanyRoleEvidence(
                source_order=0,
                role="ACTOR",
                extraction_basis="BODY_RULE",
                start=start,
                end=evidence_end,
            ),
        ),
    )
    label = HistoryCompanyRoleLabel(
        company_master_version="company-master/test",
        role_transform_version="company-role-transform/test",
        source_theme_id="379",
        theme_name="방산",
        source_history_key="contract",
        event_date=date(2024, 5, 2),
        history_content_hash=sha256_text(_RAW_TEXT),
        raw_text=_RAW_TEXT,
        mentions=(mention,),
    )
    draft = structure_history_catalysts(label, dataset_hash="d" * 64)[0]
    if officiality is not None:
        draft = replace(draft, officiality=officiality)
    return deduplicate_catalysts((draft,))


class _Database:
    def __init__(self) -> None:
        self.history_raw_text = _RAW_TEXT
        self.source_mentions: dict[tuple[int, int, str], tuple[int, str]] = {}
        self.pending_mention: tuple[int, str] | None = None
        self.actors: dict[str, tuple[str, str, str | None]] = {}
        self.projects: dict[str, str] = {}
        self.catalysts: dict[str, str] = {}
        self.revisions: dict[str, tuple[int, int, tuple[str, ...], str]] = {}
        self.reactions: set[str] = set()
        self.reaction_revisions: dict[
            tuple[str, int, str], tuple[int, str]
        ] = {}
        self.fact_counts: dict[str, int] = {}
        self.next_mention_id = 101
        self.next_revision_id = 201
        self.next_reaction_revision_id = 301
        self.commits = 0
        self.rollbacks = 0


class _Cursor:
    def __init__(self, database: _Database) -> None:
        self.database = database
        self.result: list[Any] = []

    @property
    def rowcount(self) -> int:
        return len(self.result)

    def _count(self, table: str) -> None:
        self.database.fact_counts[table] = self.database.fact_counts.get(table, 0) + 1

    def execute(self, query: str, params: Any = None) -> None:
        self.result = []
        if query.startswith("SELECT theme.source_theme_id"):
            self.result = [
                (
                    "379",
                    "contract",
                    11,
                    sha256_text(_RAW_TEXT),
                    self.database.history_raw_text,
                    _GENERATED_AT,
                )
            ]
        elif query.startswith("SELECT seed_stock_code"):
            self.result = [("012450", 31)]
        elif query.startswith("SELECT mention.source_mention_id"):
            found = self.database.source_mentions.get(
                (int(params[0]), int(params[1]), str(params[2]))
            )
            if found is not None:
                self.result = [found]
        elif query.startswith("INSERT INTO ontology.source_mentions"):
            mention_id = self.database.next_mention_id
            self.database.next_mention_id += 1
            self.database.pending_mention = (mention_id, str(params[6]))
            self.result = [(mention_id,)]
        elif query.startswith("INSERT INTO ontology.source_mention_history"):
            pending = self.database.pending_mention
            assert pending is not None
            self.database.source_mentions[
                (int(params[1]), int(params[2]), str(params[3]))
            ] = pending
        elif query.startswith("SELECT identity_hash"):
            found = self.database.actors.get(str(params[0]))
            if found is not None:
                self.result = [found]
        elif query.startswith("INSERT INTO ontology.actor_entities"):
            self.database.actors[str(params[0])] = (
                str(params[5]),
                str(params[1]),
                None if params[4] is None else str(params[4]),
            )
        elif query.startswith("INSERT INTO ontology.actor_aliases"):
            self._count("actor_aliases")
        elif query.startswith("SELECT project_fingerprint"):
            found = self.database.projects.get(str(params[0]))
            if found is not None:
                self.result = [(found,)]
        elif query.startswith("INSERT INTO ontology.projects"):
            self.database.projects[str(params[0])] = str(params[1])
        elif query.startswith("INSERT INTO ontology.project_aliases"):
            self._count("project_aliases")
        elif query.startswith("SELECT dedup_key"):
            found = self.database.catalysts.get(str(params[0]))
            if found is not None:
                self.result = [(found,)]
        elif query.startswith("INSERT INTO ontology.catalysts"):
            self.database.catalysts[str(params[0])] = str(params[1])
        elif query.startswith("SELECT catalyst_revision_id"):
            found = self.database.revisions.get(str(params[0]))
            if found is not None:
                revision_id, revision_no, versions, output_hash = found
                self.result = [(revision_id, revision_no, *versions, output_hash)]
        elif query.startswith("INSERT INTO ontology.catalyst_revisions"):
            revision_id = self.database.next_revision_id
            self.database.next_revision_id += 1
            versions = tuple(
                str(params[index]) for index in (23, 5, 19, 20, 21, 22)
            )
            self.database.revisions[str(params[0])] = (
                revision_id,
                int(params[1]),
                versions,
                str(params[25]),
            )
            self.result = [(revision_id,)]
        elif query.startswith("INSERT INTO ontology.catalyst_source_mentions"):
            self._count("catalyst_source_mentions")
        elif query.startswith("INSERT INTO ontology.catalyst_revision_spans"):
            self._count("catalyst_revision_spans")
        elif query.startswith("INSERT INTO ontology.catalyst_company_roles"):
            self._count("catalyst_company_roles")
        elif query.startswith("INSERT INTO ontology.catalyst_participants"):
            self._count("catalyst_participants")
        elif query.startswith("INSERT INTO ontology.catalyst_geographies"):
            self._count("catalyst_geographies")
        elif query.startswith("INSERT INTO ontology.catalyst_values"):
            self._count("catalyst_values")
        elif query.startswith("SELECT reaction_id FROM ontology.theme_reactions"):
            if str(params[0]) in self.database.reactions:
                self.result = [(str(params[0]),)]
        elif query.startswith("INSERT INTO ontology.theme_reactions"):
            self.database.reactions.add(str(params[0]))
        elif query.startswith("SELECT reaction_revision_id"):
            found = self.database.reaction_revisions.get(
                (str(params[0]), int(params[1]), str(params[2]))
            )
            if found is not None:
                self.result = [found]
        elif query.startswith("INSERT INTO ontology.theme_reaction_revisions"):
            revision_id = self.database.next_reaction_revision_id
            self.database.next_reaction_revision_id += 1
            self.database.reaction_revisions[
                (str(params[0]), int(params[1]), str(params[4]))
            ] = (revision_id, str(params[5]))
            self.result = [(revision_id,)]
        elif query.startswith("INSERT INTO ontology.catalyst_theme_reactions"):
            self._count("catalyst_theme_reactions")
        elif query.startswith("INSERT INTO ontology.theme_reaction_company_roles"):
            self._count("theme_reaction_company_roles")
        elif query.startswith("SELECT from_catalyst_id"):
            pass
        elif query.startswith("INSERT INTO ontology.catalyst_relations"):
            self._count("catalyst_relations")
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


def _store(database: _Database) -> PostgresCatalystEventStore:
    return PostgresCatalystEventStore(_Connection(database))  # type: ignore[arg-type]


def test_catalyst_load_uses_typed_links_and_is_idempotent() -> None:
    database = _Database()
    result = _result()

    first = _store(database).load(result, generated_at=_GENERATED_AT)
    second = _store(database).load(result, generated_at=_GENERATED_AT)

    assert (first.inserted_revisions, first.existing_revisions) == (1, 0)
    assert (second.inserted_revisions, second.existing_revisions) == (0, 1)
    assert first.source_mentions_inserted == 1
    assert second.source_mentions_existing == 1
    assert first.projects_inserted == 1
    assert first.actors_inserted == 1
    assert first.company_roles_inserted == 1
    assert first.participants_inserted == 1
    assert first.values_inserted == 1
    assert first.reactions_inserted == 1
    assert database.fact_counts["catalyst_source_mentions"] == 1
    assert database.commits == 2


def test_same_dataset_and_transform_reject_output_drift() -> None:
    database = _Database()
    _store(database).load(_result(), generated_at=_GENERATED_AT)

    with pytest.raises(CatalystTransformConflictError):
        _store(database).load(
            _result(officiality=Officiality.REPORTED),
            generated_at=_GENERATED_AT,
        )

    assert database.rollbacks == 1


def test_changed_history_source_skips_whole_catalyst() -> None:
    database = _Database()
    database.history_raw_text = "다른 원문"

    counts = _store(database).load(_result(), generated_at=_GENERATED_AT)

    assert counts.skipped_catalysts == 1
    assert counts.mismatched_histories == 1
    assert counts.inserted_revisions == 0
