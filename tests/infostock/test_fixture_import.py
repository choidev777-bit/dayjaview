from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from packages.infostock import (
    CommittedFixturePolicy,
    SnapshotConflictError,
    import_bundle,
    load_committed_fixture,
    parse_fixture_payload,
)

from .generate_fixture import build_fixture_payload
from .support import ReferenceInfostockStore, rehash_fixture

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "infostock-280.synthetic.json"
FIXTURE_POLICY = CommittedFixturePolicy(REPOSITORY_ROOT)


def test_committed_fixture_is_reproducible_and_parses_280_themes() -> None:
    committed = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert committed == build_fixture_payload()

    bundle = load_committed_fixture(FIXTURE_PATH, FIXTURE_POLICY)

    assert bundle.expected_theme_count == 280
    assert len(bundle.index_items) == 280
    assert len(bundle.details) == 280
    assert bundle.quality_summary.history_count == 280
    assert bundle.quality_summary.related_stock_count == 560
    assert bundle.quality_summary.leader_count == 280
    assert bundle.quality_summary.historical_membership_count == 280
    assert bundle.parser_version == "collect-infostock-fixture/1.0.0"
    assert (
        bundle.manifest_snapshot.raw_payload_text.encode("utf-8")
        == FIXTURE_PATH.read_bytes()
    )
    assert all(detail.snapshot.raw_hash for detail in bundle.details)
    assert all(detail.snapshot.collected_at.tzinfo for detail in bundle.details)
    assert bundle.daily.component_status == "BLOCKED"


def test_280_import_is_atomic_idempotent_and_preserves_lineage_counts() -> None:
    bundle = load_committed_fixture(FIXTURE_PATH, FIXTURE_POLICY)
    store = ReferenceInfostockStore()

    first = import_bundle(bundle, store)
    state_after_first = copy.deepcopy(store.state)
    second = import_bundle(bundle, store)

    assert first.status == "PARTIAL"
    assert first.core_status == "COMPLETE"
    assert first.daily_status == "BLOCKED"
    assert first.blockers == ("B-INFOSTOCK-AUTH",)
    assert first.themes_imported == 280
    assert first.snapshots_linked == 282
    assert first.history_rows_seen == 280
    assert first.related_stocks_seen == 560
    assert first.leaders_seen == 280
    assert first.historical_memberships_seen == 280
    assert first.theme_revisions_created == 280
    assert first.membership_revisions_created == 560
    assert first.history_revisions_created == 280
    assert first.history_leaders_created == 280
    assert first.history_memberships_created == 280
    assert first.quality_issues_created == 1
    assert first.reused is False
    assert second.run_id == first.run_id
    assert second.reused is True
    assert store.state == state_after_first
    assert len(store.state.runs) == 1
    assert len(store.state.blobs) == 282
    assert len(store.state.snapshots) == 282
    assert len(store.state.run_snapshots) == 282


def test_store_failure_rolls_back_run_snapshots_and_normalized_rows() -> None:
    bundle = load_committed_fixture(FIXTURE_PATH, FIXTURE_POLICY)
    store = ReferenceInfostockStore(fail_on_theme_id="1017")

    with pytest.raises(RuntimeError, match="synthetic store failure"):
        import_bundle(bundle, store)

    assert store.state == ReferenceInfostockStore().state
    store.fail_on_theme_id = None
    assert import_bundle(bundle, store).themes_imported == 280


def test_parser_upgrade_over_same_dataset_reuses_completed_import() -> None:
    # 운영 배포 결함 재현: 같은 수집본을 파서만 올려 재적재하면 input_hash가
    # 달라져 전체 import가 다시 돌았고, 기존 관측과 parser 표기가 달라
    # SnapshotConflictError로 bootstrap이 죽었다. 같은 dataset은 재사용한다.
    bundle = load_committed_fixture(FIXTURE_PATH, FIXTURE_POLICY)
    store = ReferenceInfostockStore()
    first = import_bundle(bundle, store)
    state_after_first = copy.deepcopy(store.state)

    upgraded = replace(
        bundle,
        parser_version="collect-infostock-fixture/9.9.9",
        input_hash="f" * 64,
    )
    second = import_bundle(upgraded, store)

    assert first.reused is False
    assert second.reused is True
    assert second.run_id == first.run_id
    assert store.state == state_after_first


def test_same_observation_time_with_changed_raw_snapshot_rolls_back() -> None:
    initial = load_committed_fixture(FIXTURE_PATH, FIXTURE_POLICY)
    store = ReferenceInfostockStore()
    import_bundle(initial, store)
    before = copy.deepcopy(store.state)

    conflicting_payload = build_fixture_payload()
    conflicting_payload["detailSnapshots"][0]["rawPayload"]["data"]["theme"][
        "outline"
    ] = "같은 collectedAt의 충돌 설명"
    rehash_fixture(conflicting_payload, 0)
    conflicting = parse_fixture_payload(conflicting_payload)

    with pytest.raises(SnapshotConflictError):
        import_bundle(conflicting, store)

    assert store.state == before
