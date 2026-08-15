from __future__ import annotations


class OperatorError(Exception):
    """운영자 콘솔 도메인 오류. HTTP 매핑은 apps/api가 한다."""


class OperatorTargetNotFound(OperatorError):
    """runId 또는 reviewId가 없다."""


class UnknownOperatorCursor(OperatorError):
    """다음 페이지 cursor가 현재 목록에 없다."""


class OperatorCommandRejected(OperatorError):
    """command를 실행하지 않았다.

    code는 계약의 ErrorCode 중 409로 매핑되는 두 값만 쓴다.
    STALE_VERSION은 expectedVersion 불일치, COMMAND_NOT_ALLOWED는 현재 상태에서
    허용되지 않는 전이이거나 같은 Idempotency-Key를 다른 내용으로 재사용한 것이다.
    """

    def __init__(self, code: str) -> None:
        if code not in {"STALE_VERSION", "COMMAND_NOT_ALLOWED"}:
            raise ValueError("unsupported operator command rejection code")
        super().__init__(code)
        self.code = code
