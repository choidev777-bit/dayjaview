#!/bin/sh
set -eu

manifest_path="${MIGRATION_MANIFEST_PATH:-/migration-order.sha256}"
migration_root="${MIGRATION_ROOT:-/migrations}"
carriage_return=$(printf '\r')

fail() {
    printf '마이그레이션 fixture 실패: %s\n' "$1" >&2
    exit 1
}

# fixture 격리 환경 또는 명시적 production 승인 아래에서만 실행한다.
[ "${DAYJAVIEW_FIXTURE_MODE:-}" = "1" ] || [ "${DAYJAVIEW_PRODUCTION_MIGRATION:-}" = "1" ] \
    || fail "DAYJAVIEW_FIXTURE_MODE=1 또는 DAYJAVIEW_PRODUCTION_MIGRATION=1이 필요합니다."
[ -r "$manifest_path" ] || fail "순서 manifest를 읽을 수 없습니다: $manifest_path"

psql -X -v ON_ERROR_STOP=1 <<'SQL'
CREATE SCHEMA IF NOT EXISTS dayjaview_fixture;
CREATE TABLE IF NOT EXISTS dayjaview_fixture.schema_migrations (
    migration_name text PRIMARY KEY,
    sha256 character(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT now()
);
SQL

while IFS='  ' read -r expected_sha migration_name; do
    [ -n "$expected_sha" ] || continue
    migration_name=$(printf '%s' "$migration_name" | sed 's/^ *//')
    migration_name=${migration_name%"$carriage_return"}
    case "$migration_name" in
        *[!A-Za-z0-9._-]*) fail "허용되지 않은 migration 파일명입니다: $migration_name" ;;
    esac
    migration_path="$migration_root/$migration_name"
    [ -r "$migration_path" ] || fail "migration 파일이 없습니다: $migration_name"

    actual_sha=$(sha256sum "$migration_path" | cut -d ' ' -f 1)
    [ "$actual_sha" = "$expected_sha" ] \
        || fail "$migration_name checksum이 manifest와 다릅니다."

    applied_sha=$(psql -X -v ON_ERROR_STOP=1 -At \
        -c "SELECT sha256 FROM dayjaview_fixture.schema_migrations WHERE migration_name = '$migration_name'" \
        2>/dev/null || true)
    if [ -n "$applied_sha" ]; then
        [ "$applied_sha" = "$expected_sha" ] \
            || fail "$migration_name 적용 이력의 checksum이 현재 manifest와 다릅니다."
        printf '마이그레이션 fixture 건너뜀(이미 적용됨): %s\n' "$migration_name"
        continue
    fi

    transaction_file=$(mktemp)
    sed '$d' "$migration_path" > "$transaction_file"
    cat >> "$transaction_file" <<SQL
INSERT INTO dayjaview_fixture.schema_migrations (migration_name, sha256)
VALUES ('$migration_name', '$expected_sha');
COMMIT;
SQL
    if ! psql -X -v ON_ERROR_STOP=1 -f "$transaction_file"; then
        rm -f "$transaction_file"
        fail "$migration_name 적용 중 PostgreSQL 오류가 발생했습니다."
    fi
    rm -f "$transaction_file"
    printf '마이그레이션 fixture 적용 완료: %s\n' "$migration_name"
done < "$manifest_path"

applied_count=$(psql -X -v ON_ERROR_STOP=1 -Atc \
    'SELECT count(*) FROM dayjaview_fixture.schema_migrations')
expected_count=$(grep -c '^[0-9a-f]\{64\}  ' "$manifest_path")
[ "$applied_count" = "$expected_count" ] \
    || fail "적용 수가 manifest와 다릅니다(expected=$expected_count, actual=$applied_count)."

printf '{"locale":"ko-KR","status":"COMPLETE","migrationCount":%s}\n' "$applied_count"
