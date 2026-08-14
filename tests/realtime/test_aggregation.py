from __future__ import annotations

from datetime import timedelta

import pytest

from packages.domain import CoverageStatus, MembershipRole
from packages.realtime import (
    DIRTY_THEME_CHECKPOINT_VERSION,
    DirtyThemeAggregator,
    HotStateStore,
    VersionedThemeCatalog,
)

from ._factories import (
    MARKET_DATE,
    PREVIOUS_DATE,
    START,
    membership,
    realtime_update,
    reference,
)


def _complete_hot_state() -> HotStateStore:
    hot = HotStateStore()
    for sequence, (stock_id, price) in enumerate(
        (("stk_1", "110"), ("stk_2", "120"), ("stk_3", "90")),
        start=1,
    ):
        hot.apply(
            realtime_update(
                stock_id,
                sequence=sequence,
                occurred_seconds=sequence,
                received_seconds=sequence,
                price=price,
            )
        )
    return hot


def test_dirty_aggregation_uses_s1_calculation_and_only_affected_theme() -> None:
    first = membership(theme_id="thm_a")
    second = membership(
        theme_id="thm_b",
        version="membership-b-v1",
        members=(
            ("stk_3", MembershipRole.CORE),
            ("stk_4", MembershipRole.CORE),
            ("stk_5", MembershipRole.RELATED),
        ),
    )
    aggregator = DirtyThemeAggregator(
        catalog=VersionedThemeCatalog((first, second)),
        references=tuple(reference(f"stk_{index}") for index in range(1, 6)),
    )
    hot = _complete_hot_state()

    assert aggregator.mark_stock(
        stock_id="stk_1",
        market_date=MARKET_DATE,
        decision_at=START + timedelta(seconds=10),
    ) == ("thm_a",)
    updates = aggregator.drain(hot)

    assert len(updates) == 1
    update = updates[0]
    assert update.theme_id == "thm_a"
    assert update.metrics.calculation_version == "theme-metrics-2026.08.1"
    assert update.metrics.membership_version == "membership-a-v1"
    assert update.metrics.coverage.status is CoverageStatus.SUFFICIENT
    assert update.metrics.weighted_return is not None
    assert update.metrics.advancing_count == 2
    assert update.metrics.valid_count == 3
    assert aggregator.pending() == ()


def test_one_stock_marks_every_currently_linked_theme_in_stable_order() -> None:
    catalog = VersionedThemeCatalog(
        (
            membership(theme_id="thm_z"),
            membership(theme_id="thm_a", version="membership-a2"),
        )
    )
    aggregator = DirtyThemeAggregator(
        catalog=catalog,
        references=(reference("stk_1"), reference("stk_2"), reference("stk_3")),
    )

    affected = aggregator.mark_stock(
        stock_id="stk_1",
        market_date=MARKET_DATE,
        decision_at=START,
    )

    assert affected == ("thm_a", "thm_z")
    assert tuple(item.theme_id for item in aggregator.pending()) == (
        "thm_a",
        "thm_z",
    )


def test_current_membership_is_not_retroactively_applied_to_prior_market_date() -> None:
    old = membership(
        version="membership-old",
        market_date=PREVIOUS_DATE,
        known_at=START - timedelta(days=2),
        members=(
            ("stk_old_1", MembershipRole.CORE),
            ("stk_old_2", MembershipRole.CORE),
            ("stk_old_3", MembershipRole.RELATED),
        ),
    )
    current = membership(
        version="membership-current",
        market_date=MARKET_DATE,
        known_at=START,
        members=(
            ("stk_new_1", MembershipRole.CORE),
            ("stk_new_2", MembershipRole.CORE),
            ("stk_new_3", MembershipRole.RELATED),
        ),
    )
    catalog = VersionedThemeCatalog((old, current))

    assert (
        catalog.affected_themes(
            stock_id="stk_new_1",
            market_date=PREVIOUS_DATE,
            decision_at=START + timedelta(hours=1),
        )
        == ()
    )
    assert catalog.affected_themes(
        stock_id="stk_old_1",
        market_date=PREVIOUS_DATE,
        decision_at=START + timedelta(hours=1),
    ) == ("thm_a",)
    assert (
        catalog.affected_themes(
            stock_id="stk_old_1",
            market_date=MARKET_DATE,
            decision_at=START + timedelta(hours=1),
        )
        == ()
    )


def test_insufficient_coverage_keeps_unavailable_metric_null_not_zero() -> None:
    membership_snapshot = membership()
    aggregator = DirtyThemeAggregator(
        catalog=VersionedThemeCatalog((membership_snapshot,)),
        references=(reference("stk_1"), reference("stk_2"), reference("stk_3")),
    )
    hot = HotStateStore()
    hot.apply(realtime_update("stk_1", price="110"))
    aggregator.mark_stock(
        stock_id="stk_1",
        market_date=MARKET_DATE,
        decision_at=START + timedelta(seconds=1),
    )

    metrics = aggregator.drain(hot)[0].metrics

    assert metrics.coverage.status is CoverageStatus.INSUFFICIENT
    assert metrics.weighted_return is None
    assert metrics.advancing_count == 1
    assert metrics.valid_count == 1
    assert "INSUFFICIENT_COVERAGE" in metrics.quality_flags
    assert metrics.coverage.core.observed_count == 1
    assert metrics.coverage.core.total_count == 2


def test_failed_dirty_batch_is_not_lost_and_can_be_retried() -> None:
    membership_snapshot = membership()
    duplicate_time = START - timedelta(days=1)
    aggregator = DirtyThemeAggregator(
        catalog=VersionedThemeCatalog((membership_snapshot,)),
        references=(
            reference("stk_1", version="ref-a", known_at=duplicate_time),
            reference("stk_1", version="ref-b", known_at=duplicate_time),
            reference("stk_2"),
            reference("stk_3"),
        ),
    )
    hot = _complete_hot_state()
    aggregator.mark_stock(
        stock_id="stk_1",
        market_date=MARKET_DATE,
        decision_at=START + timedelta(seconds=10),
    )

    with pytest.raises(ValueError, match="reference version"):
        aggregator.drain(hot)

    assert len(aggregator.pending()) == 1


def test_dirty_checkpoint_restores_pending_work_after_crash() -> None:
    membership_snapshot = membership()
    references = (reference("stk_1"), reference("stk_2"), reference("stk_3"))
    original = DirtyThemeAggregator(
        catalog=VersionedThemeCatalog((membership_snapshot,)),
        references=references,
    )
    original.mark_stock(
        stock_id="stk_1",
        market_date=MARKET_DATE,
        decision_at=START + timedelta(seconds=10),
    )
    checkpoint = original.checkpoint(created_at=START + timedelta(seconds=11))

    recovered = DirtyThemeAggregator(
        catalog=VersionedThemeCatalog((membership_snapshot,)),
        references=references,
    )
    recovered.restore(checkpoint)
    updates = recovered.drain(_complete_hot_state())

    assert checkpoint.checkpoint_version == DIRTY_THEME_CHECKPOINT_VERSION
    assert len(updates) == 1
    assert recovered.pending() == ()


def test_unvalidated_free_float_is_coverage_failure_not_fallback_weight() -> None:
    membership_snapshot = membership()
    aggregator = DirtyThemeAggregator(
        catalog=VersionedThemeCatalog((membership_snapshot,)),
        references=(
            reference("stk_1", validated=False),
            reference("stk_2"),
            reference("stk_3"),
        ),
    )
    hot = _complete_hot_state()
    aggregator.mark_stock(
        stock_id="stk_1",
        market_date=MARKET_DATE,
        decision_at=START + timedelta(seconds=10),
    )

    metrics = aggregator.drain(hot)[0].metrics

    assert metrics.weighted_return is None
    assert metrics.coverage.status is CoverageStatus.INSUFFICIENT
    assert "FREE_FLOAT_UNAVAILABLE" in metrics.quality_flags
