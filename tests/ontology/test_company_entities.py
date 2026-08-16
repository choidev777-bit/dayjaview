"""회사 정체성과 alias (회사 온톨로지 단계 2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from packages.infostock.models import StockReference, ThemeHistory, ThemeMembership
from packages.ontology import (
    COMPANY_MASTER_VERSION,
    CompanyRevisionDraft,
    build_company_master,
    normalize_company_name,
    resolve_company,
    split_share_class,
)


@dataclass(frozen=True)
class _Detail:
    source_theme_id: str
    memberships: tuple[ThemeMembership, ...]
    history: tuple[ThemeHistory, ...]


@dataclass(frozen=True)
class _Bundle:
    details: tuple[_Detail, ...]


def _membership(code: str | None, name: str) -> ThemeMembership:
    return ThemeMembership(
        source_order=0,
        stock_code=code,
        stock_name=name,
        rationale="구성종목",
        source_index=None,
        content_hash="0" * 64,
        quality_status="OK" if code else "SOURCE_CODE_MISSING",
    )


def _reference(code: str | None, name: str) -> StockReference:
    return StockReference(
        source_order=0,
        name=name,
        stock_code=code,
        source_url=None,
        display_value=f"{code}-{name}" if code else name,
        quality_status="OK" if code else "SOURCE_CODE_MISSING",
    )


def _history(
    when: date | None,
    *,
    leaders: tuple[StockReference, ...] = (),
    members: tuple[StockReference, ...] = (),
) -> ThemeHistory:
    return ThemeHistory(
        source_order=0,
        source_history_id=None,
        source_history_key=f"key:{when}:{len(leaders)}:{len(members)}",
        event_date=when,
        source_date=None,
        source_created_at=None,
        source_updated_at=None,
        raw_text="수주 소식 등에 상승",
        direction="UP",
        leaders=leaders,
        member_stocks=members,
        author=None,
        chart_flag=None,
        source_fingerprint="0" * 64,
        quality_status="OK",
        content_hash="0" * 64,
    )


def _bundle(*details: _Detail) -> _Bundle:
    return _Bundle(details=details)


def _detail(
    theme_id: str,
    *,
    memberships: tuple[ThemeMembership, ...] = (),
    history: tuple[ThemeHistory, ...] = (),
) -> _Detail:
    return _Detail(
        source_theme_id=theme_id, memberships=memberships, history=history
    )


def _company(master, seed: str):  # type: ignore[no-untyped-def]
    return next(
        company for company in master.companies if company.seed_stock_code == seed
    )


def test_normalize_only_removes_spacing_corporate_form_and_case() -> None:
    assert normalize_company_name("주식회사 한화 에어로스페이스") == normalize_company_name(
        "한화에어로스페이스"
    )
    assert normalize_company_name("㈜비큐 AI") == normalize_company_name("비큐AI")
    assert normalize_company_name("동국제강") != normalize_company_name("동국홀딩스")


def test_share_class_suffix_split() -> None:
    assert split_share_class("삼성전자우") == ("삼성전자", True)
    assert split_share_class("현대차2우B") == ("현대차", True)
    assert split_share_class("연우") == ("연우", False)
    assert split_share_class("한화에어로스페이스") == ("한화에어로스페이스", False)


def _rename_bundle() -> _Bundle:
    """001230이 동국제강에서 동국홀딩스로 바뀐 실제 관측 형태."""

    return _bundle(
        _detail(
            "1",
            memberships=(_membership("001230", "동국홀딩스"),),
            history=(
                _history(
                    date(2013, 5, 2), leaders=(_reference("001230", "동국제강"),)
                ),
                _history(
                    date(2023, 5, 18), members=(_reference("001230", "동국제강"),)
                ),
                _history(
                    date(2024, 1, 29), leaders=(_reference("001230", "동국홀딩스"),)
                ),
                _history(
                    date(2026, 4, 8), leaders=(_reference("001230", "동국홀딩스"),)
                ),
            ),
        )
    )


def test_current_and_past_names_and_code_resolve_to_one_company() -> None:
    master = build_company_master(_rename_bundle())

    assert master.master_version == COMPANY_MASTER_VERSION
    assert len(master.companies) == 1
    company = master.companies[0]
    assert company.canonical_name == "동국홀딩스"
    assert company.name_basis == "CURRENT_MEMBERSHIP"
    assert company.dart_corp_code is None
    assert {alias.alias: alias.alias_type for alias in company.aliases} == {
        "동국제강": "PAST_NAME",
        "동국홀딩스": "CURRENT_NAME",
    }

    for query, as_of in (
        ("001230", None),
        ("동국홀딩스", None),
        ("동국제강", date(2013, 5, 2)),
        ("주식회사 동국 제강", date(2020, 1, 2)),
    ):
        resolution = resolve_company(master, query, as_of=as_of)
        assert resolution.status == "RESOLVED", (query, as_of)
        assert resolution.seed_stock_code == "001230"


def test_past_alias_window_is_closed_and_current_name_stays_open() -> None:
    company = build_company_master(_rename_bundle()).companies[0]
    past = next(alias for alias in company.aliases if alias.alias == "동국제강")
    current = next(alias for alias in company.aliases if alias.alias == "동국홀딩스")

    assert (past.valid_from, past.valid_to) == (date(2013, 5, 2), date(2023, 5, 18))
    assert past.mention_count == 2
    assert (current.valid_from, current.valid_to) == (date(2024, 1, 29), None)
    assert past.source_authority == "HISTORICAL_REFERENCE"
    assert current.source_authority == "CURRENT_MEMBERSHIP"


def test_rename_becomes_a_revision_with_the_observed_boundary() -> None:
    company = build_company_master(_rename_bundle()).companies[0]

    assert [
        (revision.change_type, revision.effective_on, revision.new_value)
        for revision in company.revisions
    ] == [
        ("CREATED", date(2013, 5, 2), "동국제강"),
        ("NAME_CHANGED", date(2024, 1, 29), "동국홀딩스"),
    ]
    assert company.revisions[1].previous_value == "동국제강"
    assert len({revision.content_hash("001230") for revision in company.revisions}) == 2


def _shared_name_bundle() -> _Bundle:
    """같은 이름 OCI가 지주 전환으로 두 종목코드에 걸친 실제 형태."""

    return _bundle(
        _detail(
            "1",
            memberships=(
                _membership("010060", "OCI홀딩스"),
                _membership("456040", "OCI"),
            ),
            history=(
                _history(date(2008, 2, 26), leaders=(_reference("010060", "OCI"),)),
                _history(date(2023, 4, 24), leaders=(_reference("010060", "OCI"),)),
                _history(
                    date(2024, 1, 30), leaders=(_reference("010060", "OCI홀딩스"),)
                ),
                _history(date(2023, 6, 14), leaders=(_reference("456040", "OCI"),)),
                _history(date(2026, 8, 13), leaders=(_reference("456040", "OCI"),)),
            ),
        )
    )


def test_same_name_on_two_codes_resolves_by_the_asked_time() -> None:
    master = build_company_master(_shared_name_bundle())

    assert resolve_company(master, "OCI", as_of=date(2015, 3, 2)).seed_stock_code == (
        "010060"
    )
    assert resolve_company(master, "OCI", as_of=date(2025, 3, 2)).seed_stock_code == (
        "456040"
    )


def test_same_name_without_a_time_is_ambiguous_not_a_guess() -> None:
    master = build_company_master(_shared_name_bundle())
    resolution = resolve_company(master, "OCI")

    assert resolution.status == "AMBIGUOUS"
    assert resolution.seed_stock_code is None
    assert [candidate.seed_stock_code for candidate in resolution.candidates] == [
        "010060",
        "456040",
    ]


def test_time_outside_every_alias_window_does_not_link() -> None:
    master = build_company_master(_shared_name_bundle())
    resolution = resolve_company(master, "OCI", as_of=date(2023, 5, 15))

    assert resolution.status == "OUT_OF_VALIDITY"
    assert resolution.seed_stock_code is None
    assert [candidate.matched_alias for candidate in resolution.candidates] == [
        "OCI",
        "OCI",
    ]
    assert resolve_company(master, "없는회사", as_of=None).status == "UNKNOWN"
    assert resolve_company(master, "999999", as_of=None).status == "UNKNOWN"


def test_preferred_share_joins_the_common_company_when_name_and_code_agree() -> None:
    master = build_company_master(
        _bundle(
            _detail(
                "1",
                memberships=(
                    _membership("005930", "삼성전자"),
                    _membership("005935", "삼성전자우"),
                    _membership("115960", "연우"),
                ),
                history=(
                    _history(
                        date(2026, 6, 1), leaders=(_reference("005935", "삼성전자우"),)
                    ),
                ),
            )
        )
    )

    assert [company.seed_stock_code for company in master.companies] == [
        "005930",
        "115960",
    ]
    samsung = _company(master, "005930")
    assert [
        (instrument.stock_code, instrument.share_class, instrument.link_basis)
        for instrument in samsung.instruments
    ] == [
        ("005930", "COMMON", "STOCK_CODE"),
        ("005935", "PREFERRED", "SHARE_CLASS_NAME_AND_CODE"),
    ]
    assert samsung.canonical_name == "삼성전자"
    assert next(
        alias.alias_type for alias in samsung.aliases if alias.alias == "삼성전자우"
    ) == "SHARE_CLASS_NAME"
    assert resolve_company(master, "005935").seed_stock_code == "005930"
    assert resolve_company(master, "삼성전자우").seed_stock_code == "005930"
    # 언제부터 그 회사의 종목이었는지는 원천이 없다. 관측 시작을 넣지 않는다.
    assert all(
        (instrument.valid_from, instrument.valid_to) == (None, None)
        for instrument in samsung.instruments
    )
    assert samsung.revisions[-1] == CompanyRevisionDraft(
        change_type="INSTRUMENT_LINKED",
        effective_on=date(2026, 6, 1),
        previous_value=None,
        new_value="005935",
    )
    # 이름만 우선주형인 보통주는 합치지 않는다.
    assert _company(master, "115960").instruments[0].share_class == "COMMON"


def test_preferred_suffix_alone_never_merges_two_companies() -> None:
    master = build_company_master(
        _bundle(
            _detail(
                "1",
                memberships=(
                    _membership("000660", "SK하이닉스"),
                    _membership("000665", "가나우"),
                ),
            )
        )
    )

    assert [company.seed_stock_code for company in master.companies] == [
        "000660",
        "000665",
    ]
    assert _company(master, "000665").instruments[0].share_class == "UNKNOWN"


def test_unresolved_reference_becomes_a_review_row_not_a_company() -> None:
    master = build_company_master(
        _bundle(
            _detail(
                "1",
                history=(
                    _history(
                        date(2026, 5, 4),
                        leaders=(_reference(None, "0015G0-그린광학"),),
                    ),
                    _history(
                        date(2026, 6, 4),
                        leaders=(_reference(None, "0015G0-그린광학"),),
                        members=(_reference(None, "087730-"),),
                    ),
                ),
            )
        )
    )

    assert master.companies == ()
    assert [
        (review.source_kind, review.source_name, review.mention_count)
        for review in master.unresolved
    ] == [
        ("HISTORY_LEADER", "0015G0-그린광학", 2),
        ("HISTORY_MEMBER", "087730-", 1),
    ]
    leader = master.unresolved[0]
    assert leader.reason == "SOURCE_CODE_MISSING"
    assert (leader.first_event_date, leader.last_event_date) == (
        date(2026, 5, 4),
        date(2026, 6, 4),
    )


def test_dart_corp_code_is_attached_only_when_the_index_has_it() -> None:
    bundle = _rename_bundle()

    assert build_company_master(bundle).companies[0].dart_corp_code is None
    assert (
        build_company_master(bundle, corp_codes={"001230": "00164645"})
        .companies[0]
        .dart_corp_code
        == "00164645"
    )
    assert (
        build_company_master(bundle, corp_codes={"001230": "164645"})
        .companies[0]
        .dart_corp_code
        is None
    )


def test_build_is_deterministic() -> None:
    assert build_company_master(_shared_name_bundle()) == build_company_master(
        _shared_name_bundle()
    )
