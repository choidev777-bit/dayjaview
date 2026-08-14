"""Small transactional store and fixture mutation helpers for unit tests."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from packages.infostock.errors import SnapshotConflictError
from packages.infostock.hashing import fixture_bundle_hash, sha256_json
from packages.infostock.models import (
    ImportBundle,
    QualityIssue,
    RawSnapshot,
    ThemeDetail,
    ThemeIndexItem,
)
from packages.infostock.store import ApplyCounts, ImportTransaction, StoredImport


@dataclass
class ReferenceState:
    next_run_id: int = 1
    next_snapshot_id: int = 1
    next_theme_id: int = 1
    runs: dict[str, StoredImport] = field(default_factory=dict)
    blobs: dict[tuple[str, str], str] = field(default_factory=dict)
    snapshots: dict[tuple[str, str, str | None, datetime], dict[str, object]] = field(
        default_factory=dict
    )
    run_snapshots: set[tuple[int, int]] = field(default_factory=set)
    themes: dict[str, int] = field(default_factory=dict)
    checkpoints: set[int] = field(default_factory=set)
    issues: list[QualityIssue] = field(default_factory=list)


class ReferenceInfostockStore:
    """Copy-on-write orchestration double; PostgreSQL owns revision semantics."""

    def __init__(self, *, fail_on_theme_id: str | None = None) -> None:
        self.state = ReferenceState()
        self.fail_on_theme_id = fail_on_theme_id

    @contextmanager
    def transaction(self) -> Iterator[ImportTransaction]:
        working = copy.deepcopy(self.state)
        transaction = _ReferenceTransaction(self, working)
        yield transaction
        self.state = working


class _ReferenceTransaction:
    def __init__(self, owner: ReferenceInfostockStore, state: ReferenceState) -> None:
        self.owner = owner
        self.state = state
        self.pending: dict[int, ImportBundle] = {}

    def acquire_import_lock(self, input_hash: str) -> None:
        del input_hash

    def find_completed_import(self, input_hash: str) -> StoredImport | None:
        return self.state.runs.get(input_hash)

    def create_import_run(self, bundle: ImportBundle) -> int:
        run_id = self.state.next_run_id
        self.state.next_run_id += 1
        self.pending[run_id] = bundle
        return run_id

    def record_snapshot(
        self, run_id: int, bundle: ImportBundle, snapshot: RawSnapshot
    ) -> int:
        blob_key = (bundle.source_provider, snapshot.raw_hash)
        existing_blob = self.state.blobs.setdefault(
            blob_key, snapshot.raw_payload_text
        )
        if existing_blob != snapshot.raw_payload_text:
            raise SnapshotConflictError("reference blob conflict")
        key = (
            bundle.source_provider,
            snapshot.page_type,
            snapshot.source_entity_id,
            snapshot.collected_at,
        )
        existing = self.state.snapshots.get(key)
        if existing is not None:
            if (
                existing["raw_hash"] != snapshot.raw_hash
                or existing["source_url"] != snapshot.source_url
                or existing["parser_version"] != bundle.parser_version
            ):
                raise SnapshotConflictError("reference snapshot conflict")
            snapshot_id = int(existing["snapshot_id"])
        else:
            snapshot_id = self.state.next_snapshot_id
            self.state.next_snapshot_id += 1
            self.state.snapshots[key] = {
                "snapshot_id": snapshot_id,
                "raw_hash": snapshot.raw_hash,
                "source_url": snapshot.source_url,
                "parser_version": bundle.parser_version,
            }
        self.state.run_snapshots.add((run_id, snapshot_id))
        return snapshot_id

    def upsert_theme_index(
        self,
        bundle: ImportBundle,
        item: ThemeIndexItem,
        snapshot_id: int,
    ) -> int:
        del bundle, snapshot_id
        theme_id = self.state.themes.get(item.source_theme_id)
        if theme_id is None:
            theme_id = self.state.next_theme_id
            self.state.next_theme_id += 1
            self.state.themes[item.source_theme_id] = theme_id
        return theme_id

    def apply_theme_detail(
        self,
        bundle: ImportBundle,
        theme_id: int,
        detail: ThemeDetail,
        snapshot_id: int,
    ) -> ApplyCounts:
        del bundle, theme_id, snapshot_id
        if detail.source_theme_id == self.owner.fail_on_theme_id:
            raise RuntimeError("synthetic store failure")
        return ApplyCounts(
            theme_revisions=1,
            membership_revisions=len(detail.memberships),
            history_revisions=len(detail.history),
            history_leaders=sum(len(item.leaders) for item in detail.history),
            history_memberships=sum(
                len(item.member_stocks) for item in detail.history
            ),
        )

    def apply_daily(
        self,
        run_id: int,
        bundle: ImportBundle,
        snapshot_ids: dict[tuple[str, str | None], int],
    ) -> ApplyCounts:
        del snapshot_ids
        self.state.checkpoints.add(run_id)
        return ApplyCounts(
            daily_list_entries=len(bundle.daily.entries),
            daily_post_revisions=len(bundle.daily.posts),
            daily_relations=bundle.daily.relation_count,
        )

    def record_quality_issues(
        self, run_id: int, issues: tuple[QualityIssue, ...]
    ) -> int:
        del run_id
        self.state.issues.extend(issues)
        return len(issues)

    def complete_import_run(
        self,
        run_id: int,
        bundle: ImportBundle,
        *,
        snapshots_linked: int,
        counts: ApplyCounts,
    ) -> StoredImport:
        quality = bundle.quality_summary
        status = "SUCCEEDED" if bundle.daily.component_status == "COMPLETE" else "PARTIAL"
        stored = StoredImport(
            run_id=run_id,
            status=status,
            core_status="COMPLETE",
            daily_status=bundle.daily.component_status,
            blockers=bundle.daily.blockers,
            themes_imported=quality.theme_count,
            snapshots_linked=snapshots_linked,
            history_rows_seen=quality.history_count,
            related_stocks_seen=quality.related_stock_count,
            leaders_seen=quality.leader_count,
            historical_memberships_seen=quality.historical_membership_count,
            daily_list_entries_seen=len(bundle.daily.entries),
            daily_posts_seen=len(bundle.daily.posts),
            daily_bodies_seen=bundle.daily.body_count,
            daily_relations_seen=bundle.daily.relation_count,
            theme_revisions_created=counts.theme_revisions,
            membership_revisions_created=counts.membership_revisions,
            history_revisions_created=counts.history_revisions,
            history_leaders_created=counts.history_leaders,
            history_memberships_created=counts.history_memberships,
            quality_issues_created=counts.quality_issues,
            daily_post_revisions_created=counts.daily_post_revisions,
        )
        self.state.runs[bundle.input_hash] = stored
        return stored


def rehash_fixture(payload: dict[str, Any], *detail_positions: int) -> None:
    for position in detail_positions:
        detail = payload["detailSnapshots"][position]
        detail["rawHash"] = sha256_json(detail["rawPayload"])
    payload["bundleHash"] = fixture_bundle_hash(payload)


def move_observation_time(payload: dict[str, Any], timestamp: str) -> None:
    payload["indexSnapshot"]["collectedAt"] = timestamp
    for detail in payload["detailSnapshots"]:
        detail["collectedAt"] = timestamp
    payload["bundleHash"] = fixture_bundle_hash(payload)
