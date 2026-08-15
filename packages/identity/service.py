from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

from .errors import (
    AuthenticationRequired,
    CsrfValidationFailed,
    FeatureNotEntitled,
    InvalidCursor,
    InvalidRequest,
    OAuthCallbackRejected,
    RecentAuthenticationRequired,
    ResourceNotFound,
)
from .models import (
    Availability,
    GoogleIdentity,
    LoginCompletion,
    OAuthLoginStart,
    OAuthStateRecord,
    RealtimeTicket,
    RealtimeTicketRecord,
    Role,
    SavedItem,
    SavedMutation,
    SavedPage,
    SavedRecord,
    SavedType,
    SessionPrincipal,
    SessionRecord,
)
from .oauth import GoogleOAuthProvider, OAuthProviderError
from .repository import IdentityRepository
from .security import (
    Clock,
    CursorPosition,
    SecureTokenSource,
    SignedCursorCodec,
    SystemClock,
    TokenSource,
    constant_time_equal,
    normalize_email,
    token_hash,
    validate_internal_return_to,
)
from .targets import TargetCatalog


@dataclass(frozen=True, slots=True)
class IdentityPolicy:
    app_base_url: str = "https://dayjaview.vercel.app"
    oauth_callback_path: str = "/api/auth/google/callback"
    default_return_to: str = "/today"
    session_ttl: timedelta = timedelta(hours=8)
    oauth_state_ttl: timedelta = timedelta(minutes=10)
    realtime_ticket_ttl: timedelta = timedelta(seconds=30)
    recent_authentication_window: timedelta = timedelta(minutes=10)
    operator_bootstrap_emails: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        parsed = urlsplit(self.app_base_url)
        # 브라우저가 신뢰 가능한 컨텍스트로 취급하는 localhost만 http를 허용한다.
        local_http = parsed.scheme == "http" and parsed.hostname in (
            "localhost",
            "127.0.0.1",
        )
        if (
            (parsed.scheme != "https" and not local_http)
            or not parsed.netloc
            or parsed.path not in ("", "/")
        ):
            raise ValueError("app_base_url must be an HTTPS origin or http://localhost")
        if parsed.query or parsed.fragment:
            raise ValueError("app_base_url must not contain a query or fragment")
        if validate_internal_return_to(
            self.default_return_to,
            fallback="",
        ) != self.default_return_to:
            raise ValueError("default_return_to must be an internal path")
        if not self.oauth_callback_path.startswith("/"):
            raise ValueError("oauth_callback_path must be absolute")
        for duration in (
            self.session_ttl,
            self.oauth_state_ttl,
            self.realtime_ticket_ttl,
            self.recent_authentication_window,
        ):
            if duration <= timedelta(0):
                raise ValueError("identity durations must be positive")
        object.__setattr__(
            self,
            "operator_bootstrap_emails",
            frozenset(normalize_email(email) for email in self.operator_bootstrap_emails),
        )

    @property
    def allowed_origin(self) -> str:
        return self.app_base_url.rstrip("/")

    @property
    def oauth_redirect_uri(self) -> str:
        return f"{self.allowed_origin}{self.oauth_callback_path}"


class IdentityService:
    def __init__(
        self,
        *,
        repository: IdentityRepository,
        oauth_provider: GoogleOAuthProvider,
        target_catalog: TargetCatalog,
        policy: IdentityPolicy | None = None,
        clock: Clock | None = None,
        token_source: TokenSource | None = None,
        cursor_secret: bytes | None = None,
    ) -> None:
        self._repository = repository
        self._oauth_provider = oauth_provider
        self._target_catalog = target_catalog
        self.policy = policy or IdentityPolicy()
        self._clock = clock or SystemClock()
        self._tokens = token_source or SecureTokenSource()
        self._cursor_codec = SignedCursorCodec(cursor_secret or secrets.token_bytes(32))

    def begin_google_login(self, return_to: str | None) -> OAuthLoginStart:
        now = self._clock.now()
        safe_return_to = validate_internal_return_to(
            return_to,
            fallback=self.policy.default_return_to,
        )
        raw_state = self._tokens.create(32)
        browser_nonce = self._tokens.create(32)
        expires_at = now + self.policy.oauth_state_ttl
        self._repository.store_oauth_state(
            OAuthStateRecord(
                state_hash=token_hash(raw_state),
                browser_nonce_hash=token_hash(browser_nonce),
                return_to=safe_return_to,
                created_at=now,
                expires_at=expires_at,
            )
        )
        authorization_url = self._oauth_provider.authorization_url(
            state=raw_state,
            redirect_uri=self.policy.oauth_redirect_uri,
        )
        return OAuthLoginStart(authorization_url, browser_nonce, expires_at)

    def complete_google_login(
        self,
        *,
        code: str,
        state: str,
        browser_nonce: str,
        current_session_token: str | None = None,
    ) -> LoginCompletion:
        if (
            not 1 <= len(code) <= 2048
            or not 1 <= len(state) <= 512
            or not 1 <= len(browser_nonce) <= 512
        ):
            raise OAuthCallbackRejected
        now = self._clock.now()
        oauth_state = self._repository.consume_oauth_state(
            state_hash=token_hash(state),
            browser_nonce_hash=token_hash(browser_nonce),
            now=now,
        )
        if oauth_state is None:
            raise OAuthCallbackRejected
        try:
            identity = self._oauth_provider.exchange_code(
                code=code,
                redirect_uri=self.policy.oauth_redirect_uri,
            )
        except OAuthProviderError as error:
            raise OAuthCallbackRejected from error
        self._validate_google_identity(identity)
        identity = GoogleIdentity(
            subject=identity.subject,
            display_name=identity.display_name.strip(),
            email=None if identity.email is None else normalize_email(identity.email),
            email_verified=identity.email_verified,
        )

        user = self._repository.upsert_user(
            proposed_user_id=f"usr_{self._tokens.create(18)}",
            identity=identity,
            now=now,
        )
        self._repository.add_role(user.user_id, Role.USER)
        if self._is_bootstrap_operator(identity):
            self._repository.add_role(user.user_id, Role.OPERATOR)

        if current_session_token:
            self._repository.revoke_session(token_hash(current_session_token), now=now)

        raw_session_token = self._tokens.create(48)
        raw_csrf_token = self._tokens.create(32)
        expires_at = now + self.policy.session_ttl
        self._repository.store_session(
            SessionRecord(
                token_hash=token_hash(raw_session_token),
                user_id=user.user_id,
                csrf_token_hash=token_hash(raw_csrf_token),
                created_at=now,
                expires_at=expires_at,
                authenticated_at=now,
            )
        )
        return LoginCompletion(
            session_token=raw_session_token,
            csrf_token=raw_csrf_token,
            expires_at=expires_at,
            return_to=oauth_state.return_to,
            user=user,
            roles=self._repository.get_roles(user.user_id),
        )

    def authenticate(self, session_token: str | None) -> SessionPrincipal | None:
        if not session_token or len(session_token) > 512:
            return None
        return self._authenticate_session_hash(token_hash(session_token))

    def refresh_principal(
        self,
        principal: SessionPrincipal,
    ) -> SessionPrincipal | None:
        refreshed = self._authenticate_session_hash(principal.session_token_hash)
        if refreshed is None or refreshed.user.user_id != principal.user.user_id:
            return None
        return refreshed

    def _authenticate_session_hash(
        self,
        session_token_hash: str,
    ) -> SessionPrincipal | None:
        now = self._clock.now()
        record = self._repository.get_session(session_token_hash)
        if record is None or record.revoked_at is not None:
            return None
        if record.expires_at <= now:
            self._repository.revoke_session(session_token_hash, now=now)
            return None
        user = self._repository.get_user(record.user_id)
        if user is None:
            return None
        roles = self._repository.get_roles(user.user_id)
        if Role.USER not in roles:
            return None
        return SessionPrincipal(
            session_token_hash=session_token_hash,
            user=user,
            roles=roles,
            authenticated_at=record.authenticated_at,
            expires_at=record.expires_at,
        )

    def require_authenticated(self, session_token: str | None) -> SessionPrincipal:
        principal = self.authenticate(session_token)
        if principal is None:
            raise AuthenticationRequired
        return principal

    def require_operator(self, session_token: str | None) -> SessionPrincipal:
        principal = self.require_authenticated(session_token)
        if Role.OPERATOR not in principal.roles:
            raise FeatureNotEntitled
        return principal

    def logout(
        self,
        *,
        session_token: str | None,
        origin: str | None,
        csrf_token: str | None,
        csrf_cookie: str | None,
    ) -> None:
        principal = self.authenticate(session_token)
        self._validate_origin_and_csrf(
            principal=principal,
            origin=origin,
            csrf_token=csrf_token,
            csrf_cookie=csrf_cookie,
            allow_without_session=True,
        )
        if session_token:
            self._repository.revoke_session(token_hash(session_token), now=self._clock.now())

    def issue_realtime_ticket(
        self,
        *,
        session_token: str | None,
        origin: str | None,
        csrf_token: str | None,
        csrf_cookie: str | None,
    ) -> RealtimeTicket:
        principal = self._authorize_mutation(
            session_token=session_token,
            origin=origin,
            csrf_token=csrf_token,
            csrf_cookie=csrf_cookie,
        )
        now = self._clock.now()
        raw_ticket = self._tokens.create(32)
        expires_at = now + self.policy.realtime_ticket_ttl
        self._repository.store_realtime_ticket(
            RealtimeTicketRecord(
                ticket_hash=token_hash(raw_ticket),
                session_token_hash=principal.session_token_hash,
                user_id=principal.user.user_id,
                origin=self.policy.allowed_origin,
                created_at=now,
                expires_at=expires_at,
            )
        )
        return RealtimeTicket(raw_ticket, expires_at)

    def consume_realtime_ticket(
        self,
        *,
        ticket: str,
        origin: str,
    ) -> SessionPrincipal:
        if not constant_time_equal(origin, self.policy.allowed_origin):
            raise AuthenticationRequired
        consumed = self._repository.consume_realtime_ticket(
            ticket_hash=token_hash(ticket),
            origin=origin,
            now=self._clock.now(),
        )
        if consumed is None:
            raise AuthenticationRequired
        principal = self._authenticate_session_hash(consumed.session_token_hash)
        if principal is None or principal.user.user_id != consumed.user_id:
            raise AuthenticationRequired
        return principal

    def save_item(
        self,
        *,
        session_token: str | None,
        origin: str | None,
        csrf_token: str | None,
        csrf_cookie: str | None,
        saved_type: SavedType,
        target_id: str,
    ) -> SavedMutation:
        principal = self._authorize_mutation(
            session_token=session_token,
            origin=origin,
            csrf_token=csrf_token,
            csrf_cookie=csrf_cookie,
        )
        self._validate_target_id(target_id)
        target = self._target_catalog.get_target(saved_type, target_id)
        if target is None or target.availability is Availability.UNAVAILABLE:
            raise ResourceNotFound
        if target.required_role is not None and target.required_role not in principal.roles:
            raise FeatureNotEntitled
        record = self._repository.upsert_saved(
            SavedRecord(
                user_id=principal.user.user_id,
                saved_type=saved_type,
                target_id=target_id,
                display_name_snapshot=target.display_name,
                saved_at=self._clock.now(),
            )
        )
        return SavedMutation(saved_type, target_id, True, record.saved_at)

    def unsave_item(
        self,
        *,
        session_token: str | None,
        origin: str | None,
        csrf_token: str | None,
        csrf_cookie: str | None,
        saved_type: SavedType,
        target_id: str,
    ) -> SavedMutation:
        principal = self._authorize_mutation(
            session_token=session_token,
            origin=origin,
            csrf_token=csrf_token,
            csrf_cookie=csrf_cookie,
        )
        self._validate_target_id(target_id)
        self._repository.delete_saved(
            user_id=principal.user.user_id,
            saved_type=saved_type,
            target_id=target_id,
        )
        return SavedMutation(saved_type, target_id, False, None)

    def list_saved_items(
        self,
        *,
        session_token: str | None,
        saved_type: SavedType | None,
        cursor: str | None,
        limit: int,
    ) -> SavedPage:
        principal = self.require_authenticated(session_token)
        if not 1 <= limit <= 100:
            raise InvalidRequest("관심 목록은 한 번에 1개에서 100개까지 조회할 수 있습니다.")
        saved_filter = "ALL" if saved_type is None else saved_type.value
        position = None
        if cursor is not None:
            if not cursor or len(cursor) > 4096:
                raise InvalidCursor
            position = self._cursor_codec.decode(
                cursor,
                user_id=principal.user.user_id,
                saved_filter=saved_filter,
            )
        records = self._repository.list_saved(
            user_id=principal.user.user_id,
            saved_type=saved_type,
        )
        if position is not None:
            records = tuple(record for record in records if self._is_after(record, position))
        selected = records[: limit + 1]
        has_more = len(selected) > limit
        visible = selected[:limit]
        items = tuple(self._project_saved(record, principal.roles) for record in visible)
        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            next_cursor = self._cursor_codec.encode(
                user_id=principal.user.user_id,
                saved_filter=saved_filter,
                position=CursorPosition(last.saved_at, last.target_id, last.saved_type),
            )
        return SavedPage(items, next_cursor, has_more, limit)

    def delete_account(
        self,
        *,
        session_token: str | None,
        origin: str | None,
        csrf_token: str | None,
        csrf_cookie: str | None,
    ) -> None:
        principal = self._authorize_mutation(
            session_token=session_token,
            origin=origin,
            csrf_token=csrf_token,
            csrf_cookie=csrf_cookie,
        )
        if self._clock.now() - principal.authenticated_at > self.policy.recent_authentication_window:
            raise RecentAuthenticationRequired
        deleted = self._repository.delete_account(principal.user.user_id)
        if not deleted:
            raise AuthenticationRequired

    def _authorize_mutation(
        self,
        *,
        session_token: str | None,
        origin: str | None,
        csrf_token: str | None,
        csrf_cookie: str | None,
    ) -> SessionPrincipal:
        principal = self.require_authenticated(session_token)
        self._validate_origin_and_csrf(
            principal=principal,
            origin=origin,
            csrf_token=csrf_token,
            csrf_cookie=csrf_cookie,
            allow_without_session=False,
        )
        return principal

    def _validate_origin_and_csrf(
        self,
        *,
        principal: SessionPrincipal | None,
        origin: str | None,
        csrf_token: str | None,
        csrf_cookie: str | None,
        allow_without_session: bool,
    ) -> None:
        if origin is None or not constant_time_equal(origin, self.policy.allowed_origin):
            raise CsrfValidationFailed
        if csrf_token is None or not csrf_token or len(csrf_token) > 512:
            raise CsrfValidationFailed
        if csrf_cookie is not None and not constant_time_equal(csrf_token, csrf_cookie):
            raise CsrfValidationFailed
        if principal is None:
            if allow_without_session:
                return
            raise AuthenticationRequired
        if csrf_cookie is None:
            raise CsrfValidationFailed
        record = self._repository.get_session(principal.session_token_hash)
        if (
            record is None
            or record.revoked_at is not None
            or record.expires_at <= self._clock.now()
            or not constant_time_equal(record.csrf_token_hash, token_hash(csrf_token))
        ):
            raise CsrfValidationFailed

    def _project_saved(self, record: SavedRecord, roles: frozenset[Role]) -> SavedItem:
        target = self._target_catalog.get_target(record.saved_type, record.target_id)
        if target is None:
            return SavedItem(
                record.saved_type,
                record.target_id,
                record.display_name_snapshot,
                record.saved_at,
                Availability.UNAVAILABLE,
                "RESOURCE_NOT_FOUND",
                None,
            )
        if target.required_role is not None and target.required_role not in roles:
            return SavedItem(
                record.saved_type,
                record.target_id,
                record.display_name_snapshot,
                record.saved_at,
                Availability.UNAVAILABLE,
                "FEATURE_NOT_ENTITLED",
                None,
            )
        if target.availability is Availability.UNAVAILABLE:
            return SavedItem(
                record.saved_type,
                record.target_id,
                target.display_name,
                record.saved_at,
                Availability.UNAVAILABLE,
                target.unavailable_reason or "RESOURCE_NOT_FOUND",
                None,
            )
        return SavedItem(
            record.saved_type,
            record.target_id,
            target.display_name,
            record.saved_at,
            Availability.AVAILABLE,
            None,
            target.current_state,
        )

    @staticmethod
    def _is_after(record: SavedRecord, position: CursorPosition) -> bool:
        if record.saved_at != position.saved_at:
            return record.saved_at < position.saved_at
        return (record.target_id, record.saved_type.value) > (
            position.target_id,
            position.saved_type.value,
        )

    def _is_bootstrap_operator(self, identity: GoogleIdentity) -> bool:
        if not identity.email_verified or identity.email is None:
            return False
        return normalize_email(identity.email) in self.policy.operator_bootstrap_emails

    @staticmethod
    def _validate_google_identity(identity: GoogleIdentity) -> None:
        if not 1 <= len(identity.subject) <= 255 or identity.subject != identity.subject.strip():
            raise OAuthCallbackRejected
        if not 1 <= len(identity.display_name.strip()) <= 200:
            raise OAuthCallbackRejected
        values = [identity.subject, identity.display_name]
        if identity.email is not None:
            normalized_email = normalize_email(identity.email)
            if (
                not 1 <= len(normalized_email) <= 320
                or "@" not in normalized_email
                or any(character.isspace() for character in normalized_email)
            ):
                raise OAuthCallbackRejected
            values.append(identity.email)
        if any(
            any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
            for value in values
        ):
            raise OAuthCallbackRejected

    @staticmethod
    def _validate_target_id(target_id: str) -> None:
        if not 1 <= len(target_id) <= 128:
            raise InvalidRequest("저장 대상 식별자가 올바르지 않습니다.")
        if any(ord(character) < 0x20 or character in {"/", "\\"} for character in target_id):
            raise InvalidRequest("저장 대상 식별자가 올바르지 않습니다.")
