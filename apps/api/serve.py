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
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from packages.adapters.kiwoom import (
    CanonicalMarketEvent,
    DemandPriority,
    FixtureKiwoomAdapter,
    GatewayDataStatus,
    MarketGateway,
    MarketObservation,
    SubscriptionDemand,
    SupplementReason,
)
from packages.domain import DataStatus
from packages.events import InMemoryEventStore, LineageRef
from packages.identity import GoogleIdentity
from packages.pipeline import MarketDataPipeline
from packages.pipeline.market import RANKINGS_PARAMS, TREEMAP_PARAMS
from packages.realtime import InMemorySnapshotRepository, StockRealtimeUpdate

from .app import FixtureIdentityEnvironment, create_fixture_app
from .app_types import JsonObject
from .config import ApiSettings
from .fixture_universe import (
    FIXTURE_MARKET_DATE,
    FIXTURE_MEMBERSHIP_VERSION,
    FIXTURE_STOCK_NAMES,
    FIXTURE_THEME_NAMES,
    fixture_catalog,
    fixture_references,
)
from .snapshot_product import SnapshotProductReadRepository

KIWOOM_FIXTURE_PATH = "tests/market-gateway/fixtures/kiwoom-market-v1.json"
FIXTURE_DEMO_LOGIN_CODE = "fixture-demo-login"

_BASE = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)

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


def build_fixture_environment(
    *,
    settings: ApiSettings | None = None,
) -> tuple[FixtureIdentityEnvironment, MarketDataPipeline]:
    """게이트웨이 재생 → 파이프라인 발행 → hub 연결까지 끝낸 환경을 만든다."""

    effective_settings = settings or ApiSettings.from_environment(os.environ)
    events, gateway_status = replay_fixture_market_events()
    data_status = _to_data_status(gateway_status)
    pipeline = MarketDataPipeline(
        market_date=FIXTURE_MARKET_DATE,
        stream_id="stream_fixture_20260814",
        schema_version=effective_settings.schema_version,
        catalog=fixture_catalog(),
        references=fixture_references(),
        membership_version=FIXTURE_MEMBERSHIP_VERSION,
        theme_names=FIXTURE_THEME_NAMES,
        stock_names=FIXTURE_STOCK_NAMES,
        event_store=InMemoryEventStore(),
        snapshot_repository=InMemorySnapshotRepository(),
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
    environment.realtime_hub.publish(
        view.rankings,
        params=cast("JsonObject", RANKINGS_PARAMS),
    )
    environment.realtime_hub.publish(
        view.treemap,
        params=cast("JsonObject", TREEMAP_PARAMS),
    )
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
) -> Callable[[Scope, Receive, Send], Awaitable[None]]:
    """lifespan과 /api/health를 처리하고 나머지는 제품 앱에 넘긴다."""

    async def asgi(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
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

    environment, _pipeline = build_fixture_environment()
    application = create_asgi_app(environment, health_payload=health_payload)
    uvicorn.run(application, host=host, port=port, log_level="info")
    return 0
