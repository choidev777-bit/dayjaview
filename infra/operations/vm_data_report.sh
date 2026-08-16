#!/bin/sh
# 운영 DB 적재량 조회 — 읽기 전용. 행 수만 세고 아무것도 바꾸지 않는다.
set -eu
VM="${1:-ubuntu@api.dayjaview.duckdns.org}"
C="sudo docker compose -f /opt/dayjaview/repo/infra/deployment/compose.production.yml"

ssh "$VM" "$C exec -T postgres psql -U dayjaview -d dayjaview -X -At -F '|' -c \"
SELECT 'theme', count(*) FROM core.infostock_themes
UNION ALL SELECT 'theme_history', count(*) FROM core.infostock_theme_history
UNION ALL SELECT 'history_leader', count(*) FROM core.infostock_theme_history_leaders
UNION ALL SELECT 'stock', count(*) FROM core.infostock_stocks
UNION ALL SELECT 'daily_post', count(*) FROM core.infostock_daily_posts
UNION ALL SELECT 'daily_revision', count(*) FROM core.infostock_daily_post_revisions
UNION ALL SELECT 'daily_relation', count(*) FROM core.infostock_daily_relations
UNION ALL SELECT 'daily_relation_with_quote', count(*) FROM core.infostock_daily_relations
   WHERE to_regclass('core.infostock_daily_relations') IS NOT NULL AND relation_type = 'THEME_STOCK'
UNION ALL SELECT 'news_item', count(*) FROM news.items
UNION ALL SELECT 'ontology_label', count(*) FROM ontology.theme_history_labels
UNION ALL SELECT 'ontology_label_span', count(*) FROM ontology.theme_history_label_spans
UNION ALL SELECT 'ontology_catalyst_type', count(*) FROM ontology.catalyst_types
UNION ALL SELECT 'applied_migration', count(*) FROM dayjaview_fixture.schema_migrations
ORDER BY 1\""
