#!/bin/sh
# 시연 한 줄 실행. 운영 DB 터널을 켜고 로컬 화면 서버를 띄운다.
#
#   bash tools/kg_live/start.sh
#
# 그다음 브라우저에서 http://127.0.0.1:8899 를 연다. Ctrl+C로 둘 다 끝난다.
set -eu
cd "$(dirname "$0")/../.."

grep -q '^KG_LIVE_DATABASE_DSN=' .env.local 2>/dev/null || {
    echo "운영 DB 접속 정보를 먼저 받아옵니다."
    sh tools/kg_live/prod_db_link.sh dsn
}

if ! (exec 3<>/dev/tcp/127.0.0.1/5433) 2>/dev/null; then
    echo "운영 Postgres 터널을 엽니다..."
    sh tools/kg_live/prod_db_link.sh tunnel &
    tunnel_pid=$!
    trap 'kill "$tunnel_pid" 2>/dev/null || true' EXIT INT TERM
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        (exec 3<>/dev/tcp/127.0.0.1/5433) 2>/dev/null && break
        sleep 1
    done
fi

echo "화면 서버를 띄웁니다. 준비되면 http://127.0.0.1:8899 를 여세요."
PYTHONIOENCODING=utf-8 uv run python -m tools.kg_live.server
