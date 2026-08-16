#!/bin/sh
# 부트스트랩이 막혀 API가 못 뜰 때 서비스만 먼저 올린다.
#
#   bash infra/operations/vm_start_services.sh
#
# --no-deps로 depends_on 게이트를 건너뛴다. 적재 상태는 바꾸지 않으며
# 부트스트랩 실패 원인은 따로 고쳐야 한다.
set -eu
VM="${1:-ubuntu@api.dayjaview.duckdns.org}"
C="sudo docker compose -f /opt/dayjaview/repo/infra/deployment/compose.production.yml"

ssh "$VM" "$C up -d --no-deps api worker-news caddy && sleep 20 && $C ps -a"
