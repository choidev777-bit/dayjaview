from __future__ import annotations

from datetime import UTC, date, datetime

from packages.domain import (
    MembershipRole,
    ThemeMember,
    ThemeMembershipSnapshot,
    select_membership_snapshot,
)


def snapshot(
    version: str,
    effective_from: date,
    known_at: datetime,
    stock_ids: tuple[str, ...],
) -> ThemeMembershipSnapshot:
    return ThemeMembershipSnapshot(
        theme_id="thm_test",
        version=version,
        effective_from=effective_from,
        known_at=known_at,
        members=tuple(
            ThemeMember(stock_id=stock_id, role=MembershipRole.CORE)
            for stock_id in stock_ids
        ),
    )


def test_current_membership_is_not_applied_to_past_date() -> None:
    old = snapshot(
        "membership-v1",
        date(2026, 7, 1),
        datetime(2026, 7, 1, tzinfo=UTC),
        ("A",),
    )
    current = snapshot(
        "membership-v2",
        date(2026, 8, 1),
        datetime(2026, 8, 1, tzinfo=UTC),
        ("A", "B"),
    )

    selected = select_membership_snapshot(
        (current, old),
        theme_id="thm_test",
        market_date=date(2026, 7, 15),
        decision_at=datetime(2026, 7, 15, 1, tzinfo=UTC),
    )

    assert selected == old
    assert selected is not None
    assert not selected.contains("B")


def test_future_known_revision_is_not_available_at_decision_time() -> None:
    original = snapshot(
        "membership-v1",
        date(2026, 8, 1),
        datetime(2026, 8, 1, tzinfo=UTC),
        ("A",),
    )
    late_revision = snapshot(
        "membership-v2",
        date(2026, 8, 1),
        datetime(2026, 8, 20, tzinfo=UTC),
        ("A", "B"),
    )

    selected = select_membership_snapshot(
        (late_revision, original),
        theme_id="thm_test",
        market_date=date(2026, 8, 14),
        decision_at=datetime(2026, 8, 14, 1, tzinfo=UTC),
    )

    assert selected == original
