#!/bin/sh
# DAYJAVIEW OCI VM 부트스트랩 — deploy_production.sh가 root로 실행한다.
# 여러 번 실행해도 안전하다(멱등). 잔존물은 보고만 하고 지우지 않는다.
set -eu

[ "$(id -u)" = "0" ] || { echo "root로 실행해야 합니다 (sudo bash $0)" >&2; exit 1; }

echo "== 기존 잔존물 (참고 출력 — 삭제하지 않음, ADR-009 검증 항목)"
docker ps -a 2>/dev/null || echo "(docker 미설치 — 아래에서 설치)"
crontab -l 2>/dev/null || echo "(root crontab 없음)"
echo "시간대: $(timedatectl show -p Timezone --value 2>/dev/null || echo 확인불가) — cron 시각은 UTC 기준으로 적는다"

echo "== 방화벽: 80/443 허용 (OCI Ubuntu 기본 iptables REJECT 규칙 앞에 삽입)"
for port in 80 443; do
    if ! iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
        reject_line=$(iptables -L INPUT --line-numbers -n | awk '/REJECT/{print $1; exit}')
        if [ -n "$reject_line" ]; then
            iptables -I INPUT "$reject_line" -p tcp --dport "$port" -j ACCEPT
        else
            iptables -A INPUT -p tcp --dport "$port" -j ACCEPT
        fi
        echo "INPUT $port/tcp ACCEPT 추가"
    fi
done
netfilter-persistent save 2>/dev/null || echo "(netfilter-persistent 없음 — 저장 생략)"

echo "== Docker 설치·부팅 시 자동 기동"
if ! command -v docker >/dev/null 2>&1; then
    apt-get update -q
    DEBIAN_FRONTEND=noninteractive apt-get install -yq docker.io docker-compose-v2
fi
systemctl enable --now docker

echo "== 디렉터리·권한 (데이터 bind mount는 컨테이너 uid 10001 소유)"
mkdir -p /opt/dayjaview/data/infostock-import \
         /opt/dayjaview/data/infostock-daily \
         /opt/dayjaview/data/reference-data \
         /opt/dayjaview/data/intraday-history \
         /opt/dayjaview/data/infostock-increment
chown -R 10001:10001 /opt/dayjaview/data
mkdir -p /opt/dayjaview/backup && chmod 700 /opt/dayjaview/backup
mkdir -p /etc/dayjaview && chmod 700 /etc/dayjaview

echo "== 백업 cron 설치 (매일 07:00 UTC = 16:00 KST; 암호문 생기기 전엔 스스로 건너뜀)"
install -m 700 /opt/dayjaview/repo/infra/operations/vm_backup.sh /opt/dayjaview/vm_backup.sh
install -m 700 /opt/dayjaview/repo/infra/operations/vm_restore_drill.sh \
    /opt/dayjaview/vm_restore_drill.sh
printf '0 7 * * * root /opt/dayjaview/vm_backup.sh >> /var/log/dayjaview-backup.log 2>&1\n' \
    > /etc/cron.d/dayjaview-backup
chmod 644 /etc/cron.d/dayjaview-backup

echo "== 장후 자동 작업 cron 설치 (평일 08:30 UTC = 17:30 KST)"
install -m 700 /opt/dayjaview/repo/infra/operations/run_after_close.sh \
    /opt/dayjaview/run_after_close.sh
printf '30 8 * * 1-5 root /opt/dayjaview/run_after_close.sh >> /var/log/dayjaview-after-close.log 2>&1\n' \
    > /etc/cron.d/dayjaview-after-close
chmod 644 /etc/cron.d/dayjaview-after-close

echo "부트스트랩 완료"
