"""DailyFeaturedTheme 읽기 모델 (질의 유형 `DAY_MOVERS`).

"이날 뭐가 올랐어·빠졌어"에 답하는 데 필요한 것만 읽는다. 값을 새로 만들지
않는다 — 등락률과 종가는 원문 표에 적힌 값이고 사유는 원문 문단이다.

특징테마는 장 마감 후 발행된다. 아직 없는 날짜를 물으면 지어내지 않고
`NOT_PUBLISHED`로 답한 뒤 직전 발행일 결과를 함께 준다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol


class DbCursor(Protocol):
    def execute(
        self, query: str, params: Sequence[object] | None = None
    ) -> object: ...

    def fetchone(self) -> Sequence[Any] | None: ...

    def fetchall(self) -> Sequence[Sequence[Any]]: ...

    def close(self) -> None: ...


class DbConnection(Protocol):
    def cursor(self) -> DbCursor: ...


@dataclass(frozen=True, slots=True)
class MoverStock:
    stock_name: str
    stock_code: str | None
    close_price: int | None
    change_rate: str | None


@dataclass(frozen=True, slots=True)
class MoverTheme:
    """한 테마의 그날 반응. change_rate 부호가 올랐는지 빠졌는지를 가른다."""

    theme_name: str
    change_rate: str | None
    headline: str
    stocks: tuple[MoverStock, ...]


@dataclass(frozen=True, slots=True)
class MoverSection:
    """게시물의 한 섹션. headline은 머리글, details는 상세 문단이다."""

    section_name: str
    headline: str
    details: tuple[str, ...]
    themes: tuple[MoverTheme, ...]


@dataclass(frozen=True, slots=True)
class DayMovers:
    requested_date: date
    published_date: date | None
    status: str
    sections: tuple[MoverSection, ...]

    @property
    def is_fallback(self) -> bool:
        return self.published_date is not None and self.published_date != self.requested_date


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _rate(value: Any) -> str | None:
    if value is None:
        return None
    return format(value, "f") if isinstance(value, Decimal) else str(value)


class PostgresDailyFeaturedReader:
    """발행된 특징테마 하루치를 읽는다. 아무것도 쓰지 않는다."""

    def __init__(self, connection: DbConnection) -> None:
        self._connection = connection

    def day_movers(self, requested: date) -> DayMovers:
        db = self._connection.cursor()
        try:
            db.execute(
                "SELECT max(published_date) FROM core.infostock_daily_post_revisions"
                " WHERE observed_to IS NULL AND published_date <= %s",
                (requested,),
            )
            row = db.fetchone()
            published = None if row is None else row[0]
            if published is None:
                return DayMovers(requested, None, "NO_RECORD", ())
            status = "PUBLISHED" if published == requested else "NOT_PUBLISHED"
            return DayMovers(
                requested, published, status, self._sections(db, published)
            )
        finally:
            db.close()

    def _sections(self, db: DbCursor, published: date) -> tuple[MoverSection, ...]:
        db.execute(
            "SELECT dr.relation_type, dr.source_theme_name, dr.description,"
            " dr.raw_text, dr.paragraph_no, dr.theme_change_rate,"
            " dr.source_stock_name, dr.source_stock_code, dr.close_price,"
            " dr.change_rate"
            " FROM core.infostock_daily_relations dr"
            " JOIN core.infostock_daily_post_revisions r"
            "   ON r.daily_post_revision_id = dr.daily_post_revision_id"
            " WHERE r.observed_to IS NULL AND r.published_date = %s"
            " ORDER BY r.daily_post_revision_id, dr.source_order",
            (published,),
        )
        order: list[str] = []
        headlines: dict[str, str] = {}
        details: dict[str, list[str]] = {}
        # 표는 섹션명이 아니라 섹션 머리글로 이어져 있다(파서가 문서 순서로 붙인다).
        themes: dict[str, list[MoverTheme]] = {}
        stocks: dict[tuple[str, str], list[MoverStock]] = {}
        rates: dict[tuple[str, str], str | None] = {}
        for row in db.fetchall():
            relation_type = str(row[0])
            section_name = _text(row[1])
            if relation_type == "DESCRIPTION":
                if section_name not in headlines:
                    order.append(section_name)
                    headlines[section_name] = _text(row[2])
                    details[section_name] = []
            elif relation_type == "SECTION_DETAIL":
                details.setdefault(section_name, []).append(_text(row[3]))
                if section_name not in headlines:
                    order.append(section_name)
                    headlines[section_name] = _text(row[2])
            elif relation_type == "THEME_STOCK":
                key = (_text(row[2]), section_name)
                if key not in stocks:
                    stocks[key] = []
                    rates[key] = _rate(row[5])
                stocks[key].append(
                    MoverStock(
                        stock_name=_text(row[6]),
                        stock_code=None if row[7] is None else str(row[7]),
                        close_price=None if row[8] is None else int(row[8]),
                        change_rate=_rate(row[9]),
                    )
                )
        for (headline, theme_name), members in stocks.items():
            themes.setdefault(headline, []).append(
                MoverTheme(
                    theme_name=theme_name,
                    change_rate=rates[(headline, theme_name)],
                    headline=headline,
                    stocks=tuple(members),
                )
            )
        return tuple(
            MoverSection(
                section_name=name,
                headline=headlines[name],
                details=tuple(details.get(name, ())),
                themes=tuple(themes.get(headlines[name], ())),
            )
            for name in order
        )
