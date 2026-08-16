"""회사 master의 PostgreSQL 적재 (회사 온톨로지 단계 2).

`build_company_master`가 만든 결과를 `core`의 회사 테이블에 넣는다. 적재는
append이며 덮어쓰지 않는다. 같은 수집본으로 다시 실행하면 아무 행도 늘지
않는다. DB에 없는 종목코드는 회사를 만들지 않고 건수만 보고한다.

운영자가 고친 이름·유효기간을 이 job이 되돌리지 않도록, 이미 있는 행은
그대로 두고 비어 있는 DART 고유번호만 채운다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .company_entities import CompanyDraft, CompanyMaster
from .postgres import DbConnection, DbCursor


@dataclass(frozen=True, slots=True)
class CompanyLoadCounts:
    """적재 결과 집계."""

    companies: int
    companies_inserted: int
    aliases_inserted: int
    instruments_inserted: int
    revisions_inserted: int
    reviews_inserted: int
    unknown_stock_codes: int
    skipped_companies: int


class PostgresCompanyMasterStore:
    """`core`의 회사·alias·회사-종목·이력·검수 테이블에 master를 적재한다."""

    def __init__(self, connection: DbConnection) -> None:
        self._connection = connection

    def stock_ids(self) -> dict[str, int]:
        """6자리 종목코드 → stock_id. 새 종목을 만들지 않고 있는 것만 쓴다."""

        db = self._connection.cursor()
        try:
            db.execute("SELECT stock_code, stock_id FROM core.infostock_stocks")
            return {str(row[0]).strip(): int(row[1]) for row in db.fetchall()}
        finally:
            db.close()

    def load(self, master: CompanyMaster, *, recorded_at: datetime) -> CompanyLoadCounts:
        """회사 master를 적재하고 건수를 돌려준다."""

        stock_ids = self.stock_ids()
        companies = 0
        companies_inserted = 0
        aliases_inserted = 0
        instruments_inserted = 0
        revisions_inserted = 0
        reviews_inserted = 0
        unknown_stock_codes = 0
        skipped_companies = 0
        db = self._connection.cursor()
        try:
            for company in master.companies:
                if company.seed_stock_code not in stock_ids:
                    skipped_companies += 1
                    unknown_stock_codes += 1
                    continue
                companies += 1
                company_id, inserted = self._company_id(
                    db, company, master.master_version, recorded_at
                )
                companies_inserted += int(inserted)
                for alias in company.aliases:
                    db.execute(
                        "INSERT INTO core.company_aliases"
                        " (company_id, alias, normalized_alias, alias_type,"
                        " validity_basis, source_authority, valid_from, valid_to,"
                        " mention_count, master_version, recorded_at)"
                        " VALUES (%s, %s, %s, %s, 'OBSERVED_MENTION', %s, %s, %s,"
                        " %s, %s, %s)"
                        " ON CONFLICT (company_id, normalized_alias, alias_type)"
                        " DO NOTHING RETURNING company_alias_id",
                        (
                            company_id,
                            alias.alias,
                            alias.normalized_alias,
                            alias.alias_type,
                            alias.source_authority,
                            alias.valid_from,
                            alias.valid_to,
                            alias.mention_count,
                            master.master_version,
                            recorded_at,
                        ),
                    )
                    aliases_inserted += int(db.fetchone() is not None)
                for instrument in company.instruments:
                    stock_id = stock_ids.get(instrument.stock_code)
                    if stock_id is None:
                        unknown_stock_codes += 1
                        continue
                    db.execute(
                        "INSERT INTO core.company_instruments"
                        " (company_id, stock_id, share_class, link_basis,"
                        " valid_from, valid_to, master_version, recorded_at)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                        " ON CONFLICT (company_id, stock_id) DO NOTHING"
                        " RETURNING company_instrument_id",
                        (
                            company_id,
                            stock_id,
                            instrument.share_class,
                            instrument.link_basis,
                            instrument.valid_from,
                            instrument.valid_to,
                            master.master_version,
                            recorded_at,
                        ),
                    )
                    instruments_inserted += int(db.fetchone() is not None)
                for revision in company.revisions:
                    db.execute(
                        "INSERT INTO core.company_revisions"
                        " (company_id, revision_no, change_type, effective_on,"
                        " previous_value, new_value, evidence_basis, master_version,"
                        " recorded_at, content_hash)"
                        " SELECT %s, (SELECT COALESCE(MAX(revision_no), 0) + 1"
                        "   FROM core.company_revisions WHERE company_id = %s),"
                        " %s, %s, %s, %s, 'OBSERVED_MENTION', %s, %s, %s"
                        " WHERE NOT EXISTS (SELECT 1 FROM core.company_revisions"
                        "   WHERE company_id = %s AND content_hash = %s)"
                        " RETURNING company_revision_id",
                        (
                            company_id,
                            company_id,
                            revision.change_type,
                            revision.effective_on,
                            revision.previous_value,
                            revision.new_value,
                            master.master_version,
                            recorded_at,
                            revision.content_hash(company.seed_stock_code),
                            company_id,
                            revision.content_hash(company.seed_stock_code),
                        ),
                    )
                    revisions_inserted += int(db.fetchone() is not None)
            for review in master.unresolved:
                db.execute(
                    "INSERT INTO core.company_resolution_reviews"
                    " (source_kind, source_name, normalized_name, reason,"
                    " mention_count, first_event_date, last_event_date,"
                    " master_version, recorded_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (master_version, source_kind, normalized_name)"
                    " DO NOTHING RETURNING company_review_id",
                    (
                        review.source_kind,
                        review.source_name,
                        review.normalized_name,
                        review.reason,
                        review.mention_count,
                        review.first_event_date,
                        review.last_event_date,
                        master.master_version,
                        recorded_at,
                    ),
                )
                reviews_inserted += int(db.fetchone() is not None)
            self._connection.commit()
            return CompanyLoadCounts(
                companies=companies,
                companies_inserted=companies_inserted,
                aliases_inserted=aliases_inserted,
                instruments_inserted=instruments_inserted,
                revisions_inserted=revisions_inserted,
                reviews_inserted=reviews_inserted,
                unknown_stock_codes=unknown_stock_codes,
                skipped_companies=skipped_companies,
            )
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _company_id(
        db: DbCursor, company: CompanyDraft, master_version: str, recorded_at: datetime
    ) -> tuple[int, bool]:
        """회사 행을 찾거나 만든다. 이미 있으면 비어 있는 고유번호만 채운다."""

        db.execute(
            "SELECT company_id, dart_corp_code FROM core.company_entities"
            " WHERE seed_stock_code = %s",
            (company.seed_stock_code,),
        )
        row = db.fetchone()
        if row is not None:
            company_id = int(row[0])
            if row[1] is None and company.dart_corp_code is not None:
                db.execute(
                    "UPDATE core.company_entities"
                    " SET dart_corp_code = %s, updated_at = %s"
                    " WHERE company_id = %s AND dart_corp_code IS NULL",
                    (company.dart_corp_code, recorded_at, company_id),
                )
            return company_id, False
        db.execute(
            "INSERT INTO core.company_entities"
            " (seed_stock_code, canonical_name, name_basis, dart_corp_code,"
            " master_version, created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING company_id",
            (
                company.seed_stock_code,
                company.canonical_name,
                company.name_basis,
                company.dart_corp_code,
                master_version,
                recorded_at,
                recorded_at,
            ),
        )
        created = db.fetchone()
        if created is None:  # pragma: no cover - RETURNING은 항상 행을 준다
            raise RuntimeError("회사 행을 만들지 못했습니다.")
        return int(created[0]), True
