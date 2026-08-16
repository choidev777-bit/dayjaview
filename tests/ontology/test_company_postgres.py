"""회사 master PostgreSQL 적재 (회사 온톨로지 단계 2)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from packages.ontology import (
    COMPANY_MASTER_VERSION,
    CompanyAliasDraft,
    CompanyDraft,
    CompanyInstrumentDraft,
    CompanyMaster,
    CompanyRevisionDraft,
    PostgresCompanyMasterStore,
    UnresolvedReferenceDraft,
)

NOW = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)


class FakeDatabase:
    """적재에 쓰이는 질의만 흉내내는 최소 저장소."""

    def __init__(self, stocks: dict[str, int]) -> None:
        self.stocks = stocks
        self.companies: dict[str, dict[str, Any]] = {}
        self.aliases: dict[tuple[int, str, str], dict[str, Any]] = {}
        self.instruments: dict[tuple[int, int], dict[str, Any]] = {}
        self.revisions: dict[tuple[int, str], dict[str, Any]] = {}
        self.reviews: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.commits = 0
        self.rollbacks = 0
        self._next_id = 1

    def take_id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value


class FakeCursor:
    def __init__(self, database: FakeDatabase) -> None:
        self._database = database
        self._result: list[Any] = []
        self.rowcount = 0
        self.closed = False

    def execute(self, query: str, params: Any = None) -> None:
        self._result = []
        database = self._database
        if "FROM core.infostock_stocks" in query:
            self._result = [(code, stock_id) for code, stock_id in database.stocks.items()]
        elif query.startswith("SELECT company_id, dart_corp_code"):
            stored = database.companies.get(str(params[0]))
            if stored is not None:
                self._result = [(stored["company_id"], stored["dart_corp_code"])]
        elif query.startswith("INSERT INTO core.company_entities"):
            company_id = database.take_id()
            database.companies[str(params[0])] = {
                "company_id": company_id,
                "canonical_name": params[1],
                "name_basis": params[2],
                "dart_corp_code": params[3],
                "master_version": params[4],
            }
            self._result = [(company_id,)]
        elif query.startswith("UPDATE core.company_entities"):
            for stored in database.companies.values():
                if stored["company_id"] == int(params[2]):
                    stored["dart_corp_code"] = params[0]
        elif query.startswith("INSERT INTO core.company_aliases"):
            key = (int(params[0]), str(params[2]), str(params[3]))
            if key not in database.aliases:
                database.aliases[key] = {
                    "alias": params[1],
                    "source_authority": params[4],
                    "valid_from": params[5],
                    "valid_to": params[6],
                    "mention_count": params[7],
                }
                self._result = [(database.take_id(),)]
        elif query.startswith("INSERT INTO core.company_instruments"):
            key = (int(params[0]), int(params[1]))
            if key not in database.instruments:
                database.instruments[key] = {
                    "share_class": params[2],
                    "link_basis": params[3],
                    "valid_from": params[4],
                    "valid_to": params[5],
                }
                self._result = [(database.take_id(),)]
        elif query.startswith("INSERT INTO core.company_revisions"):
            company_id = int(params[0])
            content_hash = str(params[8])
            if (company_id, content_hash) not in database.revisions:
                revision_no = 1 + sum(
                    1 for key in database.revisions if key[0] == company_id
                )
                database.revisions[(company_id, content_hash)] = {
                    "revision_no": revision_no,
                    "change_type": params[2],
                    "effective_on": params[3],
                    "previous_value": params[4],
                    "new_value": params[5],
                }
                self._result = [(database.take_id(),)]
        elif query.startswith("INSERT INTO core.company_resolution_reviews"):
            key = (str(params[7]), str(params[0]), str(params[2]))
            if key not in database.reviews:
                database.reviews[key] = {
                    "source_name": params[1],
                    "reason": params[3],
                    "mention_count": params[4],
                    "status": "PENDING",
                }
                self._result = [(database.take_id(),)]
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


def _master(*, corp_code: str | None = "00164645") -> CompanyMaster:
    aliases = (
        CompanyAliasDraft(
            alias="동국제강",
            normalized_alias="동국제강",
            alias_type="PAST_NAME",
            source_authority="HISTORICAL_REFERENCE",
            valid_from=date(2013, 5, 2),
            valid_to=date(2023, 5, 18),
            mention_count=2,
        ),
        CompanyAliasDraft(
            alias="동국홀딩스",
            normalized_alias="동국홀딩스",
            alias_type="CURRENT_NAME",
            source_authority="CURRENT_MEMBERSHIP",
            valid_from=date(2024, 1, 29),
            valid_to=None,
            mention_count=2,
        ),
    )
    company = CompanyDraft(
        seed_stock_code="001230",
        canonical_name="동국홀딩스",
        name_basis="CURRENT_MEMBERSHIP",
        dart_corp_code=corp_code,
        aliases=aliases,
        instruments=(
            CompanyInstrumentDraft(
                stock_code="001230",
                share_class="COMMON",
                link_basis="STOCK_CODE",
                valid_from=None,
                valid_to=None,
            ),
        ),
        revisions=(
            CompanyRevisionDraft(
                change_type="CREATED",
                effective_on=date(2013, 5, 2),
                previous_value=None,
                new_value="동국제강",
            ),
            CompanyRevisionDraft(
                change_type="NAME_CHANGED",
                effective_on=date(2024, 1, 29),
                previous_value="동국제강",
                new_value="동국홀딩스",
            ),
        ),
    )
    return CompanyMaster(
        master_version=COMPANY_MASTER_VERSION,
        companies=(company,),
        unresolved=(
            UnresolvedReferenceDraft(
                source_kind="HISTORY_LEADER",
                source_name="0015G0-그린광학",
                normalized_name="0015g0-그린광학",
                reason="SOURCE_CODE_MISSING",
                mention_count=22,
                first_event_date=date(2026, 5, 4),
                last_event_date=date(2026, 6, 4),
            ),
        ),
    )


def _store(database: FakeDatabase) -> PostgresCompanyMasterStore:
    return PostgresCompanyMasterStore(FakeConnection(database))  # type: ignore[arg-type]


def test_load_writes_company_alias_instrument_revision_and_review() -> None:
    database = FakeDatabase({"001230": 77})
    counts = _store(database).load(_master(), recorded_at=NOW)

    assert (counts.companies, counts.companies_inserted) == (1, 1)
    assert (counts.aliases_inserted, counts.instruments_inserted) == (2, 1)
    assert (counts.revisions_inserted, counts.reviews_inserted) == (2, 1)
    assert (counts.unknown_stock_codes, counts.skipped_companies) == (0, 0)

    stored = database.companies["001230"]
    assert stored["canonical_name"] == "동국홀딩스"
    assert stored["dart_corp_code"] == "00164645"
    assert database.aliases[(stored["company_id"], "동국제강", "PAST_NAME")][
        "valid_to"
    ] == date(2023, 5, 18)
    assert database.aliases[(stored["company_id"], "동국홀딩스", "CURRENT_NAME")][
        "valid_to"
    ] is None
    assert database.instruments[(stored["company_id"], 77)]["share_class"] == "COMMON"
    assert sorted(
        revision["revision_no"] for revision in database.revisions.values()
    ) == [1, 2]
    assert database.reviews[
        (COMPANY_MASTER_VERSION, "HISTORY_LEADER", "0015g0-그린광학")
    ]["status"] == "PENDING"
    assert database.commits == 1


def test_load_is_idempotent_on_rerun() -> None:
    database = FakeDatabase({"001230": 77})
    store = _store(database)

    first = store.load(_master(), recorded_at=NOW)
    second = store.load(_master(), recorded_at=NOW)

    assert first.companies_inserted == 1
    assert (second.companies, second.companies_inserted) == (1, 0)
    assert (second.aliases_inserted, second.instruments_inserted) == (0, 0)
    assert (second.revisions_inserted, second.reviews_inserted) == (0, 0)
    assert len(database.companies) == 1
    assert len(database.aliases) == 2
    assert len(database.revisions) == 2


def test_missing_stock_code_leaves_the_company_out() -> None:
    database = FakeDatabase({})
    counts = _store(database).load(_master(), recorded_at=NOW)

    assert (counts.companies, counts.skipped_companies) == (0, 1)
    assert counts.unknown_stock_codes == 1
    assert database.companies == {}
    assert counts.reviews_inserted == 1


def test_rerun_fills_a_dart_corp_code_that_was_missing() -> None:
    database = FakeDatabase({"001230": 77})
    store = _store(database)

    store.load(_master(corp_code=None), recorded_at=NOW)
    assert database.companies["001230"]["dart_corp_code"] is None

    store.load(_master(), recorded_at=NOW)
    assert database.companies["001230"]["dart_corp_code"] == "00164645"
