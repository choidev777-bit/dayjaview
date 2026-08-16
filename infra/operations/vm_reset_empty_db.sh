#!/bin/sh
# 실패한 첫 배포가 남긴 "빈" PostgreSQL 볼륨을 지우고 처음부터 다시 만든다.
# 제품 데이터가 이미 있는 운영 DB에는 절대 쓰지 않는다 — 백업·복구는 runbook 7·8절.
set -eu
VM="${1:-ubuntu@api.dayjaview.duckdns.org}"
C="sudo docker compose -f /opt/dayjaview/repo/infra/deployment/compose.production.yml"
ssh "$VM" "$C down --remove-orphans && sudo docker volume rm dayjaview-production_postgres_data && echo '빈 DB 볼륨 삭제 완료 — 다음 deploy_production.sh가 새로 만든다'"
