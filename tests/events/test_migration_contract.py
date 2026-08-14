from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2] / "infra/migrations/0002_event_realtime.sql"
)
EVENT_POSTGRES = Path(__file__).resolve().parents[2] / "packages/events/postgres.py"
SNAPSHOT_POSTGRES = (
    Path(__file__).resolve().parents[2] / "packages/realtime/postgres.py"
)


def test_event_migration_has_single_writer_outbox_and_snapshot_boundaries() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    for table in (
        "event.events",
        "event.state_logs",
        "event.command_receipts",
        "event.outbox",
        "realtime.state_checkpoints",
        "serving.realtime_stream_sequences",
        "serving.realtime_snapshots",
        "serving.realtime_snapshot_requests",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

    assert "dayjaview_event_writer" in sql
    assert "dayjaview_outbox_publisher" in sql
    assert "dayjaview_realtime_writer" in sql
    assert "command_fingerprint" in sql
    assert "message_json jsonb NOT NULL" in sql
    assert "UNIQUE (event_id, state_version)" in sql
    assert "UNIQUE (stream_id, topic, params_key, sequence)" in sql
    assert "ON DELETE RESTRICT" in sql
    assert "ON DELETE CASCADE" not in sql
    assert "GRANT DELETE" not in sql
    assert "REVOKE ALL ON ALL TABLES" in sql


def test_postgres_writers_lock_and_commit_state_with_durable_receipts() -> None:
    event_code = EVENT_POSTGRES.read_text(encoding="utf-8")
    snapshot_code = SNAPSHOT_POSTGRES.read_text(encoding="utf-8")

    assert "pg_advisory_xact_lock" in event_code
    assert "dayjaview:event-writer" in event_code
    assert "self._connection.commit()" in event_code
    assert "self._connection.rollback()" in event_code
    assert "INSERT INTO event.outbox" in event_code
    assert "INSERT INTO event.command_receipts" in event_code

    assert "snapshot-publication:" in snapshot_code
    assert "snapshot-scope:" in snapshot_code
    assert "FOR UPDATE" in snapshot_code
    assert "INSERT INTO serving.realtime_snapshot_requests" in snapshot_code


def test_event_writer_cannot_update_append_only_audit_or_receipt_tables() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    writer_grants = sql.split("dayjaview_event_writer", 1)[1].split(
        "dayjaview_outbox_publisher", 1
    )[0]

    assert "GRANT SELECT, INSERT, UPDATE ON event.events" in writer_grants
    assert "ON event.command_receipts, event.state_logs, event.outbox" in writer_grants
    assert (
        "GRANT SELECT, INSERT, UPDATE\n            ON event.command_receipts"
        not in writer_grants
    )
