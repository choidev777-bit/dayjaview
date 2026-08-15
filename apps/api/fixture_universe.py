"""로컬 fixture 모드의 결정적 시장 우주.

키움 synthetic fixture(tests/market-gateway/fixtures/kiwoom-market-v1.json)에
등장하는 3개 종목을 실제 계산이 가능한 테마·기준정보로 묶는다. 기준 전일
종가는 fixture 원천의 등락률(flu_rt)과 일치하도록 정했다. 이 데이터는
fixture 모드 전용이며 live 수집·배포 경로에는 사용되지 않는다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from packages.domain import (
    MembershipRole,
    StockReference,
    ThemeMember,
    ThemeMembershipSnapshot,
)
from packages.pipeline import ThemeUniverse

FIXTURE_MARKET_DATE = date(2026, 8, 14)
FIXTURE_MEMBERSHIP_VERSION = "membership-fixture-2026-08-14T00:00:00Z"
_KNOWN_AT = datetime(2026, 8, 13, 23, 0, tzinfo=UTC)

FIXTURE_THEME_NAMES: dict[str, str] = {
    "thm_fixture_tech": "픽스처 대형 기술주",
    "thm_fixture_semi": "픽스처 반도체",
}

FIXTURE_STOCK_NAMES: dict[str, str] = {
    "KRX:005930": "삼성전자",
    "KRX:000660": "SK하이닉스",
    "KRX:035420": "NAVER",
}


def fixture_membership_snapshots() -> tuple[ThemeMembershipSnapshot, ...]:
    return (
        ThemeMembershipSnapshot(
            theme_id="thm_fixture_tech",
            version=FIXTURE_MEMBERSHIP_VERSION,
            effective_from=FIXTURE_MARKET_DATE,
            known_at=_KNOWN_AT,
            members=(
                ThemeMember("KRX:005930", MembershipRole.CORE),
                ThemeMember("KRX:000660", MembershipRole.CORE),
                ThemeMember("KRX:035420", MembershipRole.CORE),
            ),
        ),
        # 2종목뿐이라 최소 관측(3)을 못 채우는 테마. Coverage INSUFFICIENT로
        # rankings에서 제외되는 경로를 fixture에서도 그대로 노출한다.
        ThemeMembershipSnapshot(
            theme_id="thm_fixture_semi",
            version=FIXTURE_MEMBERSHIP_VERSION,
            effective_from=FIXTURE_MARKET_DATE,
            known_at=_KNOWN_AT,
            members=(
                ThemeMember("KRX:005930", MembershipRole.CORE),
                ThemeMember("KRX:000660", MembershipRole.CORE),
            ),
        ),
    )


def fixture_references() -> tuple[StockReference, ...]:
    def reference(
        stock_id: str,
        previous_close: str,
        listed_shares: int,
        free_float_ratio: str,
    ) -> StockReference:
        return StockReference(
            stock_id=stock_id,
            effective_for=FIXTURE_MARKET_DATE,
            known_at=_KNOWN_AT,
            previous_adjusted_close=Decimal(previous_close),
            listed_shares=listed_shares,
            free_float_ratio=Decimal(free_float_ratio),
            free_float_validated=True,
            version="reference-fixture-2026-08-14",
        )

    return (
        # 전일 종가는 kiwoom fixture의 flu_rt와 일치: 73200/72200-1=+1.385%
        reference("KRX:005930", "72200", 5_919_637_922, "0.75"),
        # 194000/189500-1=+2.375%
        reference("KRX:000660", "189500", 728_002_365, "0.73"),
        # 207000/203500-1=+1.720%
        reference("KRX:035420", "203500", 164_000_000, "0.60"),
    )


def fixture_universe() -> ThemeUniverse:
    return ThemeUniverse(
        version=FIXTURE_MEMBERSHIP_VERSION,
        snapshots=fixture_membership_snapshots(),
        theme_names=dict(FIXTURE_THEME_NAMES),
        stock_names=dict(FIXTURE_STOCK_NAMES),
        references=fixture_references(),
    )
