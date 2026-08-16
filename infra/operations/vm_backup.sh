#!/bin/sh
# DAYJAVIEW 일일 백업 — root cron이 실행한다(설치는 vm_bootstrap.sh).
# DB dump와 데이터 디렉터리를 암호화해 /opt/dayjaview/backup에 7일 순환 보관한다.
# 운영자 PC가 주기적으로 rsync로 당겨간다(VM에는 밖으로 미는 자격증명을 두지 않는다).
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin

passphrase=/etc/dayjaview/backup.passphrase
[ -s "$passphrase" ] || { echo "backup.passphrase가 아직 없어 건너뜀"; exit 0; }

stamp=$(date +%Y%m%d)
out=/opt/dayjaview/backup
compose="docker compose -f /opt/dayjaview/repo/infra/deployment/compose.production.yml"

$compose exec -T postgres pg_dump -U dayjaview -d dayjaview --format=custom \
    | openssl enc -aes-256-cbc -pbkdf2 -pass "file:$passphrase" \
    > "$out/db-$stamp.dump.enc"

tar -C /opt/dayjaview -cz data \
    | openssl enc -aes-256-cbc -pbkdf2 -pass "file:$passphrase" \
    > "$out/data-$stamp.tar.gz.enc"

find "$out" -name '*.enc' -mtime +7 -delete
echo "백업 완료: db-$stamp.dump.enc, data-$stamp.tar.gz.enc"
