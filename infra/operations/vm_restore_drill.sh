#!/bin/sh
# Restore an encrypted production dump into a disposable database and remove it.
set -eu

PATH=/usr/sbin:/usr/bin:/sbin:/bin
COMPOSE=/opt/dayjaview/repo/infra/deployment/compose.production.yml
BACKUP_ROOT=/opt/dayjaview/backup
PASSPHRASE=/etc/dayjaview/backup.passphrase
ARCHIVE=${1:-$(find "$BACKUP_ROOT" -maxdepth 1 -type f -name 'db-*.dump.enc' \
    -printf '%T@ %p\n' | sort -nr | sed -n '1s/^[^ ]* //p')}

[ -n "$ARCHIVE" ] || { echo "복구 시험용 DB 백업이 없습니다" >&2; exit 1; }
RESOLVED=$(readlink -f "$ARCHIVE")
case "$RESOLVED" in
    "$BACKUP_ROOT"/db-*.dump.enc) ;;
    *) echo "허용되지 않은 백업 경로입니다: $RESOLVED" >&2; exit 1 ;;
esac
[ -f "$RESOLVED" ] || { echo "백업 파일을 찾을 수 없습니다" >&2; exit 1; }
[ -s "$PASSPHRASE" ] || { echo "backup.passphrase가 없습니다" >&2; exit 1; }

DRILL_DB="dayjaview_restore_drill_$(date +%Y%m%d%H%M%S)_$$"
case "$DRILL_DB" in
    dayjaview_restore_drill_[0-9]*) ;;
    *) echo "안전하지 않은 시험 DB 이름입니다" >&2; exit 1 ;;
esac

cleanup() {
    docker compose -f "$COMPOSE" exec -T postgres \
        dropdb -U dayjaview --if-exists --force "$DRILL_DB" >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM

docker compose -f "$COMPOSE" exec -T postgres \
    createdb -U dayjaview "$DRILL_DB"
openssl enc -d -aes-256-cbc -pbkdf2 -pass "file:$PASSPHRASE" \
    -in "$RESOLVED" \
    | docker compose -f "$COMPOSE" exec -T postgres \
        pg_restore -U dayjaview --exit-on-error --no-owner --no-privileges \
        -d "$DRILL_DB"

TABLE_COUNT=$(docker compose -f "$COMPOSE" exec -T postgres \
    psql -U dayjaview -d "$DRILL_DB" -Atqc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('core','event','identity','library','news','operations','serving');")
[ "$TABLE_COUNT" -gt 0 ] || { echo "복원됐지만 업무 테이블이 없습니다" >&2; exit 1; }

echo "복구 시험 완료: $(basename "$RESOLVED"), 업무 테이블 ${TABLE_COUNT}개"
