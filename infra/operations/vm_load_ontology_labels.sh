#!/bin/sh
# E-17 소재 온톨로지 라벨을 운영 DB에 적재한다.
#
#   bash infra/operations/vm_load_ontology_labels.sh
#
# VM에 이미 올라가 있는 인포스탁 번들만 읽는다. 외부 API를 부르지 않는다.
# 이미 적재된 (history_id, 어휘 버전, 변환 버전)은 건너뛰므로 여러 번 실행해도
# 행이 늘지 않는다.
set -eu
VM="${1:-ubuntu@api.dayjaview.duckdns.org}"
C="sudo docker compose -f /opt/dayjaview/repo/infra/deployment/compose.production.yml"

ssh "$VM" "$C run --rm --no-deps -T infostock-bootstrap \
    python apps/worker-batch/ontology/load_theme_catalyst_labels.py \
    --input-dir /workspace/data/infostock-import \
    --database-url-env INFOSTOCK_DATABASE_URL"
