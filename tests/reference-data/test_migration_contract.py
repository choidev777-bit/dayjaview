from __future__ import annotations

from pathlib import Path


def test_reference_migration_has_raw_pit_and_revision_boundaries() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    migration = (
        repository_root / "infra" / "migrations" / "0002_reference_data.sql"
    ).read_text(encoding="utf-8")

    required = (
        "ingest.reference_source_snapshots",
        "core.reference_share_observations",
        "core.reference_non_float_holdings",
        "core.reference_holding_coverage",
        "core.reference_free_float_revisions",
        "core.reference_trading_calendar_revisions",
        "core.reference_daily_prices",
        "core.reference_corporate_action_revisions",
        "core.reference_adjusted_price_revisions",
        "source_revision",
        "known_from",
        "known_to",
        "lineage",
        "live_validation_status",
        "duplicate_deductions_prevented",
    )
    for token in required:
        assert token in migration


def test_reference_writer_has_no_delete_or_ddl_grant() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    migration = (
        repository_root / "infra" / "migrations" / "0002_reference_data.sql"
    ).read_text(encoding="utf-8")
    grant_section = migration.split("DO $reference_writer_boundary$", maxsplit=1)[1]

    assert "GRANT DELETE" not in grant_section
    assert "GRANT CREATE" not in grant_section
    assert "KRX_API_KEY" not in migration
    assert "OPENDART_API_KEY" not in migration
