"""리서치 조립의 env 배선 (E-22 개방 스위치).

`RESEARCH_SERVE_UNVERIFIED=1`은 검수 전 유형을 계산하되 humanVerified를
True로 위장하지 않아야 한다.
"""

from __future__ import annotations

from apps.api.production import _research_service
from packages.identity import Clock
from packages.ontology import QueryType


class _Connection:
    def close(self) -> None:  # pragma: no cover - 조립 검증용
        pass


class _FixedClock(Clock):
    def now(self):  # pragma: no cover - 조립 검증용
        raise NotImplementedError


def _boundary(environment: dict[str, str]):
    return _research_service(
        {"INFOSTOCK_DATABASE_URL": "postgresql://x", **environment},
        connect=lambda dsn: _Connection(),
        closers=[],
        clock=_FixedClock(),
    )


def test_serve_unverified_flag_opens_types_without_faking_verification() -> None:
    boundary = _boundary(
        {
            "RESEARCH_VERIFIED_QUERY_TYPES": "DAY_MOVERS",
            "RESEARCH_SERVE_UNVERIFIED": "1",
        }
    )
    assert boundary is not None
    availability = boundary._availability
    assert availability.serve_unverified is True
    assert availability.is_open(QueryType.THEME_FREQUENCY)
    # 검수 표시는 env 목록 그대로다 — 스위치가 검수 완료로 바꾸지 않는다.
    assert availability.human_verified == frozenset({QueryType.DAY_MOVERS})


def test_serve_unverified_defaults_off() -> None:
    boundary = _boundary({"RESEARCH_VERIFIED_QUERY_TYPES": "DAY_MOVERS"})
    assert boundary is not None
    availability = boundary._availability
    assert availability.serve_unverified is False
    assert not availability.is_open(QueryType.THEME_FREQUENCY)
    assert availability.is_open(QueryType.DAY_MOVERS)
