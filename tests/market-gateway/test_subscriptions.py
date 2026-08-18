from __future__ import annotations

from datetime import UTC, datetime, timedelta

from packages.adapters.kiwoom import (
    DemandPriority,
    SubscriptionDemand,
    SubscriptionManager,
)

NOW = datetime(2026, 8, 14, tzinfo=UTC)


def _stock(index: int) -> str:
    return f"KRX:{index:06d}"


def _demand(
    index: int,
    priority: DemandPriority = DemandPriority.SINGLE_SIGNAL_CANDIDATE,
    *,
    at: datetime = NOW,
    signal_count: int = 1,
) -> SubscriptionDemand:
    return SubscriptionDemand(
        stock_id=_stock(index),
        priority=priority,
        observed_at=at,
        signal_count=signal_count,
    )


def test_normal_admission_targets_180_and_never_uses_the_20_slot_headroom() -> None:
    manager = SubscriptionManager()
    demands = [_demand(index) for index in range(250)]

    plan = manager.reconcile(reversed(demands), now=NOW)

    assert len(plan.subscriptions) == 180
    assert plan.hard_limit == 200
    assert plan.subscriptions == tuple(_stock(index) for index in range(180))
    assert plan.snapshot_supplement == tuple(_stock(index) for index in range(180, 250))


def test_same_stock_in_multiple_themes_consumes_one_slot_and_best_priority_wins() -> None:
    manager = SubscriptionManager()
    demands = [
        SubscriptionDemand(
            stock_id="KRX:005930",
            priority=DemandPriority.ACTIVE_RELATED,
            observed_at=NOW,
            theme_ids=("theme-a",),
        ),
        SubscriptionDemand(
            stock_id="KRX:005930",
            priority=DemandPriority.ACTIVE_LEADER,
            observed_at=NOW + timedelta(milliseconds=1),
            signal_count=2,
            theme_ids=("theme-b",),
        ),
    ]

    plan = manager.reconcile(demands, now=NOW + timedelta(seconds=1))

    assert plan.subscriptions == ("KRX:005930",)


def test_high_priority_burst_uses_headroom_then_cools_back_to_180() -> None:
    manager = SubscriptionManager()
    initial = [_demand(index) for index in range(100000, 100180)]
    first = manager.reconcile(initial, now=NOW)
    leaders = [
        _demand(
            index,
            DemandPriority.ACTIVE_LEADER,
            at=NOW + timedelta(seconds=2),
        )
        for index in range(200000, 200021)
    ]

    burst = manager.reconcile(
        [*initial, *leaders],
        now=NOW + timedelta(seconds=2),
    )
    settled = manager.reconcile(
        [*initial, *leaders],
        now=NOW + timedelta(seconds=63),
    )

    assert len(first.subscriptions) == 180
    assert len(burst.subscriptions) == 200
    assert len(burst.retained_for_cooldown) == 20
    assert len(burst.added) == 21
    assert len(burst.removed) == 1
    assert {demand.stock_id for demand in leaders} <= set(burst.subscriptions)
    assert len(settled.subscriptions) == 180
    assert settled.retained_for_cooldown == ()


def test_hard_cap_evicts_the_deterministic_lowest_ranked_cooling_stock() -> None:
    manager = SubscriptionManager()
    initial = [_demand(index) for index in range(100000, 100180)]
    manager.reconcile(initial, now=NOW)
    leaders = [
        _demand(index, DemandPriority.ACTIVE_LEADER)
        for index in range(200000, 200021)
    ]

    plan = manager.reconcile([*initial, *leaders], now=NOW + timedelta(seconds=2))

    assert plan.removed == ("KRX:100179",)
    assert len(plan.subscriptions) == plan.hard_limit


def test_changes_inside_one_second_are_coalesced_without_slot_churn() -> None:
    manager = SubscriptionManager()
    first = manager.reconcile([_demand(1)], now=NOW)
    coalesced = manager.reconcile(
        [_demand(2, DemandPriority.ACTIVE_LEADER)],
        now=NOW + timedelta(milliseconds=500),
    )
    flushed = manager.flush(now=NOW + timedelta(seconds=1))

    assert first.subscriptions == ("KRX:000001",)
    assert coalesced.coalesced is True
    assert coalesced.added == ()
    assert coalesced.removed == ()
    assert coalesced.subscriptions == first.subscriptions
    assert flushed.coalesced is False
    assert set(flushed.subscriptions) == {"KRX:000001", "KRX:000002"}
    assert flushed.retained_for_cooldown == ("KRX:000001",)


def test_condition_exit_is_retained_for_60_seconds_then_evicted() -> None:
    manager = SubscriptionManager()
    manager.reconcile([_demand(1)], now=NOW)

    cooling = manager.reconcile([], now=NOW + timedelta(seconds=2))
    expired = manager.reconcile([], now=NOW + timedelta(seconds=63))

    assert cooling.subscriptions == ("KRX:000001",)
    assert cooling.retained_for_cooldown == ("KRX:000001",)
    assert expired.subscriptions == ()
    assert expired.removed == ("KRX:000001",)


def test_input_order_does_not_change_admission_or_eviction() -> None:
    demands = [
        _demand(
            index,
            DemandPriority.MULTI_SIGNAL_CANDIDATE,
            signal_count=(index % 3) + 1,
        )
        for index in range(220)
    ]
    forward = SubscriptionManager().reconcile(demands, now=NOW)
    reverse = SubscriptionManager().reconcile(reversed(demands), now=NOW)

    assert forward.subscriptions == reverse.subscriptions
    assert forward.snapshot_supplement == reverse.snapshot_supplement


def test_alphanumeric_krx_short_code_is_accepted() -> None:
    """KRX 단축코드에는 영문이 섞인다(신주인수권·제3자배정 등).

    인포스탁 명단 6,629건 중 53종목이 `0001A0` 형태다. 숫자만 받으면
    LiveMarketRunner가 구독 요구를 만드는 첫 tick에서 ValueError로 죽는다.
    """

    demand = SubscriptionDemand(
        stock_id="KRX:0001A0",
        priority=DemandPriority.ACTIVE_RELATED,
        observed_at=NOW,
    )

    assert demand.stock_id == "KRX:0001A0"

    for rejected in ("KRX:00001", "KRX:0001a0", "KRX:0001A00", "KOSPI:000100"):
        try:
            SubscriptionDemand(
                stock_id=rejected,
                priority=DemandPriority.ACTIVE_RELATED,
                observed_at=NOW,
            )
        except ValueError:
            continue
        raise AssertionError(f"{rejected}는 거부돼야 한다")
