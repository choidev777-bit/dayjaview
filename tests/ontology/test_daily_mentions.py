"""DailyFeaturedTheme source mention과 coverage (E-22 단계 1)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

from packages.infostock.hashing import sha256_text
from packages.infostock.models import DailyBackfill, DailyPost, DailyRelation
from packages.ontology import (
    DailyFormatFamily,
    DailyMentionScope,
    DailyMentionServingStatus,
    DailyMentionSourceKind,
    TradingDateBasis,
    classify_daily_format,
    infer_daily_trading_date,
    label_daily_mentions,
    mentions_from_daily_post,
)


def _relation(
    order: int,
    relation_type: str,
    raw_text: str,
    *,
    theme: str | None = "방산",
    stock_name: str | None = None,
    stock_code: str | None = None,
    quality: str = "OK",
    change_rate: str | None = None,
) -> DailyRelation:
    return DailyRelation(  # type: ignore[arg-type]
        source_order=order,
        relation_type=relation_type,
        source_theme_name=theme,
        source_stock_name=stock_name,
        source_stock_code=stock_code,
        description="방산 수출 기대감 등에 상승",
        raw_text=raw_text,
        quality_status=quality,
        paragraph_no=order if relation_type != "THEME_STOCK" else None,
        close_price=10000 if change_rate is not None else None,
        change_rate=change_rate,
        trade_volume=100 if change_rate is not None else None,
    )


def _post(*relations: DailyRelation, title: str = "[5/2] 특징테마") -> DailyPost:
    raw_body = "<br>".join(relation.raw_text for relation in relations)
    return DailyPost(
        source_post_key="post-1",
        source_post_id="1",
        source_url="https://example.test/1",
        title=title,
        published_date=date(2024, 5, 3),
        source_date="20240503",
        raw_body=raw_body,
        body_hash=sha256_text(raw_body),
        normalized_hash="a" * 64,
        body_status="OK",
        visibility_status="VISIBLE",
        relations=tuple(relations),
        detail_snapshot=None,
    )


def test_trading_date_prefers_title_and_falls_back_to_published_date() -> None:
    assert infer_daily_trading_date("[1/5] 특징테마", date(2012, 1, 6)) == (
        date(2012, 1, 5),
        TradingDateBasis.TITLE,
    )
    assert infer_daily_trading_date("특징테마", date(2024, 5, 3)) == (
        date(2024, 5, 3),
        TradingDateBasis.PUBLISHED_DATE,
    )
    assert infer_daily_trading_date("특징테마", None) == (
        None,
        TradingDateBasis.UNKNOWN,
    )


def test_daily_relations_become_typed_mentions_without_copying_meaning() -> None:
    post = _post(
        _relation(0, "DESCRIPTION", "방산 수출 기대감 등에 상승"),
        _relation(1, "SECTION_DETAIL", "▷정부가 수출 지원 방안을 발표."),
        _relation(
            2,
            "THEME_STOCK",
            "한화에어로스페이스\t10000\t+3.00%\t100",
            stock_name="한화에어로스페이스",
            stock_code="012450",
            change_rate="3.00",
        ),
    )

    mentions = mentions_from_daily_post(post)

    assert [item.source_kind for item in mentions] == [
        DailyMentionSourceKind.DESCRIPTION,
        DailyMentionSourceKind.DESCRIPTION,
        DailyMentionSourceKind.THEME_STOCK,
    ]
    assert [item.mention_scope for item in mentions] == [
        DailyMentionScope.HEADLINE,
        DailyMentionScope.DETAIL,
        DailyMentionScope.STOCK_ROW,
    ]
    assert mentions[2].suggested_role == "RELATED"
    assert mentions[2].serving_status is DailyMentionServingStatus.ELIGIBLE
    assert mentions[0].trading_date == date(2024, 5, 2)
    assert mentions[0].raw_text[mentions[0].start : mentions[0].end] == (
        "방산 수출 기대감 등에 상승"
    )

    # 수집 시각과 사람 검수 상태는 변환 결과 hash를 바꾸지 않는다.
    changed_lineage = replace(
        mentions[0],
        observed_at=datetime(2026, 8, 17, tzinfo=UTC),
        review_status="HUMAN_CONFIRMED",
    )
    assert changed_lineage.output_hash == mentions[0].output_hash


def test_unattributed_narrative_is_preserved_but_requires_review() -> None:
    post = replace(
        _post(
            _relation(
                0,
                "SECTION_DETAIL",
                "금일 국내증권시장은 약세 마감.",
                theme="테마시황",
            )
        ),
        body_status="PARSE_PARTIAL",
    )

    assert classify_daily_format(post) is DailyFormatFamily.NARRATIVE_UNATTRIBUTED
    mention = mentions_from_daily_post(post)[0]
    assert mention.serving_status is DailyMentionServingStatus.REVIEW_REQUIRED


def test_daily_coverage_report_counts_formats_mentions_and_quote_gaps() -> None:
    sectioned = _post(
        _relation(0, "DESCRIPTION", "방산 수출 기대감 등에 상승"),
        _relation(
            1,
            "THEME_STOCK",
            "한화에어로스페이스\t형식미상",
            stock_name="한화에어로스페이스",
            stock_code="012450",
        ),
    )
    missing = replace(
        _post(title="본문 없음"),
        source_post_key="post-2",
        body_status="MISSING",
        raw_body=None,
        body_hash=None,
    )
    backfill = DailyBackfill(
        component_status="COMPLETE",
        pages=(),
        entries=(),
        posts=(missing, sectioned),
        first_page=1,
        last_page=1,
        next_page=None,
        earliest_date=date(2024, 5, 3),
        latest_date=date(2024, 5, 3),
        coverage_complete=True,
        blockers=(),
        quality_issues=(),
    )

    mentions, report = label_daily_mentions(backfill)

    assert len(mentions) == 2
    assert report["totalPosts"] == 2
    assert report["bodyStatusCounts"] == {"MISSING": 1, "OK": 1}
    assert report["formatFamilyCounts"]["MISSING_BODY"] == 1
    assert report["formatFamilyCounts"]["SECTIONED_WITH_STOCK_TABLE"] == 1
    assert report["unknownQuoteLayoutRows"] == 1
    assert report["unsupportedPostCount"] == 1
