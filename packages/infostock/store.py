"""Persistence protocol kept independent from a PostgreSQL driver package."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from .models import ImportBundle, QualityIssue, RawSnapshot, ThemeDetail, ThemeIndexItem


@dataclass(frozen=True, slots=True)
class ApplyCounts:
    theme_revisions: int = 0
    membership_revisions: int = 0
    history_revisions: int = 0
    history_leaders: int = 0
    history_memberships: int = 0
    quality_issues: int = 0
    daily_list_entries: int = 0
    daily_post_revisions: int = 0
    daily_relations: int = 0

    def __add__(self, other: ApplyCounts) -> ApplyCounts:
        return ApplyCounts(
            theme_revisions=self.theme_revisions + other.theme_revisions,
            membership_revisions=(
                self.membership_revisions + other.membership_revisions
            ),
            history_revisions=self.history_revisions + other.history_revisions,
            history_leaders=self.history_leaders + other.history_leaders,
            history_memberships=(
                self.history_memberships + other.history_memberships
            ),
            quality_issues=self.quality_issues + other.quality_issues,
            daily_list_entries=self.daily_list_entries + other.daily_list_entries,
            daily_post_revisions=(
                self.daily_post_revisions + other.daily_post_revisions
            ),
            daily_relations=self.daily_relations + other.daily_relations,
        )


@dataclass(frozen=True, slots=True)
class StoredImport:
    run_id: int
    status: str
    core_status: str
    daily_status: str
    blockers: tuple[str, ...]
    themes_imported: int
    snapshots_linked: int
    history_rows_seen: int
    related_stocks_seen: int
    leaders_seen: int
    historical_memberships_seen: int
    daily_list_entries_seen: int
    daily_posts_seen: int
    daily_bodies_seen: int
    daily_relations_seen: int
    theme_revisions_created: int
    membership_revisions_created: int
    history_revisions_created: int
    history_leaders_created: int
    history_memberships_created: int
    quality_issues_created: int
    daily_post_revisions_created: int


class ImportTransaction(Protocol):
    def acquire_import_lock(self, input_hash: str) -> None: ...

    def find_completed_import(self, input_hash: str) -> StoredImport | None: ...

    def create_import_run(self, bundle: ImportBundle) -> int: ...

    def record_snapshot(
        self, run_id: int, bundle: ImportBundle, snapshot: RawSnapshot
    ) -> int: ...

    def upsert_theme_index(
        self,
        bundle: ImportBundle,
        item: ThemeIndexItem,
        snapshot_id: int,
    ) -> int: ...

    def apply_theme_detail(
        self,
        bundle: ImportBundle,
        theme_id: int,
        detail: ThemeDetail,
        snapshot_id: int,
    ) -> ApplyCounts: ...

    def apply_daily(
        self,
        run_id: int,
        bundle: ImportBundle,
        snapshot_ids: dict[tuple[str, str | None], int],
        *,
        missing_window: tuple[date, date] | None = None,
    ) -> ApplyCounts: ...

    def record_quality_issues(
        self,
        run_id: int,
        issues: tuple[QualityIssue, ...],
    ) -> int: ...

    def complete_import_run(
        self,
        run_id: int,
        bundle: ImportBundle,
        *,
        snapshots_linked: int,
        counts: ApplyCounts,
    ) -> StoredImport: ...

    def create_daily_increment_run(self, bundle: ImportBundle) -> int: ...

    def complete_daily_increment_run(
        self,
        run_id: int,
        bundle: ImportBundle,
        *,
        snapshots_linked: int,
        counts: ApplyCounts,
    ) -> StoredImport: ...


class InfostockStore(Protocol):
    def transaction(self) -> AbstractContextManager[ImportTransaction]: ...
