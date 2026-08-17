"""회사 master와 history 회사 역할의 PostgreSQL 적재 (단계 2·3).

`build_company_master`가 만든 결과를 `core`의 회사 테이블에 넣는다. 적재는
append이며 덮어쓰지 않는다. 같은 수집본으로 다시 실행하면 아무 행도 늘지
않는다. DB에 없는 종목코드는 회사를 만들지 않고 건수만 보고한다.

운영자가 고친 이름·유효기간을 이 job이 되돌리지 않도록, 이미 있는 행은
그대로 두고 비어 있는 DART 고유번호만 채운다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime

from packages.infostock.hashing import sha256_text

from .company_entities import CompanyDraft, CompanyMaster
from .company_roles import (
    CompanyRoleEvidence,
    HistoryCompanyRoleLabel,
    RoleExtractionBasis,
)
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

    def load(
        self, master: CompanyMaster, *, recorded_at: datetime
    ) -> CompanyLoadCounts:
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
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s,"
                        " %s, %s, %s)"
                        " ON CONFLICT (company_id, normalized_alias, alias_type)"
                        " DO NOTHING RETURNING company_alias_id",
                        (
                            company_id,
                            alias.alias,
                            alias.normalized_alias,
                            alias.alias_type,
                            alias.validity_basis,
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


class CompanyRoleTransformConflictError(RuntimeError):
    """같은 역할 변환 버전이 다른 결과로 이미 적재돼 있다."""


class CompanyRoleCompanyMissingError(RuntimeError):
    """역할 라벨이 가리키는 회사 master 행이 DB에 없다."""


@dataclass(frozen=True, slots=True)
class CompanyRoleLoadCounts:
    """history 회사 mention·역할 적재 결과."""

    total: int
    inserted: int
    existing: int
    unresolved_history: int
    mismatched_history: int
    missing_references: int
    mentions_inserted: int
    roles_inserted: int


@dataclass(frozen=True, slots=True)
class CompanyRoleSourceAlignment:
    """DB의 현재 history·typed reference에 맞춘 라벨과 변경 집계."""

    labels: tuple[HistoryCompanyRoleLabel, ...]
    histories_aligned: int
    mentions_aligned: int
    database_dataset_hash: str | None


@dataclass(frozen=True, slots=True)
class _StoredHistory:
    history_id: int
    raw_text: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class _StoredReference:
    reference_id: int
    source_stock_name: str
    source_stock_code: str | None
    resolution_status: str


@dataclass(frozen=True, slots=True)
class _PreparedRoleLabel:
    stage_revision_key: int
    history_id: int
    label: HistoryCompanyRoleLabel


class PostgresCompanyRoleStore:
    """history 회사 mention·role revision을 append 방식으로 적재한다."""

    def __init__(self, connection: DbConnection) -> None:
        self._connection = connection

    def company_ids(self) -> dict[str, int]:
        db = self._connection.cursor()
        try:
            db.execute("SELECT seed_stock_code, company_id FROM core.company_entities")
            return {str(row[0]).strip(): int(row[1]) for row in db.fetchall()}
        finally:
            db.close()

    def current_history(self) -> dict[tuple[str, str], _StoredHistory]:
        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT t.source_theme_id, h.source_history_key, h.history_id,"
                " h.raw_text, h.content_hash"
                " FROM core.infostock_theme_history h"
                " JOIN core.infostock_themes t ON t.theme_id = h.theme_id"
                " WHERE h.observed_to IS NULL"
            )
            return {
                (str(row[0]), str(row[1])): _StoredHistory(
                    history_id=int(row[2]),
                    raw_text=str(row[3]),
                    content_hash=str(row[4]),
                )
                for row in db.fetchall()
            }
        finally:
            db.close()

    def current_references(
        self,
    ) -> dict[tuple[str, str, str, int], _StoredReference]:
        """(mention kind, 테마, history key, 원천 순서) → typed source row."""

        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT 'LEADER_LIST', t.source_theme_id, h.source_history_key,"
                " l.source_order, l.history_leader_id, l.source_stock_name,"
                " l.source_stock_code, l.resolution_status"
                " FROM core.infostock_theme_history_leaders l"
                " JOIN core.infostock_theme_history h ON h.history_id = l.history_id"
                " JOIN core.infostock_themes t ON t.theme_id = h.theme_id"
                " WHERE h.observed_to IS NULL"
                " UNION ALL"
                " SELECT 'MEMBERSHIP', t.source_theme_id, h.source_history_key,"
                " m.source_order, m.history_membership_id, m.source_stock_name,"
                " m.source_stock_code, m.resolution_status"
                " FROM core.infostock_theme_history_memberships m"
                " JOIN core.infostock_theme_history h ON h.history_id = m.history_id"
                " JOIN core.infostock_themes t ON t.theme_id = h.theme_id"
                " WHERE h.observed_to IS NULL"
            )
            return {
                (str(row[0]), str(row[1]), str(row[2]), int(row[3])): _StoredReference(
                    reference_id=int(row[4]),
                    source_stock_name=str(row[5]),
                    source_stock_code=(None if row[6] is None else str(row[6]).strip()),
                    resolution_status=str(row[7]),
                )
                for row in db.fetchall()
            }
        finally:
            db.close()

    def current_dataset_hash(self) -> str | None:
        """현재 core를 만든 최신 완료 import의 dataset hash."""

        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT dataset_hash FROM ingest.infostock_import_runs"
                " WHERE status IN ('SUCCEEDED', 'PARTIAL')"
                " AND core_status = 'COMPLETE'"
                " ORDER BY finished_at DESC NULLS LAST, import_run_id DESC LIMIT 1"
            )
            row = db.fetchone()
            return None if row is None else str(row[0])
        finally:
            db.close()

    def align_labels_to_current_sources(
        self, labels: Iterable[HistoryCompanyRoleLabel]
    ) -> CompanyRoleSourceAlignment:
        """구조화 mention을 DB typed source의 코드·상태·hash에 맞춘다."""

        materialized = tuple(labels)
        companies = self.company_ids()
        histories = self.current_history()
        references = self.current_references()
        aligned_labels: list[HistoryCompanyRoleLabel] = []
        histories_aligned = 0
        mentions_aligned = 0

        for label in materialized:
            history = histories.get((label.source_theme_id, label.source_history_key))
            if history is None or history.raw_text != label.raw_text:
                aligned_labels.append(label)
                continue

            aligned_mentions = []
            for mention in label.mentions:
                if mention.mention_kind == "BODY":
                    aligned_mentions.append(mention)
                    continue
                if mention.source_reference_order is None:
                    raise ValueError("구조화 mention 원천 순서가 없습니다.")
                reference = references.get(
                    (
                        mention.mention_kind,
                        label.source_theme_id,
                        label.source_history_key,
                        mention.source_reference_order,
                    )
                )
                if (
                    reference is None
                    or reference.source_stock_name != mention.mention_text
                ):
                    aligned_mentions.append(mention)
                    continue

                stock_code = reference.source_stock_code
                if reference.resolution_status == "SOURCE_CODE_MISSING":
                    aligned = replace(
                        mention,
                        resolution_status="SOURCE_CODE_MISSING",
                        resolution_basis="NONE",
                        seed_stock_code=None,
                        roles=(),
                    )
                elif reference.resolution_status == "CODE_INVALID":
                    aligned = replace(
                        mention,
                        resolution_status="CODE_INVALID",
                        resolution_basis="NONE",
                        seed_stock_code=None,
                        roles=(),
                    )
                elif stock_code is None:
                    raise ValueError("RESOLVED typed source에 종목코드가 없습니다.")
                elif stock_code not in companies:
                    aligned = replace(
                        mention,
                        resolution_status="UNKNOWN_STOCK_CODE",
                        resolution_basis="STOCK_CODE",
                        seed_stock_code=None,
                        roles=(),
                    )
                else:
                    if mention.suggested_role is None:
                        raise ValueError("구조화 mention에 suggested role이 없습니다.")
                    extraction_basis: RoleExtractionBasis = (
                        "STRUCTURED_LEADER"
                        if mention.mention_kind == "LEADER_LIST"
                        else "STRUCTURED_MEMBERSHIP"
                    )
                    aligned = replace(
                        mention,
                        resolution_status="RESOLVED",
                        resolution_basis="STOCK_CODE",
                        seed_stock_code=stock_code,
                        roles=(
                            CompanyRoleEvidence(
                                source_order=0,
                                role=mention.suggested_role,
                                extraction_basis=extraction_basis,
                                start=mention.start,
                                end=mention.end,
                            ),
                        ),
                    )
                mentions_aligned += int(aligned != mention)
                aligned_mentions.append(aligned)

            aligned_label = replace(
                label,
                history_content_hash=history.content_hash,
                mentions=tuple(aligned_mentions),
            )
            histories_aligned += int(aligned_label != label)
            aligned_labels.append(aligned_label)

        return CompanyRoleSourceAlignment(
            labels=tuple(aligned_labels),
            histories_aligned=histories_aligned,
            mentions_aligned=mentions_aligned,
            database_dataset_hash=self.current_dataset_hash(),
        )

    def current_role_revisions(self) -> dict[tuple[int, str, str], str]:
        """(history, master 버전, transform 버전) → output hash."""

        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT history_id, company_master_version, role_transform_version,"
                " output_hash FROM ontology.history_company_role_revisions"
            )
            return {
                (int(row[0]), str(row[1]), str(row[2])): str(row[3])
                for row in db.fetchall()
            }
        finally:
            db.close()

    @staticmethod
    def _copy_rows(
        db: DbCursor, query: str, rows: Iterable[tuple[object, ...]]
    ) -> None:
        """psycopg COPY로 staging 행을 왕복 없이 전송한다."""

        copy_method = getattr(db, "copy", None)
        if copy_method is None:
            raise RuntimeError(
                "bulk 적재에는 psycopg COPY를 지원하는 cursor가 필요합니다."
            )
        with copy_method(query) as stream:
            for row in rows:
                stream.write_row(row)

    def load_bulk(
        self,
        labels: Iterable[HistoryCompanyRoleLabel],
        *,
        labeled_at: datetime,
    ) -> CompanyRoleLoadCounts:
        """COPY staging과 set-based INSERT로 역할 revision을 원자적으로 적재한다."""

        materialized = tuple(labels)
        companies = self.company_ids()
        missing_companies = sorted(
            {
                mention.seed_stock_code
                for label in materialized
                for mention in label.mentions
                if mention.resolution_status == "RESOLVED"
                and mention.seed_stock_code is not None
                and mention.seed_stock_code not in companies
            }
        )
        if missing_companies:
            raise CompanyRoleCompanyMissingError(
                "회사 master에 없는 종목코드입니다: " + ", ".join(missing_companies)
            )

        histories = self.current_history()
        references = self.current_references()
        db = self._connection.cursor()
        try:
            if getattr(db, "copy", None) is None:
                raise RuntimeError(
                    "bulk 적재에는 psycopg COPY를 지원하는 cursor가 필요합니다."
                )
            # 같은 DB에서 bulk loader 두 개가 겹치지 않게 한다. advisory lock은
            # INSERT 권한만 가진 운영 writer도 사용할 수 있고 transaction 종료 시 풀린다.
            db.execute(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended('ontology.history_company_roles.bulk', 0))"
            )
            db.fetchone()
            existing_revisions = self.current_role_revisions()

            existing = 0
            unresolved_history = 0
            mismatched_history = 0
            missing_references = 0
            prepared: list[_PreparedRoleLabel] = []
            seen_versions: dict[tuple[int, str, str], str] = {}

            for label in materialized:
                history = histories.get(
                    (label.source_theme_id, label.source_history_key)
                )
                if history is None:
                    unresolved_history += 1
                    continue
                if (
                    history.raw_text != label.raw_text
                    or history.content_hash != label.history_content_hash
                ):
                    mismatched_history += 1
                    continue

                references_valid = True
                for mention in label.mentions:
                    if mention.mention_kind == "BODY":
                        continue
                    if mention.source_reference_order is None:
                        raise ValueError("구조화 mention 원천 순서가 없습니다.")
                    reference = references.get(
                        (
                            mention.mention_kind,
                            label.source_theme_id,
                            label.source_history_key,
                            mention.source_reference_order,
                        )
                    )
                    if (
                        reference is None
                        or reference.source_stock_name != mention.mention_text
                    ):
                        references_valid = False
                        break
                if not references_valid:
                    missing_references += 1
                    continue

                version_key = (
                    history.history_id,
                    label.company_master_version,
                    label.role_transform_version,
                )
                output_hash = label.output_hash
                stored_hash = existing_revisions.get(version_key)
                if stored_hash is not None:
                    if stored_hash != output_hash:
                        raise CompanyRoleTransformConflictError(
                            f"history_id={history.history_id}의 동일 역할 변환 버전이 "
                            "다른 결과로 이미 적재돼 있습니다. 규칙을 고쳤다면 "
                            "COMPANY_ROLE_TRANSFORM_VERSION을 올리십시오."
                        )
                    existing += 1
                    continue
                duplicate_hash = seen_versions.get(version_key)
                if duplicate_hash is not None:
                    if duplicate_hash != output_hash:
                        raise CompanyRoleTransformConflictError(
                            f"history_id={history.history_id}의 입력에 동일 역할 변환 "
                            "버전과 다른 결과가 함께 있습니다."
                        )
                    existing += 1
                    continue
                seen_versions[version_key] = output_hash
                prepared.append(
                    _PreparedRoleLabel(
                        stage_revision_key=len(prepared) + 1,
                        history_id=history.history_id,
                        label=label,
                    )
                )

            if not prepared:
                self._connection.commit()
                return CompanyRoleLoadCounts(
                    total=len(materialized),
                    inserted=0,
                    existing=existing,
                    unresolved_history=unresolved_history,
                    mismatched_history=mismatched_history,
                    missing_references=missing_references,
                    mentions_inserted=0,
                    roles_inserted=0,
                )

            db.execute(
                "CREATE TEMP TABLE company_role_revision_stage ("
                " stage_revision_key bigint PRIMARY KEY,"
                " history_id bigint NOT NULL,"
                " company_master_version text NOT NULL,"
                " role_transform_version text NOT NULL,"
                " history_content_hash text NOT NULL,"
                " output_hash text NOT NULL,"
                " labeled_at timestamptz NOT NULL"
                ") ON COMMIT DROP"
            )
            db.execute(
                "CREATE TEMP TABLE company_mention_stage ("
                " stage_revision_key bigint NOT NULL,"
                " source_order integer NOT NULL,"
                " mention_kind text NOT NULL,"
                " history_leader_id bigint,"
                " history_membership_id bigint,"
                " company_id bigint,"
                " resolution_status text NOT NULL,"
                " resolution_basis text NOT NULL,"
                " suggested_role text,"
                " mention_start integer NOT NULL,"
                " mention_end integer NOT NULL,"
                " evidence_source_hash text NOT NULL,"
                " PRIMARY KEY (stage_revision_key, source_order)"
                ") ON COMMIT DROP"
            )
            db.execute(
                "CREATE TEMP TABLE company_role_stage ("
                " stage_revision_key bigint NOT NULL,"
                " mention_source_order integer NOT NULL,"
                " company_id bigint NOT NULL,"
                " source_order integer NOT NULL,"
                " role text NOT NULL,"
                " extraction_basis text NOT NULL,"
                " evidence_start integer NOT NULL,"
                " evidence_end integer NOT NULL"
                ") ON COMMIT DROP"
            )
            db.execute(
                "CREATE TEMP TABLE company_role_revision_map ("
                " stage_revision_key bigint PRIMARY KEY,"
                " role_revision_id bigint NOT NULL UNIQUE"
                ") ON COMMIT DROP"
            )
            db.execute(
                "CREATE TEMP TABLE company_mention_map ("
                " stage_revision_key bigint NOT NULL,"
                " source_order integer NOT NULL,"
                " company_mention_id bigint NOT NULL UNIQUE,"
                " PRIMARY KEY (stage_revision_key, source_order)"
                ") ON COMMIT DROP"
            )

            self._copy_rows(
                db,
                "COPY company_role_revision_stage"
                " (stage_revision_key, history_id, company_master_version,"
                " role_transform_version, history_content_hash, output_hash,"
                " labeled_at) FROM STDIN",
                (
                    (
                        item.stage_revision_key,
                        item.history_id,
                        item.label.company_master_version,
                        item.label.role_transform_version,
                        item.label.history_content_hash,
                        item.label.output_hash,
                        labeled_at,
                    )
                    for item in prepared
                ),
            )

            def mention_rows() -> Iterable[tuple[object, ...]]:
                for item in prepared:
                    label = item.label
                    body_hash = sha256_text(label.raw_text)
                    for mention in label.mentions:
                        leader_id: int | None = None
                        membership_id: int | None = None
                        if mention.mention_kind != "BODY":
                            if mention.source_reference_order is None:
                                raise ValueError("구조화 mention 원천 순서가 없습니다.")
                            reference = references[
                                (
                                    mention.mention_kind,
                                    label.source_theme_id,
                                    label.source_history_key,
                                    mention.source_reference_order,
                                )
                            ]
                            if mention.mention_kind == "LEADER_LIST":
                                leader_id = reference.reference_id
                            else:
                                membership_id = reference.reference_id
                        company_id = (
                            companies[mention.seed_stock_code]
                            if mention.seed_stock_code is not None
                            else None
                        )
                        yield (
                            item.stage_revision_key,
                            mention.source_order,
                            mention.mention_kind,
                            leader_id,
                            membership_id,
                            company_id,
                            mention.resolution_status,
                            mention.resolution_basis,
                            mention.suggested_role,
                            mention.start,
                            mention.end,
                            body_hash
                            if mention.mention_kind == "BODY"
                            else sha256_text(mention.mention_text),
                        )

            self._copy_rows(
                db,
                "COPY company_mention_stage"
                " (stage_revision_key, source_order, mention_kind,"
                " history_leader_id, history_membership_id, company_id,"
                " resolution_status, resolution_basis, suggested_role,"
                " mention_start, mention_end, evidence_source_hash) FROM STDIN",
                mention_rows(),
            )

            def role_rows() -> Iterable[tuple[object, ...]]:
                for item in prepared:
                    for mention in item.label.mentions:
                        if not mention.roles:
                            continue
                        if mention.seed_stock_code is None:
                            raise ValueError("role fact에 회사가 없습니다.")
                        company_id = companies[mention.seed_stock_code]
                        for role in mention.roles:
                            yield (
                                item.stage_revision_key,
                                mention.source_order,
                                company_id,
                                role.source_order,
                                role.role,
                                role.extraction_basis,
                                role.start,
                                role.end,
                            )

            self._copy_rows(
                db,
                "COPY company_role_stage"
                " (stage_revision_key, mention_source_order, company_id,"
                " source_order, role, extraction_basis, evidence_start,"
                " evidence_end) FROM STDIN",
                role_rows(),
            )
            db.execute(
                "ANALYZE company_role_revision_stage, company_mention_stage,"
                " company_role_stage"
            )

            db.execute(
                "WITH prepared_revisions AS ("
                " SELECT s.*, COALESCE(("
                "   SELECT MAX(r.revision_no)"
                "   FROM ontology.history_company_role_revisions r"
                "   WHERE r.history_id = s.history_id"
                " ), 0) + 1 AS revision_no"
                " FROM company_role_revision_stage s"
                "), inserted AS ("
                " INSERT INTO ontology.history_company_role_revisions"
                " (history_id, revision_no, company_master_version,"
                " role_transform_version, history_content_hash, output_hash,"
                " labeled_at)"
                " SELECT history_id, revision_no, company_master_version,"
                " role_transform_version, history_content_hash, output_hash,"
                " labeled_at FROM prepared_revisions"
                " ORDER BY stage_revision_key"
                " ON CONFLICT (history_id, company_master_version,"
                " role_transform_version) DO NOTHING"
                " RETURNING role_revision_id, history_id, company_master_version,"
                " role_transform_version"
                ")"
                " INSERT INTO company_role_revision_map"
                " (stage_revision_key, role_revision_id)"
                " SELECT s.stage_revision_key, i.role_revision_id"
                " FROM inserted i"
                " JOIN company_role_revision_stage s"
                "   ON s.history_id = i.history_id"
                "  AND s.company_master_version = i.company_master_version"
                "  AND s.role_transform_version = i.role_transform_version"
            )
            inserted = db.rowcount

            db.execute(
                "SELECT s.history_id, s.output_hash, r.output_hash"
                " FROM company_role_revision_stage s"
                " JOIN ontology.history_company_role_revisions r"
                "   ON r.history_id = s.history_id"
                "  AND r.company_master_version = s.company_master_version"
                "  AND r.role_transform_version = s.role_transform_version"
                " LEFT JOIN company_role_revision_map m"
                "   ON m.stage_revision_key = s.stage_revision_key"
                " WHERE m.stage_revision_key IS NULL"
                "   AND r.output_hash <> s.output_hash"
                " LIMIT 1"
            )
            conflict = db.fetchone()
            if conflict is not None:
                raise CompanyRoleTransformConflictError(
                    f"history_id={int(conflict[0])}의 동일 역할 변환 버전이 "
                    "다른 결과로 동시에 적재됐습니다."
                )
            existing += len(prepared) - inserted

            db.execute(
                "WITH inserted AS ("
                " INSERT INTO ontology.history_company_mentions"
                " (role_revision_id, source_order, mention_kind,"
                " history_leader_id, history_membership_id, company_id,"
                " resolution_status, resolution_basis, suggested_role,"
                " mention_start, mention_end, evidence_source_hash)"
                " SELECT m.role_revision_id, s.source_order, s.mention_kind,"
                " s.history_leader_id, s.history_membership_id, s.company_id,"
                " s.resolution_status, s.resolution_basis, s.suggested_role,"
                " s.mention_start, s.mention_end, s.evidence_source_hash"
                " FROM company_mention_stage s"
                " JOIN company_role_revision_map m"
                "   ON m.stage_revision_key = s.stage_revision_key"
                " ORDER BY s.stage_revision_key, s.source_order"
                " RETURNING company_mention_id, role_revision_id, source_order"
                ")"
                " INSERT INTO company_mention_map"
                " (stage_revision_key, source_order, company_mention_id)"
                " SELECT r.stage_revision_key, i.source_order,"
                " i.company_mention_id"
                " FROM inserted i"
                " JOIN company_role_revision_map r"
                "   ON r.role_revision_id = i.role_revision_id"
            )
            mentions_inserted = db.rowcount

            db.execute(
                "INSERT INTO ontology.history_company_roles"
                " (company_mention_id, company_id, source_order, role,"
                " extraction_basis, evidence_start, evidence_end)"
                " SELECT m.company_mention_id, s.company_id, s.source_order,"
                " s.role, s.extraction_basis, s.evidence_start, s.evidence_end"
                " FROM company_role_stage s"
                " JOIN company_mention_map m"
                "   ON m.stage_revision_key = s.stage_revision_key"
                "  AND m.source_order = s.mention_source_order"
                " ORDER BY s.stage_revision_key, s.mention_source_order,"
                " s.source_order"
            )
            roles_inserted = db.rowcount
            self._connection.commit()
            return CompanyRoleLoadCounts(
                total=len(materialized),
                inserted=inserted,
                existing=existing,
                unresolved_history=unresolved_history,
                mismatched_history=mismatched_history,
                missing_references=missing_references,
                mentions_inserted=mentions_inserted,
                roles_inserted=roles_inserted,
            )
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()

    def load(
        self,
        labels: Iterable[HistoryCompanyRoleLabel],
        *,
        labeled_at: datetime,
    ) -> CompanyRoleLoadCounts:
        """역할 revision을 적재한다. 같은 버전 재실행은 행을 늘리지 않는다."""

        materialized = tuple(labels)
        companies = self.company_ids()
        missing_companies = sorted(
            {
                mention.seed_stock_code
                for label in materialized
                for mention in label.mentions
                if mention.resolution_status == "RESOLVED"
                and mention.seed_stock_code is not None
                and mention.seed_stock_code not in companies
            }
        )
        if missing_companies:
            raise CompanyRoleCompanyMissingError(
                "회사 master에 없는 종목코드입니다: " + ", ".join(missing_companies)
            )

        histories = self.current_history()
        references = self.current_references()
        total = 0
        inserted = 0
        existing = 0
        unresolved_history = 0
        mismatched_history = 0
        missing_references = 0
        mentions_inserted = 0
        roles_inserted = 0
        db = self._connection.cursor()
        try:
            for label in materialized:
                total += 1
                history = histories.get(
                    (label.source_theme_id, label.source_history_key)
                )
                if history is None:
                    unresolved_history += 1
                    continue
                if (
                    history.raw_text != label.raw_text
                    or history.content_hash != label.history_content_hash
                ):
                    mismatched_history += 1
                    continue

                linked: dict[int, tuple[int | None, int | None]] = {}
                references_valid = True
                for mention in label.mentions:
                    if mention.mention_kind == "BODY":
                        linked[mention.source_order] = (None, None)
                        continue
                    if mention.source_reference_order is None:  # dataclass invariant
                        raise ValueError("구조화 mention 원천 순서가 없습니다.")
                    reference = references.get(
                        (
                            mention.mention_kind,
                            label.source_theme_id,
                            label.source_history_key,
                            mention.source_reference_order,
                        )
                    )
                    if (
                        reference is None
                        or reference.source_stock_name != mention.mention_text
                    ):
                        references_valid = False
                        break
                    linked[mention.source_order] = (
                        reference.reference_id
                        if mention.mention_kind == "LEADER_LIST"
                        else None,
                        reference.reference_id
                        if mention.mention_kind == "MEMBERSHIP"
                        else None,
                    )
                if not references_valid:
                    missing_references += 1
                    continue

                db.execute(
                    "SELECT role_revision_id, output_hash"
                    " FROM ontology.history_company_role_revisions"
                    " WHERE history_id = %s AND company_master_version = %s"
                    " AND role_transform_version = %s",
                    (
                        history.history_id,
                        label.company_master_version,
                        label.role_transform_version,
                    ),
                )
                found = db.fetchone()
                if found is not None:
                    if str(found[1]) != label.output_hash:
                        raise CompanyRoleTransformConflictError(
                            f"history_id={history.history_id}의 동일 역할 변환 버전이 "
                            "다른 결과로 이미 적재돼 있습니다. 규칙을 고쳤다면 "
                            "COMPANY_ROLE_TRANSFORM_VERSION을 올리십시오."
                        )
                    existing += 1
                    continue

                db.execute(
                    "INSERT INTO ontology.history_company_role_revisions"
                    " (history_id, revision_no, company_master_version,"
                    " role_transform_version, history_content_hash, output_hash,"
                    " labeled_at)"
                    " SELECT %s, COALESCE(MAX(revision_no), 0) + 1, %s, %s, %s, %s, %s"
                    " FROM ontology.history_company_role_revisions"
                    " WHERE history_id = %s"
                    " RETURNING role_revision_id",
                    (
                        history.history_id,
                        label.company_master_version,
                        label.role_transform_version,
                        label.history_content_hash,
                        label.output_hash,
                        labeled_at,
                        history.history_id,
                    ),
                )
                revision = db.fetchone()
                if revision is None:  # pragma: no cover - RETURNING은 항상 행을 준다
                    raise RuntimeError("회사 역할 revision을 만들지 못했습니다.")
                role_revision_id = int(revision[0])
                inserted += 1

                for mention in label.mentions:
                    leader_id, membership_id = linked[mention.source_order]
                    company_id = (
                        companies[mention.seed_stock_code]
                        if mention.seed_stock_code is not None
                        else None
                    )
                    source_text = (
                        label.raw_text
                        if mention.mention_kind == "BODY"
                        else mention.mention_text
                    )
                    db.execute(
                        "INSERT INTO ontology.history_company_mentions"
                        " (role_revision_id, source_order, mention_kind,"
                        " history_leader_id, history_membership_id, company_id,"
                        " resolution_status, resolution_basis, suggested_role,"
                        " mention_start, mention_end, evidence_source_hash)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                        " RETURNING company_mention_id",
                        (
                            role_revision_id,
                            mention.source_order,
                            mention.mention_kind,
                            leader_id,
                            membership_id,
                            company_id,
                            mention.resolution_status,
                            mention.resolution_basis,
                            mention.suggested_role,
                            mention.start,
                            mention.end,
                            sha256_text(source_text),
                        ),
                    )
                    stored_mention = db.fetchone()
                    if stored_mention is None:  # pragma: no cover
                        raise RuntimeError("회사 mention을 만들지 못했습니다.")
                    company_mention_id = int(stored_mention[0])
                    mentions_inserted += 1
                    for role in mention.roles:
                        if company_id is None:  # dataclass invariant
                            raise ValueError("role fact에 회사가 없습니다.")
                        db.execute(
                            "INSERT INTO ontology.history_company_roles"
                            " (company_mention_id, company_id, source_order, role,"
                            " extraction_basis, evidence_start, evidence_end)"
                            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            (
                                company_mention_id,
                                company_id,
                                role.source_order,
                                role.role,
                                role.extraction_basis,
                                role.start,
                                role.end,
                            ),
                        )
                        roles_inserted += 1
            self._connection.commit()
            return CompanyRoleLoadCounts(
                total=total,
                inserted=inserted,
                existing=existing,
                unresolved_history=unresolved_history,
                mismatched_history=mismatched_history,
                missing_references=missing_references,
                mentions_inserted=mentions_inserted,
                roles_inserted=roles_inserted,
            )
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()
