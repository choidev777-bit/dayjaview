"""고유 사건 중복 제거와 프로젝트 진행 관계 회귀 검증."""

from __future__ import annotations

from datetime import date

from packages.infostock.hashing import sha256_text
from packages.ontology.company_roles import (
    CompanyRoleEvidence,
    HistoryCompanyMention,
    HistoryCompanyRoleLabel,
)
from packages.ontology.event_dedup import (
    CatalystRelationType,
    deduplicate_catalysts,
)
from packages.ontology.event_structure import structure_history_catalysts
from packages.ontology.projects import EventStage

_COMPANY = "한화에어로스페이스"
_CODE = "012450"


def _draft(
    raw_text: str,
    *,
    key: str,
    theme_id: str,
    when: date,
):
    start = raw_text.index(_COMPANY)
    evidence_end = raw_text.find(" 등에")
    mention = HistoryCompanyMention(
        source_order=0,
        mention_kind="BODY",
        source_reference_order=None,
        mention_text=_COMPANY,
        start=start,
        end=start + len(_COMPANY),
        resolution_status="RESOLVED",
        resolution_basis="EXACT_ALIAS",
        seed_stock_code=_CODE,
        suggested_role=None,
        roles=(
            CompanyRoleEvidence(
                source_order=0,
                role="ACTOR",
                extraction_basis="BODY_RULE",
                start=start,
                end=evidence_end if evidence_end >= 0 else len(raw_text),
            ),
        ),
    )
    label = HistoryCompanyRoleLabel(
        company_master_version="company-master/test",
        role_transform_version="company-role-transform/test",
        source_theme_id=theme_id,
        theme_name=f"테마-{theme_id}",
        source_history_key=key,
        event_date=when,
        history_content_hash=sha256_text(raw_text),
        raw_text=raw_text,
        mentions=(mention,),
    )
    return structure_history_catalysts(label, dataset_hash="d" * 64)[0]


def test_same_contract_across_themes_counts_as_one_unique_catalyst() -> None:
    raw_text = "한화에어로스페이스, 폴란드 K9 공급계약 체결 등에 상승"
    first = _draft(
        raw_text,
        key="theme-a",
        theme_id="100",
        when=date(2024, 5, 2),
    )
    second = _draft(
        raw_text,
        key="theme-b",
        theme_id="200",
        when=date(2024, 5, 2),
    )

    result = deduplicate_catalysts((first, second))

    assert result.counts.source_record_count == 2
    assert result.counts.theme_reaction_count == 2
    assert result.counts.unique_catalyst_count == 1
    assert result.counts.project_count == 1
    assert len(result.catalysts[0].source_mentions) == 2
    assert len(result.catalysts[0].theme_reactions) == 2


def test_expectation_and_signed_contract_are_separate_events_in_one_project() -> None:
    expected = _draft(
        "한화에어로스페이스, 폴란드 K9 수주 기대감 등에 상승",
        key="expected",
        theme_id="100",
        when=date(2024, 4, 1),
    )
    signed = _draft(
        "한화에어로스페이스, 폴란드 K9 공급계약 체결 등에 상승",
        key="signed",
        theme_id="100",
        when=date(2024, 5, 2),
    )

    result = deduplicate_catalysts((signed, expected))

    assert result.counts.unique_catalyst_count == 2
    assert result.counts.project_count == 1
    assert expected.project_id == signed.project_id
    stages = {item.primary.event_stage for item in result.catalysts}
    assert stages == {EventStage.RUMOR, EventStage.SIGNED}
    advances = [
        item
        for item in result.relations
        if item.relation_type is CatalystRelationType.ADVANCES
    ]
    assert len(advances) == 1


def test_same_project_same_stage_with_conflicting_amount_is_review_candidate() -> None:
    first = _draft(
        "한화에어로스페이스, 폴란드 K9 1조원 공급계약 체결 등에 상승",
        key="amount-a",
        theme_id="100",
        when=date(2024, 5, 2),
    )
    second = _draft(
        "한화에어로스페이스, 폴란드 K9 2조원 공급계약 체결 등에 상승",
        key="amount-b",
        theme_id="200",
        when=date(2024, 5, 2),
    )

    result = deduplicate_catalysts((first, second))

    assert result.counts.unique_catalyst_count == 2
    assert [item.relation_type for item in result.relations] == [
        CatalystRelationType.POSSIBLE_DUPLICATE
    ]


def test_paraphrased_project_report_is_not_automatically_merged() -> None:
    first = _draft(
        "한화에어로스페이스, 폴란드 K9 계약 체결 등에 상승",
        key="wording-a",
        theme_id="100",
        when=date(2024, 5, 2),
    )
    second = _draft(
        "한화에어로스페이스, 폴란드 K9 공급계약 체결 등에 상승",
        key="wording-b",
        theme_id="200",
        when=date(2024, 5, 2),
    )

    result = deduplicate_catalysts((first, second))

    assert result.counts.unique_catalyst_count == 2
    assert [item.relation_type for item in result.relations] == [
        CatalystRelationType.POSSIBLE_DUPLICATE
    ]


def test_dedup_artifact_is_input_order_independent() -> None:
    first = _draft(
        "한화에어로스페이스, 폴란드 K9 수주 기대감 등에 상승",
        key="expected",
        theme_id="100",
        when=date(2024, 4, 1),
    )
    second = _draft(
        "한화에어로스페이스, 폴란드 K9 공급계약 체결 등에 상승",
        key="signed",
        theme_id="200",
        when=date(2024, 5, 2),
    )

    forward = deduplicate_catalysts((first, second))
    reverse = deduplicate_catalysts((second, first))

    assert forward.artifact_hash == reverse.artifact_hash
    assert forward.report() == reverse.report()
