#!/bin/sh
# 배포 상태 진단 — 컨테이너 상태와 핵심 로그 꼬리만 읽는다. 변경 없음.
set -eu
VM="${1:-ubuntu@api.dayjaview.duckdns.org}"
C="sudo docker compose -f /opt/dayjaview/repo/infra/deployment/compose.production.yml"
ssh "$VM" "$C ps -a; echo '--- api healthcheck 기록 ---'; sudo docker inspect --format '{{json .State.Health}}' dayjaview-production-api-1 2>&1 | tail -c 1200 || true; echo; echo '--- migrate 로그 ---'; $C logs --tail 40 migrate 2>&1 || true; echo '--- infostock-bootstrap 로그 ---'; $C logs --tail 40 infostock-bootstrap 2>&1 || true; echo '--- api 로그 ---'; $C logs --tail 40 api 2>&1 || true; echo '--- caddy 로그 ---'; $C logs --tail 15 caddy 2>&1 || true"
