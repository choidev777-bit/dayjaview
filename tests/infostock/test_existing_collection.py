from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest

from packages.infostock import (
    ExistingCollectionPolicy,
    ImportBundle,
    human_quality_report,
    load_existing_collection,
    machine_quality_report,
)
from packages.infostock.hashing import sha256_bytes

COLLECTION_DIR = os.environ.get("INFOSTOCK_EXISTING_IMPORT_DIR")
REPORT_ROOT = Path(__file__).parent / "reports"

pytestmark = pytest.mark.skipif(
    COLLECTION_DIR is None,
    reason="INFOSTOCK_EXISTING_IMPORT_DIR로 허용된 기존 수집본을 명시해야 합니다.",
)


@pytest.fixture(scope="module")
def actual_bundle() -> ImportBundle:
    assert COLLECTION_DIR is not None
    return load_existing_collection(
        Path(COLLECTION_DIR), ExistingCollectionPolicy()
    )


def test_actual_collection_recomputes_authoritative_counts_and_quality(
    actual_bundle: ImportBundle,
) -> None:
    bundle = actual_bundle
    quality = bundle.quality_summary

    assert quality.theme_count == 280
    assert quality.history_count == 39_696
    assert quality.related_stock_count == 6_629
    assert quality.leader_count == 65_526
    assert quality.historical_membership_count == 652_241
    assert quality.duplicate_history_count == 4
    assert quality.missing_history_date_count == 0
    assert quality.missing_history_content_count == 0
    assert quality.missing_leader_code_count == 90
    assert quality.missing_related_stock_code_count == 0
    assert quality.missing_historical_membership_code_count == 7_498
    assert quality.missing_historical_membership_field_count == 274
    assert quality.stock_name_variant_count == 311
    assert bundle.dataset_hash == (
        "4959314b09c1152f2e9ec3365a7be2f890647eede74a46991e8e2d6a2ff12017"
    )
    assert bundle.input_hash == (
        "eb9dc8c0f161878d7502ff7ad076942ae17e4180f90c73d6894618fa8356d2fc"
    )

    issue_counts = Counter(issue.issue_code for issue in bundle.quality_issues)
    assert issue_counts["SOURCE_DUPLICATE_HISTORY"] == 4
    assert issue_counts["LEADER_CODE_MISSING"] == 90
    assert issue_counts["HISTORICAL_MEMBERSHIP_CODE_MISSING"] == 7_498
    assert issue_counts["HISTORICAL_MEMBERSHIP_FIELD_MISSING"] == 274
    assert issue_counts["SOURCE_CONTENT_HASH_UNVERIFIABLE"] == 2
    assert issue_counts["STOCK_NAME_VARIANT"] == 311


def test_actual_raw_snapshots_preserve_exact_utf8_files_and_lineage(
    actual_bundle: ImportBundle,
) -> None:
    bundle = actual_bundle
    assert COLLECTION_DIR is not None
    root = Path(COLLECTION_DIR)

    assert bundle.manifest_snapshot.raw_hash == sha256_bytes(
        (root / "manifest.json").read_bytes()
    )
    assert bundle.index_snapshot.raw_hash == sha256_bytes(
        (root / "theme-index.json").read_bytes()
    )
    assert bundle.manifest_snapshot.raw_payload_text.encode("utf-8") == (
        root / "manifest.json"
    ).read_bytes()
    assert bundle.index_snapshot.raw_payload_text.encode("utf-8") == (
        root / "theme-index.json"
    ).read_bytes()
    assert len(bundle.details) == 280
    assert sum(len(detail.history) for detail in bundle.details) == 39_696
    assert sum(len(detail.memberships) for detail in bundle.details) == 6_629
    assert sum(
        len(history.member_stocks)
        for detail in bundle.details
        for history in detail.history
    ) == 652_241
    assert sum(
        history.quality_status == "SOURCE_DUPLICATE"
        for detail in bundle.details
        for history in detail.history
    ) == 4


def test_daily_existing_capture_is_importable_but_full_backfill_is_blocked(
    actual_bundle: ImportBundle,
) -> None:
    daily = actual_bundle.daily
    issue_counts = Counter(issue.issue_code for issue in daily.quality_issues)

    assert daily.component_status == "BLOCKED"
    assert daily.blockers == ("B-INFOSTOCK-AUTH", "B-DATA-RIGHTS")
    assert daily.coverage_complete is False
    assert (daily.first_page, daily.last_page, daily.next_page) == (1, 1, 2)
    assert len(daily.entries) == 5
    assert len(daily.posts) == 5
    assert daily.body_count == 1
    assert daily.relation_count == 232
    assert len(daily.pages) == 2
    assert len({snapshot.raw_hash for snapshot in daily.pages}) == 1
    assert issue_counts["SOURCE_ID_MISSING"] == 5
    assert issue_counts["SOURCE_URL_MISSING"] == 5
    assert issue_counts["BODY_MISSING"] == 4
    assert issue_counts["PAGINATION_INCOMPLETE"] == 1
    assert issue_counts["B-INFOSTOCK-AUTH"] == 1
    assert issue_counts["B-DATA-RIGHTS"] == 1


def test_committed_machine_and_korean_reports_match_actual_recalculation(
    actual_bundle: ImportBundle,
) -> None:
    bundle = actual_bundle
    committed_json = json.loads(
        (REPORT_ROOT / "actual-collection-audit.json").read_text(encoding="utf-8")
    )
    committed_markdown = (
        REPORT_ROOT / "actual-collection-audit.md"
    ).read_text(encoding="utf-8")

    assert committed_json == machine_quality_report(bundle)
    assert committed_markdown == human_quality_report(bundle)
