"""Atomic and idempotent orchestration for one validated Infostock full sync."""

from __future__ import annotations

from dataclasses import dataclass

from .models import ImportBundle
from .policy import InfostockAccessPolicy
from .store import ApplyCounts, InfostockStore, StoredImport


@dataclass(frozen=True, slots=True)
class ImportResult:
    run_id: int
    input_hash: str
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
    reused: bool


def _result(bundle: ImportBundle, stored: StoredImport, *, reused: bool) -> ImportResult:
    return ImportResult(
        run_id=stored.run_id,
        input_hash=bundle.input_hash,
        status=stored.status,
        core_status=stored.core_status,
        daily_status=stored.daily_status,
        blockers=stored.blockers,
        themes_imported=stored.themes_imported,
        snapshots_linked=stored.snapshots_linked,
        history_rows_seen=stored.history_rows_seen,
        related_stocks_seen=stored.related_stocks_seen,
        leaders_seen=stored.leaders_seen,
        historical_memberships_seen=stored.historical_memberships_seen,
        daily_list_entries_seen=stored.daily_list_entries_seen,
        daily_posts_seen=stored.daily_posts_seen,
        daily_bodies_seen=stored.daily_bodies_seen,
        daily_relations_seen=stored.daily_relations_seen,
        theme_revisions_created=stored.theme_revisions_created,
        membership_revisions_created=stored.membership_revisions_created,
        history_revisions_created=stored.history_revisions_created,
        history_leaders_created=stored.history_leaders_created,
        history_memberships_created=stored.history_memberships_created,
        quality_issues_created=stored.quality_issues_created,
        daily_post_revisions_created=stored.daily_post_revisions_created,
        reused=reused,
    )


def import_bundle(bundle: ImportBundle, store: InfostockStore) -> ImportResult:
    """Import a validated full sync in one database transaction.

    Daily may be independently BLOCKED while the 280-theme component commits as
    COMPLETE. A parser/store failure still rolls back the whole run and all rows.
    """

    InfostockAccessPolicy.require_import_scope(bundle.rights_scope)
    with store.transaction() as transaction:
        transaction.acquire_import_lock(bundle.input_hash)
        completed = transaction.find_completed_import(bundle.input_hash)
        if completed is not None:
            return _result(bundle, completed, reused=True)

        run_id = transaction.create_import_run(bundle)
        transaction.record_snapshot(run_id, bundle, bundle.manifest_snapshot)
        index_snapshot_id = transaction.record_snapshot(
            run_id, bundle, bundle.index_snapshot
        )
        snapshots_linked = 2
        theme_ids: dict[str, int] = {}
        for item in bundle.index_items:
            theme_ids[item.source_theme_id] = transaction.upsert_theme_index(
                bundle, item, index_snapshot_id
            )

        counts = ApplyCounts()
        for detail in bundle.details:
            detail_snapshot_id = transaction.record_snapshot(
                run_id, bundle, detail.snapshot
            )
            snapshots_linked += 1
            counts += transaction.apply_theme_detail(
                bundle,
                theme_ids[detail.source_theme_id],
                detail,
                detail_snapshot_id,
            )

        daily_snapshot_ids: dict[tuple[str, str | None], int] = {}
        for snapshot in bundle.daily.pages:
            snapshot_id = transaction.record_snapshot(run_id, bundle, snapshot)
            daily_snapshot_ids[(snapshot.page_type, snapshot.source_entity_id)] = (
                snapshot_id
            )
            snapshots_linked += 1
        counts += transaction.apply_daily(
            run_id, bundle, daily_snapshot_ids
        )
        all_issues = (*bundle.quality_issues, *bundle.daily.quality_issues)
        counts += ApplyCounts(
            quality_issues=transaction.record_quality_issues(
                run_id, tuple(all_issues)
            )
        )
        stored = transaction.complete_import_run(
            run_id,
            bundle,
            snapshots_linked=snapshots_linked,
            counts=counts,
        )
        return _result(bundle, stored, reused=False)
