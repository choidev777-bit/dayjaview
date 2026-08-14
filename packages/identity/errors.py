from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class IdentityError(Exception):
    """A contract-safe identity error.

    Details are deliberately small, structured values so raw provider or storage
    exceptions never cross the API boundary.
    """

    status_code: int
    code: str
    message: str
    retryable: bool = False
    details: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class InvalidRequest(IdentityError):
    def __init__(
        self,
        message: str = "요청 형식이 올바르지 않습니다.",
        *,
        reason_code: str | None = None,
    ) -> None:
        details: dict[str, str] = {}
        if reason_code is not None:
            details["reasonCode"] = reason_code
        super().__init__(400, "INVALID_REQUEST", message, details=details)


class AuthenticationRequired(IdentityError):
    def __init__(self) -> None:
        super().__init__(401, "AUTHENTICATION_REQUIRED", "로그인이 필요합니다.")


class FeatureNotEntitled(IdentityError):
    def __init__(
        self,
        message: str = "이 기능을 사용할 권한이 없습니다.",
        *,
        reason_code: str | None = None,
    ) -> None:
        details: dict[str, str] = {}
        if reason_code is not None:
            details["reasonCode"] = reason_code
        super().__init__(403, "FEATURE_NOT_ENTITLED", message, details=details)


class CsrfValidationFailed(IdentityError):
    def __init__(self) -> None:
        super().__init__(
            403,
            "INVALID_REQUEST",
            "요청의 보안 정보를 확인할 수 없습니다.",
            details={"reasonCode": "CSRF_VALIDATION_FAILED"},
        )


class RecentAuthenticationRequired(IdentityError):
    def __init__(self) -> None:
        super().__init__(
            403,
            "FEATURE_NOT_ENTITLED",
            "계정 삭제를 계속하려면 최근 로그인이 필요합니다.",
            details={"reasonCode": "RECENT_AUTHENTICATION_REQUIRED"},
        )


class ResourceNotFound(IdentityError):
    def __init__(self) -> None:
        super().__init__(404, "RESOURCE_NOT_FOUND", "요청한 항목을 찾을 수 없습니다.")


class OAuthCallbackRejected(InvalidRequest):
    def __init__(self) -> None:
        super().__init__(
            "로그인 요청을 확인할 수 없습니다. 다시 로그인해 주세요.",
            reason_code="OAUTH_CALLBACK_REJECTED",
        )


class InvalidCursor(InvalidRequest):
    def __init__(self) -> None:
        super().__init__(
            "관심 목록의 다음 페이지 정보를 확인할 수 없습니다.",
            reason_code="INVALID_CURSOR",
        )
