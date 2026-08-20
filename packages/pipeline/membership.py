"""인포스탁 수집본의 테마·구성종목을 파이프라인 멤버십으로 바꾼다.

theme detail의 **현재 관련주**만 사용한다. history에 남은 당시 주도주는 현재
관련주와 같은 데이터가 아니므로(PRD "현재 관련주와 과거 당시 주도주를 같은
데이터로 취급하지 않는다") 여기서 섞지 않는다. 그래서 현재 구성종목의 역할은
전부 CORE다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from packages.domain import (
    MembershipRole,
    StockReference,
    ThemeMember,
    ThemeMembershipSnapshot,
)
from packages.infostock import load_existing_collection
from packages.infostock.models import ThemeDetail
from packages.realtime import VersionedThemeCatalog


@dataclass(frozen=True, slots=True)
class ThemeUniverse:
    """파이프라인 한 대를 조립하는 데 필요한 테마 명단·표시 이름·기준정보."""

    version: str
    snapshots: tuple[ThemeMembershipSnapshot, ...]
    theme_names: dict[str, str]
    stock_names: dict[str, str]
    references: tuple[StockReference, ...] = field(default=())
    # 과거 사건 매칭이 같은 수집본의 history를 다시 읽지 않도록 들고 있는다.
    details: tuple[ThemeDetail, ...] = field(default=())

    def catalog(self) -> VersionedThemeCatalog:
        return VersionedThemeCatalog(self.snapshots)


def build_theme_universe(
    details: Iterable[ThemeDetail],
    *,
    version: str,
    effective_from: date,
    known_at: datetime,
) -> ThemeUniverse:
    """인포스탁 theme detail을 point-in-time membership snapshot으로 바꾼다.

    stock code가 없거나 6자리 규격을 벗어난 관련주는 시세와 연결할 수 없으므로
    제외한다. 원본 loader가 이미 `quality_status`로 표시해 둔 것을 그대로 쓴다.
    """

    ordered = tuple(details)
    snapshots: list[ThemeMembershipSnapshot] = []
    theme_names: dict[str, str] = {}
    stock_names: dict[str, str] = {}
    for detail in ordered:
        theme_id = f"thm_{detail.source_theme_id}"
        theme_names[theme_id] = detail.theme_name
        members: list[ThemeMember] = []
        for membership in detail.memberships:
            if membership.quality_status != "OK" or membership.stock_code is None:
                continue
            stock_id = f"KRX:{membership.stock_code}"
            members.append(ThemeMember(stock_id, MembershipRole.CORE))
            stock_names.setdefault(stock_id, membership.stock_name)
        snapshots.append(
            ThemeMembershipSnapshot(
                theme_id=theme_id,
                version=version,
                effective_from=effective_from,
                known_at=known_at,
                members=tuple(members),
            )
        )
    return ThemeUniverse(
        version=version,
        snapshots=tuple(snapshots),
        theme_names=theme_names,
        stock_names=stock_names,
        details=ordered,
    )


def load_theme_universe(
    directory: Path,
    *,
    effective_from: date,
    known_at: datetime,
) -> ThemeUniverse:
    """수집본 디렉터리를 검증해 읽고 그 명단으로 테마 우주를 만든다."""

    bundle = load_existing_collection(directory)
    return build_theme_universe(
        bundle.details,
        version=f"membership-infostock-{bundle.dataset_hash[:16]}",
        effective_from=effective_from,
        known_at=known_at,
    )
