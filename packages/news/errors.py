"""뉴스 evidence 경계의 명시적이고 fail-closed인 오류."""

from __future__ import annotations


class NewsPipelineError(RuntimeError):
    """뉴스 수집·정규화·저장 경계의 기본 오류."""


class SourceRightsDeniedError(NewsPipelineError):
    """공급원 권리 record가 요청 범위를 명시적으로 허용하지 않는다."""

    def __init__(self, code: str, source_id: str, detail: str) -> None:
        self.code = code
        self.source_id = source_id
        self.blocker = "B-DATA-RIGHTS"
        self.detail = detail
        super().__init__(f"{self.blocker}/{code} ({source_id}): {detail}")


class NewsSourceContractError(NewsPipelineError):
    """공급원 payload가 허용된 strict contract를 충족하지 않는다."""

    def __init__(self, code: str, path: str, detail: str) -> None:
        self.code = code
        self.path = path
        self.detail = detail
        super().__init__(f"{code} ({path}): {detail}")


class NewsTemporalConflictError(NewsPipelineError):
    """이미 알려진 publication revision을 과거 시점에서 다시 쓰려 한다."""


class NewsRevisionConflictError(NewsPipelineError):
    """같은 공급원 revision이 서로 다른 내용으로 관측됐다."""
