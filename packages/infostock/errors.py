"""Explicit failures exposed by the Infostock import boundary."""

from __future__ import annotations


class InfostockImportError(RuntimeError):
    """Base error for a rejected or rolled-back Infostock import."""


class FixtureValidationError(InfostockImportError):
    """A committed fixture does not satisfy the strict import contract."""

    def __init__(self, code: str, path: str, detail: str) -> None:
        self.code = code
        self.path = path
        self.detail = detail
        super().__init__(f"{code} ({path}): {detail}")


class DataRightsBlockedError(InfostockImportError):
    """Production collection or serving is blocked by an external gate."""

    def __init__(self, blocker: str, detail: str) -> None:
        self.blocker = blocker
        self.detail = detail
        super().__init__(f"{blocker}: {detail}")


class TemporalConflictError(InfostockImportError):
    """A changed observation would rewrite an already-known past state."""


class SnapshotConflictError(InfostockImportError):
    """One source observation time was supplied with conflicting content."""
