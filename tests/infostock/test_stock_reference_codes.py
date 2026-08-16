"""원천 종목 참조의 코드 해석 (회사 온톨로지 단계 2에서 드러난 결함)."""

from __future__ import annotations

from packages.infostock.existing_collection import _reference


def _parse(name: str, stock_code: object) -> tuple[str | None, str, str, str]:
    reference = _reference(
        {"sourceOrder": 0, "name": name, "stockCode": stock_code},
        path="$.leaders[0]",
        default_order=0,
    )
    return (
        reference.stock_code,
        reference.name,
        reference.display_value,
        reference.quality_status,
    )


def test_source_code_is_used_as_is() -> None:
    assert _parse("한화에어로스페이스", "012450") == (
        "012450",
        "한화에어로스페이스",
        "012450-한화에어로스페이스",
        "OK",
    )


def test_code_hidden_in_the_display_string_is_recovered() -> None:
    # 원천이 코드 칸을 비우고 표기에만 남긴 형태. 이름 문자열은 그대로 둔다.
    assert _parse("087730-", None) == ("087730", "087730-", "087730-", "OK")
    assert _parse("035420-네이버", None) == (
        "035420",
        "035420-네이버",
        "035420-네이버",
        "OK",
    )


def test_unlisted_display_code_stays_unresolved() -> None:
    # "0015G0"은 상장 종목코드가 아니다. 임의로 종목에 붙이지 않는다.
    assert _parse("0015G0-그린광학", None) == (
        None,
        "0015G0-그린광학",
        "0015G0-그린광학",
        "SOURCE_CODE_MISSING",
    )
    assert _parse("이름만 있는 종목", None)[3] == "SOURCE_CODE_MISSING"


def test_invalid_source_code_is_reported_not_replaced() -> None:
    assert _parse("012450-한화에어로스페이스", "12450") == (
        "12450",
        "012450-한화에어로스페이스",
        "12450-012450-한화에어로스페이스",
        "CODE_INVALID",
    )
