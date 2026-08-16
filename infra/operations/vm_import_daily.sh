#!/bin/sh
# VM에 올라간 인포스탁 번들·Daily 본문을 DB에 적재하고 서비스를 올린다.
#
#   bash infra/operations/vm_import_daily.sh
#
# 외부 API를 부르지 않는다. VM에 이미 있는 파일만 읽는다.
set -eu
VM="${1:-ubuntu@api.dayjaview.duckdns.org}"
C="sudo docker compose -f /opt/dayjaview/repo/infra/deployment/compose.production.yml"

echo "== 1) VM에 올라온 Daily 본문 확인"
ssh "$VM" 'ls /opt/dayjaview/data/infostock-daily/details 2>/dev/null | wc -l'

echo "== 2) 적재 (bootstrap 재실행)"
ssh "$VM" "$C up -d --wait infostock-bootstrap 2>&1 || $C logs --tail 20 infostock-bootstrap"

echo "== 3) 서비스 기동"
ssh "$VM" "$C up -d && sleep 25 && $C ps -a"
