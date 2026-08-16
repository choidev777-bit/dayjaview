from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import unquote

from packages.identity import (
    FeatureNotEntitled,
    FixtureGoogleOAuthProvider,
    IdentityError,
    IdentityService,
    InMemoryIdentityRepository,
    InMemoryTargetCatalog,
    InvalidRequest,
    Role,
    RuntimeOperatorStatus,
    SavedType,
    SystemClock,
    TargetCatalog,
    TargetRecord,
)
from packages.identity.security import Clock
from packages.infostock import DayMovers
from packages.operator import InMemoryOperatorRepository, OperatorRepository

from .app_types import JsonObject, JsonValue
from .config import ApiSettings
from .cookies import (
    CSRF_COOKIE,
    OAUTH_STATE_COOKIE,
    SESSION_COOKIE,
    csrf_cookie,
    expire_csrf_cookie,
    expire_oauth_state_cookie,
    expire_session_cookie,
    oauth_state_cookie,
    session_cookie,
)
from .errors import (
    ApiError,
    InvalidApiRequest,
    ProductDataUnavailable,
    ProductResourceNotFound,
    RateLimited,
    ResourceIdMismatch,
    UnsupportedMarketDate,
)
from .http import ApiRequest, ApiResponse, RateLimiter, Receive, Send
from .operator_boundary import (
    OperatorBoundary,
    OperatorStatusSource,
    StaticOperatorStatusSource,
)
from .product import (
    EmptyProductReadRepository,
    InMemoryProductReadRepository,
    ProductDocument,
    ProductReadRepository,
)
from .realtime import RealtimeSnapshotHub, RealtimeWebSocketServer


class DailyFeaturedReader(Protocol):
    """PostgresDailyFeaturedReader가 이미 만족하는 읽기 전용 표면."""

    def day_movers(self, requested: date) -> DayMovers: ...


def _day_movers_data(movers: DayMovers) -> JsonObject:
    """읽기 모델을 공개 응답으로 옮긴다. 수치를 새로 만들지 않는다."""

    return {
        "requestedDate": movers.requested_date.isoformat(),
        "publishedDate": (
            None if movers.published_date is None else movers.published_date.isoformat()
        ),
        "status": movers.status,
        "isFallback": movers.is_fallback,
        "sections": [
            {
                "sectionName": section.section_name,
                "headline": section.headline,
                "details": list(section.details),
                "themes": [
                    {
                        "themeName": theme.theme_name,
                        "changeRate": theme.change_rate,
                        "stocks": [
                            {
                                "stockName": stock.stock_name,
                                "stockCode": stock.stock_code,
                                "closePrice": stock.close_price,
                                "changeRate": stock.change_rate,
                            }
                            for stock in theme.stocks
                        ],
                    }
                    for theme in section.themes
                ],
            }
            for section in movers.sections
        ],
    }


_SAVED_PATH = re.compile(r"^/v1/me/saved/(themes|stocks|events)/([^/]+)$")
_THEME_EVENT_PATH = re.compile(r"^/v1/themes/([^/]+)/events/([^/]+)$")
_EVENT_EVIDENCE_PATH = re.compile(r"^/v1/events/([^/]+)/evidence$")
_SIMILAR_EVENTS_PATH = re.compile(r"^/v1/events/([^/]+)/similar-events$")
_HISTORICAL_EVENT_PATH = re.compile(r"^/v1/events/([^/]+)$")
_OPERATOR_JOB_PATH = re.compile(r"^/v1/operator/jobs/([^/]+)(?:/(retry|resume))?$")
_OPERATOR_REVIEW_PATH = re.compile(r"^/v1/operator/reviews/([^/]+)(?:/(resolve))?$")
_ROLE_ORDER = {Role.USER: 0, Role.HISTORICAL_PILOT: 1, Role.OPERATOR: 2}
AUTH_RATE_LIMIT = 20
AUTH_RATE_WINDOW = timedelta(minutes=5)
# 한도가 걸리는 경로. 무인증으로 저장소를 건드리는 OAuth 진입점만이다.
# 세션 조회·로그아웃은 쿠키가 있어야 하고 행을 만들지 않으므로 세지 않는다.
# 여기에 `/auth/session`을 넣으면 화면이 뜰 때마다 한도를 먹어 정상 사용자가 막힌다.
RATE_LIMITED_AUTH_PATHS = ("/auth/google", "/auth/google/callback")


class IdentityApiApp:
    def __init__(
        self,
        *,
        identity_service: IdentityService,
        operator_boundary: OperatorBoundary,
        settings: ApiSettings,
        product_repository: ProductReadRepository | None = None,
        daily_reader: DailyFeaturedReader | None = None,
        realtime_hub: RealtimeSnapshotHub | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.identity_service = identity_service
        self._operator_boundary = operator_boundary
        self._settings = settings
        self._clock = clock or SystemClock()
        self._product_repository = product_repository or EmptyProductReadRepository()
        self._daily_reader = daily_reader
        self.realtime_hub = realtime_hub or RealtimeSnapshotHub()
        self._realtime_server = RealtimeWebSocketServer(
            identity_service=identity_service,
            hub=self.realtime_hub,
            settings=settings,
            clock=self._clock,
        )
        # 무인증 인증 진입점은 요청마다 저장소 행을 만든다. 외부인이 행 수를
        # 요청 수만큼 늘리지 못하게 클라이언트 주소 단위로 센다.
        self._auth_rate_limiter = RateLimiter(
            limit=AUTH_RATE_LIMIT,
            window=AUTH_RATE_WINDOW,
        )
        self._trusted_proxy_hops = settings.trusted_proxy_hops
        if settings.app_base_url.rstrip("/") != identity_service.policy.allowed_origin:
            raise ValueError("API and identity origins must match")

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Receive,
        send: Send,
    ) -> None:
        if scope.get("type") == "websocket":
            await self._realtime_server(scope, receive, send)
            return
        if scope.get("type") != "http":
            raise RuntimeError("IdentityApiApp supports HTTP and WebSocket ASGI scopes")
        request_id = self._new_request_id()
        try:
            request = await ApiRequest.from_asgi(scope, receive)
            request_id = self._request_id(request)
            response = self._handle(request, request_id)
        except (IdentityError, ApiError) as error:
            response = self._error_response(error, request_id)
        except Exception:  # noqa: BLE001 - never expose provider or storage exception text
            response = self._internal_error_response(request_id)
        await response.send_asgi(send)

    def _handle(self, request: ApiRequest, request_id: str) -> ApiResponse:
        if request.path in RATE_LIMITED_AUTH_PATHS and not self._auth_rate_limiter.allow(
            request.client_key(trusted_proxy_hops=self._trusted_proxy_hops),
            self._clock.now(),
        ):
            raise RateLimited()
        if request.path == "/auth/google" and request.method == "GET":
            return self._begin_login(request)
        if request.path == "/auth/google/callback" and request.method == "GET":
            return self._complete_login(request, request_id)
        if request.path == "/auth/session" and request.method == "GET":
            return self._session(request, request_id)
        if request.path == "/auth/logout" and request.method == "POST":
            return self._logout(request, request_id)
        if request.path == "/v1/auth/realtime-ticket" and request.method == "POST":
            return self._realtime_ticket(request, request_id)
        if request.path == "/v1/market/session" and request.method == "GET":
            return self._market_session(request, request_id)
        if request.path == "/v1/themes/rankings" and request.method == "GET":
            return self._theme_rankings(request, request_id)
        if request.path == "/v1/insights/treemap" and request.method == "GET":
            return self._theme_treemap(request, request_id)
        if request.path == "/v1/daily/movers" and request.method == "GET":
            return self._day_movers(request, request_id)
        match = _THEME_EVENT_PATH.fullmatch(request.path)
        if match is not None and request.method == "GET":
            return self._theme_event(
                request,
                request_id,
                unquote(match.group(1)),
                unquote(match.group(2)),
            )
        match = _EVENT_EVIDENCE_PATH.fullmatch(request.path)
        if match is not None and request.method == "GET":
            return self._event_evidence(request, request_id, unquote(match.group(1)))
        match = _SIMILAR_EVENTS_PATH.fullmatch(request.path)
        if match is not None and request.method == "GET":
            return self._similar_events(request, request_id, unquote(match.group(1)))
        match = _HISTORICAL_EVENT_PATH.fullmatch(request.path)
        if match is not None and request.method == "GET":
            return self._historical_event(
                request,
                request_id,
                unquote(match.group(1)),
            )
        if request.path == "/v1/me/saved" and request.method == "GET":
            return self._list_saved(request, request_id)
        match = _SAVED_PATH.fullmatch(request.path)
        if match is not None and request.method in {"PUT", "DELETE"}:
            return self._mutate_saved(request, request_id, match.group(1), match.group(2))
        if request.path == "/v1/me" and request.method == "DELETE":
            return self._delete_account(request, request_id)
        if request.path.startswith("/v1/operator"):
            return self._operator_route(request, request_id)
        if request.path.startswith("/v1/"):
            self.identity_service.require_authenticated(request.cookies.get(SESSION_COOKIE))
        return self._not_found(request_id)

    def _begin_login(self, request: ApiRequest) -> ApiResponse:
        request.require_query_keys({"returnTo"})
        request.require_empty_body()
        started = self.identity_service.begin_google_login(request.query_value("returnTo"))
        now = self._clock.now()
        response = ApiResponse.redirect(started.authorization_url)
        response.add_cookie(
            oauth_state_cookie(
                started.browser_nonce,
                now=now,
                expires_at=started.expires_at,
            )
        )
        return response

    def _complete_login(self, request: ApiRequest, request_id: str) -> ApiResponse:
        try:
            # 실제 구글은 code·state 외에 scope·authuser·prompt를 덧붙이고,
            # 워크스페이스 계정은 hd, 사용자가 거부하면 error 계열이 온다.
            # fixture callback 기준으로 닫으면 실로그인이 전부 거부된다.
            # 값은 여전히 code·state만 읽는다.
            request.require_query_keys(
                {
                    "code",
                    "state",
                    "scope",
                    "authuser",
                    "prompt",
                    "hd",
                    "iss",  # RFC 9207 발급자 표식 — 실측에서 확인
                    "error",
                    "error_description",
                }
            )
            request.require_empty_body()
            code = request.query_value("code")
            state = request.query_value("state")
            browser_nonce = request.cookies.get(OAUTH_STATE_COOKIE)
            if code is None or state is None or browser_nonce is None:
                raise InvalidRequest(
                    "로그인 요청을 확인할 수 없습니다. 다시 로그인해 주세요.",
                    reason_code="OAUTH_CALLBACK_REJECTED",
                )
            completion = self.identity_service.complete_google_login(
                code=code,
                state=state,
                browser_nonce=browser_nonce,
                current_session_token=request.cookies.get(SESSION_COOKIE),
            )
        except IdentityError as error:
            response = self._error_response(error, request_id)
            response.add_cookie(expire_oauth_state_cookie())
            return response

        now = self._clock.now()
        location = f"{self._settings.app_base_url}{completion.return_to}"
        response = ApiResponse.redirect(location)
        response.add_cookie(expire_oauth_state_cookie())
        response.add_cookie(
            session_cookie(
                completion.session_token,
                now=now,
                expires_at=completion.expires_at,
            )
        )
        response.add_cookie(
            csrf_cookie(
                completion.csrf_token,
                now=now,
                expires_at=completion.expires_at,
            )
        )
        return response

    def _session(self, request: ApiRequest, request_id: str) -> ApiResponse:
        request.require_query_keys(set())
        request.require_empty_body()
        principal = self.identity_service.authenticate(request.cookies.get(SESSION_COOKIE))
        if principal is None:
            data: JsonObject = {"authenticated": False, "user": None, "roles": []}
        else:
            roles = sorted(principal.roles, key=_ROLE_ORDER.__getitem__)
            data = {
                "authenticated": True,
                "user": {"displayName": principal.user.display_name},
                "roles": [role.value for role in roles],
            }
        return self._success(200, data, request_id)

    def _logout(self, request: ApiRequest, request_id: str) -> ApiResponse:
        request.require_query_keys(set())
        request.require_empty_body()
        self.identity_service.logout(
            session_token=request.cookies.get(SESSION_COOKIE),
            origin=request.header("origin"),
            csrf_token=request.header("x-csrf-token"),
            csrf_cookie=request.cookies.get(CSRF_COOKIE),
        )
        response = self._success(200, {"loggedOut": True}, request_id)
        response.add_cookie(expire_session_cookie())
        response.add_cookie(expire_csrf_cookie())
        return response

    def _realtime_ticket(self, request: ApiRequest, request_id: str) -> ApiResponse:
        request.require_query_keys(set())
        request.require_empty_body()
        ticket = self.identity_service.issue_realtime_ticket(
            session_token=request.cookies.get(SESSION_COOKIE),
            origin=request.header("origin"),
            csrf_token=request.header("x-csrf-token"),
            csrf_cookie=request.cookies.get(CSRF_COOKIE),
        )
        return self._success(
            200,
            {"ticket": ticket.ticket, "expiresAt": ticket.expires_at},
            request_id,
        )

    def _market_session(self, request: ApiRequest, request_id: str) -> ApiResponse:
        self.identity_service.require_authenticated(request.cookies.get(SESSION_COOKIE))
        request.require_query_keys(set())
        request.require_empty_body()
        document = self._product_repository.market_session()
        if document is None:
            raise ProductDataUnavailable
        return self._document_response(document, request_id)

    def _theme_rankings(self, request: ApiRequest, request_id: str) -> ApiResponse:
        self.identity_service.require_authenticated(request.cookies.get(SESSION_COOKIE))
        request.require_query_keys({"limit", "marketDate"})
        request.require_empty_body()
        limit = self._query_limit(request, default=10, maximum=50)
        market_date = request.query_value("marketDate")
        if market_date is not None:
            self._validate_market_date(market_date)
        document = self._product_repository.rankings(market_date)
        if document is None:
            if market_date is not None:
                raise UnsupportedMarketDate
            raise ProductDataUnavailable
        return self._document_response(
            self._limit_items(document, limit=limit),
            request_id,
        )

    def _theme_treemap(self, request: ApiRequest, request_id: str) -> ApiResponse:
        self.identity_service.require_authenticated(request.cookies.get(SESSION_COOKIE))
        request.require_query_keys({"limit"})
        request.require_empty_body()
        limit = self._query_limit(request, default=12, maximum=12)
        document = self._product_repository.treemap()
        if document is None:
            raise ProductDataUnavailable
        return self._document_response(
            self._limit_items(document, limit=limit),
            request_id,
        )

    def _day_movers(self, request: ApiRequest, request_id: str) -> ApiResponse:
        self.identity_service.require_authenticated(request.cookies.get(SESSION_COOKIE))
        request.require_query_keys({"date"})
        request.require_empty_body()
        raw_date = request.query_value("date")
        if raw_date is None:
            raise InvalidApiRequest("조회할 날짜를 지정해 주세요.")
        self._validate_market_date(raw_date)
        if self._daily_reader is None:
            raise ProductDataUnavailable
        movers = self._daily_reader.day_movers(date.fromisoformat(raw_date))
        if movers.status == "NO_RECORD":
            raise ProductResourceNotFound
        return self._success(200, _day_movers_data(movers), request_id)

    def _theme_event(
        self,
        request: ApiRequest,
        request_id: str,
        theme_id: str,
        event_id: str,
    ) -> ApiResponse:
        self.identity_service.require_authenticated(request.cookies.get(SESSION_COOKIE))
        request.require_query_keys(set())
        request.require_empty_body()
        self._validate_identifier(theme_id)
        self._validate_identifier(event_id)
        document = self._product_repository.theme_event(theme_id, event_id)
        if document is None:
            if self._product_repository.theme_for_event(event_id) is not None:
                raise ResourceIdMismatch("themeId")
            raise ProductResourceNotFound
        return self._document_response(document, request_id)

    def _event_evidence(
        self,
        request: ApiRequest,
        request_id: str,
        event_id: str,
    ) -> ApiResponse:
        self.identity_service.require_authenticated(request.cookies.get(SESSION_COOKIE))
        request.require_query_keys({"cursor", "limit"})
        request.require_empty_body()
        self._validate_identifier(event_id)
        cursor = self._query_cursor(request)
        limit = self._query_limit(request, default=20, maximum=100)
        document = self._product_repository.evidence(event_id, cursor)
        if document is None:
            if cursor is not None:
                raise InvalidApiRequest("다음 페이지 정보를 확인해 주세요.")
            raise ProductResourceNotFound
        return self._document_response(
            self._limit_page(document, limit=limit),
            request_id,
        )

    def _similar_events(
        self,
        request: ApiRequest,
        request_id: str,
        event_id: str,
    ) -> ApiResponse:
        principal = self.identity_service.require_authenticated(
            request.cookies.get(SESSION_COOKIE)
        )
        request.require_query_keys(
            {"horizonTradingDays", "sort", "cursor", "limit"}
        )
        request.require_empty_body()
        self._validate_identifier(event_id)
        horizon = request.query_value("horizonTradingDays")
        if horizon is not None and horizon not in {"1", "5", "20"}:
            raise InvalidApiRequest("조회 기간을 확인해 주세요.")
        sort = request.query_value("sort") or "relevance"
        if sort not in {"relevance", "eventDate"}:
            raise InvalidApiRequest("정렬 기준을 확인해 주세요.")
        cursor = self._query_cursor(request)
        limit = self._query_limit(request, default=20, maximum=100)
        document = self._product_repository.similar_events(event_id, cursor)
        if document is None:
            if cursor is not None:
                raise InvalidApiRequest("다음 페이지 정보를 확인해 주세요.")
            raise ProductResourceNotFound
        availability = document.data.get("availability")
        if availability == "AVAILABLE" and Role.HISTORICAL_PILOT not in principal.roles:
            raise FeatureNotEntitled(
                "과거 유사사례 기능을 사용할 권한이 없습니다.",
                reason_code="HISTORICAL_PILOT_REQUIRED",
            )
        return self._document_response(
            self._limit_page(document, limit=limit),
            request_id,
        )

    def _historical_event(
        self,
        request: ApiRequest,
        request_id: str,
        event_id: str,
    ) -> ApiResponse:
        principal = self.identity_service.require_authenticated(
            request.cookies.get(SESSION_COOKIE)
        )
        request.require_query_keys({"contextEventId"})
        request.require_empty_body()
        self._validate_identifier(event_id)
        context_event_id = request.query_value("contextEventId")
        if context_event_id is not None:
            self._validate_identifier(context_event_id)
        if Role.HISTORICAL_PILOT not in principal.roles:
            raise FeatureNotEntitled(
                "과거 이벤트 기능을 사용할 권한이 없습니다.",
                reason_code="HISTORICAL_PILOT_REQUIRED",
            )
        document = self._product_repository.historical_event(event_id)
        if document is None:
            raise ProductResourceNotFound
        return self._document_response(document, request_id)

    def _document_response(
        self,
        document: ProductDocument,
        request_id: str,
    ) -> ApiResponse:
        return self._success(
            200,
            document.copy_data(),
            request_id,
            market_context=document.copy_market_context(),
            versions=document.copy_versions(),
        )

    @staticmethod
    def _limit_items(document: ProductDocument, *, limit: int) -> ProductDocument:
        data = document.copy_data()
        items = data.get("items")
        if not isinstance(items, list):
            raise TypeError("product snapshot items must be a list")
        data["items"] = items[:limit]
        return ProductDocument(
            data,
            document.copy_market_context(),
            document.copy_versions(),
        )

    @staticmethod
    def _limit_page(document: ProductDocument, *, limit: int) -> ProductDocument:
        data = document.copy_data()
        items = data.get("items")
        page = data.get("page")
        if not isinstance(items, list) or not isinstance(page, dict):
            raise TypeError("paginated product document is invalid")
        if len(items) > limit and page.get("nextCursor") is None:
            raise ValueError("paginated fixture needs a next cursor before slicing")
        data["items"] = items[:limit]
        page["limit"] = limit
        return ProductDocument(
            data,
            document.copy_market_context(),
            document.copy_versions(),
        )

    @staticmethod
    def _query_limit(
        request: ApiRequest,
        *,
        default: int,
        maximum: int,
    ) -> int:
        raw = request.query_value("limit")
        if raw is None:
            return default
        if re.fullmatch(r"[1-9][0-9]*", raw) is None:
            raise InvalidApiRequest("조회 개수를 확인해 주세요.")
        limit = int(raw)
        if limit > maximum:
            raise InvalidApiRequest("조회 개수를 확인해 주세요.")
        return limit

    @staticmethod
    def _query_cursor(request: ApiRequest) -> str | None:
        cursor = request.query_value("cursor")
        if cursor is None:
            return None
        if (
            not 1 <= len(cursor) <= 4096
            or cursor != cursor.strip()
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in cursor)
        ):
            raise InvalidApiRequest("다음 페이지 정보를 확인해 주세요.")
        return cursor

    @staticmethod
    def _validate_market_date(value: str) -> None:
        try:
            parsed = date.fromisoformat(value)
        except ValueError as error:
            raise InvalidApiRequest("거래일 형식을 확인해 주세요.") from error
        if parsed.isoformat() != value:
            raise InvalidApiRequest("거래일 형식을 확인해 주세요.")

    @staticmethod
    def _validate_identifier(value: str) -> None:
        if (
            not 1 <= len(value) <= 128
            or value != value.strip()
            or any(
                ord(character) < 0x20
                or ord(character) == 0x7F
                or character in {"/", "\\"}
                for character in value
            )
        ):
            raise InvalidApiRequest("식별자 형식을 확인해 주세요.")

    def _list_saved(self, request: ApiRequest, request_id: str) -> ApiResponse:
        request.require_query_keys({"type", "cursor", "limit"})
        request.require_empty_body()
        raw_type = request.query_value("type") or "ALL"
        if raw_type == "ALL":
            saved_type = None
        else:
            try:
                saved_type = SavedType(raw_type)
            except ValueError as error:
                raise InvalidRequest("관심 목록 유형이 올바르지 않습니다.") from error
        raw_limit = request.query_value("limit")
        try:
            limit = 20 if raw_limit is None else int(raw_limit)
        except ValueError as error:
            raise InvalidRequest("관심 목록 조회 개수가 올바르지 않습니다.") from error
        page = self.identity_service.list_saved_items(
            session_token=request.cookies.get(SESSION_COOKIE),
            saved_type=saved_type,
            cursor=request.query_value("cursor"),
            limit=limit,
        )
        items: list[JsonValue] = []
        for item in page.items:
            current_state: JsonValue = None
            if item.current_state is not None:
                current_state = {
                    "eventId": item.current_state.event_id,
                    "eventState": item.current_state.event_state,
                    "weightedReturn": item.current_state.weighted_return,
                    "dataStatus": item.current_state.data_status,
                    "asOf": item.current_state.as_of,
                }
            items.append(
                {
                    "savedType": item.saved_type.value,
                    "targetId": item.target_id,
                    "displayName": item.display_name,
                    "savedAt": item.saved_at,
                    "availability": item.availability.value,
                    "unavailableReason": item.unavailable_reason,
                    "currentState": current_state,
                }
            )
        return self._success(
            200,
            {
                "items": items,
                "page": {
                    "nextCursor": page.next_cursor,
                    "hasMore": page.has_more,
                    "limit": page.limit,
                },
            },
            request_id,
        )

    def _mutate_saved(
        self,
        request: ApiRequest,
        request_id: str,
        collection: str,
        raw_target_id: str,
    ) -> ApiResponse:
        request.require_query_keys(set())
        request.require_empty_body()
        target_id = unquote(raw_target_id)
        saved_type = {
            "themes": SavedType.THEME,
            "stocks": SavedType.STOCK,
            "events": SavedType.EVENT,
        }[collection]
        if request.method == "PUT":
            result = self.identity_service.save_item(
                session_token=request.cookies.get(SESSION_COOKIE),
                origin=request.header("origin"),
                csrf_token=request.header("x-csrf-token"),
                csrf_cookie=request.cookies.get(CSRF_COOKIE),
                saved_type=saved_type,
                target_id=target_id,
            )
        else:
            result = self.identity_service.unsave_item(
                session_token=request.cookies.get(SESSION_COOKIE),
                origin=request.header("origin"),
                csrf_token=request.header("x-csrf-token"),
                csrf_cookie=request.cookies.get(CSRF_COOKIE),
                saved_type=saved_type,
                target_id=target_id,
            )
        return self._success(
            200,
            {
                "savedType": result.saved_type.value,
                "targetId": result.target_id,
                "saved": result.saved,
                "savedAt": result.saved_at,
            },
            request_id,
        )

    def _delete_account(self, request: ApiRequest, request_id: str) -> ApiResponse:
        request.require_query_keys(set())
        request.require_empty_body()
        self.identity_service.delete_account(
            session_token=request.cookies.get(SESSION_COOKIE),
            origin=request.header("origin"),
            csrf_token=request.header("x-csrf-token"),
            csrf_cookie=request.cookies.get(CSRF_COOKIE),
        )
        response = self._success(202, {"status": "DELETION_STARTED"}, request_id)
        response.add_cookie(expire_session_cookie())
        response.add_cookie(expire_csrf_cookie())
        return response

    def _operator_route(self, request: ApiRequest, request_id: str) -> ApiResponse:
        session_token = request.cookies.get(SESSION_COOKIE)
        self.identity_service.require_operator(session_token)
        if request.path == "/v1/operator/status" and request.method == "GET":
            request.require_query_keys(set())
            request.require_empty_body()
            status = self._operator_boundary.status(session_token)
            return self._success(200, status, request_id)
        if request.path == "/v1/operator/jobs" and request.method == "GET":
            request.require_query_keys({"status", "cursor", "limit"})
            request.require_empty_body()
            jobs = self._operator_boundary.list_jobs(
                session_token,
                status=request.query_value("status"),
                cursor=self._query_cursor(request),
                limit=self._query_limit(request, default=10, maximum=50),
            )
            return self._success(200, jobs, request_id)
        if request.path == "/v1/operator/reviews" and request.method == "GET":
            request.require_query_keys({"type", "status", "cursor", "limit"})
            request.require_empty_body()
            reviews = self._operator_boundary.list_reviews(
                session_token,
                review_type=request.query_value("type"),
                review_status=request.query_value("status"),
                cursor=self._query_cursor(request),
                limit=self._query_limit(request, default=10, maximum=50),
            )
            return self._success(200, reviews, request_id)
        if request.path == "/v1/operator/audit" and request.method == "GET":
            request.require_query_keys({"cursor", "limit"})
            request.require_empty_body()
            audit = self._operator_boundary.audit(
                session_token,
                cursor=self._query_cursor(request),
                limit=self._query_limit(request, default=10, maximum=50),
            )
            return self._success(200, audit, request_id)
        if (
            request.path == "/v1/operator/infostock/auth-status"
            and request.method == "GET"
        ):
            request.require_query_keys(set())
            request.require_empty_body()
            auth_status = self._operator_boundary.infostock_auth_status(session_token)
            return self._success(200, auth_status, request_id)
        match = _OPERATOR_JOB_PATH.fullmatch(request.path)
        if match is not None:
            return self._operator_job(
                request,
                request_id,
                session_token,
                unquote(match.group(1)),
                match.group(2),
            )
        match = _OPERATOR_REVIEW_PATH.fullmatch(request.path)
        if match is not None:
            return self._operator_review(
                request,
                request_id,
                session_token,
                unquote(match.group(1)),
                match.group(2),
            )
        return self._not_found(request_id)

    def _operator_job(
        self,
        request: ApiRequest,
        request_id: str,
        session_token: str | None,
        run_id: str,
        command: str | None,
    ) -> ApiResponse:
        self._validate_identifier(run_id)
        request.require_query_keys(set())
        if command is None and request.method == "GET":
            request.require_empty_body()
            job = self._operator_boundary.job(session_token, run_id)
            return self._success(200, job, request_id)
        if command is not None and request.method == "POST":
            handler = (
                self._operator_boundary.retry_job
                if command == "retry"
                else self._operator_boundary.resume_job
            )
            receipt = handler(
                session_token,
                run_id,
                origin=request.header("origin"),
                csrf_token=request.header("x-csrf-token"),
                csrf_cookie=request.cookies.get(CSRF_COOKIE),
                idempotency_key=request.header("idempotency-key"),
                body=request.body,
                now=self._clock.now(),
            )
            return self._success(200, receipt, request_id)
        return self._not_found(request_id)

    def _operator_review(
        self,
        request: ApiRequest,
        request_id: str,
        session_token: str | None,
        review_id: str,
        command: str | None,
    ) -> ApiResponse:
        self._validate_identifier(review_id)
        request.require_query_keys(set())
        if command is None and request.method == "GET":
            request.require_empty_body()
            review = self._operator_boundary.review(session_token, review_id)
            return self._success(200, review, request_id)
        if command is not None and request.method == "POST":
            receipt = self._operator_boundary.resolve_review(
                session_token,
                review_id,
                origin=request.header("origin"),
                csrf_token=request.header("x-csrf-token"),
                csrf_cookie=request.cookies.get(CSRF_COOKIE),
                idempotency_key=request.header("idempotency-key"),
                body=request.body,
                now=self._clock.now(),
            )
            return self._success(200, receipt, request_id)
        return self._not_found(request_id)

    def _success(
        self,
        status_code: int,
        data: JsonObject,
        request_id: str,
        *,
        market_context: JsonObject | None = None,
        versions: JsonObject | None = None,
    ) -> ApiResponse:
        meta = self._meta(request_id)
        if market_context is not None:
            meta["marketContext"] = market_context
        if versions is not None:
            meta["versions"] = versions
        payload: JsonObject = {"data": data, "meta": meta}
        return ApiResponse.json(status_code, _json_ready(payload))

    def _error_response(
        self,
        error: IdentityError | ApiError,
        request_id: str,
    ) -> ApiResponse:
        details: JsonObject = {key: value for key, value in error.details.items()}
        payload: JsonObject = {
            "error": {
                "code": error.code,
                "message": error.message,
                "retryable": error.retryable,
                "details": details,
            },
            "meta": self._meta(request_id),
        }
        return ApiResponse.json(error.status_code, _json_ready(payload))

    def _internal_error_response(self, request_id: str) -> ApiResponse:
        payload: JsonObject = {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "요청을 처리하지 못했습니다.",
                "retryable": False,
                "details": {},
            },
            "meta": self._meta(request_id),
        }
        return ApiResponse.json(500, _json_ready(payload))

    def _not_found(self, request_id: str) -> ApiResponse:
        payload: JsonObject = {
            "error": {
                "code": "RESOURCE_NOT_FOUND",
                "message": "요청한 경로를 찾을 수 없습니다.",
                "retryable": False,
                "details": {},
            },
            "meta": self._meta(request_id),
        }
        return ApiResponse.json(404, _json_ready(payload))

    def _meta(self, request_id: str) -> JsonObject:
        return {
            "requestId": request_id,
            "apiVersion": "1",
            "schemaVersion": self._settings.schema_version,
            "generatedAt": self._clock.now(),
        }

    @staticmethod
    def _new_request_id() -> str:
        return f"req_{secrets.token_urlsafe(12)}"

    def _request_id(self, request: ApiRequest) -> str:
        candidate = request.header("x-request-id")
        if candidate is None or not 1 <= len(candidate) <= 128:
            return self._new_request_id()
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in candidate):
            return self._new_request_id()
        return candidate


@dataclass(frozen=True, slots=True)
class FixtureIdentityEnvironment:
    app: IdentityApiApp
    service: IdentityService
    repository: InMemoryIdentityRepository
    oauth_provider: FixtureGoogleOAuthProvider
    target_catalog: TargetCatalog
    product_repository: ProductReadRepository
    realtime_hub: RealtimeSnapshotHub
    operator_repository: InMemoryOperatorRepository


def create_app(
    *,
    identity_service: IdentityService,
    operator_status_source: OperatorStatusSource,
    settings: ApiSettings,
    product_repository: ProductReadRepository | None = None,
    daily_reader: DailyFeaturedReader | None = None,
    realtime_hub: RealtimeSnapshotHub | None = None,
    operator_repository: OperatorRepository | None = None,
    clock: Clock | None = None,
) -> IdentityApiApp:
    return IdentityApiApp(
        identity_service=identity_service,
        operator_boundary=OperatorBoundary(
            identity_service=identity_service,
            status_source=operator_status_source,
            repository=operator_repository,
        ),
        settings=settings,
        product_repository=product_repository,
        daily_reader=daily_reader,
        realtime_hub=realtime_hub,
        clock=clock,
    )


def create_fixture_app(
    *,
    settings: ApiSettings | None = None,
    clock: Clock | None = None,
    targets: tuple[TargetRecord, ...] = (),
    target_catalog: TargetCatalog | None = None,
    operator_status: RuntimeOperatorStatus | None = None,
    product_repository: ProductReadRepository | None = None,
    daily_reader: DailyFeaturedReader | None = None,
    realtime_hub: RealtimeSnapshotHub | None = None,
    operator_repository: InMemoryOperatorRepository | None = None,
) -> FixtureIdentityEnvironment:
    effective_settings = settings or ApiSettings()
    effective_clock = clock or SystemClock()
    repository = InMemoryIdentityRepository()
    effective_target_catalog: TargetCatalog = (
        InMemoryTargetCatalog(targets) if target_catalog is None else target_catalog
    )
    # `DAYJAVIEW_FIXTURE_LOGIN_BOUNCE=1`이면 동의 화면 없이 바로 로그인된다. 실제 구글이 없는
    # 로컬에서 화면을 보기 위한 것으로, fixture 앱에만 있다.
    oauth_provider = FixtureGoogleOAuthProvider(
        expected_redirect_uri=effective_settings.identity_policy().oauth_redirect_uri,
        bounce_code=(
            "fixture-demo-login"
            if os.environ.get("DAYJAVIEW_FIXTURE_LOGIN_BOUNCE") == "1"
            else ""
        ),
    )
    service = IdentityService(
        repository=repository,
        oauth_provider=oauth_provider,
        target_catalog=effective_target_catalog,
        policy=effective_settings.identity_policy(),
        clock=effective_clock,
    )
    runtime_status = operator_status or RuntimeOperatorStatus(
        deployment_version="fixture",
        commit="fixture",
        started_at=effective_clock.now(),
        services=(),
    )
    effective_product_repository = (
        product_repository or InMemoryProductReadRepository()
    )
    effective_realtime_hub = realtime_hub or RealtimeSnapshotHub()
    effective_operator_repository = operator_repository or InMemoryOperatorRepository()
    app = create_app(
        identity_service=service,
        operator_status_source=StaticOperatorStatusSource(runtime_status),
        settings=effective_settings,
        product_repository=effective_product_repository,
        daily_reader=daily_reader,
        realtime_hub=effective_realtime_hub,
        operator_repository=effective_operator_repository,
        clock=effective_clock,
    )
    return FixtureIdentityEnvironment(
        app,
        service,
        repository,
        oauth_provider,
        effective_target_catalog,
        effective_product_repository,
        effective_realtime_hub,
        effective_operator_repository,
    )


def _json_ready(value: JsonValue) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("response timestamps must be timezone-aware")
        normalized = value.astimezone(UTC)
        return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("response decimals must be finite")
        if value == value.to_integral_value():
            return int(value)
        converted = float(value)
        if converted in {float("inf"), float("-inf")}:
            raise ValueError("response decimal is outside the JSON number range")
        return converted
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
