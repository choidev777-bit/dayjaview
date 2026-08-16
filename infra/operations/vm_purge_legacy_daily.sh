#!/bin/sh
# legacy page-1 경로로 들어간 Daily 적재분을 지운다 — 파괴적 작업이다.
#
#   bash infra/operations/vm_purge_legacy_daily.sh CONFIRM
#
# 왜 필요한가: legacy 적재분의 관측 시각이 backfill 수집 시각보다 뒤라서
# 시간 역행 가드에 막혀 backfill이 들어가지 못한다. 지우는 게시물은 전건
# backfill에 더 완전한 형태로 들어 있음을 대조로 확인했다.
#
# 지우는 범위는 Daily뿐이다. 테마·종목·뉴스·온톨로지 라벨은 건드리지 않는다.
# 실행 전 백업을 뜬다.
set -eu
VM="${2:-ubuntu@api.dayjaview.duckdns.org}"
C="sudo docker compose -f /opt/dayjaview/repo/infra/deployment/compose.production.yml"

[ "${1:-}" = "CONFIRM" ] || {
    echo "파괴적 작업입니다. 실행하려면 CONFIRM 인자를 주세요." >&2
    exit 1
}

echo "== 1) 백업"
ssh "$VM" 'sudo /opt/dayjaview/vm_backup.sh && sudo ls -la /opt/dayjaview/backup'

echo "== 2) 삭제 (한 트랜잭션)"
ssh "$VM" "$C exec -T postgres psql -U dayjaview -d dayjaview -X -v ON_ERROR_STOP=1 -c \"
BEGIN;
DELETE FROM core.infostock_daily_relations;
DELETE FROM ingest.infostock_daily_list_entries;
DELETE FROM core.infostock_daily_post_revisions;
DELETE FROM core.infostock_daily_posts;
COMMIT;
SELECT 'daily_post' AS 표, count(*) FROM core.infostock_daily_posts
UNION ALL SELECT 'daily_revision', count(*) FROM core.infostock_daily_post_revisions
UNION ALL SELECT 'daily_relation', count(*) FROM core.infostock_daily_relations
UNION ALL SELECT 'daily_list_entry', count(*) FROM ingest.infostock_daily_list_entries
UNION ALL SELECT 'theme_history(보존)', count(*) FROM core.infostock_theme_history
UNION ALL SELECT 'ontology_label(보존)', count(*) FROM ontology.theme_history_labels
ORDER BY 1\""
