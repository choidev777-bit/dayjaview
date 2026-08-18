"""LiveMarketRunner를 fixture 포트로 검증한다: 후보→구독→보완→관측→rankings."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from packages.adapters.kiwoom import (
    ConnectionPhase,
    FixtureCallKind,
    FixtureKiwoomAdapter,
    FixtureSession,
    FixtureSnapshotCall,
    KiwoomConnection,
    KiwoomSourceEnvelope,
    MarketGateway,
    SourceChannel,
    SubscriptionManager,
)
from packages.domain import (
    DataStatus,
    MembershipRole,
    StockReference,
    ThemeMember,
    ThemeMembershipSnapshot,
)
from packages.events import InMemoryEventStore
from packages.pipeline import LiveMarketRunner, MarketDataPipeline, MarketPublishLoop
from packages.realtime import InMemorySnapshotRepository, VersionedThemeCatalog

MARKET_DATE = date(2026, 8, 14)
KNOWN_AT = datetime(2026, 8, 13, 23, 0, tzinfo=UTC)
BASE = datetime(2026, 8, 14, 0, 30, tzinfo=UTC)
SESSION_1 = "live-session-1"
SESSION_2 = "live-session-2"
THEME_MEMBERS = {
    "thm_full": ("KRX:000001", "KRX:000002", "KRX:000003"),
}
_PRICES = {
    "KRX:000001": ("10000", "10300"),
    "KRX:000002": ("20000", "20200"),
    "KRX:000003": ("40000", "40100"),
}


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


def ws_envelope(
    session_id: str,
    sequence: int,
    payload: dict[str, object],
    *,
    at: datetime,
) -> KiwoomSourceEnvelope:
    return KiwoomSourceEnvelope(
        source_schema_version="kiwoom.websocket.v1",
        channel=SourceChannel.WEBSOCKET,
        session_id=session_id,
        source_message_id=f"msg-{session_id}-{sequence}",
        source_sequence=sequence,
        source_timestamp=at,
        received_at=at,
        market_date=MARKET_DATE,
        payload=payload,
    )


def snapshot_envelope(
    session_id: str,
    sequence: int,
    rows: list[dict[str, object]],
    *,
    at: datetime,
) -> KiwoomSourceEnvelope:
    return KiwoomSourceEnvelope(
        source_schema_version="kiwoom.ka10095.v1",
        channel=SourceChannel.REST_SNAPSHOT,
        session_id=session_id,
        source_message_id=f"snap-{session_id}-{sequence}",
        source_sequence=sequence,
        source_timestamp=at,
        received_at=at,
        market_date=MARKET_DATE,
        payload={"apiId": "ka10095", "atn_stk_infr": rows},
    )


def condition_enter(code: str, *, condition_id: str = "7") -> dict[str, object]:
    return {
        "trnm": "REAL",
        "data": [
            {
                "type": "02",
                "item": code,
                "values": {"841": condition_id, "843": "I", "9001": code},
            }
        ],
    }


def trade(code: str, price: str) -> dict[str, object]:
    return {
        "trnm": "REAL",
        "data": [
            {
                "type": "0B",
                "item": code,
                "values": {"10": price, "14": "1000000", "20": "093000"},
            }
        ],
    }


def session_fixture(
    session_id: str,
    messages: tuple[KiwoomSourceEnvelope, ...],
    *,
    connected_at: datetime,
    disconnect_after_messages: bool = False,
) -> FixtureSession:
    return FixtureSession(
        connection=KiwoomConnection(session_id, connected_at),
        messages=messages,
        disconnect_after_messages=disconnect_after_messages,
    )


def make_runner(
    adapter: FixtureKiwoomAdapter,
    clock: FakeClock,
    *,
    subscriptions: SubscriptionManager | None = None,
    supplement_interval: timedelta = timedelta(seconds=30),
) -> tuple[LiveMarketRunner, MarketGateway]:
    gateway = MarketGateway(adapter, subscriptions=subscriptions)
    runner = LiveMarketRunner(
        gateway=gateway,
        market_date=MARKET_DATE,
        theme_members=THEME_MEMBERS,
        clock=clock,
        supplement_interval=supplement_interval,
    )
    return runner, gateway


def test_candidates_expand_to_theme_subscriptions_and_updates() -> None:
    messages = (
        ws_envelope(SESSION_1, 1, condition_enter("000001"), at=BASE),
        ws_envelope(SESSION_1, 2, trade("000001", "+10300"), at=BASE + timedelta(seconds=1)),
        ws_envelope(SESSION_1, 3, trade("000002", "+20200"), at=BASE + timedelta(seconds=2)),
    )
    adapter = FixtureKiwoomAdapter(
        (session_fixture(SESSION_1, messages, connected_at=BASE),)
    )
    clock = FakeClock(BASE + timedelta(seconds=5))
    runner, gateway = make_runner(adapter, clock)

    updates = runner.poll_updates()

    assert [update.stock_id for update in updates] == ["KRX:000001", "KRX:000002"]
    assert all(update.market_date == MARKET_DATE for update in updates)
    replace_calls = [
        call for call in adapter.calls if call.kind is FixtureCallKind.REPLACE_SUBSCRIPTIONS
    ]
    assert len(replace_calls) == 1
    # 직접 후보 1 + 같은 테마 관련주 2 = 3종목 구독.
    assert set(replace_calls[0].stock_ids) == set(THEME_MEMBERS["thm_full"])
    assert gateway.connection is not None
    assert runner.data_status().value in {"LIVE", "DEGRADED", "DELAYED", "PREOPEN", "CLOSED"}


def test_stale_heartbeat_reads_as_delayed_not_degraded() -> None:
    """게이트웨이 STALE은 수신 지연이다. DEGRADED로 뭉개면 화면 문구가 틀린다."""

    messages = (
        ws_envelope(SESSION_1, 1, condition_enter("000001"), at=BASE),
        ws_envelope(SESSION_1, 2, trade("000001", "+10300"), at=BASE + timedelta(seconds=1)),
    )
    adapter = FixtureKiwoomAdapter(
        (session_fixture(SESSION_1, messages, connected_at=BASE),)
    )
    clock = FakeClock(BASE + timedelta(seconds=5))
    runner, gateway = make_runner(adapter, clock)
    runner.poll_updates()

    clock.advance(gateway.heartbeat_timeout + timedelta(seconds=10))

    assert gateway.phase is ConnectionPhase.CONNECTED
    assert runner.data_status() is DataStatus.DELAYED


def test_supplement_covers_unsubscribed_demand_with_throttle() -> None:
    messages = (ws_envelope(SESSION_1, 1, condition_enter("000001"), at=BASE),)
    snapshot_rows = [
        {"stk_cd": "000001", "cur_prc": "10300", "flu_rt": "3.0", "acc_trde_prica": "163"}
    ]
    adapter = FixtureKiwoomAdapter(
        (session_fixture(SESSION_1, messages, connected_at=BASE),),
        (
            FixtureSnapshotCall(
                SESSION_1,
                (snapshot_envelope(SESSION_1, 100, snapshot_rows, at=BASE),),
            ),
        ),
    )
    clock = FakeClock(BASE + timedelta(seconds=5))
    # 구독 상한 2로 줄이면 우선순위가 낮은 직접 후보(SINGLE=5)가
    # 관련주(ACTIVE_RELATED=4)에 밀려 스냅샷 보완 대상이 된다.
    runner, _ = make_runner(
        adapter,
        clock,
        subscriptions=SubscriptionManager(
            target_limit=2, hard_limit=2, coalesce_interval=timedelta(0)
        ),
    )

    updates = runner.poll_updates()
    assert [update.stock_id for update in updates] == ["KRX:000001"]
    fetch_calls = [
        call for call in adapter.calls if call.kind is FixtureCallKind.FETCH_SNAPSHOTS
    ]
    assert len(fetch_calls) == 1
    assert fetch_calls[0].stock_ids == ("KRX:000001",)

    # 30초가 지나기 전에는 보완을 다시 부르지 않는다.
    clock.advance(timedelta(seconds=2))
    runner.poll_updates()
    fetch_calls = [
        call for call in adapter.calls if call.kind is FixtureCallKind.FETCH_SNAPSHOTS
    ]
    assert len(fetch_calls) == 1

    clock.advance(timedelta(seconds=40))
    runner.poll_updates()
    fetch_calls = [
        call for call in adapter.calls if call.kind is FixtureCallKind.FETCH_SNAPSHOTS
    ]
    assert len(fetch_calls) == 2


def test_disconnect_schedules_and_recovers_on_next_due_tick() -> None:
    first_messages = (
        ws_envelope(SESSION_1, 1, condition_enter("000001"), at=BASE),
        ws_envelope(SESSION_1, 2, trade("000001", "+10300"), at=BASE + timedelta(seconds=1)),
    )
    adapter = FixtureKiwoomAdapter(
        (
            session_fixture(
                SESSION_1, first_messages, connected_at=BASE, disconnect_after_messages=True
            ),
            session_fixture(SESSION_2, (), connected_at=BASE + timedelta(seconds=10)),
        )
    )
    clock = FakeClock(BASE + timedelta(seconds=5))
    runner, gateway = make_runner(adapter, clock)

    updates = runner.poll_updates()
    assert [update.stock_id for update in updates] == ["KRX:000001"]
    assert gateway.connection is None
    assert gateway.phase is ConnectionPhase.RECONNECTING
    assert gateway.reconnect.schedule is not None

    # backoff(기본 1초 + jitter) 전에는 재접속하지 않는다.
    clock.advance(timedelta(milliseconds=100))
    runner.poll_updates()
    assert gateway.connection is None

    clock.advance(timedelta(seconds=2))
    runner.poll_updates()
    assert gateway.connection is not None
    assert gateway.connection.session_id == SESSION_2
    assert gateway.phase is ConnectionPhase.CONNECTED
    # 재접속 시 살아있는 후보로 구독을 강제 복원한다.
    replace_sessions = [
        call.session_id
        for call in adapter.calls
        if call.kind is FixtureCallKind.REPLACE_SUBSCRIPTIONS
    ]
    assert SESSION_2 in replace_sessions


def test_initial_connect_failure_is_scheduled_not_raised() -> None:
    adapter = FixtureKiwoomAdapter(())  # 사용할 세션이 없어 접속이 실패한다.
    clock = FakeClock(BASE)
    runner, gateway = make_runner(adapter, clock)

    assert runner.poll_updates() == []
    assert gateway.phase is ConnectionPhase.RECONNECTING
    assert gateway.reconnect.schedule is not None

    clock.advance(timedelta(seconds=2))
    assert runner.poll_updates() == []  # recover도 실패하지만 예외는 없다.
    assert gateway.connection is None


def _catalog() -> VersionedThemeCatalog:
    return VersionedThemeCatalog(
        (
            ThemeMembershipSnapshot(
                theme_id="thm_full",
                version="membership-live-test-1",
                effective_from=MARKET_DATE,
                known_at=KNOWN_AT,
                members=tuple(
                    ThemeMember(stock_id, MembershipRole.CORE)
                    for stock_id in THEME_MEMBERS["thm_full"]
                ),
            ),
        )
    )


def _references() -> tuple[StockReference, ...]:
    return tuple(
        StockReference(
            stock_id=stock_id,
            effective_for=MARKET_DATE,
            known_at=KNOWN_AT,
            previous_adjusted_close=Decimal(previous_close),
            listed_shares=1_000_000,
            free_float_ratio=Decimal("0.5"),
            free_float_validated=True,
            version="reference-live-test-1",
        )
        for stock_id, (previous_close, _) in _PRICES.items()
    )


def test_publish_loop_with_live_runner_produces_rankings() -> None:
    messages = (
        ws_envelope(SESSION_1, 1, condition_enter("000001"), at=BASE),
        *(
            ws_envelope(
                SESSION_1,
                index + 2,
                trade(stock_id.removeprefix("KRX:"), f"+{current}"),
                at=BASE + timedelta(seconds=index + 1),
            )
            for index, (stock_id, (_, current)) in enumerate(_PRICES.items())
        ),
    )
    adapter = FixtureKiwoomAdapter(
        (session_fixture(SESSION_1, messages, connected_at=BASE),)
    )
    clock = FakeClock(BASE + timedelta(seconds=5))
    runner, _ = make_runner(adapter, clock)
    pipeline = MarketDataPipeline(
        market_date=MARKET_DATE,
        stream_id="stream_live_test",
        schema_version="2026-08-14.1",
        catalog=_catalog(),
        references=_references(),
        membership_version="membership-live-test-1",
        theme_names={"thm_full": "라이브 테마"},
        stock_names={stock_id: stock_id for stock_id in _PRICES},
        event_store=InMemoryEventStore(),
        snapshot_repository=InMemorySnapshotRepository(),
    )
    published = []
    loop = MarketPublishLoop(
        pipeline=pipeline,
        on_published=published.append,
        data_status=runner.data_status,
        interval=timedelta(seconds=2),
        poll_updates=runner.poll_updates,
        clock=clock,
    )

    loop.tick()  # 관측 반영 + 후보 등록
    clock.advance(timedelta(seconds=20))
    view = loop.tick()  # activate_after(10초) 경과 후 ACTIVE 전이

    items = view.rankings.payload["items"]
    assert isinstance(items, list) and len(items) == 1
    assert items[0]["classification"]["themeId"] == "thm_full"
    assert float(items[0]["weightedReturn"]) > 0
    assert len(published) == 2
