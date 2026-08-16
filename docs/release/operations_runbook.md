# DAYJAVIEW 운영 runbook — 배포·백업·복구 (F-25)

작성일 2026-08-16. 실제 리소스 생성·변경(OCI, Vercel, DNS, live 수집)은 CLAUDE.md 승인 항목이다 — 이 문서는 절차 기록이며, 실행은 승인 아래에서만 한다.

## 0. 토폴로지

```
브라우저 ── https://dayjaview.vercel.app          (Vercel, 정적 SPA)
   │            └─ /api/* rewrite ──┐  (/api를 벗겨 전달)
   │                                ▼
   └─ wss 직접 ──────────► api.dayjaview.duckdns.org  (OCI VM, Caddy 443)
                                    ▼
                            api 컨테이너 :8000 (serve_live_api, 거래일 루프 내장)
                                    ▼
                            postgres 컨테이너 (backend 내부망, 비공개)
```

- VM: 기존 OCI `VM.Standard.A1.Flex` 4 OCPU·24GB, Ubuntu 22.04 ARM64 (ADR-009). DuckDNS A record 연결 완료.
- compose 프로젝트: [infra/deployment/compose.production.yml](../../infra/deployment/compose.production.yml) · ingress: [Caddyfile.production](../../infra/deployment/Caddyfile.production) (TLS 자동 발급).
- 웹: Vercel Git 연동(`choidev777-bit/dayjaview`, Root Directory `apps/web`), rewrite는 [apps/web/vercel.json](../../apps/web/vercel.json).
- 별도 market worker 컨테이너는 없다 — 키움 접속·거래일 전환(A-8)은 api 프로세스 안의 `TradingDayLoop`가 수행한다. 기준정보(KRX·OpenDART)도 거래일 세션 준비 때 api가 직접 수집한다.
- Redis는 배포하지 않는다 — 코드가 읽지 않는다(F-23에서 확인).

## 1. VM 준비 (최초 1회)

```bash
# SSH 접속은 운영자 단말의 키로만 한다(키 복사 금지, ADR-009 7항)
ssh ubuntu@api.dayjaview.duckdns.org

# 1) 이전 프로젝트 잔존 확인·정리 (ADR-009 검증 항목)
docker ps -a && docker volume ls && crontab -l && systemctl list-units --type=service --state=running

# 2) OS 업데이트·Docker 설치·부팅 시 자동 기동
sudo apt-get update && sudo apt-get -y upgrade
sudo apt-get -y install docker.io docker-compose-v2
sudo systemctl enable --now docker

# 3) 방화벽 — 공개는 22/80/443뿐 (OCI 콘솔 security list와 VM ufw 모두)
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable

# 4) 디렉터리 — 데이터는 bind mount, 소유자는 컨테이너 uid 10001
sudo mkdir -p /opt/dayjaview/data/{infostock-import,reference-data,intraday-history,infostock-increment}
sudo chown -R 10001:10001 /opt/dayjaview/data
sudo mkdir -p /opt/dayjaview/backup && sudo chmod 700 /opt/dayjaview/backup

# 5) 코드
sudo git clone https://github.com/choidev777-bit/dayjaview.git /opt/dayjaview/repo
```

## 2. Secret 주입 (최초 1회, 회전 시 갱신)

`/etc/dayjaview/*.env` — Git 밖 root 소유 0600 파일(ADR-009 7항). **값은 운영자가 직접 입력**하고 채팅·로그에 붙여넣지 않는다. `.env.local`의 같은 이름 항목에서 옮긴다.

```bash
sudo mkdir -p /etc/dayjaview && sudo chmod 700 /etc/dayjaview
sudo touch /etc/dayjaview/{postgres,migrate,api,worker}.env /etc/dayjaview/backup.passphrase
sudo chmod 600 /etc/dayjaview/* && sudo chown root:root /etc/dayjaview/*
```

| 파일 | 항목 (이름만 — 값은 직접 입력) |
|---|---|
| `postgres.env` | `POSTGRES_PASSWORD` (새로 생성한 강한 무작위 값) |
| `migrate.env` | `PGPASSWORD` (= POSTGRES_PASSWORD) |
| `api.env` | `DATABASE_URL=postgresql://dayjaview:<POSTGRES_PASSWORD>@postgres:5432/dayjaview` · `SESSION_SIGNING_SECRET`(32바이트 이상, 없으면 기동 실패) · `GOOGLE_OAUTH_CLIENT_ID` · `GOOGLE_OAUTH_CLIENT_SECRET` · `OPERATOR_BOOTSTRAP_GOOGLE_EMAILS` · `KRX_API_KEY` · `OPENDART_API_KEY` · `KIWOOM_MODE`(real 또는 demo) · `KIWOOM_APP_KEY` · `KIWOOM_APP_SECRET` · (선택) `KIWOOM_CONDITION_IDS` |
| `worker.env` | `INFOSTOCK_DATABASE_URL` (= DATABASE_URL과 같은 값) |
| `backup.passphrase` | 백업 암호화 문구 한 줄 (VM 밖 금고에도 보관 — 잃으면 백업을 못 푼다) |

주의: 구글 키가 **둘 다** 있어야 실 로그인이다. 반쪽만 넣으면 api가 즉시 기동 실패한다(fixture로 조용히 떨어지지 않음 — 의도된 동작).

## 3. 데이터 반입 (최초 1회)

인포스탁 280테마 import 번들(gitignore 대상)을 운영자 PC에서 올린다.

```bash
# 운영자 PC에서
rsync -av --chown=10001:10001 ./data/infostock/import/ ubuntu@api.dayjaview.duckdns.org:/tmp/infostock-import/
ssh ubuntu@api.dayjaview.duckdns.org "sudo rsync -a --chown=10001:10001 /tmp/infostock-import/ /opt/dayjaview/data/infostock-import/ && rm -rf /tmp/infostock-import"
```

## 4. 백엔드 배포 (갱신 시 반복)

```bash
cd /opt/dayjaview/repo && sudo git pull
cd infra/deployment
sudo docker compose -f compose.production.yml build          # ARM64 네이티브 빌드
sudo docker compose -f compose.production.yml up -d          # migrate → api 순서는 compose가 보장
sudo docker compose -f compose.production.yml ps             # api (healthy) 확인
curl -fsS https://api.dayjaview.duckdns.org/api/health       # TLS + health 한 번에 확인
```

- 마이그레이션 이력은 DB의 `dayjaview_fixture.schema_migrations`에 남는다(역사적 스키마 이름 — production에서도 같은 ledger를 쓴다).
- api는 `TRUSTED_PROXY_HOPS=1`로 돈다. Caddy(2.7+)가 X-Forwarded-For를 접속 주소로 강제하므로 위조가 안 되는 값이다. Vercel rewrite 경유 요청은 Vercel edge 주소로 집계되므로, 로그인 진입(`/auth/google`) 429가 정상 사용자에게 자주 보이면 이 설계를 재검토한다(F-24 발견 2 참고).

## 5. 웹 배포 (Vercel — 사용자 계정 작업)

1. Vercel 대시보드 → Add New Project → GitHub `choidev777-bit/dayjaview` 연결.
2. Root Directory `apps/web`, Framework `Vite` (자동 감지), 환경변수 불필요(운영 기본값 내장).
3. Production 도메인이 `dayjaview.vercel.app`인지 확인 — OAuth redirect·CSP·CORS가 이 origin에 고정돼 있다.
4. 구글 클라우드 콘솔 → OAuth 클라이언트 → 승인된 리디렉션 URI에 `https://dayjaview.vercel.app/api/auth/google/callback` 추가 (F-21 ①).
5. 이후 `main` push마다 자동 재배포된다.

## 6. 배포 후 스모크

| 확인 | 명령/방법 | 기대 |
|---|---|---|
| API TLS·health | `curl -fsS https://api.dayjaview.duckdns.org/api/health` | `"status": "HEALTHY", "fixtureMode": false` |
| 웹 | 브라우저 `https://dayjaview.vercel.app` | 화면 렌더, 콘솔 CSP 오류 없음 |
| rewrite | `curl -fsS https://dayjaview.vercel.app/api/health` | 위와 같은 응답 |
| 로그인 왕복 | 브라우저에서 구글 로그인 | `/auth/session` roles에 USER(운영자 메일이면 +OPERATOR) |
| WSS | 로그인 후 홈 화면 | 실시간 상태 연결 표시(비거래일은 PREOPEN이 정상) |
| 거래일 동작 | 다음 개장일 장중 | rankings 갱신 — **키움·기준정보 live 호출 발생(승인 항목 2)** |

## 7. 백업 (매일 자동)

원칙(ADR-009 8항): PostgreSQL과 수집 데이터를 **암호화해 VM 밖으로**. VM에는 외부로 미는 자격증명을 두지 않는다 — VM은 만들고, 운영자 PC가 당겨간다.

`/opt/dayjaview/backup.sh`를 아래 내용으로 만들고 `sudo chmod 700`:

```bash
#!/bin/sh
# 매일 DB dump + 데이터 디렉터리를 암호화 보관, 7일 순환
set -eu
stamp=$(date +%Y%m%d)
out=/opt/dayjaview/backup
cd /opt/dayjaview/repo/infra/deployment
docker compose -f compose.production.yml exec -T postgres \
  pg_dump -U dayjaview -d dayjaview --format=custom \
  | openssl enc -aes-256-cbc -pbkdf2 -pass file:/etc/dayjaview/backup.passphrase \
  > "$out/db-$stamp.dump.enc"
tar -C /opt/dayjaview -cz data \
  | openssl enc -aes-256-cbc -pbkdf2 -pass file:/etc/dayjaview/backup.passphrase \
  > "$out/data-$stamp.tar.gz.enc"
find "$out" -name '*.enc' -mtime +7 -delete
```

```bash
# root crontab (sudo crontab -e) — 매일 16:00 KST = 07:00 UTC (장 마감 뒤)
0 7 * * * /opt/dayjaview/backup.sh >> /var/log/dayjaview-backup.log 2>&1
```

```bash
# 운영자 PC에서 주기적으로 당겨오기 (최소 주 1회)
rsync -av ubuntu@api.dayjaview.duckdns.org:/opt/dayjaview/backup/ ./dayjaview-backup/
```

## 8. 복구

**복원 drill은 공개 출시 전 1회 필수**(ADR-009). 운영 DB를 덮지 말고 임시 DB에 풀어 검증한다.

```bash
# DB 복원 (drill: dayjaview_restore 데이터베이스에)
cd /opt/dayjaview/repo/infra/deployment
docker compose -f compose.production.yml exec -T postgres createdb -U dayjaview dayjaview_restore
openssl enc -d -aes-256-cbc -pbkdf2 -pass file:/etc/dayjaview/backup.passphrase \
  < /opt/dayjaview/backup/db-<날짜>.dump.enc \
  | docker compose -f compose.production.yml exec -T postgres pg_restore -U dayjaview -d dayjaview_restore
# 표 개수·최근 행으로 눈 검증 후: dropdb -U dayjaview dayjaview_restore
```

호스트 전체 손실 시 재구축 순서: 1절(VM 준비) → 2절(secret — 금고 사본) → 3절(데이터: 백업의 `data-*.tar.gz.enc` 복원) → 4절(배포) → DB를 운영 `dayjaview`에 pg_restore → 6절(스모크). DuckDNS는 새 VM 공인 IP로 A record만 갱신하면 된다.

```bash
# 데이터 디렉터리 복원
openssl enc -d -aes-256-cbc -pbkdf2 -pass file:/etc/dayjaview/backup.passphrase \
  < data-<날짜>.tar.gz.enc | sudo tar -C /opt/dayjaview -xz
sudo chown -R 10001:10001 /opt/dayjaview/data
```

## 9. 인포스탁 일일 증분 cron (승인 항목 2 이후에만 등록)

`worker-infostock-increment`는 profile 뒤라 `up`으로는 돌지 않는다. live 호출 승인 후:

```bash
# root crontab — 평일 17:30 KST = 08:30 UTC (장후, 특징테마 게시 뒤)
30 8 * * 1-5 cd /opt/dayjaview/repo/infra/deployment && docker compose -f compose.production.yml run --rm worker-infostock-increment >> /var/log/dayjaview-infostock.log 2>&1
```

종료 코드: 0 SUCCEEDED · 2 PARTIAL · 3 AUTH_REQUIRED(세션 재인증 필요) · 4 RATE_LIMITED · 1 FAILED. 같은 구간 재실행은 reused로 끝나 안전하다.

## 10. 롤백

```bash
cd /opt/dayjaview/repo && sudo git log --oneline -5     # 되돌릴 commit 확인
sudo git checkout <직전 정상 commit>
cd infra/deployment
sudo docker compose -f compose.production.yml build
sudo docker compose -f compose.production.yml up -d
```

- DB는 되돌리지 않는다(roll-forward 원칙). 새 마이그레이션이 포함된 배포가 문제면 앱만 되돌리지 말고 수정본을 앞으로 배포한다.
- 롤백 뒤 `main`을 다시 배포할 준비가 되면 `sudo git checkout main && sudo git pull`.

## 11. 재부팅 복구

`docker.service`가 enable돼 있고 장기 서비스가 `restart: unless-stopped`라 재부팅 후 자동 복귀한다. 확인:

```bash
sudo reboot   # (계획 재부팅 시)
# 재접속 후
sudo docker compose -f /opt/dayjaview/repo/infra/deployment/compose.production.yml ps
curl -fsS https://api.dayjaview.duckdns.org/api/health
```

## 12. 일상 확인 명령

| 목적 | 명령 |
|---|---|
| 컨테이너 상태 | `sudo docker compose -f compose.production.yml ps` |
| api 로그 | `sudo docker compose -f compose.production.yml logs -f --tail 200 api` |
| 거래일 세션 로그 | api 로그에서 `기준정보`·`identity 조립` 검색 |
| DB 콘솔 | `sudo docker compose -f compose.production.yml exec postgres psql -U dayjaview -d dayjaview` |
| 디스크 | `df -h /` · `sudo du -sh /opt/dayjaview/data/* /opt/dayjaview/backup` |
| 인증서 | Caddy가 자동 갱신 — `sudo docker compose -f compose.production.yml logs caddy \| grep -i cert` |
