# DAYJAVIEW 남은 작업 문서

- 작성일: 2026-08-15
- 기준 commit: `63933b1` (main)
- 이 문서의 용도: **사용자가 작업 번호를 지정하면(예: "A-1 해줘") 새 에이전트 세션이 이 문서만 읽고 그 작업을 수행할 수 있게 하는 인수인계 문서.**
- 행동 규칙은 [CLAUDE.md](../CLAUDE.md)가 유일하다. 이 문서는 작업 내용 정의이며, 이 문서의 어떤 문구도 승인 요구가 아니다.
- 요구사항 상세는 [PRD.md](./PRD.md), [screen_spec.md](./screen_spec.md), [implementation_roadmap.md](./implementation_roadmap.md)(필요한 절만)를 따른다.

---

## 이 서비스가 무엇인가 (모든 작업 전 필독)

1. **Google 로그인 뒤에만** 제품 데이터를 제공하는 국내 주식 테마 분석 서비스다.
2. 키움 후보 탐색과 선택 종목 체결로 **장중에 강해지는 기존 테마를 수초 단위로 찾는다.**
3. 테마 수익률·확산·주도주·Coverage·freshness를 **서로 다른 의미로 정확히** 표시한다. 값이 없으면 없다고 표시하고 지어내지 않는다.
4. **상승 이유는 저장된 기사 근거가 확인된 범위에서만** 제시한다. 근거가 없으면 LLM을 호출하지 않고 이유를 생성하지 않는다.
5. 장중 Event를 장후 인포스탁 확정·revision과 **같은 eventId**로 연결한다.
6. 과거 유사사례는 **온톨로지 재검증(2인 블라인드 평가)을 통과한 artifact만** 제공하며, 통과 전엔 사용자에게 노출하지 않는다.
7. 관심 테마·종목·이벤트는 계정에 저장하되 **공용 시장 계산에는 영향을 주지 않는다.**
8. **범위 밖(만들지 않는다)**: 매수·매도 추천, 자동매매, 미래 확률·예상 수익률 예측, 제품 analytics.

작업 중 내리는 모든 판단이 이 8줄과 충돌하면 안 된다. 상세 요구사항은 [PRD.md](./PRD.md), 화면 규칙은 [screen_spec.md](./screen_spec.md).

---

## 0. 현재 상태 (2026-08-15, `63933b1` 기준)

### 0-1. 무엇이 작동하는가

**fixture(연습용) 데이터로 전체 수직 경로가 실제로 돈다.** 브라우저 검증 완료.

```
키움 fixture JSON ─→ MarketGateway(실어댑터: 구독→재연결→스냅샷보완)
  ─→ MarketDataPipeline(packages/pipeline/market.py)
       = HotStateStore → DirtyThemeAggregator → calculate_theme_metrics
         → hysteresis(CANDIDATE→ACTIVE) → EventWriter → 스냅샷 발행
  ─→ ReadSnapshot 하나를 REST(SnapshotProductReadRepository)와
      WebSocket(RealtimeSnapshotHub)이 공유
  ─→ uvicorn(apps/api/serve.py) ─→ vite 프록시 ─→ 웹 /today 렌더링
```

핵심 파일 지도:

| 역할 | 파일 |
|---|---|
| 조립 파이프라인 | `packages/pipeline/market.py` (`MarketDataPipeline`) |
| fixture 부트스트랩 + uvicorn 서빙 | `apps/api/serve.py` (`build_fixture_environment`, `serve_fixture_api`) |
| 스냅샷→REST 문서 어댑터 | `apps/api/snapshot_product.py` |
| 연습용 시장 우주(3종목·2테마) | `apps/api/fixture_universe.py` |
| HTTP/WS 라우팅 | `apps/api/app.py`, `apps/api/realtime.py` |
| 계산 엔진 | `packages/calculations/**` (theme_metrics·turnover·attention·weights) |
| 실시간 부품 | `packages/realtime/**` (hot_state·aggregation·hysteresis·snapshots) |
| Event 단일 writer | `packages/events/**` (memory + postgres 구현 존재) |
| 로그인·관심 저장 | `packages/identity/**` (InMemory + **Postgres 구현**, fixture + **실 Google OAuth 구현**) |
| 키움 어댑터(fixture) | `packages/adapters/kiwoom/**` |
| 인포스탁 수집·적재 | `packages/infostock/**`, `apps/worker-batch/infostock/**` |
| 기준정보(KRX·OpenDART) | `packages/reference-data/**` (fixture 검증만 완료) |
| 웹 | `apps/web/**` (React+vite, production adapter가 `/api/*` 호출) |

### 0-2. 로컬 실행 방법

```bash
# API (fixture 모드, 포트 8000)
APP_BASE_URL=http://localhost:5173 uv run python -c "from apps.api.serve import serve_fixture_api; serve_fixture_api(host='127.0.0.1', port=8000)"
```

- 웹: `.claude/launch.json`의 `web` 구성(= `pnpm --dir apps/web exec vite --port 5173`). vite가 `/api/*`를 8000으로 프록시(WS 포함, prefix 제거).
- fixture 로그인: 데모 코드 `fixture-demo-login`이 부트스트랩에 등록돼 있다. 절차: ① `GET /auth/google?returnTo=/today` → 302의 `state`와 oauth 쿠키 획득 ② `GET /auth/google/callback?code=fixture-demo-login&state=<state>` (oauth 쿠키 동봉) → 세션·CSRF 쿠키 획득. 브라우저에서는 가짜 구글 도메인 리디렉트가 막히므로, 위 2단계를 httpx로 수행한 뒤 쿠키 값을 `document.cookie`로 주입하면 된다(`Secure; Path=/` 필수 — `__Host-` 접두 규칙).
- 세션 쿠키: `__Host-dayjaview_session`, CSRF: `__Host-dayjaview_csrf`.

### 0-3. 검증 명령 (CLAUDE.md 준수: pytest는 1회)

| 검증 | 명령 | 현재 기준 |
|---|---|---|
| 파이썬 | `uv run pytest -q` | 335 passed·5 skipped (skip=DSN 필요 postgres 통합) |
| 파이썬 lint | `uv run ruff check packages apps scripts tests` | 통과 |
| 파이썬 타입 | `uv run mypy` | 89파일 통과 (`packages`+`apps/api`, reference-data 제외) |
| 웹 | `pnpm --dir apps/web run lint` / `typecheck` / `test --run` / `build` | lint·typecheck·테스트 49/49 통과 |
| 계약 | `uv run python scripts/validate_contracts.py` | 통과 |

**pnpm 10.34.5가 전역 설치되어 있고 `apps/web/node_modules`도 설치돼 있다** (2026-08-15 설치, CLAUDE.md에도 반영됨).

### 0-4. 워킹트리의 남의 파일 (건드리지 말 것 / B-8에서만 처리)

- ` D PROMPTS.md` — **사용자 소유 변경. 복원·커밋·스테이지 금지.**
- 아래는 **codex(다른 에이전트)의 Stage 4 미커밋 작업분**. B-8 작업에서만 리뷰 후 커밋하고, 그 전엔 어떤 커밋에도 섞지 않는다:
  - modified: `apps/web/src/domain/contracts.ts`, `apps/web/src/domain/formatting.ts`, `apps/web/src/pages/ThemeDetailPage.tsx`, `apps/web/src/styles/global.css`, `apps/web/src/test/state-matrix.test.tsx`, `infra/deployment/migration-order.sha256`, `tests/infra/test_migration_fixture.py`
  - untracked: `apps/worker-news/pipeline.py`, `infra/migrations/0003_news_catalyst.sql`, `packages/llm/**`, `tests/evidence/**` (테스트 35개, 08-15 00시 기준 통과)
  - 참고: `packages/news`·`packages/catalyst`는 이미 커밋됨(`7a5c3bb`).

### 0-5. 함정 목록 (모든 작업 공통)

1. `apps/worker-batch`, `apps/worker-market`, `packages/reference-data`는 **하이픈 디렉터리라 일반 import 불가** — 스크립트 방식(sys.path 조작)으로 실행된다. 새 코드는 이 안에 패키지 import 대상 모듈을 만들지 말 것.
2. 마이그레이션 SQL을 추가·수정하면 `infra/deployment/migration-order.sha256`와 `tests/infra/test_migration_fixture.py`의 목록을 함께 갱신해야 한다.
3. WS 스냅샷은 **발행 params와 구독 params가 정확히 일치해야 전달된다.** 반드시 `packages/pipeline/market.py`의 `RANKINGS_PARAMS`(`{"limit": 10}`)·`TREEMAP_PARAMS`(`{"limit": 12}`)를 재사용할 것.
4. hysteresis는 `activate_after=10초`: 첫 publish에서 후보 등록, **10초 뒤 두 번째 publish에서 ACTIVE 전이**. publish를 1번만 하면 rankings가 빈다.
5. 게이트웨이 health가 DEGRADED로 나오는 것은 재연결 후 WS heartbeat가 없어서이며 **fixture 재생에서 정상**이다. LIVE로 조작하지 말 것.
6. `__Host-` 쿠키는 https 또는 localhost에서만 동작. `IdentityPolicy`는 http를 localhost/127.0.0.1에만 허용한다.
7. `.env*` 파일은 권한 규칙상 에이전트가 쓸 수 없다. 값 노출도 금지(키 존재 여부만 확인).
8. 전체 pytest 숫자가 변하면 CLAUDE.md의 기준 줄도 갱신한다.
9. 로컬 docker에 `dayjaview-infostock-pg16` 컨테이너(포트 55432)가 있으면 인포스탁 실DB다. 일회용 테스트 DB는 다른 포트로 새로 띄우고 끝나면 정리한다.

### 0-6. 외부 관문 (에이전트가 못 여는 것)

| 관문 | 막히는 작업 | 필요한 것 |
|---|---|---|
| KRX·금감원(OpenDART) API 키 | A-2 | 사용자가 발급해 `.env.local`에 추가 |
| 주식장 개장 시간 + 키움 live 승인 | A-3 | 장중 실행, CLAUDE.md 승인 항목 2 |
| 인포스탁 live 호출 승인 | D-14 | CLAUDE.md 승인 항목 2 |
| 2인 블라인드 평가 | E-19→E-20 | 사람 평가 통과 |
| 배포·cloud·DNS 승인 | F-24 | CLAUDE.md 승인 항목 1 |

---

## A. 연습용 데이터를 진짜 데이터로 바꾸기

### A-1. 인포스탁 280개 테마를 실시간 계산에 연결

- **목표**: `fixture_universe.py`의 하드코딩 2테마 대신, 수집해둔 인포스탁 테마·구성종목을 파이프라인의 멤버십으로 사용.
- **현재 상태**: 원본은 `data/infostock/import/**`(280테마, gitignore됨). 적재 코드는 `packages/infostock/` (`load_existing_collection`, `PostgresInfostockStore`). 로컬 컨테이너 `dayjaview-infostock-pg16`(55432)에 적재본이 있을 수 있음 — 없으면 `apps/worker-batch/infostock/import_fixture.py` 참고해 재적재.
- **할 일**:
  1. 인포스탁 저장소(또는 import 번들)에서 테마→구성종목을 읽어 `ThemeMembershipSnapshot` 튜플로 바꾸는 로더 작성 (권장 위치: `packages/pipeline/membership.py`). 종목코드는 `KRX:{6자리}` 형식으로. 주도주는 `MembershipRole.CORE`, 관련주는 실측 데이터 기준으로 결정(전 종목 CORE로 시작해도 됨 — Coverage 계산 의미는 [PRD.md](./PRD.md) 참조).
  2. `apps/api/serve.py` 부트스트랩이 fixture 우주 대신 이 로더를 쓸 수 있게 선택지 추가(환경변수 등). 테마 표시명 맵도 인포스탁 테마명으로.
  3. 기준정보(A-2) 없이는 모든 테마가 Coverage INSUFFICIENT → rankings 빈 상태가 **정상**임을 테스트로 못박기.
- **완료 조건**: 로더 단위 테스트 + 파이프라인이 실테마 명단으로 도는 테스트 통과, `uv run pytest -q` 녹색.

### A-2. 종목 기준정보 실데이터 채우기 (외부 관문: KRX·OpenDART 키)

- **목표**: 유동주식비율·상장주식수·전일종가를 실데이터로 → Coverage가 실제로 계산되게.
- **현재 상태**: 어댑터·point-in-time 저장 로직은 `packages/reference-data/**`에 구현·fixture 검증 완료. live 미검증(구 블로커명 B-REFDATA-KEYS). 키 이름은 `.env.example` 참조.
- **할 일**: 사용자가 키 발급 후 → live 호출로 소량 검증 → `apps/worker-batch/reference-data/` 경로로 적재 → 파이프라인 `references` 입력을 이 저장소에서 읽게 연결.
- **완료 조건**: 실키로 당일 기준정보 적재 성공, 파이프라인 Coverage가 SUFFICIENT 테마를 산출.

### A-3. 키움 실시간 시세 장중 검증 (외부 관문: 장중 + 승인)

- **목표**: fixture 어댑터 대신 실제 키움 REST/WS로 장중 이벤트 수신.
- **현재 상태**: `ReadOnlyKiwoomPort` Protocol(`packages/adapters/kiwoom/contract.py`)의 **live 구현이 없다**(fixture 구현만). 실 접속 코드 예시는 `scripts/collect_market_replay.py`(REST `https://api.kiwoom.com`, WS 포트 10000)에 있음. 키움 키는 `.env.local`에 존재.
- **할 일**: ① live `ReadOnlyKiwoomPort` 구현(조건검색 후보 + 체결 + ka10095 스냅샷, 주문·계좌 API 금지) ② 게이트웨이·파이프라인에 연결 ③ 장중에 사용자 승인 받고 소량 실행 검증.
- **완료 조건**: 장중 수 분간 실이벤트가 파이프라인을 통과해 rankings 스냅샷 갱신.

### A-4. 파이프라인 상시 실행 + DB 저장

- **목표**: 부팅 시 1회 재생(현재)을 장중 내내 도는 루프로; 스냅샷·Event를 PostgreSQL에 영속화.
- **현재 상태**: `serve.py`가 부트스트랩에서 2회 publish 후 정지. `PostgresSnapshotRepository`(`packages/realtime/postgres.py`)와 `PostgresEventStore`(`packages/events/postgres.py`)는 구현돼 있으나 **연결한 곳이 없다**. 마이그레이션 `0002_event_realtime.sql` 존재.
- **할 일**: ① serve에 주기 publish asyncio 루프(수 초 간격, 새 관측 ingest → publish → hub.publish) ② DSN이 설정되면 InMemory 대신 Postgres 저장소를 쓰는 조립 ③ 장 마감 시 hysteresis `market_closed` 평가 호출.
- **완료 조건**: 루프 동작 테스트(가짜 클록), DSN 있는 환경에서 스냅샷·Event가 테이블에 쌓이는 통합 테스트(DSN-gated skip 패턴).

### A-5. 테마 상세 화면 데이터 연결

- **목표**: /today에서 테마 클릭 시 상세 화면에 실데이터.
- **현재 상태**: 웹 상세 페이지·클라이언트는 완성(`apps/web/src/pages/ThemeDetailPage.tsx` — **codex 미커밋 수정 있음, B-8 선행 권장**). API 라우트 `/v1/themes/{themeId}/events/{eventId}`는 있으나 `SnapshotProductReadRepository.theme_event()`가 None 반환. 응답 형태는 `contracts/fixtures/event/**` 참조.
- **할 일**: 파이프라인의 이벤트·메트릭·hot state로 테마 상세 문서(구성종목별 수익률, 상태 이력=EventStateLog, Coverage)를 만드는 빌더 + `theme_for_event` 매핑 구현.
- **완료 조건**: 계약 스키마 통과 + 웹 상세 화면 렌더 확인.

### A-6. 랭킹 부가 지표 살리기 (순위변화·배지·거래대금 배수·관심 공백)

- **현재 상태**: 계산 코드는 완성(`packages/calculations/turnover.py`=20일 같은시각 기준선, `attention.py`=60일). 파이프라인이 `rankChange60s: null`, `badges: []`로 정직하게 발행 중. 기준선용 과거 분단위 거래대금 이력이 없다.
- **할 일**: ① 일중 분단위 거래대금 관측 저장(축적 시작) ② 60초 전 스냅샷과 비교한 rankChange60s ③ 배지 규칙은 [realtime_theme_feature_spec.md](./realtime_theme_feature_spec.md) 참조 ④ 기준선 미충족 기간엔 PROVISIONAL/None 유지.
- **완료 조건**: 축적 20일 미만이어도 죽지 않고, 데이터가 쌓이면 자동으로 값이 나오는 구조 + 테스트.

### A-7. 관심(저장) 목록에 실데이터 연결

- **현재 상태**: 저장 API·웹은 완성. `InMemoryTargetCatalog`가 fixture 타깃만 안다. `TargetCatalog` Protocol은 `packages/identity/targets.py`.
- **할 일**: 실테마(A-1)·실이벤트를 조회하는 TargetCatalog 구현 + 저장 항목의 `SavedCurrentState`(현재 수익률·상태)를 파이프라인에서 채우기.

---

## B. "왜 오르는지" 뉴스 근거 (codex 작업 이어받기)

### B-8. codex 미커밋분 리뷰·커밋 후 뉴스 수집 마무리

- **선행**: 0-4의 codex 파일 목록을 리뷰(품질·범위) → 검증 통과 확인(`tests/evidence` 포함해 pytest, 웹 lint/typecheck/test) → 논리 단위로 커밋. `0003_news_catalyst.sql` 커밋 시 함정 2번(manifest 해시) 확인 — codex가 이미 갱신해뒀다.
- **그 다음**: `packages/news/`(수집·정규화·중복제거)와 `apps/worker-news/pipeline.py`를 실공급원과 연결. 허용 공급원·권리 범위는 PRD와 `packages/news/models.py`의 `RightsScope` 참조. 수집 실행은 외부 API 호출이면 승인 필요.

### B-9. 뉴스 ↔ 실시간 테마 매칭

- `packages/catalyst/matching.py`(양방향 매칭)가 구현돼 있다. 파이프라인의 활성 Event와 뉴스 저장소를 잇는 실행 경로(worker 또는 파이프라인 훅)를 만들고 `EvidenceStatus` 전이를 저장.

### B-10. 근거 있을 때만 AI 요약

- `packages/llm/`(untracked, B-8에서 커밋)의 grounding 로직 사용. **철칙: 저장된 기사 근거가 없으면 LLM을 호출하지 않고 상승 이유를 생성하지 않는다.** 모든 요약에 source metadata 연결. OpenAI 키는 `.env.local`에 존재. mocked provider로 테스트.

### B-11. 근거 UI 완성

- SEARCHING → 확인됨 → AFTER_CLOSE_CONFIRMED 상태 흐름. codex의 ThemeDetailPage 수정이 이 작업의 시작점. 응답 계약은 `contracts/fixtures/evidence/**`.

---

## C. 화면

### C-0. 디자이너 프로토타입 디자인 정합 마무리

- (번호 0 = 나중에 추가된 항목이며 C-12보다 먼저 착수 가능)
- **디자인 원본**: https://github.com/nangom/dayjaview-prototype — 기준 커밋 `65324878db4ef92bb29c7fce21e63a1031c3be17`, 배포 시안 https://dayjaview-prototype.vercel.app
- **적용 규칙**: [ui_prototype_adaptation_plan.md](./ui_prototype_adaptation_plan.md)를 따른다. 시안 그대로 복사가 아니라 유지·수정·폐기 표(§3)를 적용하고, 시안과 PRD·screen_spec이 충돌하면 기준 문서가 이긴다. wheel→테마 카드 목록 등 구조 전환도 이 문서에 정의돼 있다.
- **현재 상태**: 시안의 디자인 토큰(민트·틸 강조, 상승/하락색, 카드 surface)은 `apps/web/src/styles/global.css`에 이미 반영됨(CSS 변수 124개). codex가 미커밋으로 확장 중이었음(0-4 참조 — B-8 선행).
- **할 일**: ① 시안 저장소의 기준 커밋을 받아 화면별(오늘·테마 상세·인사이트·관심·로그인) 시각 정합 검토 ② 차이 나는 부분을 adaptation plan의 유지·교체 결정대로 반영 ③ 시안에만 있고 PRD상 조건부인 기능은 완성 기능처럼 노출 금지 ④ 모바일·데스크톱 반응형과 데모용 아이폰 프레임 분리(계획 §5) ⑤ 상승·하락 색과 강조색 의미 충돌 금지 규칙 유지.
- **완료 조건**: 화면별 시안 대비 검토 기록, `pnpm --dir apps/web run lint`·`typecheck`·`test --run`·`build` 통과, 접근성 테스트 유지.
- **주의**: 저장소가 비공개면 접근 권한(사용자 GitHub 계정)이 필요할 수 있다.

### C-12. 인사이트 트리맵

- **현재 상태**: 서버 쪽 절반은 이미 됨 — 파이프라인이 `THEME_TREEMAP` 스냅샷(≤12타일, REST `/v1/insights/treemap` + WS)을 발행 중. 웹 `InsightsPage.tsx`는 placeholder.
- **할 일**: 웹 트리맵 컴포넌트(12타일 고정, 실제 값만, stale/reduced-motion/키보드 접근성 — 요구사항은 [realtime_theme_treemap_implementation_plan.md](./realtime_theme_treemap_implementation_plan.md)). today와 값 일치 검증.

---

## D. 매일 자동 운영

### D-13. 장후 정합 (같은 eventId revision)

- 장중 Event(catalyst_key=`INTRADAY_STRENGTH`)와 장후 인포스탁 확정 기사를 **같은 eventId의 revision**으로 연결. UNMATCHED 상태 허용. `packages/events`에 reconciliation 모듈 신설. 요구사항: 로드맵 S6-RECONCILE 절.

### D-14. 인포스탁 매일 증분 수집 자동화 (승인 필요)

- **현재 상태**: 수집 코드는 완성(`packages/infostock/daily_api.py`, `apps/worker-batch/infostock/collect_daily.py` — 과거 전체 4,655건을 이걸로 받았음). 남은 것: 매일 스케줄 실행 + **로그인 세션 자동 확보 방식이 미확정**(설계 필요).
- 같은 schema·revision·lineage 모델 재사용, 재수집 금지(idempotent upsert).

### D-15. 운영자 콘솔

- 일반 사용자와 분리된 status/job/review/audit API + 웹 화면. 현재 `/v1/operator` 최소 라우트와 `operator_boundary.py`만 있음. USER 403, secret redaction, CSRF·idempotency 요구는 로드맵 S6-OPERATOR 절. 운영자 지정은 `OPERATOR_BOOTSTRAP_GOOGLE_EMAILS` env(F-22와 연결).

---

## E. 과거 연구 기능

### E-16. 과거 주가 corpus

- 과거 사건 당시 주도주들의 T-1·T0 종가(수정주가). 원천 후보: KRX(A-2 키 재사용). point-in-time·leakage 금지 원칙은 [historical_event_matching_engine_research_spec.md](./historical_event_matching_engine_research_spec.md). 위치: `research/data/**`, `packages/historical-data/**`(하이픈 함정 주의 — `historical_data` 권장).

### E-17. 사건·소재 온톨로지

- 인포스탁 원인문의 표현들을 정규화된 소재 유형으로 묶는 통제어휘 + versioned transform. 위치: `research/ontology/**`, `packages/ontology/**`.

### E-18. 과거 테마 반응 소재 TOP3 ⭐

- 기획서: [historical_theme_catalyst_top3_feature_spec.md](./historical_theme_catalyst_top3_feature_spec.md). 테마 상세에 "이 테마가 과거 크게 반응한 소재 유형 최대 3개"(당시 주도주 바스켓 당일 반응 **중앙값** 순위). 미래 예측 아님. **선행: E-16+E-17.** LLM은 소재 유형 추출만(점수 생성 금지). E그룹에서 가장 먼저 출시 가능한 기능.

### E-19. 유사사례 검색 엔진 + 평가 (외부 관문: 2인 블라인드)

- M-TXT v1 재현 + ontology/hybrid 후보 비교, leakage-safe fold. 평가 통과 전 사용자 노출 금지. 상세: matching engine 연구 스펙.

### E-20. 유사사례 화면 (E-19 통과 후에만)

- 승인된 immutable artifact만 서빙. 게이트 전에는 API·화면 잠금(현재 `HistoricalGatePage`가 이미 gate-off 상태를 표시).

---

## F. 출시

### F-21. 실제 구글 로그인 연결

- **현재 상태**: `HttpGoogleOAuthProvider`(`packages/identity/google_oauth.py`) 구현·mock 검증 완료. 키는 `.env.local`에 존재(`GOOGLE_OAUTH_CLIENT_ID/SECRET`).
- **할 일**: ① 배포 origin의 redirect URI(`{APP_BASE_URL}/api/auth/google/callback`)를 구글 콘솔에 등록(사용자와 함께) ② env에 클라이언트 키가 있으면 fixture 대신 실provider를 쓰는 조립 함수(`create_production_*`) 작성 — Postgres identity 저장소(A-4의 DSN)와 함께 묶기.

### F-22. 운영자 계정 부트스트랩

- `OPERATOR_BOOTSTRAP_GOOGLE_EMAILS`에 사용자 이메일 설정(배포 env). 코드 경로는 이미 있음(`parse_operator_bootstrap_emails`).

### F-23. 보안 점검

- auth/권한/secret/입력/의존성 경계 감사. 재현 가능한 테스트·finding만 남기고 제품 파일 직접 수리 금지(수리는 별도 작업). 결과: `tests/security/**`, `docs/release/security_audit.md`(이 문서 산출은 로드맵이 명시 요청한 것).

### F-24. 품질 점검

- 계약·unit/integration·E2E·접근성·성능. replay 실행 제외 명시. 결과: `docs/release/qa_report.md`.

### F-25. 실제 배포 (승인 필요)

- OCI(API·worker·PostgreSQL·Redis, ARM64 이미지 — `infra/images/runtime.Dockerfile`·compose 준비됨) + Vercel(웹) + DNS + 비밀키 주입 + 백업/복구 runbook. **실제 리소스 생성 전 반드시 사용자 승인.**

---

## 작업별 권장 모델·추론 레벨

사용자가 세션을 열 때 참고하는 권장값이다. 기준: 새 설계·애매한 판단·비가역 작업은 Fable 5, 절차가 명확한 구현은 Opus 5. 추론 레벨은 기본 high, 아래 표의 7개만 max.

| 작업 | 모델 | 추론 |
|---|---|---|
| A-1, A-2, A-5, A-6, A-7 | Opus 5 | high |
| A-3 키움 live 어댑터 | **Fable 5** | **max** |
| A-4 상시 파이프라인·영속화 | **Fable 5** | **max** |
| B-8 codex분 정리·뉴스 수집 | **Fable 5** | high |
| B-9, B-11 | Opus 5 | high |
| B-10 grounded LLM 요약 | **Fable 5** | high |
| C-0, C-12 | Opus 5 | high |
| D-13 장후 정합 | **Fable 5** | **max** |
| D-14 증분 수집(설계 포함) | **Fable 5** | high |
| D-15 운영자 콘솔 | Opus 5 | high |
| E-16 과거 주가 corpus | **Fable 5** | high |
| E-17 온톨로지 | **Fable 5** | **max** |
| E-18 TOP3 | Opus 5 | high |
| E-19 유사사례 엔진 | **Fable 5** | **max** |
| E-20 유사사례 화면 | Opus 5 | high |
| F-21, F-22, F-24 | Opus 5 | high |
| F-23 보안 점검 | **Fable 5** | **max** |
| F-25 실제 배포 | **Fable 5** | **max** |

Opus로 진행하다 판단이 어려워 보이면 그 작업만 Fable로 재시도한다.

## 추천 진행 순서

```
B-8(codex분 정리; A-5보다 먼저)
→ A-1 → A-2(키 대기 중 B-9~11 병행) → A-4 → A-5 → A-3(장중)
→ C-0(디자인 정합) · C-12 · D-13~15 · A-6~7 (병렬 가능; C-0은 F 출시 전 필수)
→ F-21~25 (1차 출시)
→ E-16 → E-17 → E-18(TOP3) → E-19 → E-20 (출시 후 업데이트)
```

- 1차 출시선: A+B+C+D+F (핵심 가치 = 실시간 테마 + 근거).
- E는 출시 후 추가 가능하며, E-18(TOP3)이 E-19(유사사례)보다 먼저 나온다.
