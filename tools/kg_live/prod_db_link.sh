#!/bin/sh
# 운영 Postgres(컨테이너 내부)로 읽기 전용 터널을 준비한다.
#
#   bash tools/kg_live/prod_db_link.sh probe   # 컨테이너 IP만 확인
#   bash tools/kg_live/prod_db_link.sh dsn     # .env.local에 KG_LIVE_DATABASE_DSN append
#   bash tools/kg_live/prod_db_link.sh tunnel  # 127.0.0.1:5433 로 포워딩(포그라운드)
#
# 값(비밀번호)은 어떤 경우에도 표준출력에 찍지 않는다.
set -eu
VM="${VM:-ubuntu@api.dayjaview.duckdns.org}"
CID='sudo docker inspect -f "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}" dayjaview-production-postgres-1'

case "${1:-probe}" in
probe)
    ip=$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$VM" "$CID" 2>/dev/null | tr -d '\r')
    [ -n "$ip" ] || { echo "postgres 컨테이너 IP를 못 찾았습니다." >&2; exit 1; }
    echo "postgres_container_ip=$ip"
    ;;
dsn)
    ip=$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$VM" "$CID" 2>/dev/null | tr -d '\r')
    [ -n "$ip" ] || { echo "postgres 컨테이너 IP를 못 찾았습니다." >&2; exit 1; }
    pw=$(ssh -o BatchMode=yes "$VM" "sudo sed -n 's/^POSTGRES_PASSWORD=//p' /etc/dayjaview/postgres.env | tail -1" 2>/dev/null | tr -d '\r')
    [ -n "$pw" ] || { echo "POSTGRES_PASSWORD를 못 읽었습니다." >&2; exit 1; }
    sed -i '/^KG_LIVE_DATABASE_DSN=/d' .env.local 2>/dev/null || true
    printf 'KG_LIVE_DATABASE_DSN=postgresql://dayjaview:%s@127.0.0.1:5433/dayjaview\n' "$pw" >> .env.local
    printf 'KG_LIVE_PG_CONTAINER_IP=%s\n' "$ip" > .tmp/kg_live_pg_ip 2>/dev/null || \
        { mkdir -p .tmp && printf 'KG_LIVE_PG_CONTAINER_IP=%s\n' "$ip" > .tmp/kg_live_pg_ip; }
    echo ".env.local에 KG_LIVE_DATABASE_DSN 기록 완료 (값 미출력). container_ip=$ip"
    ;;
tunnel)
    ip=$(sed -n 's/^KG_LIVE_PG_CONTAINER_IP=//p' .tmp/kg_live_pg_ip 2>/dev/null | tail -1)
    [ -n "$ip" ] || ip=$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$VM" "$CID" 2>/dev/null | tr -d '\r')
    [ -n "$ip" ] || { echo "postgres 컨테이너 IP를 못 찾았습니다." >&2; exit 1; }
    echo "터널 시작: 127.0.0.1:5433 -> $ip:5432 (Ctrl+C로 종료)"
    exec ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -N -L "5433:$ip:5432" "$VM"
    ;;
*)
    echo "사용법: probe | dsn | tunnel" >&2; exit 2 ;;
esac
