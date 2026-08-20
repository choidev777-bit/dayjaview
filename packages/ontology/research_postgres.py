"""자연어 리서치 답변이 읽는 Postgres 저장소 (E-22 단계 5).

읽기 전용이다. 값을 만들지 않고 저장된 행만 옮긴다. Daily 조회 기준은
발행일이 아니라 `ontology.source_mention_daily.trading_date`이며, 서빙 대상이
아닌 형식(`serving_status <> 'ELIGIBLE'`)은 답에서 제외한다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Protocol

from .company_entities import (
    CompanyAliasDraft,
    CompanyDraft,
    CompanyInstrumentDraft,
    CompanyMaster,
)
from .daily_mentions import DAILY_MENTION_TRANSFORM_VERSION
from .query_answers import (
    CatalystCompanyRoleRow,
    CatalystFilter,
    CatalystSummary,
    DailyDay,
    DailySection,
    DailyStock,
    DailyStockRow,
    DailyTheme,
    OutcomeObservation,
    ThemeDailyChange,
    ThemeHistoryRecord,
    ThemeMembership,
    ValueFact,
)
from .query_contracts import QueryPrerequisite
from .query_planning import QuestionCatalog, ThemeEntry


class DbCursor(Protocol):
    def execute(self, query: str, params: Sequence[object] | None = None) -> object: ...

    def fetchone(self) -> Sequence[Any] | None: ...

    def fetchall(self) -> Sequence[Sequence[Any]]: ...

    def close(self) -> None: ...


class DbConnection(Protocol):
    def cursor(self) -> DbCursor: ...


class PriceOutcomeReader(Protocol):
    """E-16 가격 corpus 읽기. 없으면 결과 질문은 gate가 닫힌 채로 둔다."""

    def price_range_from(self) -> date: ...

    def returns(
        self, stock_code: str, occurred_on: date, horizons: Sequence[int]
    ) -> tuple[date | None, Decimal | None, Mapping[int, Decimal | None], str | None]: ...


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _text(value: Any) -> str:
    return "" if value is None else str(value)


@dataclass(frozen=True, slots=True)
class _DailyRow:
    trading_date: date
    published_date: date | None
    post_revision_id: int
    relation_type: str
    theme_name: str
    description: str
    raw_text: str
    theme_change_rate: Decimal | None
    stock_name: str
    stock_code: str | None
    close_price: int | None
    change_rate: Decimal | None


_DAILY_SELECT = (
    "SELECT DISTINCT ON (dr.daily_relation_id)"
    " smd.trading_date, smd.published_date, dr.daily_post_revision_id,"
    " dr.relation_type, dr.source_theme_name, dr.description, dr.raw_text,"
    " dr.theme_change_rate, dr.source_stock_name, dr.source_stock_code,"
    " dr.close_price, dr.change_rate, dr.source_order, dr.daily_relation_id"
    " FROM ontology.source_mention_daily smd"
    " JOIN core.infostock_daily_relations dr"
    "   ON dr.daily_relation_id = smd.daily_relation_id"
    " JOIN core.infostock_daily_post_revisions r"
    "   ON r.daily_post_revision_id = dr.daily_post_revision_id"
    " WHERE r.observed_to IS NULL AND smd.serving_status = 'ELIGIBLE'"
    "   AND smd.transform_version = %s"
)


class PostgresResearchRepository:
    """단계 1~4 적재분과 인포스탁 core를 읽어 답변 계산에 넘긴다."""

    def __init__(
        self,
        connection: DbConnection,
        *,
        price_reader: PriceOutcomeReader | None = None,
        daily_transform_version: str = DAILY_MENTION_TRANSFORM_VERSION,
    ) -> None:
        self._connection = connection
        self._price_reader = price_reader
        self._daily_transform_version = daily_transform_version

    # ------------------------------------------------------------ 메타

    def versions(self) -> Mapping[str, str]:
        db = self._connection.cursor()
        try:
            versions: dict[str, str] = {}
            db.execute(
                "SELECT max(master_version) FROM core.company_entities"
            )
            versions["companyMaster"] = _text(self._scalar(db))
            db.execute(
                "SELECT max(vocabulary_version), max(transform_version)"
                " FROM ontology.theme_history_labels"
            )
            row = db.fetchone()
            if row is not None:
                versions["ontologyVocabulary"] = _text(row[0])
                versions["classificationTransform"] = _text(row[1])
            db.execute(
                "SELECT max(transform_version) FROM ontology.source_mention_daily"
            )
            versions["dailyParser"] = _text(self._scalar(db))
            db.execute(
                "SELECT max(dedup_policy_version), max(dataset_hash),"
                " max(event_structure_transform_version)"
                " FROM ontology.current_catalyst_revisions"
            )
            row = db.fetchone()
            if row is not None:
                versions["dedupPolicy"] = _text(row[0])
                versions["datasetHash"] = _text(row[1])
                versions["eventStructureTransform"] = _text(row[2])
            if self._price_reader is not None:
                versions["priceCorpusFrom"] = (
                    self._price_reader.price_range_from().isoformat()
                )
            return {key: value for key, value in versions.items() if value}
        finally:
            db.close()

    def ready_prerequisites(self) -> frozenset[QueryPrerequisite]:
        checks: tuple[tuple[QueryPrerequisite, str], ...] = (
            (QueryPrerequisite.E17_LABELS_DB, "ontology.theme_history_labels"),
            (QueryPrerequisite.E22_STAGE_1, "ontology.source_mention_daily"),
            (QueryPrerequisite.E22_STAGE_2, "core.company_entities"),
            (QueryPrerequisite.E22_STAGE_3, "ontology.history_company_roles"),
            (QueryPrerequisite.E22_STAGE_4, "ontology.catalyst_revisions"),
        )
        ready: set[QueryPrerequisite] = set()
        db = self._connection.cursor()
        try:
            for prerequisite, table in checks:
                db.execute(f"SELECT EXISTS (SELECT 1 FROM {table} LIMIT 1)")
                if bool(self._scalar(db)):
                    ready.add(prerequisite)
        finally:
            db.close()
        if self._price_reader is not None and QueryPrerequisite.E22_STAGE_4 in ready:
            ready.add(QueryPrerequisite.E22_STAGE_6)
        return frozenset(ready)

    @staticmethod
    def _scalar(db: DbCursor) -> Any:
        row = db.fetchone()
        return None if row is None else row[0]

    # ------------------------------------------------------------ Daily

    def _daily_rows(self, trading_date: date) -> tuple[_DailyRow, ...]:
        db = self._connection.cursor()
        try:
            db.execute(
                _DAILY_SELECT
                + " AND smd.trading_date = %s"
                + " ORDER BY dr.daily_relation_id, dr.daily_post_revision_id,"
                " dr.source_order",
                (self._daily_transform_version, trading_date),
            )
            return tuple(self._to_daily_row(row) for row in db.fetchall())
        finally:
            db.close()

    @staticmethod
    def _to_daily_row(row: Sequence[Any]) -> _DailyRow:
        return _DailyRow(
            trading_date=row[0],
            published_date=row[1],
            post_revision_id=int(row[2]),
            relation_type=str(row[3]),
            theme_name=_text(row[4]),
            description=_text(row[5]),
            raw_text=_text(row[6]),
            theme_change_rate=_decimal(row[7]),
            stock_name=_text(row[8]),
            stock_code=None if row[9] is None else str(row[9]).strip(),
            close_price=None if row[10] is None else int(row[10]),
            change_rate=_decimal(row[11]),
        )

    def _latest_trading_date(self, requested: date) -> date | None:
        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT max(trading_date) FROM ontology.source_mention_daily"
                " WHERE trading_date <= %s AND serving_status = 'ELIGIBLE'"
                "   AND transform_version = %s",
                (requested, self._daily_transform_version),
            )
            return self._scalar(db)
        finally:
            db.close()

    def _unsplit_post_count(self, trading_date: date) -> int:
        """같은 거래일에 게시물이 여럿이면 발행일을 거래일로 가르지 못한 것이다."""

        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT count(DISTINCT dr.daily_post_revision_id)"
                " FROM ontology.source_mention_daily smd"
                " JOIN core.infostock_daily_relations dr"
                "   ON dr.daily_relation_id = smd.daily_relation_id"
                " JOIN core.infostock_daily_post_revisions r"
                "   ON r.daily_post_revision_id = dr.daily_post_revision_id"
                " WHERE r.observed_to IS NULL AND smd.trading_date = %s"
                "   AND smd.transform_version = %s",
                (trading_date, self._daily_transform_version),
            )
            return int(self._scalar(db) or 0)
        finally:
            db.close()

    @staticmethod
    def _sections(rows: Sequence[_DailyRow]) -> tuple[DailySection, ...]:
        order: list[str] = []
        headlines: dict[str, str] = {}
        details: dict[str, list[str]] = {}
        stocks: dict[tuple[str, str], list[DailyStock]] = {}
        rates: dict[tuple[str, str], Decimal | None] = {}
        for row in rows:
            section_name = row.theme_name
            if row.relation_type == "DESCRIPTION":
                if section_name not in headlines:
                    order.append(section_name)
                    headlines[section_name] = row.description
                    details[section_name] = []
            elif row.relation_type == "SECTION_DETAIL":
                if section_name not in headlines:
                    order.append(section_name)
                    headlines[section_name] = row.description
                    details[section_name] = []
                details[section_name].append(row.raw_text)
            elif row.relation_type == "THEME_STOCK":
                # THEME_STOCK은 description이 섹션 머리글, source_theme_name이 테마다.
                key = (row.description, section_name)
                if key not in stocks:
                    stocks[key] = []
                    rates[key] = row.theme_change_rate
                stocks[key].append(
                    DailyStock(
                        stock_name=row.stock_name,
                        stock_code=row.stock_code,
                        close_price=row.close_price,
                        change_rate=row.change_rate,
                    )
                )
        themes_by_headline: dict[str, list[DailyTheme]] = defaultdict(list)
        for (headline, theme_name), members in stocks.items():
            themes_by_headline[headline].append(
                DailyTheme(
                    theme_name=theme_name,
                    change_rate=rates[(headline, theme_name)],
                    stocks=tuple(members),
                )
            )
        return tuple(
            DailySection(
                section_name=name,
                headline=headlines[name],
                details=tuple(details.get(name, ())),
                themes=tuple(themes_by_headline.get(headlines[name], ())),
            )
            for name in order
        )

    def daily_day(self, trading_date: date) -> DailyDay:
        rows = self._daily_rows(trading_date)
        if rows:
            return DailyDay(
                trading_date=trading_date,
                published_date=rows[0].published_date,
                status="PUBLISHED",
                sections=self._sections(rows),
                unsplit_post_count=self._unsplit_post_count(trading_date),
            )
        fallback = self._latest_trading_date(trading_date)
        if fallback is None:
            return DailyDay(trading_date, None, "NO_RECORD", ())
        rows = self._daily_rows(fallback)
        return DailyDay(
            trading_date=fallback,
            published_date=rows[0].published_date if rows else None,
            status="NOT_PUBLISHED",
            sections=self._sections(rows),
            unsplit_post_count=self._unsplit_post_count(fallback),
        )

    def daily_days(self, start: date, end: date) -> tuple[DailyDay, ...]:
        db = self._connection.cursor()
        try:
            db.execute(
                _DAILY_SELECT
                + " AND smd.trading_date BETWEEN %s AND %s"
                + " ORDER BY dr.daily_relation_id, dr.daily_post_revision_id,"
                " dr.source_order",
                (self._daily_transform_version, start, end),
            )
            grouped: dict[date, list[_DailyRow]] = defaultdict(list)
            for row in db.fetchall():
                parsed = self._to_daily_row(row)
                grouped[parsed.trading_date].append(parsed)
        finally:
            db.close()
        return tuple(
            DailyDay(
                trading_date=trading_date,
                published_date=rows[0].published_date,
                status="PUBLISHED",
                sections=self._sections(rows),
            )
            for trading_date, rows in sorted(grouped.items())
        )

    def _company_stock_codes(self, seed_stock_code: str) -> tuple[str, ...]:
        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT s.stock_code FROM core.company_entities c"
                " JOIN core.company_instruments i ON i.company_id = c.company_id"
                " JOIN core.infostock_stocks s ON s.stock_id = i.stock_id"
                " WHERE c.seed_stock_code = %s",
                (seed_stock_code,),
            )
            codes = {str(row[0]).strip() for row in db.fetchall()}
        finally:
            db.close()
        codes.add(seed_stock_code)
        return tuple(sorted(codes))

    def stock_daily_rows(
        self, seed_stock_code: str, start: date, end: date
    ) -> tuple[DailyStockRow, ...]:
        codes = self._company_stock_codes(seed_stock_code)
        db = self._connection.cursor()
        try:
            db.execute(
                _DAILY_SELECT
                + " AND smd.trading_date BETWEEN %s AND %s"
                + " AND dr.relation_type = 'THEME_STOCK'"
                + " AND dr.source_stock_code = ANY(%s)"
                + " ORDER BY dr.daily_relation_id, dr.daily_post_revision_id,"
                " dr.source_order",
                (self._daily_transform_version, start, end, list(codes)),
            )
            rows = [self._to_daily_row(row) for row in db.fetchall()]
        finally:
            db.close()
        return tuple(
            DailyStockRow(
                trading_date=row.trading_date,
                theme_name=row.theme_name,
                section_headline=row.description,
                stock_name=row.stock_name,
                stock_code=row.stock_code,
                close_price=row.close_price,
                change_rate=row.change_rate,
            )
            for row in sorted(rows, key=lambda item: (item.trading_date, item.theme_name))
        )

    def theme_daily_changes(
        self, theme_names: Sequence[str], start: date, end: date
    ) -> tuple[ThemeDailyChange, ...]:
        if not theme_names:
            return ()
        db = self._connection.cursor()
        try:
            db.execute(
                _DAILY_SELECT
                + " AND smd.trading_date BETWEEN %s AND %s"
                + " AND dr.relation_type = 'THEME_STOCK'"
                + " AND dr.source_theme_name = ANY(%s)"
                + " ORDER BY dr.daily_relation_id, dr.daily_post_revision_id,"
                " dr.source_order",
                (self._daily_transform_version, start, end, list(theme_names)),
            )
            rows = [self._to_daily_row(row) for row in db.fetchall()]
        finally:
            db.close()
        seen: dict[tuple[date, str], ThemeDailyChange] = {}
        for row in rows:
            key = (row.trading_date, row.theme_name)
            if key in seen:
                continue
            seen[key] = ThemeDailyChange(
                trading_date=row.trading_date,
                theme_name=row.theme_name,
                change_rate=row.theme_change_rate,
                section_headline=row.description,
            )
        return tuple(seen[key] for key in sorted(seen))

    # ------------------------------------------------------------ 테마 구성

    def theme_members(self, source_theme_id: str) -> tuple[ThemeMembership, ...]:
        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT t.source_theme_id, t.current_name, m.source_stock_code,"
                " m.source_stock_name, m.rationale"
                " FROM core.infostock_theme_stock_memberships m"
                " JOIN core.infostock_themes t ON t.theme_id = m.theme_id"
                " WHERE t.source_theme_id = %s AND m.observed_to IS NULL"
                " ORDER BY m.source_rank, m.source_stock_name",
                (source_theme_id,),
            )
            return tuple(
                ThemeMembership(
                    source_theme_id=str(row[0]),
                    theme_name=_text(row[1]),
                    stock_code=None if row[2] is None else str(row[2]).strip(),
                    stock_name=_text(row[3]),
                    reason=None if row[4] is None else str(row[4]),
                )
                for row in db.fetchall()
            )
        finally:
            db.close()

    def stock_theme_memberships(
        self, seed_stock_code: str
    ) -> tuple[ThemeMembership, ...]:
        codes = self._company_stock_codes(seed_stock_code)
        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT t.source_theme_id, t.current_name, m.source_stock_code,"
                " m.source_stock_name, m.rationale"
                " FROM core.infostock_theme_stock_memberships m"
                " JOIN core.infostock_themes t ON t.theme_id = m.theme_id"
                " WHERE m.source_stock_code = ANY(%s) AND m.observed_to IS NULL"
                " ORDER BY t.current_name",
                (list(codes),),
            )
            return tuple(
                ThemeMembership(
                    source_theme_id=str(row[0]),
                    theme_name=_text(row[1]),
                    stock_code=None if row[2] is None else str(row[2]).strip(),
                    stock_name=_text(row[3]),
                    reason=None if row[4] is None else str(row[4]),
                )
                for row in db.fetchall()
            )
        finally:
            db.close()

    def theme_history(
        self,
        source_theme_id: str,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[ThemeHistoryRecord, ...]:
        clauses = ["t.source_theme_id = %s", "h.observed_to IS NULL"]
        params: list[object] = [source_theme_id]
        if date_from is not None:
            clauses.append("h.event_date >= %s")
            params.append(date_from)
        if date_to is not None:
            clauses.append("h.event_date <= %s")
            params.append(date_to)
        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT DISTINCT ON (h.history_id)"
                " t.source_theme_id, t.current_name, h.source_history_key,"
                " h.event_date, h.raw_text, l.primary_type_id, ct.name_ko,"
                " l.type_ids, l.direction, l.certainty, l.continuation"
                " FROM core.infostock_theme_history h"
                " JOIN core.infostock_themes t ON t.theme_id = h.theme_id"
                " LEFT JOIN ontology.theme_history_labels l"
                "   ON l.history_id = h.history_id"
                " LEFT JOIN ontology.catalyst_types ct"
                "   ON ct.vocabulary_version = l.vocabulary_version"
                "  AND ct.type_id = l.primary_type_id"
                f" WHERE {' AND '.join(clauses)}"
                " ORDER BY h.history_id, l.labeled_at DESC",
                params,
            )
            records = tuple(
                ThemeHistoryRecord(
                    source_theme_id=str(row[0]),
                    theme_name=_text(row[1]),
                    source_history_key=str(row[2]),
                    event_date=row[3],
                    raw_text=_text(row[4]),
                    primary_catalyst_type=None if row[5] is None else str(row[5]),
                    primary_catalyst_name_ko=None if row[6] is None else str(row[6]),
                    catalyst_types=tuple(str(item) for item in (row[7] or ())),
                    direction=_text(row[8]) or "UNKNOWN",
                    certainty=_text(row[9]) or "UNSPECIFIED",
                    continuation=bool(row[10]),
                )
                for row in db.fetchall()
            )
        finally:
            db.close()
        return tuple(
            sorted(
                records,
                key=lambda item: (
                    item.event_date is None,
                    item.event_date or date.max,
                    item.source_history_key,
                ),
            )
        )

    # ------------------------------------------------------------ 고유 사건

    def _catalyst_where(
        self, catalyst_filter: CatalystFilter
    ) -> tuple[list[str], list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        if catalyst_filter.date_from is not None:
            clauses.append("cr.occurred_on >= %s")
            params.append(catalyst_filter.date_from)
        if catalyst_filter.date_to is not None:
            clauses.append("cr.occurred_on <= %s")
            params.append(catalyst_filter.date_to)
        if catalyst_filter.catalyst_type is not None:
            clauses.append("%s = ANY(cr.type_ids)")
            params.append(catalyst_filter.catalyst_type)
        if catalyst_filter.seed_stock_code is not None:
            role_clause = (
                "SELECT 1 FROM ontology.catalyst_company_roles ccr"
                " JOIN core.company_entities ce ON ce.company_id = ccr.company_id"
                " WHERE ccr.catalyst_revision_id = cr.catalyst_revision_id"
                "   AND ce.seed_stock_code = %s"
            )
            params.append(catalyst_filter.seed_stock_code)
            if catalyst_filter.roles:
                role_clause += " AND ccr.role = ANY(%s)"
                params.append(list(catalyst_filter.roles))
            clauses.append(f"EXISTS ({role_clause})")
        if catalyst_filter.source_theme_id is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM ontology.catalyst_theme_reactions ctr"
                " JOIN ontology.theme_reaction_revisions trr"
                "   ON trr.reaction_revision_id = ctr.reaction_revision_id"
                " JOIN core.infostock_theme_history th"
                "   ON th.history_id = trr.history_id"
                " JOIN core.infostock_themes tt ON tt.theme_id = th.theme_id"
                " WHERE ctr.catalyst_revision_id = cr.catalyst_revision_id"
                "   AND tt.source_theme_id = %s)"
            )
            params.append(catalyst_filter.source_theme_id)
        if catalyst_filter.topic_text:
            # 사건 문장에 주제어가 있는 것만("로봇 정책"의 로봇). 게시물 전체가
            # 아니라 그 사건의 근거 구간만 본다 — 같은 글 딴 문장에 낱말이
            # 있다는 이유로 무관한 사건이 딸려 오면 안 된다.
            clauses.append(
                "EXISTS (SELECT 1 FROM ontology.source_mentions psm"
                " JOIN ontology.source_mention_history psmh"
                "   ON psmh.source_mention_id = psm.source_mention_id"
                " JOIN core.infostock_theme_history pth"
                "   ON pth.history_id = psmh.history_id"
                " WHERE psm.source_mention_id = cr.primary_source_mention_id"
                "   AND substr(pth.raw_text, psm.start_offset + 1,"
                "              psm.end_offset - psm.start_offset) ILIKE %s)"
            )
            params.append(f"%{catalyst_filter.topic_text}%")
        return clauses, params

    def catalysts(self, catalyst_filter: CatalystFilter) -> tuple[CatalystSummary, ...]:
        clauses, params = self._catalyst_where(catalyst_filter)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = "" if catalyst_filter.limit is None else " LIMIT %s"
        db = self._connection.cursor()
        try:
            db.execute(
                "WITH latest AS ("
                " SELECT DISTINCT ON (cr.catalyst_id) cr.catalyst_revision_id,"
                " cr.catalyst_id, cr.occurred_on, cr.primary_type_id, cr.type_ids,"
                " cr.event_stage, cr.certainty, cr.novelty_type, cr.action,"
                " cr.object_text, cr.project_id, cr.vocabulary_version,"
                " sm.start_offset, sm.end_offset, sm.review_status,"
                " smh.history_id"
                " FROM ontology.current_catalyst_revisions cr"
                " JOIN ontology.source_mentions sm"
                "   ON sm.source_mention_id = cr.primary_source_mention_id"
                " LEFT JOIN ontology.source_mention_history smh"
                "   ON smh.source_mention_id = sm.source_mention_id"
                f"{where}"
                " ORDER BY cr.catalyst_id, cr.revision_no DESC"
                ")"
                " SELECT l.catalyst_revision_id, l.catalyst_id, l.occurred_on,"
                " l.primary_type_id, ct.name_ko, l.type_ids, l.event_stage,"
                " l.certainty, l.novelty_type, l.action, l.object_text,"
                " l.project_id, l.start_offset, l.end_offset, l.review_status,"
                " h.raw_text,"
                " (SELECT count(*) FROM ontology.catalyst_source_mentions csm"
                "   WHERE csm.catalyst_revision_id = l.catalyst_revision_id),"
                " (SELECT count(*) FROM ontology.catalyst_theme_reactions ctr"
                "   WHERE ctr.catalyst_revision_id = l.catalyst_revision_id)"
                " FROM latest l"
                " LEFT JOIN ontology.catalyst_types ct"
                "   ON ct.vocabulary_version = l.vocabulary_version"
                "  AND ct.type_id = l.primary_type_id"
                " LEFT JOIN core.infostock_theme_history h"
                "   ON h.history_id = l.history_id"
                " ORDER BY l.occurred_on DESC NULLS LAST, l.catalyst_id"
                f"{limit}",
                (*params, *((catalyst_filter.limit,) if limit else ())),
            )
            base = db.fetchall()
        finally:
            db.close()
        if not base:
            return ()
        revision_ids = [int(row[0]) for row in base]
        roles = self._catalyst_roles(revision_ids)
        geographies = self._catalyst_geographies(revision_ids)
        themes = self._catalyst_themes(revision_ids)
        summaries: list[CatalystSummary] = []
        for row in base:
            revision_id = int(row[0])
            raw_text = _text(row[15])
            start = int(row[12])
            end = int(row[13])
            summaries.append(
                CatalystSummary(
                    catalyst_id=str(row[1]),
                    occurred_on=row[2],
                    primary_catalyst_type=None if row[3] is None else str(row[3]),
                    primary_catalyst_name_ko=None if row[4] is None else str(row[4]),
                    catalyst_types=tuple(str(item) for item in (row[5] or ())),
                    event_stage=_text(row[6]),
                    certainty=_text(row[7]),
                    novelty_type=_text(row[8]),
                    action=None if row[9] is None else str(row[9]),
                    object_text=None if row[10] is None else str(row[10]),
                    project_id=None if row[11] is None else str(row[11]),
                    geography_codes=geographies.get(revision_id, ()),
                    theme_names=themes.get(revision_id, ()),
                    company_roles=roles.get(revision_id, ()),
                    source_record_count=int(row[16] or 0),
                    theme_reaction_count=int(row[17] or 0),
                    evidence_text=raw_text[start:end] if raw_text else "",
                    evidence_start=start,
                    evidence_end=end,
                    review_state=_text(row[14]) or "AI_DRAFT",
                )
            )
        return tuple(summaries)

    def _catalyst_roles(
        self, revision_ids: Sequence[int]
    ) -> dict[int, tuple[CatalystCompanyRoleRow, ...]]:
        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT ccr.catalyst_revision_id, ce.seed_stock_code,"
                " ce.canonical_name, ccr.role, ccr.impact"
                " FROM ontology.catalyst_company_roles ccr"
                " JOIN core.company_entities ce ON ce.company_id = ccr.company_id"
                " WHERE ccr.catalyst_revision_id = ANY(%s)"
                " ORDER BY ccr.catalyst_revision_id, ce.seed_stock_code, ccr.role",
                (list(revision_ids),),
            )
            grouped: dict[int, list[CatalystCompanyRoleRow]] = defaultdict(list)
            for row in db.fetchall():
                grouped[int(row[0])].append(
                    CatalystCompanyRoleRow(
                        seed_stock_code=str(row[1]).strip(),
                        company_name=_text(row[2]),
                        role=_text(row[3]),
                        impact=_text(row[4]),
                    )
                )
        finally:
            db.close()
        return {key: tuple(value) for key, value in grouped.items()}

    def _catalyst_geographies(
        self, revision_ids: Sequence[int]
    ) -> dict[int, tuple[str, ...]]:
        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT catalyst_revision_id, geography_code"
                " FROM ontology.catalyst_geographies"
                " WHERE catalyst_revision_id = ANY(%s)"
                " ORDER BY catalyst_revision_id, geography_code",
                (list(revision_ids),),
            )
            grouped: dict[int, list[str]] = defaultdict(list)
            for row in db.fetchall():
                grouped[int(row[0])].append(str(row[1]))
        finally:
            db.close()
        return {key: tuple(value) for key, value in grouped.items()}

    def _catalyst_themes(
        self, revision_ids: Sequence[int]
    ) -> dict[int, tuple[str, ...]]:
        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT DISTINCT ctr.catalyst_revision_id, t.current_name"
                " FROM ontology.catalyst_theme_reactions ctr"
                " JOIN ontology.theme_reaction_revisions trr"
                "   ON trr.reaction_revision_id = ctr.reaction_revision_id"
                " JOIN core.infostock_theme_history h"
                "   ON h.history_id = trr.history_id"
                " JOIN core.infostock_themes t ON t.theme_id = h.theme_id"
                " WHERE ctr.catalyst_revision_id = ANY(%s)"
                " ORDER BY ctr.catalyst_revision_id, t.current_name",
                (list(revision_ids),),
            )
            grouped: dict[int, list[str]] = defaultdict(list)
            for row in db.fetchall():
                grouped[int(row[0])].append(_text(row[1]))
        finally:
            db.close()
        return {key: tuple(value) for key, value in grouped.items()}

    def value_facts(self, catalyst_filter: CatalystFilter) -> tuple[ValueFact, ...]:
        clauses, params = self._catalyst_where(catalyst_filter)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        db = self._connection.cursor()
        try:
            db.execute(
                "WITH latest AS ("
                " SELECT DISTINCT ON (cr.catalyst_id) cr.catalyst_revision_id,"
                " cr.catalyst_id, cr.occurred_on"
                " FROM ontology.current_catalyst_revisions cr"
                f"{where}"
                " ORDER BY cr.catalyst_id, cr.revision_no DESC"
                ")"
                " SELECT l.catalyst_id, l.occurred_on, cv.fact_type,"
                " cv.reported_value, cv.normalized_value, cv.unit, cv.currency,"
                " cv.value_basis, cv.eligible_for_sum, h.raw_text,"
                " cv.evidence_start, cv.evidence_end, t.current_name"
                " FROM latest l"
                " JOIN ontology.catalyst_values cv"
                "   ON cv.catalyst_revision_id = l.catalyst_revision_id"
                " JOIN ontology.source_mentions sm"
                "   ON sm.source_mention_id = cv.source_mention_id"
                " LEFT JOIN ontology.source_mention_history smh"
                "   ON smh.source_mention_id = sm.source_mention_id"
                " LEFT JOIN core.infostock_theme_history h"
                "   ON h.history_id = smh.history_id"
                " LEFT JOIN core.infostock_themes t ON t.theme_id = h.theme_id"
                " ORDER BY cv.normalized_value DESC, l.catalyst_id, cv.fact_type",
                params,
            )
            rows = db.fetchall()
        finally:
            db.close()
        facts: list[ValueFact] = []
        for row in rows:
            raw_text = _text(row[9])
            start = int(row[10])
            end = int(row[11])
            facts.append(
                ValueFact(
                    catalyst_id=str(row[0]),
                    occurred_on=row[1],
                    fact_type=_text(row[2]),
                    reported_value=_text(row[3]),
                    normalized_value=_decimal(row[4]) or Decimal(0),
                    unit=_text(row[5]),
                    currency=None if row[6] is None else str(row[6]).strip(),
                    value_basis=_text(row[7]),
                    eligible_for_sum=bool(row[8]),
                    theme_name=_text(row[12]),
                    evidence_text=raw_text[start:end] if raw_text else "",
                )
            )
        return tuple(facts)

    def outcomes(
        self, catalyst_filter: CatalystFilter, *, horizons: Sequence[int]
    ) -> tuple[OutcomeObservation, ...]:
        if self._price_reader is None:
            return ()
        summaries = self.catalysts(
            CatalystFilter(
                date_from=catalyst_filter.date_from,
                date_to=catalyst_filter.date_to,
                catalyst_type=catalyst_filter.catalyst_type,
                seed_stock_code=catalyst_filter.seed_stock_code,
                roles=("ACTOR", "ISSUER", "CONTRACTOR", "TARGET"),
                limit=catalyst_filter.limit,
            )
        )
        observations: list[OutcomeObservation] = []
        for summary in summaries:
            if summary.occurred_on is None:
                continue
            for role in summary.company_roles:
                if (
                    catalyst_filter.seed_stock_code is not None
                    and role.seed_stock_code != catalyst_filter.seed_stock_code
                ):
                    continue
                base_date, base_close, returns, missing = self._price_reader.returns(
                    role.seed_stock_code, summary.occurred_on, horizons
                )
                observations.append(
                    OutcomeObservation(
                        catalyst_id=summary.catalyst_id,
                        occurred_on=summary.occurred_on,
                        seed_stock_code=role.seed_stock_code,
                        company_name=role.company_name,
                        base_trading_date=base_date,
                        base_close=base_close,
                        returns=returns,
                        missing_reason=missing,
                        evidence_text=summary.evidence_text,
                    )
                )
        return tuple(observations)

    def leader_outcomes(
        self, catalyst_filter: CatalystFilter, *, horizons: Sequence[int]
    ) -> tuple[OutcomeObservation, ...]:
        """조건에 맞는 사건들의 **당시 주도주** 실제 수익률.

        회사 축(outcomes)과 달리 사건에 붙은 테마 반응의 주도주 목록을 쓴다
        (계획서 4.1 "회사 또는 당시 주도주 outcome"). 최근 사건부터 최대
        12건, 사건당 주도주 5종목까지만 가격을 조회해 폭주를 막는다.
        """

        if self._price_reader is None:
            return ()
        clauses, params = self._catalyst_where(catalyst_filter)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        event_limit = catalyst_filter.limit or 12
        theme_clause = ""
        theme_params: list[object] = []
        if catalyst_filter.source_theme_id is not None:
            # 테마로 물었으면 그 테마 반응의 주도주만 센다.
            theme_clause = " AND tt.source_theme_id = %s"
            theme_params.append(catalyst_filter.source_theme_id)
        db = self._connection.cursor()
        try:
            db.execute(
                "WITH latest AS ("
                " SELECT DISTINCT ON (cr.catalyst_id) cr.catalyst_revision_id,"
                " cr.catalyst_id, cr.occurred_on"
                " FROM ontology.current_catalyst_revisions cr"
                f"{where}"
                " ORDER BY cr.catalyst_id, cr.revision_no DESC"
                "), picked AS ("
                " SELECT * FROM latest WHERE occurred_on IS NOT NULL"
                " ORDER BY occurred_on DESC, catalyst_id LIMIT %s"
                ")"
                " SELECT p.catalyst_id, p.occurred_on, ce.seed_stock_code,"
                " ce.canonical_name, th.raw_text"
                " FROM picked p"
                " JOIN ontology.catalyst_theme_reactions ctr"
                "   ON ctr.catalyst_revision_id = p.catalyst_revision_id"
                " JOIN ontology.theme_reaction_revisions trr"
                "   ON trr.reaction_revision_id = ctr.reaction_revision_id"
                " JOIN ontology.theme_reaction_company_roles trcr"
                "   ON trcr.reaction_revision_id = trr.reaction_revision_id"
                "  AND trcr.role = 'LEADER'"
                " JOIN core.company_entities ce ON ce.company_id = trcr.company_id"
                " JOIN core.infostock_theme_history th"
                "   ON th.history_id = trr.history_id"
                " JOIN core.infostock_themes tt ON tt.theme_id = th.theme_id"
                f"{theme_clause}"
                " ORDER BY p.occurred_on DESC, p.catalyst_id, ce.seed_stock_code",
                (*params, event_limit, *theme_params),
            )
            rows = db.fetchall()
        finally:
            db.close()
        observations: list[OutcomeObservation] = []
        seen: set[tuple[str, str]] = set()
        per_event: dict[str, int] = {}
        for row in rows:
            catalyst_id = str(row[0])
            occurred_on = row[1]
            stock_code = str(row[2]).strip()
            key = (catalyst_id, stock_code)
            if key in seen:
                continue
            if per_event.get(catalyst_id, 0) >= 5:
                continue
            seen.add(key)
            per_event[catalyst_id] = per_event.get(catalyst_id, 0) + 1
            base_date, base_close, returns, missing = self._price_reader.returns(
                stock_code, occurred_on, horizons
            )
            observations.append(
                OutcomeObservation(
                    catalyst_id=catalyst_id,
                    occurred_on=occurred_on,
                    seed_stock_code=stock_code,
                    company_name=_text(row[3]),
                    base_trading_date=base_date,
                    base_close=base_close,
                    returns=returns,
                    missing_reason=missing,
                    evidence_text=_text(row[4])[:300],
                )
            )
        return tuple(observations)


def load_question_catalog(connection: DbConnection) -> QuestionCatalog:
    """질문에서 알아볼 이름 목록을 DB에서 읽는다. 여기 없는 이름은 해석하지 않는다."""

    db = connection.cursor()
    try:
        db.execute(
            "SELECT c.company_id, c.seed_stock_code, c.canonical_name, c.name_basis,"
            " c.dart_corp_code, c.master_version FROM core.company_entities c"
            " ORDER BY c.seed_stock_code"
        )
        companies = db.fetchall()
        db.execute(
            "SELECT company_id, alias, normalized_alias, alias_type, validity_basis,"
            " source_authority, valid_from, valid_to, mention_count"
            " FROM core.company_aliases ORDER BY company_id, alias"
        )
        alias_rows = db.fetchall()
        db.execute(
            "SELECT i.company_id, s.stock_code, i.share_class, i.link_basis,"
            " i.valid_from, i.valid_to FROM core.company_instruments i"
            " JOIN core.infostock_stocks s ON s.stock_id = i.stock_id"
            " ORDER BY i.company_id, s.stock_code"
        )
        instrument_rows = db.fetchall()
        db.execute(
            "SELECT source_theme_id, current_name FROM core.infostock_themes"
            " WHERE is_active ORDER BY source_theme_id"
        )
        theme_rows = db.fetchall()
    finally:
        db.close()

    aliases: dict[int, list[CompanyAliasDraft]] = defaultdict(list)
    for row in alias_rows:
        aliases[int(row[0])].append(
            CompanyAliasDraft(
                alias=_text(row[1]),
                normalized_alias=_text(row[2]),
                alias_type=_text(row[3]),  # type: ignore[arg-type]
                validity_basis=_text(row[4]),  # type: ignore[arg-type]
                source_authority=_text(row[5]),  # type: ignore[arg-type]
                valid_from=row[6],
                valid_to=row[7],
                mention_count=int(row[8] or 0),
            )
        )
    instruments: dict[int, list[CompanyInstrumentDraft]] = defaultdict(list)
    for row in instrument_rows:
        instruments[int(row[0])].append(
            CompanyInstrumentDraft(
                stock_code=str(row[1]).strip(),
                share_class=_text(row[2]),  # type: ignore[arg-type]
                link_basis=_text(row[3]),  # type: ignore[arg-type]
                valid_from=row[4],
                valid_to=row[5],
            )
        )
    master_version = ""
    drafts: list[CompanyDraft] = []
    for row in companies:
        company_id = int(row[0])
        master_version = _text(row[5]) or master_version
        drafts.append(
            CompanyDraft(
                seed_stock_code=str(row[1]).strip(),
                canonical_name=_text(row[2]),
                name_basis=_text(row[3]),  # type: ignore[arg-type]
                dart_corp_code=None if row[4] is None else str(row[4]).strip(),
                aliases=tuple(aliases.get(company_id, ())),
                instruments=tuple(instruments.get(company_id, ())),
                revisions=(),
            )
        )
    return QuestionCatalog(
        company_master=CompanyMaster(
            master_version=master_version or "company-master/unknown",
            companies=tuple(drafts),
            unresolved=(),
        ),
        themes=tuple(
            ThemeEntry(source_theme_id=str(row[0]), theme_name=_text(row[1]))
            for row in theme_rows
        ),
    )


__all__ = [
    "PostgresResearchRepository",
    "PriceOutcomeReader",
    "load_question_catalog",
]
