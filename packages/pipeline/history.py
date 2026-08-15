"""거래일별 장중 이력 축적: 분단위 거래대금·구성종목·관심 신호.

동일 시각 기준선(20거래일)과 관심 공백(60거래일)은 과거 장중 자료가 있어야
나온다. 승인된 과거 분봉이 없으므로 DAYJAVIEW가 매일 1분 단위 누적 거래대금을
직접 축적한다(realtime_theme_feature_spec.md 13.5).

거래일 하나가 디렉터리 하나다.

    <root>/<YYYY-MM-DD>/bucket-HHMM.json  그 분 경계의 종목별 누적 거래대금
    <root>/<YYYY-MM-DD>/membership.json   그날 유효했던 테마 구성종목
    <root>/<YYYY-MM-DD>/attention.json    장 마감 시 테마별 관심 신호

기준선은 과거 각 거래일에 **그날 유효했던** 구성종목으로 계산해야 하므로
(`calculate_theme_turnover`가 history date마다 membership을 다시 고른다)
구성종목도 같이 축적한다. 오늘 명단으로 과거를 계산하면 그 사이 편입·제외된
종목이 조용히 섞인다.

축적이 모자란 기간에도 죽지 않는다. 파일이 없는 날은 그냥 빠지고, 계산 쪽이
관측 수 미달을 PROVISIONAL·None으로 표시한다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from packages.calculations import AttentionDaySignal
from packages.domain import (
    MembershipRole,
    StockTradingValueObservation,
    ThemeMember,
    ThemeMembershipSnapshot,
    UnavailableReason,
)

BUCKET_PREFIX = "bucket-"
MEMBERSHIP_FILE = "membership.json"
ATTENTION_FILE = "attention.json"


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _read(path: Path) -> object | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


class IntradayHistory:
    """장중 이력을 거래일별 파일로 쌓고 다시 읽는다."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def trading_days(self) -> tuple[date, ...]:
        """축적본이 있는 거래일 오름차순."""

        if not self._root.exists():
            return ()
        days: list[date] = []
        for child in self._root.iterdir():
            if not child.is_dir():
                continue
            try:
                days.append(date.fromisoformat(child.name))
            except ValueError:
                continue
        return tuple(sorted(days))

    def _day(self, market_date: date) -> Path:
        return self._root / market_date.isoformat()

    def record_turnover(
        self,
        *,
        market_date: date,
        time_bucket: time,
        observations: Iterable[StockTradingValueObservation],
    ) -> None:
        _write(
            self._day(market_date) / f"{BUCKET_PREFIX}{time_bucket:%H%M}.json",
            [
                {
                    "stockId": observation.stock_id,
                    "observedAt": observation.observed_at.isoformat(),
                    "cumulativeTradingValue": (
                        None
                        if observation.cumulative_trading_value is None
                        else str(observation.cumulative_trading_value)
                    ),
                    "comparable": observation.comparable,
                    "fresh": observation.fresh,
                    "tradingHalted": observation.trading_halted,
                    "corporateActionUnresolved": (
                        observation.corporate_action_unresolved
                    ),
                }
                for observation in observations
            ],
        )

    def load_turnover(
        self,
        *,
        time_bucket: time,
        trading_days: Iterable[date],
    ) -> tuple[StockTradingValueObservation, ...]:
        observations: list[StockTradingValueObservation] = []
        for market_date in trading_days:
            rows = _read(
                self._day(market_date) / f"{BUCKET_PREFIX}{time_bucket:%H%M}.json"
            )
            if not isinstance(rows, list):
                continue
            for row in rows:
                observations.append(
                    StockTradingValueObservation(
                        stock_id=str(row["stockId"]),
                        market_date=market_date,
                        observed_at=datetime.fromisoformat(str(row["observedAt"])),
                        time_bucket=time_bucket,
                        cumulative_trading_value=_decimal(
                            row["cumulativeTradingValue"]
                        ),
                        comparable=bool(row["comparable"]),
                        fresh=bool(row["fresh"]),
                        trading_halted=bool(row["tradingHalted"]),
                        corporate_action_unresolved=bool(
                            row["corporateActionUnresolved"]
                        ),
                    )
                )
        return tuple(observations)

    def record_membership(
        self,
        *,
        market_date: date,
        snapshots: Iterable[ThemeMembershipSnapshot],
    ) -> None:
        _write(
            self._day(market_date) / MEMBERSHIP_FILE,
            [
                {
                    "themeId": snapshot.theme_id,
                    "version": snapshot.version,
                    "effectiveFrom": snapshot.effective_from.isoformat(),
                    "knownAt": snapshot.known_at.isoformat(),
                    "members": [
                        {"stockId": member.stock_id, "role": member.role.value}
                        for member in snapshot.members
                    ],
                }
                for snapshot in snapshots
            ],
        )

    def load_membership(
        self,
        trading_days: Iterable[date],
    ) -> tuple[ThemeMembershipSnapshot, ...]:
        snapshots: list[ThemeMembershipSnapshot] = []
        for market_date in trading_days:
            rows = _read(self._day(market_date) / MEMBERSHIP_FILE)
            if not isinstance(rows, list):
                continue
            for row in rows:
                snapshots.append(
                    ThemeMembershipSnapshot(
                        theme_id=str(row["themeId"]),
                        version=str(row["version"]),
                        effective_from=date.fromisoformat(str(row["effectiveFrom"])),
                        known_at=datetime.fromisoformat(str(row["knownAt"])),
                        members=tuple(
                            ThemeMember(
                                stock_id=str(member["stockId"]),
                                role=MembershipRole(str(member["role"])),
                            )
                            for member in row["members"]
                        ),
                    )
                )
        return tuple(snapshots)

    def record_attention(
        self,
        *,
        market_date: date,
        signals: Mapping[str, AttentionDaySignal],
    ) -> None:
        _write(
            self._day(market_date) / ATTENTION_FILE,
            {
                theme_id: {
                    "isAttention": signal.is_attention,
                    "membershipVersion": signal.membership_version,
                    "calculationVersion": signal.calculation_version,
                    "baselineVersion": signal.baseline_version,
                    "attentionPolicyVersion": signal.attention_policy_version,
                    "turnoverMultiple": (
                        None
                        if signal.turnover_multiple is None
                        else str(signal.turnover_multiple)
                    ),
                    "highInterestCount": signal.high_interest_count,
                    "validCount": signal.valid_count,
                    "highInterestRatio": (
                        None
                        if signal.high_interest_ratio is None
                        else str(signal.high_interest_ratio)
                    ),
                    "weightedReturn": (
                        None
                        if signal.weighted_return is None
                        else str(signal.weighted_return)
                    ),
                    "unavailableReason": (
                        None
                        if signal.unavailable_reason is None
                        else signal.unavailable_reason.value
                    ),
                }
                for theme_id, signal in signals.items()
            },
        )

    def load_attention(
        self,
        trading_days: Iterable[date],
    ) -> dict[str, tuple[AttentionDaySignal, ...]]:
        """테마별 일자 오름차순 관심 신호."""

        by_theme: dict[str, list[AttentionDaySignal]] = {}
        for market_date in trading_days:
            rows = _read(self._day(market_date) / ATTENTION_FILE)
            if not isinstance(rows, dict):
                continue
            for theme_id, row in rows.items():
                reason = row["unavailableReason"]
                by_theme.setdefault(str(theme_id), []).append(
                    AttentionDaySignal(
                        market_date=market_date,
                        is_attention=row["isAttention"],
                        membership_version=row["membershipVersion"],
                        calculation_version=str(row["calculationVersion"]),
                        baseline_version=str(row["baselineVersion"]),
                        attention_policy_version=str(row["attentionPolicyVersion"]),
                        turnover_multiple=_decimal(row["turnoverMultiple"]),
                        high_interest_count=row["highInterestCount"],
                        valid_count=row["validCount"],
                        high_interest_ratio=_decimal(row["highInterestRatio"]),
                        weighted_return=_decimal(row["weightedReturn"]),
                        unavailable_reason=(
                            None if reason is None else UnavailableReason(str(reason))
                        ),
                    )
                )
        return {theme_id: tuple(items) for theme_id, items in by_theme.items()}
