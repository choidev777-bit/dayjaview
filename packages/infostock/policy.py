"""Fail-closed path, authentication, and data-rights gates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import DataRightsBlockedError, FixtureValidationError


@dataclass(frozen=True, slots=True)
class CommittedFixturePolicy:
    """Allow reads only below the explicitly committed fixture directory."""

    repository_root: Path
    relative_fixture_root: Path = Path("tests/infostock/fixtures")

    def validate(self, candidate: Path) -> Path:
        fixture_root = (self.repository_root.resolve() / self.relative_fixture_root).resolve()
        resolved = candidate.resolve()
        if not resolved.is_relative_to(fixture_root) or resolved.suffix.lower() != ".json":
            raise FixtureValidationError(
                "UNAPPROVED_FIXTURE_PATH",
                "$fixture",
                "tests/infostock/fixtures 아래의 tracked JSON만 읽을 수 있습니다.",
            )
        return resolved


@dataclass(frozen=True, slots=True)
class ExistingCollectionPolicy:
    """Validate an explicitly supplied, read-only collector output directory.

    No default path is provided.  This prevents a production worker from silently
    consuming an ignored developer directory.
    """

    def validate(self, candidate: Path) -> Path:
        resolved = candidate.resolve(strict=True)
        if not resolved.is_dir():
            raise FixtureValidationError(
                "COLLECTION_PATH_INVALID", "$collection", "수집본 경로가 디렉터리가 아닙니다."
            )
        for name in ("manifest.json", "theme-index.json"):
            path = resolved / name
            if not path.is_file() or path.is_symlink():
                raise FixtureValidationError(
                    "COLLECTION_FILE_MISSING",
                    f"$collection/{name}",
                    "필수 수집본 파일이 없거나 symbolic link입니다.",
                )
        return resolved


class InfostockAccessPolicy:
    """Local audited imports are allowed; production access remains fail-closed."""

    @staticmethod
    def require_import_scope(rights_scope: str) -> None:
        if rights_scope not in {"FIXTURE_ONLY", "LOCAL_AUDITED_IMPORT"}:
            raise DataRightsBlockedError(
                "B-DATA-RIGHTS",
                "승인된 fixture 또는 명시적으로 지정한 기존 수집본만 적재할 수 있습니다.",
            )

    @staticmethod
    def require_fixture_import(rights_scope: str) -> None:
        """Backward-compatible alias for the Stage 1 import gate."""

        InfostockAccessPolicy.require_import_scope(rights_scope)

    @staticmethod
    def require_production_collection() -> None:
        raise DataRightsBlockedError(
            "B-DATA-RIGHTS",
            "공급원별 저장·가공 권리 승인 전 production Infostock 수집은 비활성 상태입니다.",
        )

    @staticmethod
    def require_production_serving() -> None:
        raise DataRightsBlockedError(
            "B-DATA-RIGHTS",
            "재배포 권리 승인 전 production Infostock 데이터 표시는 비활성 상태입니다.",
        )

    @staticmethod
    def require_daily_browser_collection(
        *, auth_verified: bool = False, rights_verified: bool = False
    ) -> None:
        if not auth_verified:
            raise DataRightsBlockedError(
                "B-INFOSTOCK-AUTH",
                "검증된 암호화 browser session이 없어 DailyFeaturedTheme live 수집을 시작하지 않습니다.",
            )
        if not rights_verified:
            raise DataRightsBlockedError(
                "B-DATA-RIGHTS",
                "DailyFeaturedTheme 저장·가공 권리 증거가 없어 live 수집을 시작하지 않습니다.",
            )
