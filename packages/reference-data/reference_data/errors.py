"""기준정보 경계에서 사용하는 명시적 실패 유형."""

from __future__ import annotations


class ReferenceDataError(RuntimeError):
    """모든 기준정보 오류의 기반 예외."""


class SourceContractError(ReferenceDataError):
    """원천 응답이나 fixture가 계약을 만족하지 않는다."""

    def __init__(self, code: str, path: str, detail: str) -> None:
        self.code = code
        self.path = path
        self.detail = detail
        super().__init__(f"{code} ({path}): {detail}")


class SourceTransportError(ReferenceDataError):
    """비밀값을 노출하지 않는 외부 read 실패."""

    def __init__(self, provider: str, endpoint: str, detail: str) -> None:
        self.provider = provider
        self.endpoint = endpoint
        self.detail = detail
        super().__init__(f"{provider} 원천 조회 실패 ({endpoint}): {detail}")


class MissingCredentialError(ReferenceDataError):
    """live 원천 key가 없어 호출을 시작하지 않는다."""

    def __init__(self, missing_names: tuple[str, ...]) -> None:
        self.blocker = "B-REFDATA-KEYS"
        self.missing_names = missing_names
        joined = ", ".join(missing_names)
        super().__init__(
            f"B-REFDATA-KEYS: live 검증에 필요한 환경변수가 없습니다: {joined}"
        )


class TemporalConflictError(ReferenceDataError):
    """이미 알려진 시각보다 과거에 변경 revision을 끼워 넣으려 했다."""
