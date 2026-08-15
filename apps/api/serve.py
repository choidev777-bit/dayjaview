"""fixture 모드 API 실행: 게이트웨이 → 파이프라인 → REST/WSS 실서빙.

키움 synthetic fixture를 실제 Market Gateway로 재생해 canonical 이벤트를
얻고, MarketDataPipeline으로 계산·Event·스냅샷을 만든 뒤, 같은 스냅샷을
REST(product repository)와 WebSocket(realtime hub)에 연결해 uvicorn으로
서빙한다. 외부 네트워크 호출은 없다.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast

from packages.adapters.kiwoom import (
    CanonicalMarketEvent,
    DemandPriority,
    FixtureKiwoomAdapter,
    GatewayDataStatus,
    LiveKiwoomAdapter,
    MarketGateway,
    MarketObservation,
    SubscriptionDemand,
    SupplementReason,
)
from packages.adapters.kiwoom.live import KST as _KST
from packages.domain import DataStatus
from packages.events import (
    EventStore,
    InMemoryEventStore,
    LineageRef,
    PostgresEventStore,
)
from packages.identity import GoogleIdentity
from packages.pipeline import (
    LiveMarketRunner,
    MarketDataPipeline,
    MarketPublishLoop,
    PublishedView,
    ThemeUniverse,
    load_collected_references,
    load_theme_universe,
)
from packages.pipeline.market import RANKINGS_PARAMS, TREEMAP_PARAMS
from packages.realtime import (
    InMemorySnapshotRepository,
    PostgresSnapshotRepository,
    SnapshotRepository,
    StockRealtimeUpdate,
)

from .app import FixtureIdentityEnvironment, create_fixture_app
from .app_types import JsonObject
from .config import ApiSettings
from .fixture_universe import FIXTURE_MARKET_DATE, fixture_universe
from .realtime import RealtimeSnapshotHub
from .snapshot_product import SnapshotProductReadRepository

KIWOOM_FIXTURE_PATH = "tests/market-gateway/fixtures/kiwoom-market-v1.json"
FIXTURE_DEMO_LOGIN_CODE = "fixture-demo-login"
THEME_UNIVERSE_MODE_ENV = "THEME_UNIVERSE_MODE"
INFOSTOCK_IMPORT_DIR_ENV = "INFOSTOCK_IMPORT_DIR"
REFERENCE_DATA_DIR_ENV = "REFERENCE_DATA_DIR"
DATABASE_DSN_ENV = "DATABASE_URL"
KIWOOM_MODE_ENV = "KIWOOM_MODE"
KIWOOM_APP_KEY_ENV = "KIWOOM_APP_KEY"
KIWOOM_APP_SECRET_ENV = "KIWOOM_APP_SECRET"
KIWOOM_CONDITION_IDS_ENV = "KIWOOM_CONDITION_IDS"
DEFAULT_INFOSTOCK_IMPORT_DIR = "./data/infostock/import"
PUBLISH_INTERVAL = timedelta(seconds=2)
MARKET_CLOSE_KST = time(15, 30)

_BASE = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
# 인포스탁 테마 명단은 장 시작 전에 확보된 것으로 취급한다.
_INFOSTOCK_MEMBERSHIP_KNOWN_AT = datetime(2026, 8, 13, 23, 0, tzinfo=UTC)

HealthPayload = Callable[[], dict[str, object]]
Scope = Mapping[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


def _demands(at: datetime) -> tuple[SubscriptionDemand, ...]:
    return (
        SubscriptionDemand("KRX:005930", DemandPriority.ACTIVE_LEADER, at),
        SubscriptionDemand("KRX:000660", DemandPriority.ACTIVE_CORE, at),
        SubscriptionDemand("KRX:035420", DemandPriority.ACTIVE_RELATED, at),
    )


def replay_fixture_market_events(
    fixture_path: str = KIWOOM_FIXTURE_PATH,
) -> tuple[tuple[CanonicalMarketEvent, ...], GatewayDataStatus]:
    """fixture를 실제 게이트웨이 경로(구독→재연결→보완)로 재생한다."""

    gateway = MarketGateway(FixtureKiwoomAdapter.from_path(fixture_path))
    demands = _demands(_BASE)
    gateway.connect(now=_BASE)
    gateway.reconcile_subscriptions(demands, now=_BASE, force=True)
    for second in (1, 2, 3, 4):
        gateway.poll_once(now=_BASE + timedelta(seconds=second))
    gateway.recover(
        demands,
        supplement_stock_ids=("KRX:005930", "KRX:000660", "KRX:035420"),
        now=_BASE + timedelta(seconds=6, milliseconds=200),
    )
    gateway.supplement(
        ("KRX:035420",),
        reason=SupplementReason.COVERAGE,
        now=_BASE + timedelta(seconds=6, milliseconds=500),
    )
    health = gateway.health(
        ("KRX:005930", "KRX:000660", "KRX:035420"),
        now=_BASE + timedelta(seconds=6, milliseconds=600),
    )
    return gateway.accepted_events, health.data_status


def _to_update(
    event: CanonicalMarketEvent,
) -> StockRealtimeUpdate | None:
    if not isinstance(event.data, MarketObservation):
        return None
    return StockRealtimeUpdate(
        message_id=event.event_id,
        stock_id=event.stock_id,
        market_date=FIXTURE_MARKET_DATE,
        source=f"kiwoom:{event.lineage.session_id}",
        source_sequence=event.source_sequence,
        occurred_at=event.source_timestamp,
        received_at=event.received_at,
        current_price=event.data.current_price,
        cumulative_trading_value=event.data.cumulative_trading_value,
        base_price=event.data.base_price,
        lineage=(
            LineageRef(
                kind="market-event",
                identifier=event.event_id,
                version=event.schema_version,
            ),
        ),
    )


def _to_data_status(status: GatewayDataStatus) -> DataStatus:
    try:
        return DataStatus(status.value)
    except ValueError:
        return DataStatus.DEGRADED


def theme_universe_from_environment(
    environment: Mapping[str, str],
    *,
    market_date: date = FIXTURE_MARKET_DATE,
    membership_known_at: datetime = _INFOSTOCK_MEMBERSHIP_KNOWN_AT,
) -> ThemeUniverse:
    """`THEME_UNIVERSE_MODE`로 연습용 2테마와 인포스탁 실테마 명단을 고른다.

    infostock 모드에는 기준정보(A-2)가 아직 없으므로 references가 비어 있다.
    그래서 모든 테마가 Coverage INSUFFICIENT로 남고 rankings가 비는 것이 이
    모드의 정상 상태다. live 서빙은 `market_date`에 당일을 넘겨 같은 로더를
    재사용한다.
    """

    mode = environment.get(THEME_UNIVERSE_MODE_ENV, "fixture").strip().lower()
    if mode == "fixture":
        return fixture_universe()
    if mode != "infostock":
        raise ValueError(
            f"{THEME_UNIVERSE_MODE_ENV}는 fixture 또는 infostock이어야 합니다: {mode}"
        )
    universe = load_theme_universe(
        Path(
            environment.get(INFOSTOCK_IMPORT_DIR_ENV, DEFAULT_INFOSTOCK_IMPORT_DIR)
        ),
        effective_from=market_date,
        known_at=membership_known_at,
    )
    directory = environment.get(REFERENCE_DATA_DIR_ENV, "").strip()
    if not directory:
        return universe
    return replace(
        universe,
        references=load_collected_references(
            Path(directory),
            market_date=market_date,
            decision_at=membership_known_at,
            stock_ids=universe.stock_names,
        ),
    )


def create_pipeline_stores(
    environment: Mapping[str, str],
) -> tuple[EventStore, SnapshotRepository]:
    """`DATABASE_URL`이 있으면 Postgres 영속 저장소, 없으면 InMemory를 조립한다."""

    dsn = environment.get(DATABASE_DSN_ENV, "").strip()
    if not dsn:
        return InMemoryEventStore(), InMemorySnapshotRepository()
    import psycopg

    # 파이프라인은 단일 루프에서 순차로 쓰므로 두 저장소가 연결 하나를 공유한다.
    # psycopg cursor overload가 저장소의 DbConnection Protocol과 이름만 다르다.
    connection: Any = psycopg.connect(dsn)
    return PostgresEventStore(connection), PostgresSnapshotRepository(connection)


def publish_view_to_hub(hub: RealtimeSnapshotHub, view: PublishedView) -> None:
    hub.publish(view.rankings, params=cast("JsonObject", RANKINGS_PARAMS))
    hub.publish(view.treemap, params=cast("JsonObject", TREEMAP_PARAMS))


def build_fixture_environment(
    *,
    settings: ApiSettings | None = None,
    universe: ThemeUniverse | None = None,
) -> tuple[FixtureIdentityEnvironment, MarketDataPipeline]:
    """게이트웨이 재생 → 파이프라인 발행 → hub 연결까지 끝낸 환경을 만든다."""

    effective_settings = settings or ApiSettings.from_environment(os.environ)
    effective_universe = universe or theme_universe_from_environment(os.environ)
    events, gateway_status = replay_fixture_market_events()
    data_status = _to_data_status(gateway_status)
    event_store, snapshot_repository = create_pipeline_stores(os.environ)
    pipeline = MarketDataPipeline(
        market_date=FIXTURE_MARKET_DATE,
        # Postgres 영속화에서 publication_id·command message_id가 stream_id로
        # 만들어지므로, 재기동한 프로세스가 이전 실행과 충돌하지 않게
        # 부팅마다 고유한 stream_id를 쓴다.
        stream_id=f"stream_fixture_20260814_{secrets.token_hex(4)}",
        schema_version=effective_settings.schema_version,
        catalog=effective_universe.catalog(),
        references=effective_universe.references,
        membership_version=effective_universe.version,
        theme_names=effective_universe.theme_names,
        stock_names=effective_universe.stock_names,
        event_store=event_store,
        snapshot_repository=snapshot_repository,
    )
    for event in events:
        update = _to_update(event)
        if update is not None:
            pipeline.apply_update(update)
    # 첫 발행이 후보 상태를 만들고, hysteresis activate_after(10초)가 지난
    # 두 번째 발행에서 ACTIVE 전이와 rankings 항목이 나온다.
    pipeline.publish(now=_BASE + timedelta(seconds=7), data_status=data_status)
    view = pipeline.publish(
        now=_BASE + timedelta(seconds=20),
        data_status=data_status,
    )
    environment = create_fixture_app(
        settings=effective_settings,
        product_repository=SnapshotProductReadRepository(pipeline),
    )
    publish_view_to_hub(environment.realtime_hub, view)
    environment.oauth_provider.register_code(
        FIXTURE_DEMO_LOGIN_CODE,
        GoogleIdentity(
            subject="fixture-demo-user",
            display_name="픽스처 사용자",
            email="fixture@dayjaview.test",
            email_verified=True,
        ),
    )
    return environment, pipeline


def create_asgi_app(
    environment: FixtureIdentityEnvironment,
    *,
    health_payload: HealthPayload | None = None,
    publish_loop: MarketPublishLoop | None = None,
) -> Callable[[Scope, Receive, Send], Awaitable[None]]:
    """lifespan과 /api/health를 처리하고 나머지는 제품 앱에 넘긴다.

    publish_loop가 있으면 lifespan startup에 상시 발행 task로 띄우고
    shutdown에 취소한다.
    """

    async def asgi(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            loop_task: asyncio.Task[None] | None = None
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    if publish_loop is not None:
                        loop_task = asyncio.create_task(publish_loop.run())
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    if loop_task is not None:
                        loop_task.cancel()
                        try:
                            await loop_task
                        except asyncio.CancelledError:
                            pass
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if (
            scope["type"] == "http"
            and scope.get("path") == "/api/health"
            and health_payload is not None
        ):
            try:
                payload = await asyncio.to_thread(health_payload)
                status = 200
            except SystemExit as exc:
                payload = {
                    "status": "UNHEALTHY",
                    "locale": "ko-KR",
                    "messageKo": str(exc),
                }
                status = 503
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(
                "utf-8"
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"cache-control", b"no-store"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await environment.app(dict(scope), receive, send)

    return asgi


def serve_fixture_api(
    *,
    host: str,
    port: int,
    health_payload: HealthPayload | None = None,
) -> int:
    import uvicorn

    environment, pipeline = build_fixture_environment()
    publish_loop = MarketPublishLoop(
        pipeline=pipeline,
        on_published=lambda view: publish_view_to_hub(
            environment.realtime_hub, view
        ),
        data_status=lambda: pipeline.last_data_status,
        interval=PUBLISH_INTERVAL,
    )
    application = create_asgi_app(
        environment,
        health_payload=health_payload,
        publish_loop=publish_loop,
    )
    uvicorn.run(application, host=host, port=port, log_level="info")
    return 0


def _load_env_file(path: Path) -> None:
    """`.env.local`의 키움 키를 process 환경에 채운다. 기존 환경값이 우선한다."""

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[:1] == value[-1:] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def build_live_environment(
    *,
    settings: ApiSettings | None = None,
) -> tuple[
    FixtureIdentityEnvironment,
    MarketDataPipeline,
    LiveMarketRunner,
    LiveKiwoomAdapter,
]:
    """실 키움 게이트웨이 → 당일 파이프라인 → hub 조립. 접속은 첫 tick에서 한다.

    A-3 live 검증 경로다. 실제 외부 호출(키움 REST/WS)이 발생하므로
    CLAUDE.md 승인 항목 2에 따라 사용자 승인 아래에서만 실행한다.
    """

    mode = os.environ.get(KIWOOM_MODE_ENV, "").strip().lower()
    if mode not in ("real", "demo"):
        raise ValueError(
            f"live 서빙은 {KIWOOM_MODE_ENV}=real 또는 demo가 필요합니다"
            " (fixture 모드는 serve_fixture_api를 사용)"
        )
    app_key = os.environ.get(KIWOOM_APP_KEY_ENV, "").strip()
    app_secret = os.environ.get(KIWOOM_APP_SECRET_ENV, "").strip()
    if not app_key or not app_secret:
        raise ValueError(
            f"{KIWOOM_APP_KEY_ENV}/{KIWOOM_APP_SECRET_ENV}가 필요합니다 (.env.local)"
        )
    condition_ids = tuple(
        part.strip()
        for part in os.environ.get(KIWOOM_CONDITION_IDS_ENV, "").split(",")
        if part.strip()
    )
    effective_settings = settings or ApiSettings.from_environment(os.environ)
    market_date = datetime.now(_KST).date()
    boot_at = datetime.now(UTC)
    universe = theme_universe_from_environment(
        os.environ,
        market_date=market_date,
        membership_known_at=boot_at,
    )
    event_store, snapshot_repository = create_pipeline_stores(os.environ)
    pipeline = MarketDataPipeline(
        market_date=market_date,
        stream_id=(
            f"stream_live_{market_date.strftime('%Y%m%d')}_{secrets.token_hex(4)}"
        ),
        schema_version=effective_settings.schema_version,
        catalog=universe.catalog(),
        references=universe.references,
        membership_version=universe.version,
        theme_names=universe.theme_names,
        stock_names=universe.stock_names,
        event_store=event_store,
        snapshot_repository=snapshot_repository,
    )
    adapter = LiveKiwoomAdapter(
        mode=mode,
        app_key=app_key,
        app_secret=app_secret,
        condition_ids=condition_ids,
    )
    runner = LiveMarketRunner(
        gateway=MarketGateway(adapter),
        market_date=market_date,
        theme_members={
            snapshot.theme_id: tuple(member.stock_id for member in snapshot.members)
            for snapshot in universe.snapshots
        },
    )
    environment = create_fixture_app(
        settings=effective_settings,
        product_repository=SnapshotProductReadRepository(pipeline),
    )
    # 실 구글 로그인(F-21) 전까지 로컬 확인용 데모 로그인을 유지한다.
    environment.oauth_provider.register_code(
        FIXTURE_DEMO_LOGIN_CODE,
        GoogleIdentity(
            subject="fixture-demo-user",
            display_name="픽스처 사용자",
            email="fixture@dayjaview.test",
            email_verified=True,
        ),
    )
    return environment, pipeline, runner, adapter


def serve_live_api(
    *,
    host: str,
    port: int,
    env_file: str | None = ".env.local",
    health_payload: HealthPayload | None = None,
) -> int:
    """실 키움 접속으로 장중 이벤트를 파이프라인에 흘려보내며 서빙한다."""

    import uvicorn

    if env_file:
        _load_env_file(Path(env_file))
    environment, pipeline, runner, adapter = build_live_environment()
    publish_loop = MarketPublishLoop(
        pipeline=pipeline,
        on_published=lambda view: publish_view_to_hub(
            environment.realtime_hub, view
        ),
        data_status=runner.data_status,
        interval=PUBLISH_INTERVAL,
        poll_updates=runner.poll_updates,
        market_close_at=datetime.combine(
            pipeline.market_date, MARKET_CLOSE_KST, tzinfo=_KST
        ),
    )
    application = create_asgi_app(
        environment,
        health_payload=health_payload,
        publish_loop=publish_loop,
    )
    try:
        uvicorn.run(application, host=host, port=port, log_level="info")
    finally:
        adapter.close()
    return 0
