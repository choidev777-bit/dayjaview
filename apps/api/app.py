from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

if TYPE_CHECKING:
    from identity import (
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
        TargetRecord,
    )
    from identity.security import Clock
else:
    from packages.identity import (
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
        TargetRecord,
    )
    from packages.identity.security import Clock

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
from .http import ApiRequest, ApiResponse, Receive, Send
from .operator_boundary import (
    OperatorBoundary,
    OperatorStatusSource,
    StaticOperatorStatusSource,
)

_SAVED_PATH = re.compile(r"^/v1/me/saved/(themes|stocks|events)/([^/]+)$")
_ROLE_ORDER = {Role.USER: 0, Role.HISTORICAL_PILOT: 1, Role.OPERATOR: 2}


class IdentityApiApp:
    def __init__(
        self,
        *,
        identity_service: IdentityService,
        operator_boundary: OperatorBoundary,
        settings: ApiSettings,
        clock: Clock | None = None,
    ) -> None:
        self.identity_service = identity_service
        self._operator_boundary = operator_boundary
        self._settings = settings
        self._clock = clock or SystemClock()
        if settings.app_base_url.rstrip("/") != identity_service.policy.allowed_origin:
            raise ValueError("API and identity origins must match")

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Receive,
        send: Send,
    ) -> None:
        if scope.get("type") != "http":
            raise RuntimeError("IdentityApiApp supports HTTP ASGI scopes only")
        request_id = self._new_request_id()
        try:
            request = await ApiRequest.from_asgi(scope, receive)
            request_id = self._request_id(request)
            response = self._handle(request, request_id)
        except IdentityError as error:
            response = self._error_response(error, request_id)
        except Exception:  # noqa: BLE001 - never expose provider or storage exception text
            response = self._internal_error_response(request_id)
        await response.send_asgi(send)

    def _handle(self, request: ApiRequest, request_id: str) -> ApiResponse:
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
            request.require_query_keys({"code", "state"})
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
        if request.path == "/v1/operator/status" and request.method == "GET":
            request.require_query_keys(set())
            request.require_empty_body()
            status = self._operator_boundary.status(session_token)
            return self._success(200, status, request_id)
        self.identity_service.require_operator(session_token)
        return self._not_found(request_id)

    def _success(
        self,
        status_code: int,
        data: JsonObject,
        request_id: str,
    ) -> ApiResponse:
        payload: JsonObject = {"data": data, "meta": self._meta(request_id)}
        return ApiResponse.json(status_code, _json_ready(payload))

    def _error_response(self, error: IdentityError, request_id: str) -> ApiResponse:
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
    target_catalog: InMemoryTargetCatalog


def create_app(
    *,
    identity_service: IdentityService,
    operator_status_source: OperatorStatusSource,
    settings: ApiSettings,
    clock: Clock | None = None,
) -> IdentityApiApp:
    return IdentityApiApp(
        identity_service=identity_service,
        operator_boundary=OperatorBoundary(
            identity_service=identity_service,
            status_source=operator_status_source,
        ),
        settings=settings,
        clock=clock,
    )


def create_fixture_app(
    *,
    settings: ApiSettings | None = None,
    clock: Clock | None = None,
    targets: tuple[TargetRecord, ...] = (),
    operator_status: RuntimeOperatorStatus | None = None,
) -> FixtureIdentityEnvironment:
    effective_settings = settings or ApiSettings()
    effective_clock = clock or SystemClock()
    repository = InMemoryIdentityRepository()
    target_catalog = InMemoryTargetCatalog(targets)
    oauth_provider = FixtureGoogleOAuthProvider(
        expected_redirect_uri=effective_settings.identity_policy().oauth_redirect_uri
    )
    service = IdentityService(
        repository=repository,
        oauth_provider=oauth_provider,
        target_catalog=target_catalog,
        policy=effective_settings.identity_policy(),
        clock=effective_clock,
    )
    runtime_status = operator_status or RuntimeOperatorStatus(
        deployment_version="fixture",
        commit="fixture",
        started_at=effective_clock.now(),
        services=(),
    )
    app = create_app(
        identity_service=service,
        operator_status_source=StaticOperatorStatusSource(runtime_status),
        settings=effective_settings,
        clock=effective_clock,
    )
    return FixtureIdentityEnvironment(app, service, repository, oauth_provider, target_catalog)


def _json_ready(value: JsonValue) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("response timestamps must be timezone-aware")
        normalized = value.astimezone(UTC)
        return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
