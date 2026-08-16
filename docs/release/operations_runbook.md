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

## 1~4. 백엔드 배포 — 명령 하나 (최초·갱신 공통)

운영자 PC의 Git Bash에서, 저장소 루트에서:

```bash
bash infra/operations/deploy_production.sh
```

[deploy_production.sh](../../infra/operations/deploy_production.sh)가 순서대로 수행한다. 여러 번 실행해도 안전하다.

1. **코드 전송** — 커밋된 트리를 `git archive`로 `/opt/dayjaview/repo`에 밀어넣는다. 저장소가 비공개라 **VM에는 GitHub 자격증명을 두지 않는다** — VM은 git을 모르고, 배포 원본은 항상 운영자 PC의 HEAD다.
2. **부트스트랩** ([vm_bootstrap.sh](../../infra/operations/vm_bootstrap.sh), 멱등) — 잔존물 보고(삭제 안 함), 방화벽 80/443(OCI Ubuntu의 기본 iptables REJECT 앞에 삽입), Docker 설치·부팅 자동 기동, 데이터 디렉터리(uid 10001)·`/etc/dayjaview`(700), 백업 cron 설치.
3. **secret 주입** — `POSTGRES_PASSWORD`·`SESSION_SIGNING_SECRET`·백업 암호문은 **VM에서 생성해 유지**(재실행에도 안 바뀜), `GOOGLE_OAUTH_*`·`OPERATOR_BOOTSTRAP_GOOGLE_EMAILS`·`KRX_API_KEY`·`OPENDART_API_KEY`·`KIWOOM_*`는 로컬 `.env.local`에서 읽어 `/etc/dayjaview/*.env`(root 0600)로 보낸다. 값은 화면에 출력하지 않는다. `KIWOOM_MODE=real` 고정.
4. **인포스탁 번들 전송** — `data/infostock/import/**` → `/opt/dayjaview/data/infostock-import`.
5. **빌드·기동·health 대기** — ARM64 네이티브 빌드(첫 회 수 분), `up -d`(migrate → api 순서 보장), api healthy까지 최대 3분 대기.

끝나면 스크립트가 안내하는 두 가지를 한다:

```bash
# 외부에서 TLS+health 확인 (인증서 첫 발급에 1~2분)
curl -fsS https://api.dayjaview.duckdns.org/api/health
```

```bash
# 백업 암호문을 비밀번호 관리자에 보관 (잃으면 백업을 못 푼다)
ssh ubuntu@api.dayjaview.duckdns.org 'sudo cat /etc/dayjaview/backup.passphrase'
```

참고:
- 구글 키가 **둘 다** 있어야 실 로그인이다. 반쪽만이면 api가 즉시 기동 실패한다(fixture로 조용히 떨어지지 않음 — 의도된 동작).
- 마이그레이션 이력은 DB의 `dayjaview_fixture.schema_migrations`에 남는다(역사적 스키마 이름 — production도 같은 ledger).
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

[vm_backup.sh](../../infra/operations/vm_backup.sh)가 매일 07:00 UTC(16:00 KST, 장 마감 뒤)에 DB dump와 데이터 디렉터리를 암호화해 `/opt/dayjaview/backup`에 7일 순환 보관한다. cron 등록은 부트스트랩이 이미 했다(`/etc/cron.d/dayjaview-backup`).

운영자 PC에서 주기적으로 당겨온다 (최소 주 1회):

```bash
rsync -av ubuntu@api.dayjaview.duckdns.org:/opt/dayjaview/backup/ ./dayjaview-backup/
```

rsync가 없으면 `scp -r ubuntu@api.dayjaview.duckdns.org:/opt/dayjaview/backup ./dayjaview-backup`로 대체.

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

호스트 전체 손실 시 재구축 순서: `deploy_production.sh` 재실행(새 VM이면 secret이 새로 생성된다 — DB 복원 전에 postgres volume을 비운 상태로) → 데이터 디렉터리를 백업의 `data-*.tar.gz.enc`로 복원 → DB를 운영 `dayjaview`에 pg_restore → 6절(스모크). 이전 백업을 풀려면 **보관해 둔 예전 backup.passphrase**가 필요하다. DuckDNS는 새 VM 공인 IP로 A record만 갱신하면 된다.

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

배포 원본은 운영자 PC의 HEAD이므로 롤백도 운영자 PC에서 한다:

```bash
git log --oneline -5                      # 되돌릴 commit 확인
git checkout <직전 정상 commit>
bash infra/operations/deploy_production.sh
git checkout main                         # 확인 후 복귀
```

- DB는 되돌리지 않는다(roll-forward 원칙). 새 마이그레이션이 포함된 배포가 문제면 앱만 되돌리지 말고 수정본을 앞으로 배포한다.

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
