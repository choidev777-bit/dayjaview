#!/bin/sh
# Weekday D-14 increment followed by D-13 reconciliation.
set -eu

COMPOSE=/opt/dayjaview/repo/infra/deployment/compose.production.yml
MARKET_DATE=${1:-$(TZ=Asia/Seoul date +%F)}
END_DATE=$(printf '%s' "$MARKET_DATE" | tr -d '-')

cd /opt/dayjaview/repo/infra/deployment
docker compose -f "$COMPOSE" run --rm --no-deps worker-infostock-increment \
    python apps/worker-batch/infostock/collect_increment.py \
    --output-root /workspace/data/infostock-increment \
    --end-date "$END_DATE" --approved
docker compose -f "$COMPOSE" run --rm --no-deps worker-after-close-reconcile \
    python apps/worker-batch/infostock/reconcile_after_close.py \
    --market-date "$MARKET_DATE"
