from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

from httpx import ASGITransport, AsyncClient
from jsonschema import Draft202012Validator, FormatChecker

from apps.api import (
    InMemoryProductReadRepository,
    ProductDocument,
    create_fixture_app,
)
from apps.api.app_types import JsonObject
from apps.api.cookies import SESSION_COOKIE
from packages.identity import GoogleIdentity, Role
from packages.infostock import DayMovers, MoverSection, MoverStock, MoverTheme

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = _ROOT / "contracts" / "fixtures"
_SCHEMA = json.loads(
    (_ROOT / "contracts" / "schemas" / "stage0.schema.json").read_text(
        encoding="utf-8"
    )
)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())


@dataclass(slots=True)
class MutableClock:
    current: datetime = datetime(2026, 8, 14, 3, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


def _fixture(relative_path: str) -> JsonObject:
    value = json.loads((_FIXTURES / relative_path).read_text(encoding="utf-8"))
    return cast(JsonObject, value)


def _document(relative_path: str) -> ProductDocument:
    return ProductDocument.from_response(_fixture(relative_path))


def _product_repository() -> InMemoryProductReadRepository:
    repository = InMemoryProductReadRepository()
    repository.put_market_session(_document("market/session.live.json"))
    ranking = _document("rankings/live.json")
    repository.put_rankings(ranking)
    repository.put_rankings(ranking, market_date="2026-08-14")
    repository.put_treemap(_document("treemap/live.json"))
    repository.put_theme_event(
        "thm_nuclear",
        "evt_current",
        _document("event/single-source.json"),
    )
    repository.put_evidence(
        "evt_current",
        _document("evidence/single-source.json"),
    )
    repository.put_similar_events(
        "evt_current",
        _document("similar/gated.json"),
    )
    repository.put_historical_event(
        "evt_historical",
        _document("similar/event-detail.json"),
    )
    return repository


def _service_login(environment, *, subject: str = "google-product-user"):
    started = environment.service.begin_google_login("/today")
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    code = f"code-{subject}"
    environment.oauth_provider.register_code(
        code,
        GoogleIdentity(subject, "제품 사용자"),
    )
    return environment.service.complete_google_login(
        code=code,
        state=state,
        browser_nonce=started.browser_nonce,
    )


def _authenticate_client(client: AsyncClient, session_token: str) -> None:
    client.cookies.set(
        SESSION_COOKIE,
        session_token,
        domain="dayjaview.vercel.app",
        path="/",
    )


def _assert_contract(definition: str, payload: object) -> None:
    validator = _VALIDATOR.evolve(schema={"$ref": f"#/$defs/{definition}"})
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    assert not errors, "\n".join(error.message for error in errors)


def test_anonymous_core_rest_returns_no_product_data() -> None:
    async def scenario() -> None:
        environment = create_fixture_app(
            clock=MutableClock(),
            product_repository=_product_repository(),
        )
        transport = ASGITransport(app=environment.app)
        paths = (
            "/v1/market/session",
            "/v1/themes/rankings",
            "/v1/insights/treemap",
            "/v1/themes/thm_nuclear/events/evt_current",
            "/v1/events/evt_current/evidence",
            "/v1/events/evt_current/similar-events",
            "/v1/events/evt_historical",
        )
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
        ) as client:
            for path in paths:
                response = await client.get(path)
                assert response.status_code == 401
                payload = response.json()
                _assert_contract("ErrorResponse", payload)
                assert "data" not in payload
                assert "evt_current" not in response.text
                assert "thm_nuclear" not in response.text

    asyncio.run(scenario())


def test_authenticated_core_rest_preserves_machine_contract_semantics() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        environment = create_fixture_app(
            clock=clock,
            product_repository=_product_repository(),
        )
        completion = _service_login(environment)
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
        ) as client:
            _authenticate_client(client, completion.session_token)
            responses = {
                "MarketSessionResponse": await client.get(
                    "/v1/market/session",
                    headers={"X-Request-Id": "req_runtime_market"},
                ),
                "RankingResponse": await client.get(
                    "/v1/themes/rankings",
                    params={"limit": 10, "marketDate": "2026-08-14"},
                ),
                "TreemapResponse": await client.get(
                    "/v1/insights/treemap",
                    params={"limit": 12},
                ),
                "ThemeDetailResponse": await client.get(
                    "/v1/themes/thm_nuclear/events/evt_current"
                ),
                "EvidenceListResponse": await client.get(
                    "/v1/events/evt_current/evidence",
                    params={"limit": 20},
                ),
                "SimilarEventsResponse": await client.get(
                    "/v1/events/evt_current/similar-events"
                ),
            }
            for definition, response in responses.items():
                assert response.status_code == 200
                assert response.headers["cache-control"] == "private, no-store"
                _assert_contract(definition, response.json())

            market = responses["MarketSessionResponse"].json()
            assert market["meta"]["requestId"] == "req_runtime_market"
            assert market["meta"]["generatedAt"] == "2026-08-14T03:00:00.000Z"
            ranking = responses["RankingResponse"].json()
            item = ranking["data"]["items"][0]
            assert item["weightedReturn"] == 0.027
            assert item["coverage"]["core"]["countRatio"] == 0.8095
            assert item["qualityFlags"] == []
            assert ranking["meta"]["marketContext"]["dataStatus"] == "LIVE"
            assert ranking["meta"]["versions"] == {
                "calculationVersion": "theme-metrics-2026.08.1",
                "rankingModelVersion": "theme-rank-2026.08.1",
                "membershipVersion": "membership-2026-08-14T00:10:00Z",
            }
            similar = responses["SimilarEventsResponse"].json()
            assert similar["data"]["availability"] == "GATED"
            assert similar["data"]["items"] == []
            assert similar["meta"]["versions"]["ontologyVersion"] is None
            serialized = json.dumps(responses["ThemeDetailResponse"].json())
            assert "reviewStatus" not in serialized
            assert "internalNote" not in serialized

    asyncio.run(scenario())


def test_null_zero_empty_coverage_and_quality_flags_are_not_collapsed() -> None:
    async def scenario() -> None:
        repository = InMemoryProductReadRepository()
        repository.put_rankings(_document("rankings/calculation-unavailable.json"))
        repository.put_treemap(
            _document("treemap/insufficient-coverage-excluded.json")
        )
        environment = create_fixture_app(
            clock=MutableClock(),
            product_repository=repository,
        )
        completion = _service_login(environment, subject="google-null-user")
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
        ) as client:
            _authenticate_client(client, completion.session_token)
            ranking_response = await client.get("/v1/themes/rankings")
            treemap_response = await client.get("/v1/insights/treemap")
            _assert_contract("RankingResponse", ranking_response.json())
            _assert_contract("TreemapResponse", treemap_response.json())

            item = ranking_response.json()["data"]["items"][0]
            assert item["weightedReturn"] is None
            assert item["advancingCount"] is None
            assert item["coverage"]["core"] == {
                "observedCount": 0,
                "totalCount": 0,
                "countRatio": None,
                "observedWeightRatio": None,
            }
            assert item["qualityFlags"] == [
                "INSUFFICIENT_COVERAGE",
                "FREE_FLOAT_UNAVAILABLE",
            ]
            assert treemap_response.json()["data"]["items"] == []

    asyncio.run(scenario())


def test_identifier_query_and_historical_entitlement_fail_closed() -> None:
    async def scenario() -> None:
        environment = create_fixture_app(
            clock=MutableClock(),
            product_repository=_product_repository(),
        )
        completion = _service_login(environment, subject="google-gate-user")
        principal = environment.service.require_authenticated(completion.session_token)
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
        ) as client:
            _authenticate_client(client, completion.session_token)
            mismatch = await client.get(
                "/v1/themes/thm_wrong/events/evt_current"
            )
            invalid_limit = await client.get(
                "/v1/themes/rankings",
                params={"limit": "0"},
            )
            unknown_query = await client.get(
                "/v1/market/session",
                params={"userId": "usr_other"},
            )
            unsupported_date = await client.get(
                "/v1/themes/rankings",
                params={"marketDate": "2026-08-13"},
            )
            gated_detail = await client.get("/v1/events/evt_historical")

            assert mismatch.status_code == 409
            assert mismatch.json()["error"]["code"] == "RESOURCE_ID_MISMATCH"
            assert invalid_limit.status_code == 400
            assert unknown_query.status_code == 400
            assert unsupported_date.status_code == 422
            assert gated_detail.status_code == 403
            assert gated_detail.json()["error"]["code"] == "FEATURE_NOT_ENTITLED"

            environment.repository.add_role(
                principal.user.user_id,
                Role.HISTORICAL_PILOT,
            )
            entitled = await client.get(
                "/v1/events/evt_historical",
                params={"contextEventId": "evt_current"},
            )
            assert entitled.status_code == 200
            _assert_contract("HistoricalEventResponse", entitled.json())
            assert entitled.json()["data"]["futureOutcomeExcludedFromSelection"] is True

    asyncio.run(scenario())


def test_product_projection_rejects_operator_only_fields() -> None:
    response = _fixture("event/single-source.json")
    data = cast(JsonObject, response["data"])
    data["reviewStatus"] = "PENDING"

    try:
        ProductDocument.from_response(response)
    except ValueError as error:
        assert "reviewStatus" in str(error)
    else:
        raise AssertionError("operator-only field must be rejected")

    try:
        ProductDocument(
            data={"items": []},
            market_context={"sessionToken": "must-not-cross-the-boundary"},
        )
    except ValueError as error:
        assert "sessionToken" in str(error)
    else:
        raise AssertionError("session credential field must be rejected")


class _StubDailyReader:
    """DAY_MOVERS 응답 형태만 확인하는 최소 reader."""

    def __init__(self, movers: DayMovers) -> None:
        self._movers = movers
        self.requested: list[date] = []

    def day_movers(self, requested: date) -> DayMovers:
        self.requested.append(requested)
        return self._movers


def _movers(
    *,
    requested: date,
    published: date | None,
    status: str,
) -> DayMovers:
    section = MoverSection(
        section_name="2차전지 등",
        headline="K-배터리, 2분기 실적 반등 기대감 등에 상승",
        details=("▷언론에 따르면, 배터리 업계가 2분기 실적 반등의 기반을 마련.",),
        themes=(
            MoverTheme(
                theme_name="2차전지",
                change_rate="10.86",
                headline="K-배터리, 2분기 실적 반등 기대감 등에 상승",
                stocks=(
                    MoverStock("신성델타테크", "065350", 34000, "21.00"),
                    MoverStock("에코프로", "086520", 118000, "-23.69"),
                ),
            ),
        ),
    )
    return DayMovers(requested, published, status, (section,))


def test_day_movers_returns_sections_with_source_quotes_and_rates() -> None:
    async def scenario() -> None:
        reader = _StubDailyReader(
            _movers(
                requested=date(2026, 6, 29),
                published=date(2026, 6, 29),
                status="PUBLISHED",
            )
        )
        environment = create_fixture_app(clock=MutableClock(), daily_reader=reader)
        completion = _service_login(environment)
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
        ) as client:
            _authenticate_client(client, completion.session_token)
            response = await client.get(
                "/v1/daily/movers", params={"date": "2026-06-29"}
            )

        assert response.status_code == 200
        _assert_contract("DayMoversResponse", response.json())
        data = response.json()["data"]
        assert reader.requested == [date(2026, 6, 29)]
        assert (data["status"], data["isFallback"]) == ("PUBLISHED", False)
        section = data["sections"][0]
        assert section["details"][0].startswith("▷")
        stocks = section["themes"][0]["stocks"]
        assert [item["changeRate"] for item in stocks] == ["21.00", "-23.69"]
        assert stocks[0]["closePrice"] == 34000

    asyncio.run(scenario())


def test_day_movers_falls_back_to_the_last_published_day_without_inventing() -> None:
    async def scenario() -> None:
        reader = _StubDailyReader(
            _movers(
                requested=date(2026, 8, 16),
                published=date(2026, 8, 14),
                status="NOT_PUBLISHED",
            )
        )
        environment = create_fixture_app(clock=MutableClock(), daily_reader=reader)
        completion = _service_login(environment)
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
        ) as client:
            _authenticate_client(client, completion.session_token)
            response = await client.get(
                "/v1/daily/movers", params={"date": "2026-08-16"}
            )

        assert response.status_code == 200
        _assert_contract("DayMoversResponse", response.json())
        data = response.json()["data"]
        assert data["status"] == "NOT_PUBLISHED"
        assert data["isFallback"] is True
        assert data["publishedDate"] == "2026-08-14"
        assert data["requestedDate"] == "2026-08-16"

    asyncio.run(scenario())


def test_day_movers_rejects_bad_date_and_reports_missing_record() -> None:
    async def scenario() -> None:
        reader = _StubDailyReader(
            _movers(requested=date(2005, 1, 1), published=None, status="NO_RECORD")
        )
        environment = create_fixture_app(clock=MutableClock(), daily_reader=reader)
        completion = _service_login(environment)
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
        ) as client:
            _authenticate_client(client, completion.session_token)
            invalid = await client.get(
                "/v1/daily/movers", params={"date": "2026-6-29"}
            )
            missing_param = await client.get("/v1/daily/movers")
            no_record = await client.get(
                "/v1/daily/movers", params={"date": "2005-01-01"}
            )

        assert invalid.status_code == 400
        assert missing_param.status_code == 400
        assert no_record.status_code == 404

    asyncio.run(scenario())


def test_day_movers_requires_authentication() -> None:
    async def scenario() -> None:
        reader = _StubDailyReader(
            _movers(
                requested=date(2026, 6, 29),
                published=date(2026, 6, 29),
                status="PUBLISHED",
            )
        )
        environment = create_fixture_app(clock=MutableClock(), daily_reader=reader)
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
        ) as client:
            response = await client.get(
                "/v1/daily/movers", params={"date": "2026-06-29"}
            )

        assert response.status_code == 401
        assert reader.requested == []

    asyncio.run(scenario())
