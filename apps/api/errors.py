from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ApiError(Exception):
    """A small, contract-safe error raised by the API read boundary."""

    status_code: int
    code: str
    message: str
    retryable: bool = False
    details: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class InvalidApiRequest(ApiError):
    def __init__(self, message: str = "요청 형식을 확인해 주세요.") -> None:
        super().__init__(400, "INVALID_REQUEST", message)


class RateLimited(ApiError):
    def __init__(self) -> None:
        super().__init__(
            429,
            "RATE_LIMITED",
            "요청이 너무 잦습니다. 잠시 후 다시 시도해 주세요.",
            retryable=True,
        )


class ProductResourceNotFound(ApiError):
    def __init__(self) -> None:
        super().__init__(404, "RESOURCE_NOT_FOUND", "요청한 항목을 찾을 수 없습니다.")


class ResourceIdMismatch(ApiError):
    def __init__(self, field: str) -> None:
        super().__init__(
            409,
            "RESOURCE_ID_MISMATCH",
            "요청한 테마와 이벤트의 관계를 확인할 수 없습니다.",
            details={"field": field},
        )


class StaleOperatorVersion(ApiError):
    def __init__(self) -> None:
        super().__init__(
            409,
            "STALE_VERSION",
            "화면의 상태가 최신이 아닙니다. 새로고침 후 다시 시도해 주세요.",
        )


class OperatorCommandNotAllowed(ApiError):
    def __init__(self) -> None:
        super().__init__(
            409,
            "COMMAND_NOT_ALLOWED",
            "현재 상태에서는 이 작업을 실행할 수 없습니다.",
        )


class UnsupportedMarketDate(ApiError):
    def __init__(self) -> None:
        super().__init__(
            422,
            "UNSUPPORTED_MARKET_DATE",
            "지원하지 않는 거래일입니다.",
        )


class ProductDataUnavailable(ApiError):
    def __init__(self) -> None:
        super().__init__(
            503,
            "DATA_TEMPORARILY_UNAVAILABLE",
            "현재 신뢰할 수 있는 데이터를 제공할 수 없습니다.",
            retryable=True,
        )
