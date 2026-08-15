"""live 서빙의 거래일 세션 조립(A-8 배선).

키움 실접속은 하지 않는다. LiveKiwoomAdapter는 생성 시 네트워크를 열지
않으므로 조립과 전환만 검증하고 poll은 부르지 않는다.
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.api.config import ApiSettings
from apps.api.realtime import RealtimeSnapshotHub
from apps.api.serve import (
    LivePipelineHandle,
    LiveSessionController,
    _report_period,
)
from apps.api.snapshot_product import SnapshotProductReadRepository
from packages.domain import DataStatus
from packages.pipeline import MarketPublishLoop

ENV = {
    "KIWOOM_MODE": "demo",
    "KIWOOM_APP_KEY": "test-key",
    "KIWOOM_APP_SECRET": "test-secret",
    "THEME_UNIVERSE_MODE": "fixture",
}


def _controller(handle: LivePipelineHandle, **overrides: str) -> LiveSessionController:
    return LiveSessionController(
        settings=ApiSettings(app_base_url="http://localhost:5173"),
        hub=RealtimeSnapshotHub(),
        handle=handle,
        environment={**ENV, **overrides},
    )


def test_report_period_picks_the_latest_filed_combination() -> None:
    assert _report_period(date(2026, 8, 15)) == (2026, "11012")
    assert _report_period(date(2026, 12, 1)) == (2026, "11012")
    assert _report_period(date(2026, 8, 14)) == (2025, "11011")
    assert _report_period(date(2026, 4, 1)) == (2025, "11011")
    assert _report_period(date(2026, 2, 1)) == (2025, "11012")


def test_handle_answers_empty_until_a_session_exists() -> None:
    handle = LivePipelineHandle()
    repository = SnapshotProductReadRepository(handle)

    assert handle.latest_rankings is None
    assert handle.last_data_status is DataStatus.PREOPEN
    assert handle.theme_detail("evt_any") is None
    assert repository.rankings(None) is None
    assert repository.evidence("evt_any", None) is None


def test_build_session_assembles_the_day_and_switches_the_handle() -> None:
    handle = LivePipelineHandle()
    controller = _controller(handle)
    try:
        session = controller.build_session(date(2026, 8, 18))

        assert isinstance(session, MarketPublishLoop)
        assert handle.latest_rankings is None  # 아직 publish 전
        # handle이 그날 파이프라인을 본다: publish하면 rankings가 나온다.
        view = session.tick()
        assert handle.latest_rankings is view.rankings
    finally:
        controller.close()


def test_rolling_to_a_new_day_replaces_the_session() -> None:
    handle = LivePipelineHandle()
    controller = _controller(handle)
    try:
        first = controller.build_session(date(2026, 8, 18))
        assert first is not None
        first.tick()
        first_snapshot = handle.latest_rankings

        second = controller.build_session(date(2026, 8, 19))
        assert second is not None
        # 새 세션으로 갈아끼우면 이전 발행분은 더 보이지 않는다.
        assert handle.latest_rankings is None
        second.tick()
        assert handle.latest_rankings is not first_snapshot
        assert handle.latest_rankings is not None
        assert handle.latest_rankings.market_date == date(2026, 8, 19)
    finally:
        controller.close()


def test_failed_reference_preparation_leaves_the_day_dark() -> None:
    """기준정보 준비가 실패한 날은 계산을 시작하지 않는다 (PD-001 10항)."""

    handle = LivePipelineHandle()
    controller = _controller(
        handle,
        THEME_UNIVERSE_MODE="infostock",
        INFOSTOCK_IMPORT_DIR="does-not-exist",
    )
    try:
        first = controller.build_session(date(2026, 8, 18))

        assert first is None
        assert handle.latest_rankings is None
        assert handle.last_data_status is DataStatus.PREOPEN
    finally:
        controller.close()


def test_missing_kiwoom_configuration_refuses_to_start() -> None:
    with pytest.raises(ValueError, match="KIWOOM_MODE"):
        _controller(LivePipelineHandle(), KIWOOM_MODE="")
    with pytest.raises(ValueError, match="KIWOOM_APP_KEY"):
        _controller(LivePipelineHandle(), KIWOOM_APP_KEY="")
