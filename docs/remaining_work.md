# DAYJAVIEW 남은 작업 문서

- 작성일: 2026-08-15
- 기준 commit: `91c774a` (main)
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

## 0. 현재 상태 (2026-08-15, `91c774a` 기준)

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
| 파이썬 | `uv run pytest -q` | 381 passed·5 skipped (skip=DSN 필요 postgres 통합) |
| 파이썬 lint | `uv run ruff check packages apps scripts tests` | 통과 |
| 파이썬 타입 | `uv run mypy` | 89파일 통과 (`packages`+`apps/api`, reference-data 제외) |
| 웹 | `pnpm --dir apps/web run lint` / `typecheck` / `test --run` / `build` | lint·typecheck·테스트 49/49 통과 |
| 계약 | `uv run python scripts/validate_contracts.py` | 통과 |

**pnpm 10.34.5가 전역 설치되어 있고 `apps/web/node_modules`도 설치돼 있다** (2026-08-15 설치, CLAUDE.md에도 반영됨).

### 0-4. 워킹트리의 남의 파일 (해소됨, 2026-08-15)

- codex의 Stage 4 작업분은 `2c855fb`로 전부 커밋됐다 (worker-news pipeline, packages/llm, 0003 마이그레이션, evidence 테스트, ThemeDetailPage 근거 UI). 검증도 재확인됨.
- `PROMPTS.md` 삭제도 같은 커밋에 포함돼 처리 완료.

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
- **이어서 할 일**: 당일 수집이 성공하면 **같은 키·같은 어댑터로 E-16(과거 전 종목 일봉) 백필을 백그라운드로 시작한다.** 1.5만 회 호출이라 며칠 걸리므로 출시 후에 시작하면 E-18이 밀린다. 범위와 필드는 [E-16](#e-16-과거-주가-corpus) 참조.

### A-3. 키움 실시간 시세 장중 검증 (외부 관문: 장중 + 승인)

- **목표**: fixture 어댑터 대신 실제 키움 REST/WS로 장중 이벤트 수신.
- **현재 상태**: `ReadOnlyKiwoomPort` Protocol(`packages/adapters/kiwoom/contract.py`)의 **live 구현이 없다**(fixture 구현만). 실 접속 코드 예시는 `scripts/collect_market_replay.py`(REST `https://api.kiwoom.com`, WS 포트 10000)에 있음. 키움 키는 `.env.local`에 존재.
- **이미 확보된 검증 자산 (장중 관문 없이 쓸 수 있다)**: `data/market-replay/2026-08-14/`에 2026-08-14 **09:00~10:39 KST** 실시장 입력이 저장돼 있다(체결 156만 건 포함 180만 event, 종목 2,182개, 인포스탁 281개 테마 명단 동결, 5.7GB). 보조 수집 `data/market-replay-supplemental/2026-08-14/`는 10:09~10:39 구간 `ka10095` 30초 snapshot 13.4만 건. **둘 다 `.gitignore`된 로컬 전용이고 이 PC에만 있다.** 이 fixture에 Market Gateway를 연결하면 장중 승인 없이 후보 발견 → 구독 → 테마 집계 → CANDIDATE/ACTIVE 승격 → 순위 → 홈화면까지 재생 검증이 된다. 목적과 설계는 [one_time_market_replay_collection_plan.md](./one_time_market_replay_collection_plan.md) 0절, 실제 확보 범위와 미달 게이트는 [market_replay_2026-08-14_completion_report.md](./market_replay_2026-08-14_completion_report.md).
- **fixture로 검증 안 되는 것**: 장 후반이 없어 **WEAKENING/CLOSED 소멸 전이**, 종가 기준 지표, D-13 장후 정합은 못 본다. 1분봉은 0행이라 후보 밖 사후 분석도 불가. 동시 구독 상한이 180이라 초 단위 체결은 조건검색 후보 위주다. 이 항목들은 A-3에서 실제 장중 실행으로만 닫힌다.
- **할 일**: ① live `ReadOnlyKiwoomPort` 구현(조건검색 후보 + 체결 + ka10095 스냅샷, 주문·계좌 API 금지) ② 게이트웨이·파이프라인에 연결 ③ 장중에 사용자 승인 받고 소량 실행 검증.
- **완료 조건**: 장중 수 분간 실이벤트가 파이프라인을 통과해 rankings 스냅샷 갱신.

### A-4. 파이프라인 상시 실행 + DB 저장

- **목표**: 부팅 시 1회 재생(현재)을 장중 내내 도는 루프로; 스냅샷·Event를 PostgreSQL에 영속화.
- **현재 상태**: `serve.py`가 부트스트랩에서 2회 publish 후 정지. `PostgresSnapshotRepository`(`packages/realtime/postgres.py`)와 `PostgresEventStore`(`packages/events/postgres.py`)는 구현돼 있으나 **연결한 곳이 없다**. 마이그레이션 `0002_event_realtime.sql` 존재.
- **할 일**: ① serve에 주기 publish asyncio 루프(수 초 간격, 새 관측 ingest → publish → hub.publish) ② DSN이 설정되면 InMemory 대신 Postgres 저장소를 쓰는 조립 ③ 장 마감 시 hysteresis `market_closed` 평가 호출.
- **완료 조건**: 루프 동작 테스트(가짜 클록), DSN 있는 환경에서 스냅샷·Event가 테이블에 쌓이는 통합 테스트(DSN-gated skip 패턴).

### A-5. 테마 상세 화면 데이터 연결

- **목표**: /today에서 테마 클릭 시 상세 화면에 실데이터.
- **현재 상태**: 웹 상세 페이지·클라이언트는 완성(`apps/web/src/pages/ThemeDetailPage.tsx` — 근거 UI 포함해 `2c855fb`로 커밋됨. 단 **C-0에서 시안 구조로 전면 교체된다**). API 라우트 `/v1/themes/{themeId}/events/{eventId}`는 있으나 `SnapshotProductReadRepository.theme_event()`가 None 반환. 응답 형태는 `contracts/fixtures/event/**` 참조.
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

### A-8. 거래일 전환과 매일 기준정보 수집

- **목표**: 장 시작 전에 그날 기준정보를 자동으로 받고, 파이프라인이 새 거래일로 넘어가게 한다. **지금 구조는 하루밖에 못 돈다.**
- **현재 상태** (2026-08-15 확인):
  - 거래일이 상수다. `apps/api/fixture_universe.py:22`의 `FIXTURE_MARKET_DATE = date(2026, 8, 14)`로 `serve.py`가 파이프라인을 만들고(219행), 기준정보도 이 날짜로 **부팅 때 한 번만** 읽는다(171행).
  - `MarketPublishLoop`(`packages/pipeline/runner.py`)는 하루 안에서만 돈다. `close_market`을 적용하면 `_market_close_applied`가 True로 남고 다음 거래일로 넘어가는 코드가 없다.
  - 수집기 `apps/worker-batch/reference-data/collect_daily.py`는 완성됐지만 **수동 실행**이다. 스케줄이 없다. A-2에서 2026-08-14 하루치만 받았다.
  - D-14는 인포스탁 테마 명단 수집이라 이 항목과 다르다. **기준정보 매일 수집을 맡은 작업 항목이 지금까지 없었다.**
- **장중에는 기준정보가 아예 안 나온다 (착수 후 실측으로 드러남)**: 당일 KRX 일별매매 row는 그날 장이 끝나야 나온다. 그래서 장중 시점에는 **전일종가 0/2,411**, **기업행위 해소 1/2,411**이다. A-2에서 확보한 93.5%는 **장 마감 후** 데이터 기준이고, 장중에는 전 테마 Coverage INSUFFICIENT로 순위가 하루 종일 빈다. 원인은 두 단계다 — ⓐ 직전 거래일을 알려면 그 사이 날짜의 KRX 응답이 있어야 하고(수집 lookback으로 해결), ⓑ 그걸 넘겨도 기업행위(권리락·액면분할) 원천이 없어 `resolve_previous_adjusted_close`가 fail-closed로 값을 만들지 않는다. `CorporateActionReference` 타입만 있고 이걸 만드는 수집기가 없다.
- **할 일**:
  1. **장중 기준가 확보.** 키움 실시간 체결에 **FID 11(전일대비)**이 실제로 온다(실 replay 데이터에서 확인: `10=+92,500`, `11=+1,400`, `12=+1.54` → 기준가 91,100). `현재가 − 전일대비`가 그날 기준가이고, 권리락 당일이면 키움이 조정된 기준가로 전일대비를 계산하므로 **기업행위가 자동 반영된다.** 별도 원천이 필요 없다. 지금 `packages/adapters/kiwoom/normalizer.py`가 FID 11을 읽지 않는다(fixture에 11이 없어 여태 드러나지 않음). ka10095 스냅샷에도 대응 필드가 있는지 함께 확인한다. **A-3와 같은 파일이라 A-3 종료 후 착수한다.**
  2. 장 시작 전 `collect_daily.py`를 그날 거래일로 실행하는 스케줄. 실패하면 그날 계산을 시작하지 않는다([product_decisions.md](./product_decisions.md) PD-001 10항 — 조용한 대체 금지).
  3. `market_date`를 상수에서 빼고 거래일 달력으로 결정한다. 달력은 `derive_trading_calendar`가 KRX 응답 유무로 만든다. 직전 거래일 판정에 lookback이 필요하다(A-2에서 10일로 검증).
  4. 거래일이 바뀌면 새 기준정보를 읽어 파이프라인을 재구성한다. Event·스냅샷 저장소는 유지한다.
  5. 비거래일에는 파이프라인을 세우지 않고 그 상태를 화면에 표시한다.
- **진행 (2026-08-15)**: 1은 `a52b12c`, 2·3·4·5는 `5273e9d`로 완료. 계산 경로는 다 뚫렸고 `apps/api/serve.py` 배선 1건만 남았다.
  - 1 — `MarketObservation`·`StockRealtimeUpdate`에 `base_price`를 싣고, 파이프라인이 전일 종가가 **빈 종목에만** 채운다. 기존 값은 덮지 않고 `known_at`이 늦은 기준정보를 덧붙여 point-in-time 선택이 판단하게 한다. 2026-08-14 실시장 녹화분 체결 287,753건 중 287,752건에서 기준가 산출, FID 12(등락률) 교차 검산 불일치 0건.
  - **배선 잔여**: `serve_live_api`가 아직 `MarketPublishLoop`을 직접 쓴다. `TradingDayLoop`으로 바꾸려면 `build_live_environment`를 "앱 1회 조립"과 "거래일별 세션 조립"으로 쪼개야 한다. 설계 판단 둘 — ⓐ `LiveMarketRunner`가 키움 WS 연결과 조건검색 후보 상태를 쥐고 있어 날짜 전환 때 연결을 끊고 다시 맺을지, ⓑ `SnapshotProductReadRepository`가 파이프라인을 잡고 있어 REST·WS가 새 세션을 보게 할 포인터를 어디에 둘지(`SnapshotSource` Protocol만 만족하면 되므로 얇은 forwarding 객체로 충분). **A-3 ③ 장중 검증이 이 경로를 실제로 통과시키므로 그 결과를 보고 정하는 편이 낫다.**
  - `packages/pipeline/trading_day.py` — KST 거래일 판정. 주말 확정, 달력이 아는 과거 날짜는 그 판정, 나머지 평일은 거래일로 가정. **KRX에 달력 endpoint가 없고 일별매매가 마감 후에 나와 장 시작 전에 오늘이 공휴일인지 원천으로 확인할 방법이 없다.** 실제 휴장이면 장중 이벤트가 오지 않아 게이트웨이 health가 드러낸다.
  - `TradingDayLoop`(`packages/pipeline/runner.py`) — 날짜가 바뀌면 이전 장을 닫고 그날 파이프라인으로 교체, 비거래일에는 세우지 않음. `MarketPublishLoop.close()`를 더해 마감 시각 뒤 tick이 없었어도 전환 시 마감 시각으로 닫는다.
  - `prepare_reference_data`(`packages/pipeline/daily.py`) — 그날 수집본이 없으면 `collect_daily` 실행, `COMPLETE`가 아니면 예외로 그날 계산 미시작.
- **완료 조건**: 가짜 클록으로 장 마감 → 다음 거래일 전환 → 새 전일종가로 계산되는 테스트 통과(완료), 비거래일 건너뜀 테스트(완료), 장중 기준가로 유동시총이 계산되는 테스트 통과(완료), **그리고 `serve_live_api`가 `TradingDayLoop`으로 도는 것**(미완).
- **선행**: A-2(완료). **A-4와 맞물린다** — A-4가 하루 안의 상시 루프를 만들었고 이 항목이 그 위에 날짜 축을 얹는다.
- **왜 필요한가**: 지금 배포하면 ① 장중 내내 순위가 비고 ② 다음 날 어제 전일종가로 계산해 모든 수익률이 틀린다. 둘 다 조용히 틀리므로 화면만 봐서는 알 수 없다. F 출시 전 필수.

---

## B. "왜 오르는지" 뉴스 근거 (codex 작업 이어받기)

### B-8. codex 미커밋분 리뷰·커밋 후 뉴스 수집 마무리 (완료, 2026-08-15)

- codex분은 `2c855fb`로 커밋·검증 완료 (0-4 참조).
- 실공급원 구현 완료: `packages/news/live.py`(RSS + NAVER API HUB `특징주` 최신순 + 보완 검색, 전부 mock transport 테스트) + `apps/worker-news/collect.py`(수집 루프 진입점). 공급원 설정은 env — `NEWS_RSS_SOURCES`(`source_id|매체명|URL`을 `;`로 구분), `NAVER_API_HUB_CLIENT_ID/SECRET`.
- 남은 것: ① live 수집 실행(외부 API 호출 = 승인 필요) ② 뉴스 Postgres 영속화 조립(`0003` 테이블은 있으나 `PostgresNewsStore` 미구현 — A-4·B-9에서) ③ `.env.example`에 `NEWS_RSS_SOURCES` 항목 추가(에이전트는 `.env*` 쓰기 불가, 사용자 수행).

### B-9. 뉴스 ↔ 실시간 테마 매칭

- `packages/catalyst/matching.py`(양방향 매칭)가 구현돼 있다. 파이프라인의 활성 Event와 뉴스 저장소를 잇는 실행 경로(worker 또는 파이프라인 훅)를 만들고 `EvidenceStatus` 전이를 저장.

### B-10. 근거 있을 때만 AI 요약

- `packages/llm/`(`2c855fb`로 커밋됨)의 grounding 로직 사용. **철칙: 저장된 기사 근거가 없으면 LLM을 호출하지 않고 상승 이유를 생성하지 않는다.** 모든 요약에 source metadata 연결. OpenAI 키는 `.env.local`에 존재. mocked provider로 테스트.

### B-11. 근거 UI 완성

- SEARCHING → 확인됨 → AFTER_CLOSE_CONFIRMED 상태 흐름. codex의 ThemeDetailPage 수정이 이 작업의 시작점. 응답 계약은 `contracts/fixtures/evidence/**`.

---

## C. 화면

> **2026-08-15 정정.** 이전 C-0은 "토큰은 이미 반영됐고 검토만 남았다"고 적혀 있었으나 사실이 아니었다. 근거였던 [ui_prototype_adaptation_plan.md](./ui_prototype_adaptation_plan.md) `0.2-draft`가 **존재하지 않는 커밋**을 감사한 문서였고, 거기 적힌 민트·틸 색은 시안에 없는 색이다. 현재 `global.css`의 청록 팔레트는 시안과 무관하다. C-0은 **검토가 아니라 전면 교체**다. adaptation plan은 `1.0`으로 재작성됐다.

### C-0. 시안 디자인 이식 (전면 교체)

- **결정 (2026-08-15)**: 이 저장소는 **시안 디자인을 그대로 사용한다.** 시각 계층(색·타이포·간격·곡률·모션·화면 배치·이동 구조)은 시안이 최상위이고, 데이터 계층(수치 의미·상태 enum·식별자·계약·게이트)은 PRD·screen_spec·api_contract가 최상위다. 충돌하면 시안 배치를 유지하고 그 자리에 들어갈 값만 계약을 따른다.
- **디자인 원본**: https://github.com/nangom/dayjaview-prototype — 기준 커밋 **`da00c8f`** (2026-08-15 `main`, 고정). 배포 시안 https://dayjaview-prototype.vercel.app. 디자이너가 시안을 갱신해도 자동으로 따라가지 않는다.
- **적용 규칙**: [ui_prototype_adaptation_plan.md](./ui_prototype_adaptation_plan.md) `1.0`. 토큰 실측값은 §7, 화면별 규칙은 §8, 레이아웃은 §11, 순위 휠 접근성은 §12.1.
- **시안 실제 모습** (직접 확인한 사실):
  - Next.js 15.3 + React 19 모노레포. 토큰은 `packages/design-tokens/src/tokens.css`(`--djv-*` 39개 + 다크 11개), 화면은 `apps/web/src/app/page.tsx` 단일 파일 596줄.
  - 브랜드색 **주황 `#ff6600`**, 배경 웜 그레이 `#eae8e3`, Pretendard 7단계, 곡률 11~28px.
  - **당근 Seed 디자인 시스템 위에 올라가 있다.** `layout.tsx`가 `@seed-design/css/base.css`를 먼저 로드하므로 **`tokens.css`에 적힌 fallback 값은 실제 렌더링 값이 아니다.** 12색 중 6색이 다르다(brand `#ff6f0f`→`#ff6600`, text `#212124`→`#1a1c20`, text-muted `#868b94`→`#555d6d`, border `#eaebee`→`#00000010`, surface-muted `#f7f7f8`→`#f3f4f5`, brand-soft `#fff0e5`→`#fff2ec`). **adaptation plan §7.1의 실측값 열을 쓸 것.** 제품에 Seed를 설치하지 않고 실측값을 직접 기입한다.
  - **모바일 전용.** 데스크톱 레이아웃이 없다. 제품 최대 폭은 `--djv-app-max-width: 420px` 단일 열. `page.module.css`의 393×852 `.phone` 프레임은 디자이너 목업 wrapper이므로 제품에 넣지 않는다.
  - 하단 탭 4개: **홈 · 실시간 · 즐겨찾기 · 리서치**.
  - 홈은 **순위 휠**(`ThemeRankingWheel.tsx` — 카드 3벌 복제 무한 스크롤 + 진입 시 자동 스크롤). 이전 문서가 "폐기"로 적었으나 **유지한다.**
  - 시안에만 있는 화면: 리서치(자연어 질문), 상승 소재 상세, 주도 종목 상세.
- **현재 구현과의 차이** (전부 교체 대상):

| | 시안 | 현재 구현 |
|---|---|---|
| 브랜드색 | 주황 `#ff6600` | 청록 `#0e6f64` (`global.css:11`) |
| 토큰 | `--djv-*` 39개 | `--color-*` 등 22개 |
| 단위 | px | rem |
| 레이아웃 | 420px 단일 열 | 1152px + 데스크톱 사이드바 |
| 하단 탭 | 4개 (홈·실시간·즐겨찾기·리서치) | 3개 (오늘·인사이트·관심) (`App.tsx:25`) |
| 홈 | 순위 휠 | 세로 카드 목록 |
| 토큰 파일 | 별도 패키지 | `global.css` 931줄에 섞임 |

- **할 일**:
  1. `apps/web/src/styles/tokens.css` 신설 — 시안 토큰 39개를 adaptation plan §7 실측값으로 이식. `global.css`의 기존 22개 변수 제거.
  2. shell 교체 — 420px 단일 열, 데스크톱 사이드바(`.sidebar`, `64rem`·`40rem` 미디어쿼리) 제거, 하단 탭 4개, 당근 monochrome 아이콘 도입(`@karrotmarket/react-monochrome-icon`).
  3. route 추가 — `/research`(자리만). 탭 표시명을 홈·실시간·즐겨찾기·리서치로 교체. path는 기존 `/today`·`/insights`·`/saved` 유지.
  4. 홈 — 순위 휠 이식. 시각·조작감은 그대로, DOM 3벌 복제는 1벌로 줄이고 방향키 이동을 추가(§12.1).
  5. 테마 상세 — 시안 섹션 순서·카드 구조로 교체(§8.3). 조건부 섹션(과거 소재 TOP3·이벤트 스터디·케이스)은 게이트 미통과 시 섹션 자체를 숨김.
  6. 즐겨찾기 — 시안의 저장 목록 + 최근 본 테마 구성(§8.6). 최근 본 테마는 서버 저장(시안은 `localStorage`).
  7. 로그인 — 시안 모달 시각 유지, 이메일·비밀번호 폼을 Google 로그인 버튼으로 교체(§8.7).
  8. 리서치 — 화면 자리와 시각 구성만. 자연어 질의 기능은 구현하지 않는다(요구사항 문서 없음).
  9. 상태 표현 — 시안에 없는 loading·DELAYED·DEGRADED·empty·error·Coverage를 시안 시각 언어로 신규 작성(§10).
- **완료 조건**: 배포 시안과 화면별 대조, `pnpm --dir apps/web run lint`·`typecheck`·`test --run`·`build` 통과, 기존 접근성 테스트 유지.
- **선행 없음.** C-12보다 먼저 착수한다. C-12(트리맵)는 C-0의 시안 3단 배치를 전제로 하므로 C-0 이후가 낫다.
- **주의**: 데스크톱 사이드바를 지우면 `apps/web/src/test/**`의 라우팅·접근성 테스트가 깨질 수 있다. 테스트도 시안 구조 기준으로 함께 고친다.

### C-12. 인사이트 트리맵

- **현재 상태**: 서버 쪽 절반은 이미 됨 — 파이프라인이 `THEME_TREEMAP` 스냅샷(≤12타일, REST `/v1/insights/treemap` + WS)을 발행 중. 웹 `InsightsPage.tsx`는 placeholder.
- **할 일**: 웹 트리맵 컴포넌트. **배치는 시안의 3단 고정 구조**(상단 2 · 중단 3 · 하단 3, `page.tsx`의 `RealtimeThemeScreen`)를 따르고, 타일 수·값·상태 규칙은 [realtime_theme_treemap_implementation_plan.md](./realtime_theme_treemap_implementation_plan.md)와 [screen_spec.md](./screen_spec.md) §6.3을 따른다. 실제 값만, stale/reduced-motion/키보드 접근성. today와 값 일치 검증.
- **선행**: C-0.

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

- **범위**: 주도주 T-1·T0가 아니라 **전 종목 일봉**이다. KRX 일별매매정보 엔드포인트(`stk_bydd_trd`·`ksq_bydd_trd`·`knx_bydd_trd`)는 **1회 호출 = 1거래일 = 그 시장 전 종목**이라 종목을 골라 받는 옵션 자체가 없다. 골라 담는 쪽이 더 번거롭고, 전 종목 21년치라도 인덱스 포함 1~2GB에 그친다.
- **기간·대상**: 2005-03(인포스탁 사건 기록 시작) ~ 현재, 코스피·코스닥·코넥스. **상장폐지 종목을 반드시 포함한다** — 빼면 과거 통계가 생존 편향으로 낙관 쪽으로 왜곡되고, 테마주는 폐지 비율이 특히 높다.
- **필드**: 리서치 스펙 6.4 `daily_prices` 그대로 — 원주가 OHLC + **수정주가 OHLC + `adjustment_version`**. 액면분할·무상증자로 과거 주가가 소급 조정되므로 원주가와 조정주가를 함께 남기고 조정 버전을 찍어야 같은 질문에 같은 답이 재현된다.
- **왜 T-1·T0로 부족한가**: E-18(TOP3)만 보면 T-1·T0로 충분하지만, 리서치 스펙 8절 지표(20거래일 내 최고 종가 발생일, 양의 수익률이 끊길 때까지 연속 거래일 수 등)와 시장 대비 초과수익은 **T+20까지·전 종목**이 있어야 계산된다. E-19와 자연어 질의도 같은 전제다.
- **수집 시작 시점**: 열쇠가 A-2와 같은 KRX 키이고 어댑터(`packages/reference-data/reference_data/adapters.py`)와 수정주가 로직(`adjusted_price.py`)이 이미 fixture 검증 완료라, **A-2에서 당일 수집이 성공하면 그 자리에서 과거 백필을 백그라운드로 시작한다.** 21년 × 거래일 245 × 3시장 ≈ 1.5만 회 호출이라 레이트리밋 때문에 며칠 걸린다. 출시 후에 시작하면 E-18이 그만큼 밀린다. 순서(E는 출시 후)를 바꾸는 게 아니라 **수집만 앞당기는 것**이다.
- point-in-time·leakage 금지 원칙은 [historical_event_matching_engine_research_spec.md](./historical_event_matching_engine_research_spec.md). 위치: `research/data/**`, `packages/historical-data/**`(하이픈 함정 주의 — `historical_data` 권장).

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

사용자가 세션을 열 때 참고하는 권장값이다.

| 작업 | 모델 | 추론 |
|---|---|---|
| A-1, A-2, A-5, A-6, A-7 | Opus 5 | max |
| A-8 거래일 전환·매일 수집 | **Fable 5** | **max** |
| A-3 키움 live 어댑터 | **Fable 5** | **max** |
| A-4 상시 파이프라인·영속화 | **Fable 5** | **max** |
| B-8 codex분 정리·뉴스 수집 | **Fable 5** | high |
| B-9, B-11 | Opus 5 | high |
| B-10 grounded LLM 요약 | **Fable 5** | high |
| C-0, C-12 | Opus 5 | max |
| D-13 장후 정합 | **Fable 5** | **max** |
| D-14 증분 수집(설계 포함) | **Fable 5** | high |
| D-15 운영자 콘솔 | Opus 5 | max |
| E-16 과거 주가 corpus | **Fable 5** | high |
| E-17 온톨로지 | **Fable 5** | **max** |
| E-18 TOP3 | Opus 5 | high |
| E-19 유사사례 엔진 | **Fable 5** | **max** |
| E-20 유사사례 화면 | Opus 5 | max |
| F-21, F-22, F-24 | Opus 5 | max |
| F-23 보안 점검 | **Fable 5** | **max** |
| F-25 실제 배포 | **Fable 5** | **max** |

Opus로 진행하다 판단이 어려워 보이면 그 작업만 Fable로 재시도한다.

## 추천 진행 순서

```
B-8 완료 (2026-08-15)
→ C-0(시안 디자인 전면 이식) → C-12(트리맵)
→ A-1 → A-2(키 대기 중 B-9~11 병행) → A-4 → A-5 → A-3(장중)
→ D-13~15 · A-6~7 (병렬 가능)
→ F-21~25 (1차 출시)
→ E-16 → E-17 → E-18(TOP3) → E-19 → E-20 (출시 후 업데이트)
```

- **C-0을 앞으로 당겼다.** 화면 골격(토큰·shell·탭·홈 구조)이 바뀌므로, A-5·B-11·C-12처럼 화면을 건드리는 작업을 먼저 하면 두 번 만들게 된다.
- 1차 출시선: A+B+C+D+F (핵심 가치 = 실시간 테마 + 근거).
- E는 출시 후 추가 가능하며, E-18(TOP3)이 E-19(유사사례)보다 먼저 나온다.
