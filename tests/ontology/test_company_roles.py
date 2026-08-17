"""테마 history 회사 mention·역할 연결 (회사 온톨지 단계 3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from packages.infostock.hashing import sha256_text
from packages.infostock.models import StockReference, ThemeHistory, ThemeMembership
from packages.ontology import (
    COMPANY_ROLE_TRANSFORM_VERSION,
    build_company_master,
    classify_history_company_roles,
    label_company_history,
    query_company_appearance,
    query_company_direct_events,
    query_company_theme_association,
)


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


def _membership(order: int, code: str, name: str) -> ThemeMembership:
    return ThemeMembership(
        source_order=order,
        stock_code=code,
        stock_name=name,
        rationale="구성종목",
        source_index=None,
        content_hash=sha256_text(f"{code}:{name}"),
    )


def _reference(order: int, code: str | None, name: str) -> StockReference:
    return StockReference(
        source_order=order,
        name=name,
        stock_code=code,
        source_url=None,
        display_value=f"{code}-{name}" if code else name,
        quality_status="OK" if code else "SOURCE_CODE_MISSING",
    )


def _history(
    key: str,
    raw_text: str,
    *,
    when: date = date(2024, 5, 2),
    leaders: tuple[StockReference, ...] = (),
    members: tuple[StockReference, ...] = (),
    order: int = 0,
) -> ThemeHistory:
    return ThemeHistory(
        source_order=order,
        source_history_id=None,
        source_history_key=key,
        event_date=when,
        source_date=None,
        source_created_at=None,
        source_updated_at=None,
        raw_text=raw_text,
        direction="UP",
        leaders=leaders,
        member_stocks=members,
        author=None,
        chart_flag=None,
        source_fingerprint=sha256_text(f"fingerprint:{key}"),
        quality_status="OK",
        content_hash=sha256_text(raw_text),
    )


def _bundle(
    histories: tuple[ThemeHistory, ...] = (),
    *,
    theme_id: str = "379",
    theme_name: str = "항공기부품",
) -> _Bundle:
    names = (
        ("012450", "한화에어로스페이스"),
        ("005930", "삼성전자"),
        ("035720", "카카오"),
        ("041510", "에스엠"),
        ("373220", "LG에너지솔루션"),
        ("006400", "삼성SDI"),
        ("000720", "현대건설"),
        ("000660", "SK하이닉스"),
        ("005870", "휴니드"),
    )
    return _Bundle(
        dataset_hash="d" * 64,
        details=(
            _Detail(
                source_theme_id=theme_id,
                theme_name=theme_name,
                memberships=tuple(
                    _membership(order, code, name)
                    for order, (code, name) in enumerate(names)
                ),
                history=histories,
            ),
        ),
    )


def _label(history: ThemeHistory):
    bundle = _bundle((history,))
    master = build_company_master(bundle)
    return classify_history_company_roles(
        source_theme_id="379",
        theme_name="항공기부품",
        history=history,
        master=master,
    )


def test_body_actor_and_structured_leader_are_separate_mentions() -> None:
    raw_text = (
        "한화에어로스페이스, GE와 약 3억 달러 규모 항공기 엔진 부품 공급계약 "
        "체결 등에 상승(주도주 : 한화에어로스페이스, 휴니드)"
    )
    label = _label(
        _history(
            "contract",
            raw_text,
            leaders=(
                _reference(0, "012450", "한화에어로스페이스"),
                _reference(1, "005870", "휴니드"),
            ),
        )
    )

    hanwha = [
        mention
        for mention in label.mentions
        if mention.seed_stock_code == "012450"
    ]
    assert [mention.mention_kind for mention in hanwha] == ["BODY", "LEADER_LIST"]
    assert hanwha[0].role_names == ("ACTOR", "CONTRACTOR")
    assert hanwha[1].role_names == ("LEADER",)
    assert label.role_transform_version == COMPANY_ROLE_TRANSFORM_VERSION

    body_evidence = raw_text[
        hanwha[0].roles[1].start : hanwha[0].roles[1].end
    ]
    assert body_evidence.startswith("한화에어로스페이스")
    assert body_evidence.endswith("공급계약 체결")
    # 구조화 leader의 span은 raw_text가 아니라 leader source_stock_name 기준이다.
    assert (hanwha[1].start, hanwha[1].end) == (0, len("한화에어로스페이스"))


def test_all_semantic_company_roles_require_explicit_body_evidence() -> None:
    cases = (
        ("삼성전자, 유상증자 발표 소식 등에 상승", "005930", {"ACTOR", "ISSUER"}),
        ("카카오, 에스엠 인수 추진 소식 등에 상승", "035720", {"ACTOR"}),
        ("카카오, 에스엠 인수 추진 소식 등에 상승", "041510", {"TARGET"}),
        (
            "LG에너지솔루션, 삼성SDI와 공급계약 체결 소식 등에 상승",
            "373220",
            {"ACTOR", "CONTRACTOR"},
        ),
        (
            "LG에너지솔루션, 삼성SDI와 공급계약 체결 소식 등에 상승",
            "006400",
            {"COUNTERPARTY"},
        ),
        ("현대건설 수혜 기대감 등에 상승", "000720", {"BENEFICIARY"}),
        ("SK하이닉스 비용 부담 우려 등에 하락", "000660", {"ADVERSELY_AFFECTED"}),
        ("에스엠, 공정위 제재 소식 등에 하락", "041510", {"TARGET"}),
    )

    for position, (raw_text, code, expected) in enumerate(cases):
        label = _label(_history(f"role-{position}", raw_text))
        mention = next(
            item
            for item in label.mentions
            if item.mention_kind == "BODY" and item.seed_stock_code == code
        )
        assert set(mention.role_names) == expected, (raw_text, code)
        for evidence in mention.roles:
            evidence_text = raw_text[evidence.start : evidence.end]
            assert mention.mention_text in evidence_text


def test_unresolved_structured_name_is_kept_without_a_role_fact() -> None:
    label = _label(
        _history(
            "unresolved",
            "정책 기대감 등에 상승(주도주 : 0015G0-그린광학)",
            leaders=(_reference(0, None, "0015G0-그린광학"),),
        )
    )

    mention = label.mentions[0]
    assert mention.mention_kind == "LEADER_LIST"
    assert mention.suggested_role == "LEADER"
    assert mention.resolution_status == "SOURCE_CODE_MISSING"
    assert mention.seed_stock_code is None
    assert mention.roles == ()


def test_company_missing_from_persisted_master_is_kept_unresolved() -> None:
    history = _history(
        "not-persisted",
        "한화에어로스페이스, 공급계약 체결 등에 상승"
        "(주도주 : 한화에어로스페이스)",
        leaders=(_reference(0, "012450", "한화에어로스페이스"),),
    )
    bundle = _bundle((history,))
    label = classify_history_company_roles(
        source_theme_id="379",
        theme_name="항공기부품",
        history=history,
        master=build_company_master(bundle),
        resolvable_seed_codes=frozenset(),
    )

    assert [mention.mention_kind for mention in label.mentions] == [
        "BODY",
        "LEADER_LIST",
    ]
    assert {mention.resolution_status for mention in label.mentions} == {
        "UNKNOWN_STOCK_CODE"
    }
    assert [mention.resolution_basis for mention in label.mentions] == [
        "EXACT_ALIAS",
        "STOCK_CODE",
    ]
    assert all(mention.seed_stock_code is None for mention in label.mentions)
    assert all(mention.roles == () for mention in label.mentions)


def test_leader_only_observation_never_enters_direct_company_events() -> None:
    history = _history(
        "leader-only",
        "글로벌 국방비 확대 전망 등에 상승(주도주 : 한화에어로스페이스)",
        leaders=(_reference(0, "012450", "한화에어로스페이스"),),
    )
    label = _label(history)

    appearance = query_company_appearance((label,), "012450")
    direct = query_company_direct_events((label,), "012450")
    association = query_company_theme_association((label,), "012450")

    assert len(appearance) == 1
    assert appearance[0].roles == ("LEADER",)
    assert direct == ()
    assert association[0].observation_count == 1
    assert association[0].roles == ("LEADER",)


def test_three_company_queries_dedupe_mentions_within_each_history() -> None:
    histories = (
        _history(
            "direct",
            "한화에어로스페이스, 엔진 공급계약 체결 등에 상승"
            "(주도주 : 한화에어로스페이스)",
            leaders=(_reference(0, "012450", "한화에어로스페이스"),),
            order=0,
        ),
        _history(
            "leader",
            "방산 수출 기대감 등에 상승(주도주 : 한화에어로스페이스)",
            leaders=(_reference(0, "012450", "한화에어로스페이스"),),
            when=date(2024, 5, 3),
            order=1,
        ),
        _history(
            "related",
            "우주산업 성장 기대감 등에 상승",
            members=(_reference(0, "012450", "한화에어로스페이스"),),
            when=date(2024, 5, 4),
            order=2,
        ),
    )
    bundle = _bundle(histories)
    labels, report = label_company_history(bundle, build_company_master(bundle))

    appearance = query_company_appearance(labels, "012450")
    direct = query_company_direct_events(labels, "012450")
    association = query_company_theme_association(labels, "012450")

    assert [item.source_history_key for item in appearance] == [
        "direct",
        "leader",
        "related",
    ]
    assert [item.source_history_key for item in direct] == ["direct"]
    assert len(appearance[0].mentions) == 2
    assert association[0].observation_count == 3
    assert association[0].roles == ("ACTOR", "CONTRACTOR", "LEADER", "RELATED")
    assert report["directEventHistories"] == 1
    assert report["reviewStatus"] == "AI_DRAFT"


def test_labeling_order_and_output_hash_are_deterministic() -> None:
    first = _history("b", "삼성전자, 유상증자 발표 등에 상승", order=1)
    second = _history("a", "현대건설 수혜 기대감 등에 상승", order=0)
    bundle = _bundle((first, second))
    master = build_company_master(bundle)

    labels, _ = label_company_history(bundle, master)
    rerun, _ = label_company_history(bundle, master)

    assert [label.source_history_key for label in labels] == ["a", "b"]
    assert [label.output_hash for label in labels] == [
        label.output_hash for label in rerun
    ]
