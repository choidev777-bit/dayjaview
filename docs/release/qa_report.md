# DAYJAVIEW 품질 점검 (F-24)

- **점검일**: 2026-08-16 · 기준 commit `ba88b66`
- **범위**: 기계 계약, 파이썬 unit/integration, 웹 unit, 핵심 fixture E2E, 접근성, 성능
- **원칙**: 점검 중에는 제품 파일을 고치지 않았다. 실행한 검증과 재현 조건, 발견만 남겼다.
- **수리(2026-08-16, 점검 이후)**: 발견 1(배포 차단)만 고쳤다. 2·3·4·5는 그대로다. 아래 "발견 1" 절의 본문은 *수리 전 상태* 설명이고, 무엇을 고쳤는지는 같은 절 끝의 **수리** 항목이 기준이다.
- **저장 market replay는 실행하지 않았다.** `data/market-replay*`의 저장 capture를 재생하지 않았고 gap recovery도 돌리지 않았다. `tests/test_market_replay.py`·`tests/market-replay-adapter/**`의 offline unit test만 전체 스위트에 포함돼 돌았다.
- **외부 호출 없음**: 키움·인포스탁·OpenDART·구글 live 호출은 하지 않았다. 모든 측정은 저장된 수집본과 fixture로 했다.

---

## 실행한 검증

| 축 | 명령·방법 | 결과 |
|---|---|---|
| 계약 | `uv run python scripts/validate_contracts.py` | 통과 — HTTP 30 · WS 9 · schema 79 · fixture 43 · 문서 JSON 예시 21 |
| 계약 test | `uv run pytest tests/contracts -q` | 11 passed |
| 파이썬 전체 | `uv run pytest -q` | **518 passed · 8 skipped** (skip 8건 전부 DSN 필요한 PostgreSQL 통합) |
| 파이썬 lint | `uv run ruff check .` | All checks passed |
| 파이썬 타입 | `uv run mypy` | 108개 파일 no issues |
| 웹 lint | `pnpm --dir apps/web run lint` (`--max-warnings=0`) | 통과 |
| 웹 타입 | `pnpm --dir apps/web run typecheck` | 통과 |
| 웹 test | `pnpm --dir apps/web run test -- --run` | 8 files · **83 passed** |
| 웹 build | `pnpm --dir apps/web run build` | 727 modules · production fixture boundary 9개 산출물 확인 |
| 스택 config | `docker compose -f infra/deployment/compose.local.yml config` (Docker 29.7.2) | 통과 (컨테이너 기동은 미실행) |
| E2E | fixture API(`serve_fixture_api`, :8000) + web dev server(:5173)를 실제로 띄우고 브라우저로 주행 | 아래 표 |
| 접근성 | 실행 중인 6개 화면에서 axe-core 4.13.0, WCAG 2.0/2.1 A·AA + best-practice | 위반 23곳 (전부 색상 대비) |
| 성능 | 실규모 우주(280 테마 · 2,411 종목)로 파이프라인 지연, 실행 중인 API로 REST·WS 지연 | 아래 표 |

측정 환경: Windows 11 · CPython 3.12 · in-memory Event/Snapshot 저장소(Postgres 아님). 배포는 Postgres를 쓰므로 publish 비용은 여기 값보다 커진다.

---

## 발견 요약

| # | 심각도 | 항목 | 재현 | 상태 |
|---|---|---|---|---|
| 1 | **높음(배포 차단)** | Coverage 100% 테마에서 `observed_weight_ratio`가 1을 미세하게 넘겨 publish 전체가 ValueError로 죽고, 발행 루프가 조용히 멈춘다 | 실규모 우주 5주기 내 | **수리** |
| 2 | 중간 | `/auth/*` 요청 한도가 인증된 `/auth/session`까지 함께 세고 프록시 뒤에서 전 사용자가 한 통을 공유한다 | 이번 E2E 중 브라우저 하나로 429 발생 | 미수리 |
| 3 | 중간 | 색상 대비 WCAG AA 미달 23곳 (6화면 중 5화면) | axe-core 실행 중 화면 | 미수리 |
| 4 | 정보성 | 웹 접근성 테스트가 `color-contrast` 규칙을 끈 채 통과한다 — 3번은 CI에서 절대 안 걸린다 | `apps/web/src/test/accessibility.test.tsx:16` | 미수리 |
| 5 | 정보성 | publish 한 주기가 실규모에서 0.33~1.0초. 85%가 `_select_references` 선형 스캔 | cProfile | 미수리 |

---

## 1. (높음) Coverage 100% 테마에서 publish가 ValueError로 죽는다

`packages/calculations/theme_metrics.py:301-320`이 분모와 분자를 **다른 정밀도로** 더한다.

- 분모 `total_capitalization`: 기본 context(28자리)에서 합산 → 반올림된다.
- 분자: `localcontext(prec=60)` 안에서 합산 → 반올림되지 않는다.

핵심 종목을 **전부** 관측한 테마는 분자와 분모가 같은 항목의 합인데, 분모만 28자리로 깎여 몫이 1을 아주 조금 넘는다. 실측값:

```
observed_weight_ratio = 1.00000000000000000000000000021567689661155042496495031472971
1과의 차이 = 2.16e-28   (core 9종목, 시총 합 21,328,200,063,472.39987840873590)
```

`packages/domain/coverage.py:34`의 `if not Decimal(0) <= ratio <= Decimal(1)`이 이 값을 거부해 `ValueError`를 던진다.

**왜 배포 차단인가**

- `DirtyThemeAggregator.drain`은 원자적이다. 계산이 터지면 dirty 항목을 지우지 않으므로 **다음 주기도 같은 테마에서 같은 자리에서 다시 터진다.**
- `MarketPublishLoop.run`·`TradingDayLoop.run`에 예외 처리가 없다(`packages/pipeline/runner.py:79-86`, `147-153`). 예외는 `asyncio.create_task(publish_loop.run())`이 만든 task 안에서 끝나고, lifespan은 shutdown 때까지 그 task를 보지 않는다(`apps/api/serve.py:300-316`).
- 그래서 **화면에는 장애가 드러나지 않는다.** API는 마지막 스냅샷을 계속 200으로 서빙하고, `/api/health`는 발행 루프 상태를 보지 않는다. 시각이 멈춘 것을 사용자가 데이터 지연 표시로도 알 수 없다.

**재현**: 저장된 수집본으로 실규모 우주를 조립하고(테마 280 · 종목 2,411 · reference 2,411, 전일종가 2,410건) 전 종목에 체결을 흘리면 5주기 안에 발생한다. 기존 테스트가 못 잡은 이유는 fixture 시총이 작아 합이 28자리를 넘지 않기 때문이다. 구독 200종목 프로파일에서는 핵심 종목이 부분 관측이라 분자가 분모보다 확실히 작아 발생하지 않았다.

**수리 (2026-08-16)**: 분모 합산을 분자와 같은 `localcontext(prec=60)` 안으로 옮겼다(`packages/calculations/theme_metrics.py:301-317`). 두 합이 같은 정밀도에서 정확히 계산되므로 전량 관측이면 몫이 정확히 1이고, 부분 관측이면 그대로 1 미만이다. 시총은 음수가 될 수 없으니 분자가 분모를 넘는 경우는 남지 않는다.

- 회귀 테스트: `tests/calculations/test_theme_metrics.py::test_full_core_observation_keeps_weight_ratio_at_one_with_real_scale_caps` — 조 단위 시총 3종목을 전량 관측시켜 비율이 정확히 1임을 고정한다. 수리 전 코드에서는 같은 ValueError로 실패한다(확인함).
- 실규모 재현 재실행: 전 종목 확산 프로파일이 40주기를 끝까지 완주하고 **순위 98건**을 만든다(수리 전에는 중단). 98은 A-2가 기록한 "Coverage SUFFICIENT 후보 99개"와 같은 자릿수다.
- **고치지 않은 것**: 발행 루프의 실패 처리 방식은 그대로다(`packages/pipeline/runner.py:79-86`, `147-153`). 다른 원인으로 `tick()`이 터지면 여전히 task가 조용히 멈추고 API는 마지막 스냅샷을 계속 서빙한다. 어떤 동작이 맞는지(테마 하나만 건너뛰기 / 상태를 DEGRADED로 내리기 / health에 드러내기)는 설계 결정이라 별도 작업이다.

## 2. (중간) 인증 진입점 요청 한도가 정상 사용자를 막는다

`apps/api/app.py:132`의 한도는 경로 접두사 `/auth/`에 통째로 걸린다(`AUTH_RATE_LIMIT=20`, `AUTH_RATE_WINDOW=5분`). 여기에는 로그인 시작·콜백뿐 아니라 **이미 로그인한 사용자의 `/auth/session` 조회**도 들어간다. 웹은 앱이 뜰 때마다 `/api/auth/session`을 호출한다(`apps/web/src/adapters/productionRepository.ts:269`, 운영자 콘솔도 별도로 호출).

- 이번 E2E 중 브라우저 한 대로 5분 안에 20회를 넘겨 로그인 콜백이 `429 RATE_LIMITED`로 막혔다. 세는 단위는 전송 계층 주소뿐이다(`apps/api/http.py:145-150`, 프록시 header는 위조 가능하므로 의도적으로 무시).
- 배포에서 API 앞에 프록시·LB가 서면 **모든 사용자가 그 프록시 주소 하나로 합산된다.** 20회/5분은 전체 사용자 몫이 되어 정상 사용자가 로그인·세션 확인에서 429를 받는다. 회사망·모바일 CGNAT처럼 출구 주소를 공유하는 경우도 같다.

## 3. (중간) 색상 대비 WCAG AA 미달 23곳

실행 중인 화면에서 axe-core로 잰 값이다. 규칙 위반은 전부 `color-contrast` 하나이고, 이름·역할·구조·폼·랜드마크 규칙 위반은 0건이다.

| 화면 | 위반 노드 | 통과 검사 | 가장 나쁜 값 |
|---|---|---|---|
| 로그인 | 2 | 28 | `#cc5200` on `#eae8e3` = 3.59 (필요 4.5) |
| `/today` | 2 | 33 | `#ff6600` on `#eae8e3` = **2.39** (필요 3.0, 30px) |
| `/insights` | 1 | 31 | `#a76700` on `#f3f4f5` = 4.16 (필요 4.5) |
| `/saved` | 5 | 33 | `#ff6600` on `#fff2ec` = **2.67** (활성 탭 라벨 "즐겨찾기") |
| 테마 상세 | 13 | 36 | 흰 글자 on `#ff9e5e` = **2.03** (상태 칩 "활성") |
| `/operator.html` | **0** | 25 | — |

반복되는 원인은 브랜드 오렌지 계열(`#ff6600`·`#ff781f`·`#ff9e5e`·`#cc5200`)과 상승 빨강(`#e5484d`)을 작은 글자·밝은 배경에 쓴 것이다. 개별 요소가 아니라 토큰 문제라 한곳에서 고칠 수 있다.

## 4. (정보성) 접근성 테스트가 색상 대비를 끄고 통과한다

`apps/web/src/test/accessibility.test.tsx:16,24`가 `rules: { 'color-contrast': { enabled: false } }`로 axe를 돌린다. jsdom은 색을 계산하지 못하므로 이 설정 자체는 불가피하다. 결과적으로 3번의 23곳은 **CI가 구조적으로 볼 수 없는 영역**이고, 실제 브라우저에서 돌려야만 드러난다.

## 5. (정보성) publish 한 주기 비용의 85%가 참조 선택 선형 스캔이다

`packages/realtime/aggregation.py:277`의 `_select_references`가 dirty 테마의 **구성종목마다 전체 reference 튜플(2,411건)을 처음부터 끝까지 훑는다.** cProfile(구독 200종목 프로파일, publish 5회 = 2.15초):

| 함수 | 누적 | 비중 |
|---|---|---|
| `publish` | 2.173s | 100% |
| └ `DirtyThemeAggregator.drain` | 2.129s | 98% |
| &nbsp;&nbsp;└ **`_select_references`** (875회) | 1.829s | **85%** |
| &nbsp;&nbsp;└ `calculate_theme_metrics` | 0.241s | 11% |

---

## 성능 측정치

### 파이프라인 (PRD 533-536 목표 대비)

실규모 우주(테마 280 · 종목 2,411 · reference 2,411).

| 구간 | 목표 | p50 | p95 | p99 | max | 판정 |
|---|---|---|---|---|---|---|
| 체결 수신 → 서버 종목 상태 반영 (`apply_update`, n=20,000) | P95 500ms | 3.47ms | **6.21ms** | 8.95ms | 27.02ms | 통과 (여유 80배) |
| 영향 테마 재계산 + 스냅샷 발행 (`publish`, 구독 200종목·초당 500체결, n=40) | P95 1s | 332.5ms | **393.0ms** | 474.3ms | 474.3ms | 통과 |
| 같은 구간 (구독 200종목·초당 100체결, n=40) | P95 1s | 346.3ms | **407.8ms** | 568.1ms | 568.1ms | 통과 |
| 같은 구간 (전 종목 2,411개 확산, n=40) | P95 1s | 910.9ms | **995.9ms** | 1,142.7ms | 1,142.7ms | 아슬아슬 — 목표선에 붙는다 |
| 같은 구간, 발견 1 수리 후 · 순위 98건 실제 산출 (n=40) | P95 1s | 597.5ms | **773.3ms** | 948.0ms | 948.0ms | 통과 |

- 전 종목 확산 첫 줄은 reference가 비었던 조건(Coverage 전부 INSUFFICIENT·순위 0건)에서 완주한 값이다. reference를 채운 같은 프로파일은 **발견 1로 중단**됐고, 수리 후 다시 돌린 값이 둘째 줄이다. 순위 98건을 실제로 만들면서도 목표 안에 든다.
- 서버 발행 주기는 2초다(`PUBLISH_INTERVAL`). publish 한 번이 0.33~1.0초를 쓰므로 주기의 17~50%를 계산이 차지한다. "체결 → 홈 반영 P95 3초" 예산에서 서버 구간(발행 대기 최대 2초 + 계산 최대 1초)만으로 3초에 닿는다. **브라우저 렌더 여유가 사실상 없다.**

### REST·WebSocket (실행 중인 fixture API)

| 항목 | n | p50 | p95 | max |
|---|---|---|---|---|
| `GET /v1/themes/rankings` | 100 | 3.24ms | 4.56ms | 4.94ms |
| `GET /v1/insights/treemap` | 100 | 2.55ms | 3.83ms | 5.83ms |
| `GET /v1/me/saved` | 100 | 1.52ms | 3.69ms | 5.75ms |
| `GET /v1/themes/{id}/events/{eventId}` | 100 | 2.06ms | 3.61ms | 4.67ms |
| `GET /v1/events/{id}/evidence` | 100 | 1.88ms | 4.11ms | 6.81ms |
| `POST /v1/auth/realtime-ticket` | 20 | 1.34ms | 2.58ms | 4.03ms |

WebSocket: 연결 32.3ms · `subscribe` → 첫 응답 0.7ms · rank 스냅샷 도착 간격 p50 2,006ms / max 2,022ms(서버 발행 주기 2,000ms와 일치) · 13초 동안 `subscribed` 1 + `theme_rank_snapshot` 8 + `theme_treemap_snapshot` 7 수신.

### 웹 번들 (production build)

| 산출물 | 원본 | gzip |
|---|---|---|
| `index-*.js` | 99.77 kB | 32.50 kB |
| `tokens-*.js` | 194.23 kB | 61.98 kB |
| `operator-*.js` (별도 entry) | 13.05 kB | 4.22 kB |
| CSS 합계 | 30.40 kB | 6.55 kB |

사용자 SPA가 받는 JS는 294.0 kB(gzip 94.5 kB)다. 운영자 번들은 사용자 entry와 분리돼 있다. dev 서버 기준 화면 로드는 TTFB 9ms · DOMContentLoaded 170ms였다(모듈 43개, 번들 미적용이라 배포값과 다르다).

---

## 핵심 fixture E2E 주행

fixture API(:8000) + web dev server(:5173, `/api` 프록시)로 실제 브라우저에서 돌렸다.

| 단계 | 확인한 것 | 결과 |
|---|---|---|
| 비로그인 진입 | 로그인 화면만 나오고 제품 데이터 0건 | 통과 |
| 구글 로그인 | `/auth/google` 302 → state·nonce 1회 소비 → 콜백 302 → `/today` | 통과 |
| 오늘 | 순위 1건, "+1.8%", "3 / 3종목 상승", 데이터 상태 "일부 데이터 지연 · 기준 09:00" | 통과 (fixture 계산값과 일치) |
| 테마 상세 | 지표 3종, 근거 상태(수집 지연 문구), 주도 종목 3건(SK하이닉스 +2.4% · NAVER +1.7% · 삼성전자 +1.4%) | 통과 |
| 관심 저장 | `PUT /v1/me/saved/themes/{id}` 200, 버튼이 "관심에서 저장 해제"로 전환 | 통과 |
| 저장 목록 | 저장일 2026.08.16, 실시간 값 함께 표시, 상세 링크 | 통과 |
| 실시간 | 트리맵 1타일, 면적·색이 같은 테마 수익률 원값 | 통과 |
| 리서치 | 게이트 off — "준비 중" 안내만, 답변 생성 없음 | 통과 |
| 로그아웃 | 이후 `GET /v1/themes/rankings` → **401 AUTHENTICATION_REQUIRED**, 화면은 로그인으로 복귀 | 통과 |
| 운영자 콘솔 | `/operator.html`에서 상태·인포스탁 인증·작업·검수·감사 5개 절 렌더 | 통과 |
| WebSocket | ticket 발급 → `auth` → `subscribe` → 스냅샷 수신 | 통과 |

주행 중 200으로 응답한 제품 endpoint: `/auth/session`, `/auth/google`, `/auth/google/callback`, `/v1/themes/rankings`, `/v1/insights/treemap`, `/v1/themes/{id}/events/{eventId}`, `/v1/events/{id}/evidence`, `/v1/me/saved`(GET·PUT), `/v1/auth/realtime-ticket`.

**주행 방식의 한계 두 가지.** ① 브라우저 pane이 화면에 표시되지 않아 좌표 클릭이 실제 요소와 어긋났다. 로그인 버튼만 좌표 클릭으로 눌렸고 이후 이동·저장은 DOM 클릭(`element.click()`)으로 했다. 실제 키보드 입력도 pane에 전달되지 않아 Tab 이동 검증은 못 했다(키보드 계약은 웹 unit test가 덮는다). ② fixture OAuth provider는 `accounts.google.test`(닿지 않는 주소)로 보내므로, 브라우저 대신 CLI에서 로그인 시작 응답의 `state`와 nonce 쿠키를 받아 브라우저에 넣고 콜백을 완료했다. 서버 검증 경로(state 해시·nonce 바인딩·1회 소비)는 전부 실제로 탔다.

---

## 실행하지 않은 것

| 항목 | 이유 |
|---|---|
| 저장 market replay 재생·gap recovery | 로드맵이 F-24에서 명시적으로 제외 |
| 키움·인포스탁·OpenDART·구글 live 호출 | 승인 필요 작업이고 QA 범위 밖 |
| docker compose 컨테이너 기동 | config 검증과 `tests/infra/**`(전체 스위트 포함)만 돌렸다. ARM64 이미지 빌드는 F-25 몫 |
| PostgreSQL 통합 8건 | DSN 미제공으로 skip. 지난 A-4·F-22에서 일회용 PostgreSQL 16으로 통과 확인된 것들이다 |
| 실제 마우스·키보드 입력 E2E | 브라우저 pane 미표시 (위 한계 ① 참조) |
| 부하·동시접속 측정 | 단일 클라이언트 지연만 쟀다. 다중 사용자 처리량은 배포 환경에서 재야 한다 |
