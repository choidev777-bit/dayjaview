from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "infra/migrations/0001_infostock_store.sql"
)


def test_postgresql_migration_exposes_full_model_and_writer_boundaries() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    for table in (
        "ingest.infostock_import_runs",
        "ingest.infostock_sync_components",
        "ingest.infostock_source_blobs",
        "ingest.infostock_source_snapshots",
        "ingest.infostock_import_run_snapshots",
        "ingest.infostock_quality_issues",
        "ingest.infostock_daily_list_entries",
        "ingest.infostock_daily_backfill_checkpoints",
        "core.infostock_themes",
        "core.infostock_theme_revisions",
        "core.infostock_stocks",
        "core.infostock_stock_name_observations",
        "core.infostock_theme_stock_memberships",
        "core.infostock_theme_history",
        "core.infostock_theme_history_leaders",
        "core.infostock_theme_history_memberships",
        "core.infostock_daily_posts",
        "core.infostock_daily_post_revisions",
        "core.infostock_daily_relations",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

    assert "CREATE EXTENSION IF NOT EXISTS btree_gist" in sql
    assert sql.count("EXCLUDE USING gist") == 4
    assert "WHERE observed_to IS NULL" in sql
    assert "ON DELETE RESTRICT" in sql
    assert "ON DELETE CASCADE" not in sql
    assert "'FIXTURE_ONLY', 'LOCAL_AUDITED_IMPORT'" in sql
    assert "PRODUCTION_APPROVED" not in sql
    assert "raw_payload_text text NOT NULL" in sql
    assert "source_content_hash" in sql
    assert all(value in sql for value in ("DAILY_MANIFEST", "DAILY_LIST", "DAILY_DETAIL"))
    assert "dayjaview_infostock_writer" in sql
    assert "REVOKE ALL ON ALL TABLES" in sql
    assert "GRANT SELECT, INSERT, UPDATE" in sql
    assert "GRANT DELETE" not in sql
    assert "point_in_time_safe boolean NOT NULL DEFAULT false" in sql
    assert "stock_code ~ '^[0-9A-Z]{6}$'" in sql


def test_current_and_historical_memberships_and_leaders_are_separate() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    current = sql.split(
        "CREATE TABLE IF NOT EXISTS core.infostock_theme_stock_memberships", 1
    )[1].split("CREATE UNIQUE INDEX", 1)[0]
    leader = sql.split(
        "CREATE TABLE IF NOT EXISTS core.infostock_theme_history_leaders", 1
    )[1].split("CREATE INDEX", 1)[0]
    historical = sql.split(
        "CREATE TABLE IF NOT EXISTS core.infostock_theme_history_memberships", 1
    )[1].split("CREATE INDEX", 1)[0]

    assert "observed_from timestamptz NOT NULL" in current
    assert "history_id bigint NOT NULL" in leader
    assert "history_id bigint NOT NULL" in historical
    assert "REFERENCES core.infostock_theme_stock_memberships" not in leader
    assert "REFERENCES core.infostock_theme_stock_memberships" not in historical
    assert "source_stock_name text NOT NULL" in leader
    assert "source_stock_name text NOT NULL" in historical


def test_daily_revision_and_checkpoint_contract_supports_s6_incremental_updates() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    revision = sql.split(
        "CREATE TABLE IF NOT EXISTS core.infostock_daily_post_revisions", 1
    )[1].split("CREATE UNIQUE INDEX", 1)[0]
    checkpoint = sql.split(
        "CREATE TABLE IF NOT EXISTS ingest.infostock_daily_backfill_checkpoints", 1
    )[1].split("REVOKE ALL", 1)[0]

    assert "revision_no integer NOT NULL" in revision
    assert "raw_body text" in revision
    assert "normalized_hash" in revision
    assert "visibility_status" in revision
    assert "observed_from" in revision and "observed_to" in revision
    assert "next_page integer" in checkpoint
    assert "coverage_complete boolean NOT NULL" in checkpoint
    assert "cursor_json jsonb NOT NULL" in checkpoint
    assert "blockers text[] NOT NULL" in checkpoint
