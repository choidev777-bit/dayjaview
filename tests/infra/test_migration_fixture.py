from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "infra" / "deployment" / "migration-order.sha256"
MIGRATIONS = ROOT / "infra" / "migrations"
RUNNER = ROOT / "infra" / "operations" / "local_migrate.sh"
EXPECTED_ORDER = (
    "0001_identity_library.sql",
    "0001_infostock_store.sql",
    "0002_reference_data.sql",
    "0002_event_realtime.sql",
)


def _manifest_entries() -> list[tuple[str, str]]:
    return [
        tuple(line.split("  ", 1))  # type: ignore[misc]
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_migration_manifest_has_exact_deterministic_order_and_hashes() -> None:
    entries = _manifest_entries()
    assert tuple(name for _, name in entries) == EXPECTED_ORDER
    assert {path.name for path in MIGRATIONS.glob("*.sql")} == set(EXPECTED_ORDER)

    for expected_sha, name in entries:
        payload = (MIGRATIONS / name).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_sha
        lines = (MIGRATIONS / name).read_text(encoding="utf-8").splitlines()
        assert lines[-1] == "COMMIT;"
        assert lines.count("COMMIT;") == 1


def test_migration_runner_tracks_checksum_and_reexecution_idempotency() -> None:
    runner = RUNNER.read_text(encoding="utf-8")

    assert 'DAYJAVIEW_FIXTURE_MODE:-' in runner
    assert "sha256sum" in runner
    assert "*[!A-Za-z0-9._-]*" in runner
    assert "dayjaview_fixture.schema_migrations" in runner
    assert "migration_name text PRIMARY KEY" in runner
    assert 'sed \'$d\'' in runner
    assert "INSERT INTO dayjaview_fixture.schema_migrations" in runner
    assert "건너뜀(이미 적용됨)" in runner
    assert "적용 수가 manifest와 다릅니다" in runner
    assert "ON_ERROR_STOP=1" in runner


def test_migration_manifest_contains_no_secret_names_or_values() -> None:
    manifest = MANIFEST.read_text(encoding="utf-8")
    for token in ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "CREDENTIAL"):
        assert token not in manifest.upper()
