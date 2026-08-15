from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "infra" / "deployment" / "migration-order.sha256"
MIGRATIONS = ROOT / "infra" / "migrations"
RUNNER = ROOT / "infra" / "operations" / "local_migrate.sh"
EXPECTED_ORDER = (
    "0001_identity_library.sql",
    "0001_infostock_store.sql",
    "0002_reference_data.sql",
    "0002_event_realtime.sql",
    "0003_news_catalyst.sql",
    "0004_event_reconciliation.sql",
)


def _manifest_entries() -> list[tuple[str, str]]:
    return [
        tuple(line.split("  ", 1))  # type: ignore[misc]
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _posix_shell() -> Path:
    shell = shutil.which("sh")
    if shell is not None:
        return Path(shell)

    git = shutil.which("git")
    assert git is not None, "POSIX shell 또는 Git 실행 파일이 필요합니다."
    git_shell = Path(git).resolve().parent.parent / "bin" / "sh.exe"
    assert git_shell.is_file(), "Git for Windows의 POSIX shell을 찾지 못했습니다."
    return git_shell


def _run_fixture_runner(
    tmp_path: Path, manifest_payload: bytes
) -> subprocess.CompletedProcess[bytes]:
    migration_name = "0001_fixture.sql"
    migration_payload = b"BEGIN;\nSELECT 1;\nCOMMIT;\n"
    expected_sha = hashlib.sha256(migration_payload).hexdigest()

    migration_root = tmp_path / "migrations"
    migration_root.mkdir()
    (migration_root / migration_name).write_bytes(migration_payload)

    manifest = tmp_path / "migration-order.sha256"
    manifest.write_bytes(
        manifest_payload.replace(b"{sha256}", expected_sha.encode("ascii"))
    )

    runner = tmp_path / "local-migrate"
    runner.write_bytes(RUNNER.read_bytes().replace(b"\r\n", b"\n"))
    runner.chmod(0o755)

    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    psql = stub_bin / "psql"
    psql.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *\"SELECT count(*) FROM dayjaview_fixture.schema_migrations\"*) "
        "printf '1\\n' ;;\n"
        "esac\n",
        encoding="utf-8",
        newline="\n",
    )
    psql.chmod(0o755)

    shell = _posix_shell()
    path_separator = ":" if sys.platform == "win32" else os.pathsep
    environment = os.environ.copy()
    environment.update(
        {
            "DAYJAVIEW_FIXTURE_MODE": "1",
            "MIGRATION_MANIFEST_PATH": manifest.as_posix(),
            "MIGRATION_ROOT": migration_root.as_posix(),
            "PATH": f"{stub_bin.as_posix()}{path_separator}{environment['PATH']}",
        }
    )
    return subprocess.run(
        [shell, runner.as_posix()],
        check=False,
        capture_output=True,
        env=environment,
    )


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
    assert "carriage_return=$(printf '\\r')" in runner
    assert 'migration_name=${migration_name%"$carriage_return"}' in runner
    assert "sha256sum" in runner
    assert "*[!A-Za-z0-9._-]*" in runner
    assert "dayjaview_fixture.schema_migrations" in runner
    assert "migration_name text PRIMARY KEY" in runner
    assert 'sed \'$d\'' in runner
    assert "INSERT INTO dayjaview_fixture.schema_migrations" in runner
    assert "건너뜀(이미 적용됨)" in runner
    assert "적용 수가 manifest와 다릅니다" in runner
    assert "ON_ERROR_STOP=1" in runner


@pytest.mark.parametrize("line_ending", (b"\n", b"\r\n"), ids=("lf", "crlf"))
def test_migration_runner_normalizes_only_manifest_line_ending_cr(
    tmp_path: Path, line_ending: bytes
) -> None:
    completed = _run_fixture_runner(
        tmp_path,
        b"{sha256}  0001_fixture.sql" + line_ending,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert completed.stdout.decode("utf-8").splitlines() == [
        "마이그레이션 fixture 적용 완료: 0001_fixture.sql",
        '{"locale":"ko-KR","status":"COMPLETE","migrationCount":1}',
    ]


@pytest.mark.parametrize(
    "unsafe_name",
    (
        b"0001_\rfixture.sql",
        b"0001_fixture.sql\r\r",
        b"0001_\tfixture.sql",
        b"0001_\x01fixture.sql",
    ),
    ids=("embedded-cr", "extra-trailing-cr", "tab", "control-byte"),
)
def test_migration_runner_rejects_non_line_ending_control_characters(
    tmp_path: Path, unsafe_name: bytes
) -> None:
    completed = _run_fixture_runner(
        tmp_path,
        b"{sha256}  " + unsafe_name + b"\n",
    )

    assert completed.returncode == 1
    assert "허용되지 않은 migration 파일명입니다" in completed.stderr.decode(
        "utf-8", errors="replace"
    )


def test_migration_manifest_contains_no_secret_names_or_values() -> None:
    manifest = MANIFEST.read_text(encoding="utf-8")
    for token in ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "CREDENTIAL"):
        assert token not in manifest.upper()
