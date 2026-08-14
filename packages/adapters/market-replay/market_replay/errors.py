"""저장 market capture를 canonical event로 바꾸는 경계 오류."""

from __future__ import annotations


class ReplayAdapterError(ValueError):
    """입력 전체를 거부해야 하는 replay adapter 오류."""


class CaptureRecordError(ReplayAdapterError):
    """저장 record의 구조나 값이 capture 계약과 일치하지 않음."""


class SchemaMismatchError(CaptureRecordError):
    """지원하지 않는 capture schema version임."""


class PayloadIntegrityError(CaptureRecordError):
    """저장 payload와 보존된 SHA-256이 일치하지 않음."""


class UnsupportedEventError(CaptureRecordError):
    """제품 canonical market event로 변환할 수 없는 event type임."""


class InputLimitExceededError(ReplayAdapterError):
    """명시한 bounded input 상한을 초과함."""


class TruncatedInputError(ReplayAdapterError):
    """stream이 완전한 record 경계 전에 끝남."""


class MixedSessionError(ReplayAdapterError):
    """한 배치에 서로 다른 capture session이 섞임."""
