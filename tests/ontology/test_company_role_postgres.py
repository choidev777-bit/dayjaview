"""history 회사 mention·역할 PostgreSQL 적재 (회사 온톨로지 단계 3)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

import pytest

from packages.infostock.hashing import sha256_text
from packages.infostock.models import StockReference, ThemeHistory, ThemeMembership
from packages.ontology import (
    CompanyRoleCompanyMissingError,
    CompanyRoleTransformConflictError,
    PostgresCompanyRoleStore,
    build_company_master,
    label_company_history,
)

NOW = datetime(2026, 8, 17, 4, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _Detail:
    source_theme_id: str
    theme_name: str
    memberships: tuple[ThemeMembership, ...]
    history: tuple[ThemeHistory, ...]


@dataclass(frozen=True)
class _Bundle:
    dataset_hash: str
    details: tuple[_Detail, ...]


def _reference(order: int, code: str, name: str) -> StockReference:
    return StockReference(
        source_order=order,
        name=name,
        stock_code=code,
        source_url=None,
        display_value=f"{code}-{name}",
        quality_status="OK",
    )


def _history(
    key: str,
    raw_text: str,
    *,
    leaders: tuple[StockReference, ...],
) -> ThemeHistory:
    return ThemeHistory(
        source_order=0,
        source_history_id=None,
        source_history_key=key,
        event_date=date(2024, 5, 2),
        source_date=None,
        source_created_at=None,
        source_updated_at=None,
        raw_text=raw_text,
        direction="UP",
        leaders=leaders,
        member_stocks=(),
        author=None,
        chart_flag=None,
        source_fingerprint=sha256_text(f"fingerprint:{key}"),
        quality_status="OK",
        content_hash=sha256_text(raw_text),
    )


def _bundle(histories: tuple[ThemeHistory, ...]) -> _Bundle:
    names = (("012450", "한화에어로스페이스"), ("005870", "휴니드"))
    return _Bundle(
        dataset_hash="d" * 64,
        details=(
            _Detail(
                source_theme_id="379",
                theme_name="항공기부품",
                memberships=tuple(
                    ThemeMembership(
                        source_order=order,
                        stock_code=code,
                        stock_name=name,
                        rationale="구성종목",
                        source_index=None,
                        content_hash=sha256_text(f"{code}:{name}"),
                    )
                    for order, (code, name) in enumerate(names)
                ),
                history=histories,
            ),
        ),
    )


class FakeRoleDatabase:
    def __init__(self) -> None:
        self.dataset_hash = "a" * 64
        self.companies = {"012450": 7, "005870": 8}
        self.histories: dict[tuple[str, str], tuple[int, str, str]] = {}
        self.references: dict[
            tuple[str, str, str, int], tuple[int, str, str | None, str]
        ] = {}
        self.revisions: dict[tuple[int, str, str], dict[str, Any]] = {}
        self.mentions: list[dict[str, Any]] = []
        self.roles: list[dict[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self._next_id = 1

    def take_id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value


class FakeRoleCursor:
    def __init__(self, database: FakeRoleDatabase) -> None:
        self._database = database
        self._result: list[Any] = []
        self.rowcount = 0

    def execute(self, query: str, params: Any = None) -> None:
        self._result = []
        database = self._database
        if query.startswith("SELECT seed_stock_code, company_id"):
            self._result = list(database.companies.items())
        elif query.startswith("SELECT t.source_theme_id, h.source_history_key"):
            self._result = [
                (theme, key, history_id, raw_text, content_hash)
                for (theme, key), (history_id, raw_text, content_hash) in (
                    database.histories.items()
                )
            ]
        elif query.startswith("SELECT 'LEADER_LIST'"):
            self._result = [
                (kind, theme, key, order, reference_id, name, code, status)
                for (kind, theme, key, order), (
                    reference_id,
                    name,
                    code,
                    status,
                ) in (database.references.items())
            ]
        elif query.startswith("SELECT dataset_hash FROM ingest.infostock_import_runs"):
            self._result = [(database.dataset_hash,)]
        elif query.startswith("SELECT role_revision_id, output_hash"):
            stored = database.revisions.get(
                (int(params[0]), str(params[1]), str(params[2]))
            )
            if stored is not None:
                self._result = [(stored["role_revision_id"], stored["output_hash"])]
        elif query.startswith("INSERT INTO ontology.history_company_role_revisions"):
            history_id = int(params[0])
            revision_id = database.take_id()
            key = (history_id, str(params[1]), str(params[2]))
            database.revisions[key] = {
                "role_revision_id": revision_id,
                "revision_no": 1
                + sum(
                    revision["revision_no"] >= 1
                    for stored_key, revision in database.revisions.items()
                    if stored_key[0] == history_id and stored_key != key
                ),
                "history_content_hash": params[3],
                "output_hash": params[4],
            }
            self._result = [(revision_id,)]
        elif query.startswith("INSERT INTO ontology.history_company_mentions"):
            mention_id = database.take_id()
            database.mentions.append(
                {
                    "company_mention_id": mention_id,
                    "role_revision_id": params[0],
                    "source_order": params[1],
                    "mention_kind": params[2],
                    "history_leader_id": params[3],
                    "history_membership_id": params[4],
                    "company_id": params[5],
                    "resolution_status": params[6],
                    "resolution_basis": params[7],
                    "suggested_role": params[8],
                    "mention_start": params[9],
                    "mention_end": params[10],
                    "evidence_source_hash": params[11],
                }
            )
            self._result = [(mention_id,)]
        elif query.startswith("INSERT INTO ontology.history_company_roles"):
            database.roles.append(
                {
                    "company_mention_id": params[0],
                    "company_id": params[1],
                    "source_order": params[2],
                    "role": params[3],
                    "extraction_basis": params[4],
                    "evidence_start": params[5],
                    "evidence_end": params[6],
                }
            )
        else:  # pragma: no cover - 새 SQL은 명시적으로 fake에 추가한다.
            raise AssertionError(f"흉내내지 않는 질의입니다: {query}")

    def fetchone(self) -> Any:
        return self._result[0] if self._result else None

    def fetchall(self) -> list[Any]:
        return list(self._result)

    def close(self) -> None:
        pass


class FakeRoleConnection:
    def __init__(self, database: FakeRoleDatabase) -> None:
        self._database = database

    def cursor(self) -> FakeRoleCursor:
        return FakeRoleCursor(self._database)

    def commit(self) -> None:
        self._database.commits += 1

    def rollback(self) -> None:
        self._database.rollbacks += 1


def _label_and_database():
    history = _history(
        "contract",
        "한화에어로스페이스, 엔진 공급계약 체결 등에 상승(주도주 : 한화에어로스페이스)",
        leaders=(_reference(0, "012450", "한화에어로스페이스"),),
    )
    bundle = _bundle((history,))
    labels, _ = label_company_history(bundle, build_company_master(bundle))
    database = FakeRoleDatabase()
    database.histories[("379", "contract")] = (
        101,
        history.raw_text,
        history.content_hash,
    )
    database.references[("LEADER_LIST", "379", "contract", 0)] = (
        201,
        "한화에어로스페이스",
        "012450",
        "RESOLVED",
    )
    return labels[0], database


def _store(database: FakeRoleDatabase) -> PostgresCompanyRoleStore:
    return PostgresCompanyRoleStore(FakeRoleConnection(database))  # type: ignore[arg-type]


def test_role_load_writes_revision_typed_mentions_and_role_facts() -> None:
    label, database = _label_and_database()
    counts = _store(database).load((label,), labeled_at=NOW)

    assert (counts.total, counts.inserted, counts.existing) == (1, 1, 0)
    assert (counts.mentions_inserted, counts.roles_inserted) == (2, 3)
    assert [mention["mention_kind"] for mention in database.mentions] == [
        "BODY",
        "LEADER_LIST",
    ]
    assert database.mentions[0]["history_leader_id"] is None
    assert database.mentions[1]["history_leader_id"] == 201
    assert {role["role"] for role in database.roles} == {
        "ACTOR",
        "CONTRACTOR",
        "LEADER",
    }
    assert all(role["company_id"] == 7 for role in database.roles)
    assert database.commits == 1


def test_role_load_is_idempotent_and_rejects_same_version_drift() -> None:
    label, database = _label_and_database()
    store = _store(database)

    first = store.load((label,), labeled_at=NOW)
    second = store.load((label,), labeled_at=NOW)

    assert (first.inserted, second.inserted, second.existing) == (1, 0, 1)
    assert len(database.revisions) == 1
    assert len(database.mentions) == 2

    body = label.mentions[0]
    changed = replace(
        label,
        mentions=(replace(body, roles=()), *label.mentions[1:]),
    )
    with pytest.raises(CompanyRoleTransformConflictError):
        store.load((changed,), labeled_at=NOW)
    assert database.rollbacks == 1


def test_missing_company_master_row_fails_before_any_role_is_written() -> None:
    label, database = _label_and_database()
    database.companies = {}

    with pytest.raises(CompanyRoleCompanyMissingError):
        _store(database).load((label,), labeled_at=NOW)

    assert database.revisions == {}
    assert database.mentions == []


def test_missing_typed_source_reference_skips_the_whole_history_revision() -> None:
    label, database = _label_and_database()
    database.references = {}

    counts = _store(database).load((label,), labeled_at=NOW)

    assert counts.missing_references == 1
    assert counts.inserted == 0
    assert database.revisions == {}
    assert database.commits == 1


def test_bulk_load_requires_copy_capable_cursor() -> None:
    label, database = _label_and_database()

    with pytest.raises(RuntimeError, match="psycopg COPY"):
        _store(database).load_bulk((label,), labeled_at=NOW)

    assert database.revisions == {}
    assert database.rollbacks == 1


def test_alignment_uses_database_typed_source_resolution_and_content_hash() -> None:
    label, database = _label_and_database()
    database.histories[("379", "contract")] = (
        101,
        label.raw_text,
        "b" * 64,
    )
    database.references[("LEADER_LIST", "379", "contract", 0)] = (
        201,
        "한화에어로스페이스",
        None,
        "SOURCE_CODE_MISSING",
    )

    result = _store(database).align_labels_to_current_sources((label,))

    aligned = result.labels[0]
    structured = aligned.mentions[1]
    assert aligned.history_content_hash == "b" * 64
    assert structured.resolution_status == "SOURCE_CODE_MISSING"
    assert structured.resolution_basis == "NONE"
    assert structured.seed_stock_code is None
    assert structured.roles == ()
    assert result.histories_aligned == 1
    assert result.mentions_aligned == 1
    assert result.database_dataset_hash == "a" * 64
