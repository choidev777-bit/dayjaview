from __future__ import annotations

import asyncio
from datetime import timedelta

from httpx import ASGITransport, AsyncClient

from apps.api.serve import (
    FIXTURE_DEMO_LOGIN_CODE,
    build_fixture_environment,
    create_asgi_app,
    create_pipeline_stores,
)
from packages.events import InMemoryEventStore
from packages.pipeline import MarketPublishLoop, PublishedView
from packages.realtime import InMemorySnapshotRepository

BASE_URL = "https://dayjaview.vercel.app"


async def _login(client: AsyncClient, environment) -> None:
    started = await client.get(
        "/auth/google",
        params={"returnTo": "/today"},
        follow_redirects=False,
    )
    assert started.status_code == 302
    from urllib.parse import parse_qs, urlsplit

    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    completed = await client.get(
        "/auth/google/callback",
        params={"code": FIXTURE_DEMO_LOGIN_CODE, "state": state},
        follow_redirects=False,
    )
    assert completed.status_code == 302


def test_fixture_environment_serves_computed_rankings_over_rest_and_ws() -> None:
    async def scenario() -> None:
        environment, pipeline = build_fixture_environment()
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
            anonymous = await client.get("/v1/themes/rankings")
            assert anonymous.status_code == 401

            await _login(client, environment)
            response = await client.get("/v1/themes/rankings")
            assert response.status_code == 200
            body = response.json()
            items = body["data"]["items"]
            assert len(items) == 1
            item = items[0]
            assert item["classification"]["themeId"] == "thm_fixture_tech"
            assert item["lifecycleStatus"] == "ACTIVE"
            # 실제 계산 결과(1/3 동일가중 상한): 각 fixture 수익률의 평균
            from decimal import Decimal

            expected = float(
                (
                    (Decimal("73200") / Decimal("72200") - 1)
                    + (Decimal("194000") / Decimal("189500") - 1)
                    + (Decimal("207000") / Decimal("203500") - 1)
                )
                / Decimal(3)
            )
            assert abs(item["weightedReturn"] - expected) < 1e-9
            assert item["advancingCount"] == 3
            assert item["validCount"] == 3
            assert item["coverage"]["status"] == "SUFFICIENT"
            # 주도주는 최고 수익률 종목 SK하이닉스(+2.37%)
            assert item["leader"]["symbol"] == "000660"
            assert item["leader"]["name"] == "SK하이닉스"

            session = await client.get("/v1/market/session")
            assert session.status_code == 200
            session_body = session.json()
            assert session_body["data"]["sessionPhase"] == "REGULAR"
            assert session_body["meta"]["marketContext"]["marketDate"] == "2026-08-14"

            treemap = await client.get("/v1/insights/treemap")
            assert treemap.status_code == 200
            treemap_items = treemap.json()["data"]["items"]
            assert treemap_items[0]["themeId"] == "thm_fixture_tech"
            assert (
                treemap_items[0]["weightedReturn"] == item["weightedReturn"]
            )

        # REST 문서와 WebSocket hub가 같은 스냅샷을 본다
        latest = pipeline.latest_rankings
        assert latest is not None
        ws_message = latest.to_ws_message(subscription_id="sub_check")
        assert ws_message["payload"]["items"] == items
        from apps.api import normalize_topic_request

        topic = normalize_topic_request(
            {"name": "theme_rank_snapshot", "params": {"limit": 10}}
        )
        hub_snapshot = environment.realtime_hub.latest(topic)
        assert hub_snapshot is not None
        assert hub_snapshot.snapshot_id == latest.snapshot_id

    asyncio.run(scenario())


def test_asgi_wrapper_serves_health_and_delegates_product_routes() -> None:
    async def scenario() -> None:
        environment, _pipeline = build_fixture_environment()
        application = create_asgi_app(
            environment,
            health_payload=lambda: {"status": "HEALTHY", "locale": "ko-KR"},
        )
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
            health = await client.get("/api/health")
            assert health.status_code == 200
            assert health.json()["status"] == "HEALTHY"

            session = await client.get("/auth/session")
            assert session.status_code == 200
            assert session.json()["data"]["authenticated"] is False

    asyncio.run(scenario())


def test_pipeline_stores_default_to_memory_without_dsn() -> None:
    event_store, snapshot_repository = create_pipeline_stores({})
    assert isinstance(event_store, InMemoryEventStore)
    assert isinstance(snapshot_repository, InMemorySnapshotRepository)


def test_lifespan_starts_and_cancels_publish_loop() -> None:
    async def scenario() -> None:
        environment, pipeline = build_fixture_environment()
        published: list[PublishedView] = []
        publish_loop = MarketPublishLoop(
            pipeline=pipeline,
            on_published=published.append,
            data_status=lambda: pipeline.last_data_status,
            interval=timedelta(milliseconds=10),
        )
        application = create_asgi_app(environment, publish_loop=publish_loop)

        messages: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        sent: list[dict[str, object]] = []

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        await messages.put({"type": "lifespan.startup"})
        lifespan = asyncio.create_task(
            application({"type": "lifespan"}, messages.get, send)
        )
        await asyncio.sleep(0.1)
        assert {"type": "lifespan.startup.complete"} in sent
        assert len(published) >= 2  # 상시 루프가 주기적으로 발행한다

        await messages.put({"type": "lifespan.shutdown"})
        await lifespan
        assert {"type": "lifespan.shutdown.complete"} in sent
        count_after_shutdown = len(published)
        await asyncio.sleep(0.05)
        assert len(published) == count_after_shutdown  # 루프가 취소되었다

    asyncio.run(scenario())
