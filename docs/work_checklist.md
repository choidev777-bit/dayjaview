# DAYJAVIEW 작업 체크리스트

- **용도**: 작업 항목별 완료 여부만 기록한다. 작업 내용 정의는 [remaining_work.md](./remaining_work.md)가 원본이고, 이 문서는 상태판이다.
- **갱신 규칙**: 작업을 끝내면 그 줄의 `[ ]`를 `[x]`로 바꾸고 뒤에 `— 완료일 commit해시`를 적는다. 못 끝냈으면 `[ ]`로 두고 남은 것을 한 줄로 적는다.
- **마지막 갱신**: 2026-08-16 · 기준 commit `a30ba7e` (E-17 완료, 기준셋·정확도는 후속 commit) · `uv run pytest -q` 571 passed·8 skipped

## 요약

| 그룹 | 완료 | 남음 |
|---|---|---|
| A. 실데이터 연결 | 7 / 8 | A-3 |
| B. 뉴스 근거 | 4 / 4 | (live 수집 실행·`.env.example` 항목만, B-8 참조) |
| C. 화면 | 2 / 2 | — |
| D. 매일 자동 운영 | 3 / 3 | — |
| E. 과거 연구 | 1 / 6 | E-16, E-18 ~ E-21 |
| F. 출시 | 3 / 5 | F-21, F-25 |

**1차 출시선(A+B+C+D+F) 기준으로 A 1건 · F 2건이 남았다.** E는 출시 후 — 다만 **E-21 1단계는 1차 출시에 붙일 수 있다. E-17(온톨로지)은 완료됐다.**

## A. 연습용 데이터를 진짜 데이터로 바꾸기

- [x] **A-1** 인포스탁 280개 테마를 실시간 계산에 연결 — 2026-08-15 `f47f2f2`
- [x] **A-2** 종목 기준정보 실데이터 채우기 — 2026-08-15 `5d4f311` · `c9ec059` · `5581961` · `9992e5c` · `1d3a61f` · `58421c8`
  - 실키 live 수집 성공: 전일종가 2,410/2,411, 상장주식수 2,410/2,411, 유동주식비율 VERIFIED 2,254(93.5%), 280개 테마 중 99개가 Coverage SUFFICIENT 후보.
  - live 응답에서만 드러난 결함 6건 수정: 최대주주 `계` 합계 행(이중 차감), 자기주식 `-`(0주 표기), 정기보고서 `as_of`가 결산기준일과 어긋나 저장분이 안 읽히던 것, 한 종목 공시 형태로 전체 적재가 중단되던 것, 결산기준일을 보고서코드로 가정하던 것, 비유동 보유를 주주별로 골라 이미 처분한 주주가 계속 차감되던 것.
  - 유동주식비율은 공시(OpenDART) 기준으로 자기완결 계산하고, 유동시총에 곱할 상장주식수만 KRX 최신을 쓴다. 두 값이 일치할 때만 확정하던 규칙은 기준일 차이로 정상 데이터를 버려 폐기했다.
  - 잔여 157종목: MISSING 118(공시 표 형식 상이) · CONFLICT 24(스팩·대량소각 등) · STALE 15(2025년 자료만 존재). 기업행위(권리락·액면분할) 원천이 없어 장중 시점 전일 종가가 비는 경우가 있다.
  - PD-001 잔여: 가중치 상한 20·25·30·35% 백테스트 미실시. 초기값 30%로 운영 중.
- [ ] **A-3** 키움 실시간 시세 장중 검증 — ①(live `ReadOnlyKiwoomPort` 어댑터)·②(게이트웨이·파이프라인 연결, `serve_live_api`)는 2026-08-15 `6bda408`로 완료. 남은 것: ③ 장중 소량 실행 검증(외부 관문: 개장일 + 실행 승인. 2026-08-15는 토요일·광복절 휴장).
- [x] **A-4** 파이프라인 상시 실행 + DB 저장 — 2026-08-15 `4dbee13`
  - 상시 publish 루프, DSN 있으면 Postgres 저장소, 장 마감 `close_market`. DSN 통합 테스트는 일회용 PostgreSQL 16으로 실행해 통과 확인.
- [x] **A-5** 테마 상세 화면 데이터 연결 — 2026-08-15 `4a9b538`
  - `theme_detail` 빌더 + `theme_for_event` 매핑. 계약 스키마 통과 + 브라우저 렌더 확인.
- [x] **A-6** 랭킹 부가 지표 살리기 — 2026-08-15 `1d64608`
  - `IntradayHistory`(`packages/pipeline/history.py`)가 거래일별로 분단위 누적 거래대금·그날 구성종목·장 마감 관심 신호를 파일로 축적한다. 실서빙 세션이 매 분 경계마다 쌓는다(`INTRADAY_HISTORY_ROOT`, 기본 `./data/intraday-history`).
  - 과거 기준선은 그날 유효했던 구성종목으로 계산해야 해서 명단도 같이 축적한다. 오늘 명단으로 과거를 재구성하면 그 사이 편입·제외된 종목이 섞인다.
  - `rankChange60s`는 60초 전 발행 순위와 비교, 급부상 배지(`RISING_FAST`)는 60초 순위 상승 3계단 + 5분 전 대비 수익률 증가(spec 13.8). 비교할 기록이 없으면 0이 아니라 null·배지 없음.
  - 거래대금 배수·관심 공백은 상세 `currentReaction`에 실린다. 축적 20거래일 미만이면 PROVISIONAL(품질 플래그 `PROVISIONAL_BASELINE`), 관측 미달이면 null. 축적이 차면 별도 조치 없이 값이 나온다.
  - 축적은 실서빙에서만 시작한다. fixture 서빙은 합성 데이터라 기준선을 오염시키지 않도록 연결하지 않았다. **실축적 0일이라 실제 배수·공백 값은 A-3 ③ 이후 장중 운영으로 쌓여야 나온다.**
- [x] **A-7** 관심 목록에 실데이터 연결 — 2026-08-15 `59c1ed1`
  - `SnapshotTargetCatalog`(fixture·live 양쪽 배선)가 그날 파이프라인의 테마·Event·종목 명단을 조회한다. 명단에 없으면 저장 404, 저장된 항목의 `SavedCurrentState`는 rankings와 같은 스냅샷에서 오늘 Event·상태·가중수익률을 읽는다.
  - 아직 공개 상태가 아닌 테마(후보 Event)와 종목은 `currentState` 없음. 비거래일·세션 없음이면 대상을 알지 못해 목록에서 UNAVAILABLE로 남는다.
- [x] **A-8** 거래일 전환과 매일 기준정보 수집 — 2026-08-15 `5273e9d` · `a52b12c` · `4672cd7`
  - ①(장중 기준가) `a52b12c`. 장중에는 당일 KRX row가 없어 전일종가 0/2,411·기업행위 해소 1/2,411이었다(실측). 키움 0B의 FID 11(전일대비)과 ka10095의 `base_pric`로 그날 기준가를 얻고, 전일 종가가 빈 종목에만 채운다. 권리락은 키움이 조정된 기준가로 전일대비를 계산해 자동 반영. 2026-08-14 실시장 녹화분 체결 287,753건 중 287,752건 산출, 등락률 교차 검산 불일치 0건.
  - ②③④⑤ `5273e9d`. `trading_day.py`(거래일 판정), `TradingDayLoop`(날짜 전환·비거래일 미가동·전환 시 이전 장 마감), `prepare_reference_data`(그날 수집본 없으면 수집, 실패 시 그날 계산 미시작).
  - 배선 `4672cd7`. `serve_live_api`가 `TradingDayLoop`으로 돈다. 키움 접속은 날짜가 바뀌면 끊고 다시 맺고(`LiveSessionController` — 토큰·WS·조건검색 후보가 그날 세션의 것), REST·WS는 `LivePipelineHandle`(SnapshotSource 위임 전달자)로 항상 현재 세션을 본다. `REFERENCE_DATA_ROOT` 설정 시 세션 빌드가 그날 기준정보 수집까지 포함하고, 실패한 날은 계산을 시작하지 않는다(PD-001 10항). Event·스냅샷 저장소는 세션이 바뀌어도 유지.
  - 잔여(A-8 밖): 실장중 통과 확인은 A-3 ③과 같은 날 이뤄진다. 공휴일 원천이 없어 휴장 평일에는 세션이 서되 이벤트가 없어 health DEGRADED로 드러난다.

## B. "왜 오르는지" 뉴스 근거

- [x] **B-8** codex 미커밋분 정리 + 뉴스 수집 마무리 — 2026-08-15 `2c855fb` · `2623713`
  - 잔여: live 수집 실행(외부 API 호출 승인 필요), `.env.example`에 `NEWS_RSS_SOURCES` 항목 추가(에이전트가 `.env*`를 쓸 수 없어 사용자 수행).
- [x] **B-9** 뉴스 ↔ 실시간 테마 매칭 — 2026-08-15 `cf153bd`
- [x] **B-10** 근거 있을 때만 AI 요약 — 2026-08-15 `9f83666`
- [x] **B-11** 근거 UI 완성 — 2026-08-15 `6c7fb83`
- [x] **(B 잔여)** 근거 REST 배선 — 2026-08-15 `92b7c94`. `MarketDataPipeline.evidence_document`(공개 Event만, 판정 전 SEARCHING) + `SnapshotProductReadRepository.evidence`. 브라우저에서 `/evidence` 200과 근거 섹션 렌더 확인. 잔여였던 live 수집 실행·`.env.example` 항목 추가는 B-8 줄에 그대로 남아 있다.

## C. 화면

- [x] **C-0** 시안 디자인 전면 이식 — 2026-08-15 `dd62033` (토큰 `tokens.css`, 브랜드색 `#ff6600`, 하단 탭 4개)
- [x] **C-12** 인사이트 트리맵 — 2026-08-15 `4eb51c6`

## D. 매일 자동 운영

- [x] **D-13** 장후 정합 (같은 eventId revision) — 2026-08-15 `f63bdb7`
  - `RECONCILE_EVENT` command + `packages/events/reconciliation.py`. 같은 날 같은 테마의 인포스탁 UP history만 MATCHED(분류 CONFIRMED/INFOSTOCK 승격), 기사 없으면 UNMATCHED, 늦은 기사는 UNMATCHED→MATCHED. 재실행 안전(결정적 message_id·종결 Event 건너뜀), state_logs에 axis 열 추가(`0004` 마이그레이션).
  - 실행 주체(매일 장후 스케줄)는 D-14의 증분 수집과 함께 배선한다. 지금은 모듈·command·영속화·테스트까지.
- [x] **D-14** 인포스탁 매일 증분 수집 자동화 — 2026-08-15 `32d1353`
  - `packages/infostock/increment.py` + worker `collect_increment.py`(매일 장후 스케줄러가 부르는 진입점). lookback 창(기본 7일)만 수집해 S1과 같은 schema·revision·lineage로 적재. 재실행은 input_hash로 reused(idempotent), 수정은 revision, 삭제는 **창 안으로 제한한** NOT_VISIBLE revision. `0005` 마이그레이션(INCREMENTAL run의 core SKIPPED).
  - 세션 설계 확정: Daily API는 무인증 공개 endpoint(S1 전체 4,655건이 무인증으로 수집된 실측 근거). 자동 로그인 없음 — 401/403이 오면 AUTH_REQUIRED로 멈춰 운영자에게 드러남(FR-10), 429는 RATE_LIMITED.
  - DSN 게이트 테스트는 일회용 PostgreSQL 16으로 통과(revision·창 내 숨김·reused, 0004·0005 실적용). **live 호출은 실행하지 않음** — 남은 것: 사용자 승인 아래 `--approved` 첫 실행과 배포 시 cron 등록(F-25).
- [x] **D-15** 운영자 콘솔 — 2026-08-16 `775e1c0`
  - 계약이 이미 정의해 둔 운영자 surface 10개(status·jobs·job·retry·resume·reviews·review·resolve·audit·infostock auth-status)를 전부 구현. `packages/operator`(도메인·in-memory 저장소) + `apps/api/operator_boundary.py`(allowlist 투영).
  - command 3개는 같은 순서를 지킨다: 같은 Idempotency-Key 재요청은 저장된 receipt 재생(중복 실행 없음) → 대상 없음 404 → expectedVersion 불일치 409 `STALE_VERSION` → 허용되지 않는 전이 409 `COMMAND_NOT_ALLOWED`. 실행한 command만 revision을 올리고 audit을 남긴다. retry는 FAILED·RATE_LIMITED·AUTH_REQUIRED, resume은 PARTIAL에서만.
  - redaction: internal_context와 command 사유 원문은 어떤 응답에도 넣지 않고, 투영 값이 안전 패턴을 벗어나면 500으로 닫는다. `authorize_operator_command`가 role을 먼저 봐서 일반 사용자에게 CSRF 실패 대신 권한 없음으로 답한다.
  - 화면은 `apps/web/operator.html` + `src/operator/**`로 사용자 SPA와 다른 entry다. 두 번들은 서로를 import하지 않고 사용자 router·navigation에도 없다.
  - **job·review 데이터를 넣는 쪽은 아직 배선하지 않았다.** 저장소는 프로세스 in-memory이고 `record_job`·`open_review` 진입점만 있다. 수집 worker와 D-13 정합 실행을 여기에 연결하는 것과 Postgres 영속화는 별도 작업이다.

## E. 과거 연구 기능 (출시 후)

- [ ] **E-16** 과거 주가 corpus — 진행 중, 2026-08-16 `b4aac7d`. 코드는 완료: `packages/historical_data/`(전 필드 parser · 재개 가능 백필 · 원주가+수정주가+adjustment_version SQLite corpus 빌더) + worker `apps/worker-batch/historical-data/`(`backfill_daily_prices.py`·`build_corpus.py`) + 테스트 19건. 데이터 위치는 `research/data/`(gitignore).
  - **원천 경계 실측 (probe 6구간)**: KRX Open API 일별매매는 **2010-01-01부터만 제공**한다 — 2009-12-30(그해 폐장일)까지 빈 응답, 2010-01-04부터 데이터, KOSPI·KOSDAQ 동일. **E-16이 정의한 2005-03~2009-12 구간은 이 원천에 없다.** 그 구간을 채우려면 다른 원천(정보데이터시스템 수동 CSV, 유료 벤더 등) 확보가 필요하며 이는 사용자 판단 사항. 인포스탁 사건 라벨(E-17)은 주가 없이도 전 기간 가능하고, E-18 통계는 2010년 이후 사건(밀도가 높은 구간)으로 계산된다.
  - 수정주가는 KRX 전일대비가 함의하는 기준가(종가−전일대비)/직전종가 factor를 소급 적용(A-8 ①과 같은 성질, 현금배당 미반영은 통상 관례와 동일). 전일대비 결측·비정상 factor는 값을 만들지 않고 그 앞 구간 NULL + `adjustment_breaks` 기록. 같은 입력이면 같은 출력(재현성 테스트 포함).
  - 남은 것: 2010-01-01~현재 백필(~1.2만 호출) 완료 확인 → `build_corpus.py` 실행·검증(상장폐지 종목 포함 여부를 종목 수로 확인). 2005-02 probe 빈 봉투 6개는 corpus 트리 오염 방지를 위해 scratchpad로 옮겨 둠(삭제 아님).
- [x] **E-17** 사건·소재 온톨로지 — 2026-08-16 `a30ba7e`
  - `packages/ontology/` — 통제어휘 v1(소재 유형 28종·키워드 900개, `vocabulary.py`) + versioned transform(`catalyst-transform/1.0.0`). 분류 축 4개: 소재 유형(복수 라벨, primary=원문 첫 등장 유형)·방향(꼬리 동사 우선)·확실성(확정/기대·전망 — 한국어 후치 수식이라 **마지막 표지 우선**: "타결 기대감"=기대, "협상 타결 소식"=확정)·지속(재부각·모멘텀). LLM 미사용 — 결정론적 키워드 span 선점 매칭(시작 위치→긴 키워드 우선)이라 같은 입력·같은 버전이면 같은 출력.
  - 설계 절차는 작업 정의 그대로: 전수 39,696건 어절 빈도 분석 → 고정 seed(20260816) 표본 1,000건 정독 → 유형 확정. **게이트: 기타(미분류) 전수 6.0%·표본 3.1%** (기준 10% 이하 → GO).
  - **기준셋 정확도 (2026-08-16 추가)**: 설계 표본과 겹치지 않는 새 seed(20260817) 1,000건을 블라인드(원문만) 수동 라벨한 `tests/ontology/goldset_v1.tsv`(key+라벨만, 원문 미포함이라 커밋 가능) 기준 — **primary 엄격 73.2% · 허용(대안 포함) 80.0% · 유형 포함 84.1% · 방향 99.8% · 확실성 86.7%** (어휘 1.1.0). 재채점은 `apps/worker-batch/ontology/score_gold_set.py` 1회 실행. 기준셋 정독 중 드러난 부분 문자열 충돌 4종(중국산→국산, 방송사→송사, 삼성화재→화재, 10억달러→달러)은 어휘 1.1.0에서 수리하고 회귀 테스트로 고정. 최초 설계 표본 100건 자기 대조의 "~98%"는 오염된(설계에 쓴 표본) 낙관치였음 — 기준셋 수치가 공식값이다. 주의: 이후 어휘 개선을 기준셋 불일치로부터 하려면 dev/test 분할(500/500)로 나눠 절반은 측정 전용으로 보존할 것.
  - 전수 라벨링 worker `apps/worker-batch/ontology/label_theme_history.py` → `research/ontology/labels.jsonl`·`coverage_report.json` 생성(어휘 버전·내용 해시·dataset hash 동봉, 게이트 미달 시 종료 코드 2). `research/ontology/`는 인포스탁 원문이 실려 gitignore — 로컬 전용, 같은 수집본·버전이면 재생성 동일.
  - E-18 요구 형태 충족: 복수 라벨 + primary 1개(한 사건이 여러 유형 표본을 부풀리지 않게), 근거 span(원문 오프셋), 온톨로지·분류 버전 보존. 방향은 로더 `_direction`(양쪽 표지 존재 시 MIXED)과 달리 반사 문장("유가 급락 …에 상승")을 UP으로 판정한다.
  - 한계(작업 정의가 기록 요구): 인포스탁이 기록한 사건만 다루며 기록 밀도가 연도별로 크게 다르다(2005년 96건 → 2024년 5,837건). 확실성은 문장 단위라 확정+기대가 섞인 복합 사유는 마지막 표지 계열로 수렴한다.
- [ ] **E-18** 과거 테마 반응 소재 TOP3 — 미착수. 선행: E-16 + E-17(완료).
- [ ] **E-19** 유사사례 검색 엔진 + 평가 — 미착수. 외부 관문: 2인 블라인드 평가.
- [ ] **E-20** 유사사례 화면 — 미착수. E-19 통과 전 노출 금지(현재 `HistoricalGatePage`가 gate-off 표시).
- [ ] **E-21** 리서치 탭 자연어 질의 — 미착수. 요구사항은 2026-08-15 작성 완료(PRD FR-11·6.2, screen_spec 11.7). 3단계로 나뉨 — 1단계(선행 없음, 1차 출시에 붙일 수 있음) / 2단계(선행 E-16+E-17) / 3단계(선행 E-19).

## F. 출시

- [ ] **F-21** 실제 구글 로그인 연결 — ②(조립 함수)는 2026-08-16 `4682549`로 완료. 남은 것: ① redirect URI 구글 콘솔 등록(사용자 계정 작업).
  - `create_production_app`(`apps/api/production.py`)이 env를 보고 고른다: 구글 키 둘 다 있으면 `HttpGoogleOAuthProvider`, 둘 다 없으면 fixture. **반쪽만 설정하면 즉시 실패** — fixture provider는 데모 code를 그대로 받으므로 실배포에서 fixture로 떨어지는 것이 인증 우회다. `DATABASE_URL`이 있으면 `PostgresIdentityRepository`(파이프라인과 connection 분리). `serve_live_api`가 이 조립을 쓰고, 데모 code 등록은 fixture provider일 때만 한다.
  - 일회용 PostgreSQL 16으로 확인: 같은 DSN으로 다시 조립해도 로그인 세션이 살아 있다(실제 영속). 조립된 앱의 `/auth/google`이 `accounts.google.com`으로 `redirect_uri=https://dayjaview.vercel.app/api/auth/google/callback`을 달고 나가는 것도 확인.
  - **① 사용자가 할 것**: 구글 클라우드 콘솔 → 해당 OAuth 2.0 클라이언트 ID → 승인된 리디렉션 URI에 `https://dayjaview.vercel.app/api/auth/google/callback` 추가. 로컬에서 실구글 로그인을 확인하려면 `.env.local`의 `APP_BASE_URL`을 웹 dev 서버와 같은 origin(`http://localhost:5173`)으로 바꾸고 `http://localhost:5173/api/auth/google/callback`도 함께 등록한다(지금 값은 `http://localhost:3000`이라 `/api` 프록시 origin과 다르다). 에이전트는 `.env*`를 쓸 수 없다.
- [x] **F-22** 운영자 계정 부트스트랩 — 2026-08-16 `5bf11e9`
  - `.env.local`에 `OPERATOR_BOOTSTRAP_GOOGLE_EMAILS=teamfomc@gmail.com` 설정 완료(2026-08-16). `.env.*`는 gitignore 대상이라 commit에는 안 잡힌다. 배포 env 주입은 다른 secret과 함께 F-25에서, 실제 구글 계정으로의 확인은 F-21 ① 이후.
  - **정정**: "에이전트는 `.env*` 쓰기 불가"는 사실이 아니었다. `Read(./.env.local)`만 deny이고 append 쓰기는 된다. 기존 바이트를 건드리지 않는 append로만 쓰고 내용은 출력하지 않는다.
  - `parse_operator_bootstrap_emails`·`ApiSettings.from_environment`에 테스트가 없어서 **환경변수 문자열이 역할로 이어지는지 확인된 적이 없었다.** env 값 → 로그인 → `/auth/session` roles에 OPERATOR → 운영자 API 200, 목록 밖·미검증 이메일·미설정은 403으로 고정했다.
  - **역할은 로그인 시점에 부여된다.** 이미 로그인한 뒤 env를 켰다면 다시 로그인해야 운영자가 된다(테스트로 고정).
  - 실행 중인 fixture 서버에서 데모 로그인이 `roles: [USER, OPERATOR]`를 받고, 브라우저 `/operator.html`이 운영자 5개 endpoint를 200으로 읽어 D-15 콘솔이 렌더되는 것까지 확인했다. local fixture 서버는 `.claude/launch.json`이 데모 계정을 운영자로 부트스트랩한다.
  - **부수 수정** 2026-08-16 `5e5b54f`: `infra/deployment/environment.contract.json`이 구글 키를 `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`으로 선언하는데 코드·`.env.example`은 `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`을 읽고 있었다. 계약대로 배포하면 구글 키가 비어 보여 fixture provider(인증 우회)로 떨어진다. 계약 이름을 코드에 맞추고, compose secret 검사의 금지 이름도 고쳤다(이전 이름으로는 실제 변수를 못 잡았다). 계약 파일을 읽는 코드가 없어 아무도 못 잡던 것이라 `test_compose_contract.py`에 계약↔코드 이름 일치 검사를 넣어 고정했다.
- [x] **F-23** 보안 점검 — 2026-08-16 `0e12854`
  - 결과: [security_audit.md](./release/security_audit.md) · 재현 테스트 `tests/security/**` 11개. **제품 파일은 고치지 않았다**(로드맵이 수리를 별도 작업으로 못박음). 13건 발견 — 중간 1 · 낮음 6 · 정보성 6.
  - **낮음(심각도 정정)** 근거 기사 URL의 scheme이 http(s)로 제한되지 않는다. `canonical_url`은 "절대 주소인가"만 봐서 `javascript://host/%0a…`가 통과하고, 저장되는 값은 정규화본이 아니라 공급원 원문이다. 그게 웹의 유일한 동적 `href`(`ThemeDetailPage.tsx:134`)에 들어간다. **처음에 "중간"으로 올린 근거("React가 `href` scheme을 검사하지 않는다")는 사실이 아니었다** — react-dom 19.2.8이 렌더 시점에 `javascript:` href를 차단한다(운영 빌드 포함). 지금 화면에서는 실행되지 않아 낮음으로 내렸다. 검증 안 된 값이 저장·전달되는 것과, React가 막지 않는 `data:` 계열은 그대로 남는다.
  - **중간** OpenDART 키가 `?crtfc_key=`로 URL에 실려 httpx 예외 메시지에 박히고 `__cause__`에 보존된다. 바로 옆 KRX 어댑터는 헤더로 올바르게 보낸다. 지금은 worker가 최상위 메시지만 출력해 유출이 없지만 traceback을 로깅하면 새어 나온다.
  - **낮음** 인증 진입점에 rate limit이 없고(무인증 `/auth/google`이 요청마다 행 생성) 만료 레코드를 지우는 경로가 없다 · 웹 정적 호스트에 보안 헤더 설정 파일이 없다(운영자 콘솔 clickjacking·CSP 부재) · OpenDART ZIP 무제한 해제 · 외부 응답 크기 상한 없음 · 인포스탁 `urlopen` 리다이렉트 추종.
  - **F-25에 직접 걸리는 것 2건**: 배포 env 계약이 코드가 안 읽는 `SESSION_SIGNING_SECRET`·`APPLICATION_ENCRYPTION_KEY`를 production 필수로 선언한다(회전해도 보호 대상 없음). 그리고 커서 서명 키가 프로세스마다 무작위라 **API를 2개 이상 띄우면 관심 목록 2페이지부터 실패한다**(fail-closed).
  - **의존성 취약점 조회**(최초판에서 미실시로 남겼던 것, 2026-08-16 실행): 웹 `pnpm audit` **0건**(dev 포함 267개), 파이썬 `pip-audit` **1건** — 개발 전용 `pytest 8.4.1`의 `PYSEC-2026-1845`(UNIX `/tmp/pytest-of-{user}` 경로, 수정본 9.0.3). 제품에 실리지 않아 정보성이고, major 상향이라 올리는 건 별도 작업이다.
  - 방어가 확인된 축: OAuth state 브라우저 바인딩·1회 소비, CSRF 3중(Origin+double-submit+서버 해시), `__Host-` 쿠키, 원문 토큰 미저장, 운영자 필드 allowlist, SQL 전면 파라미터화, 위험 함수(`pickle`/`eval`/`os.system`/`yaml.load`/`lxml`/`verify=False`) 전무, 웹 XSS 싱크 전무.
  - **수리** 2026-08-16 `fb95950`: 13건 중 **12건을 고쳤다**(11번 `/api/health` 무인증만 표준 범위라 유지). 수집 URL scheme 제한(거부 사유 `INVALID_URL`), OpenDART 예외 체인 차단, 외부 응답 32MiB 스트리밍 상한·ZIP 64MiB 상한·리다이렉트 거부·`_slug` 점 제거, `/auth/*` 한도(5분 20회 → 429)와 만료 레코드 `purge_expired`, `SESSION_SIGNING_SECRET`을 커서 서명 키로 연결(공유 저장소인데 없으면 기동 실패), `apps/web/vercel.json` 보안 헤더, 운영자 repo `safeReturnTo`, `pytest` 9.0.3(`pip-audit` 0건). `tests/security/**`는 이제 고쳐진 동작을 고정한다.
  - 수리 중 **새로 드러난 것**: 계약이 production 필수로 선언한 `REDIS_URL`·`INFOSTOCK_SESSION_STATE_PATH`도 코드가 전혀 읽지 않았다(Redis는 저장소에 아예 없다). 8번과 같은 성격이라 함께 필수에서 내리고 "production 필수 = 코드가 읽는다"를 테스트로 고정했다. **F-25에서 이 3개 값은 주입하지 않아도 된다.**
- [x] **F-24** 품질 점검 — 2026-08-16 `372f749`
  - 결과: [qa_report.md](./release/qa_report.md). **제품 파일은 고치지 않았다**(수리는 별도 작업). 5건 발견 — 높음 1 · 중간 2 · 정보성 2.
  - 실행: 계약 검증(HTTP 30·WS 9·schema 79·fixture 43) · `uv run pytest -q` 518 passed·8 skipped · ruff·mypy(108 파일) · 웹 lint·typecheck·test(83)·build · compose config · 브라우저 E2E · axe-core 6화면 · 실규모 성능. **저장 market replay는 실행하지 않았다**(로드맵이 제외).
  - **높음(배포 차단)**: 핵심 종목을 전부 관측한 테마에서 `observed_weight_ratio`가 1을 2.16e-28만큼 넘겨 `CoveragePart`가 ValueError를 던진다. 분모는 기본 28자리로, 분자는 `prec=60`으로 더하기 때문이다(`theme_metrics.py:301-320`). dirty batch가 원자적이라 다음 주기도 같은 자리에서 죽고, 발행 루프에 예외 처리가 없어 **task가 조용히 멈춘 채 API는 마지막 스냅샷을 계속 서빙한다.** 실규모 우주 5주기 내 재현. 기존 테스트는 fixture 시총이 작아 못 잡았다.
  - **중간**: `/auth/*` 요청 한도(20회/5분·전송 계층 주소)가 인증된 `/auth/session`까지 함께 세서, 프록시 뒤 배포에서는 전 사용자가 한 통을 공유해 정상 사용자가 429를 받는다(이번 E2E 중 브라우저 하나로 발생) · 색상 대비 WCAG AA 미달 23곳(최악 2.03:1, 브랜드 오렌지 토큰 문제. 운영자 콘솔은 0건).
  - **정보성**: 웹 a11y 테스트가 `color-contrast` 규칙을 끄고 통과해 대비 문제는 CI가 구조적으로 못 본다 · publish 한 주기의 85%가 `_select_references` 선형 스캔(테마×멤버×2,411 참조).
  - 성능 판정: 체결→종목 상태 반영 P95 **6.2ms**(목표 500ms) · 테마 재계산+발행 P95 **393~996ms**(목표 1초, 전 종목 확산에서 목표선에 붙음) · REST P95 2.6~4.6ms · WS 스냅샷 간격 2,006ms(발행 주기 2초와 일치) · 사용자 SPA JS 294kB(gzip 94.5kB).
  - **수리(발견 1)**: 분모 합산을 분자와 같은 `prec=60` 안으로 옮겼다. 전량 관측이면 몫이 정확히 1이 된다. 회귀 테스트 1건 추가(수리 전 코드에서 실패하는 것 확인). 실규모 재현이 40주기 완주하며 **순위 98건**을 만들고 publish P95는 773ms다. `uv run pytest -q` 519 passed·8 skipped · ruff·mypy 통과. **발행 루프의 실패 처리(조용히 멈춤)는 안 고쳤다** — 설계 결정이라 별도 작업.
  - **수리(발견 2)**: 요청 한도를 `/auth/` 전체가 아니라 무인증 OAuth 진입점 둘(`/auth/google`, `/auth/google/callback`)에만 건다 — 세션 조회는 예산을 안 먹는다. 세는 단위는 `TRUSTED_PROXY_HOPS`를 선언했을 때만 `X-Forwarded-For`의 그 자리 값을 쓰고, 기본 0이면 전송 계층 주소만 쓴다(위조 header를 기본으로 믿지 않는다). 회귀 테스트 2건, env 계약에 `TRUSTED_PROXY_HOPS` 선언. 실행 중인 서버로 재확인: 세션 60회 연속 200 → 로그인 시작 302, 로그인 시작 연속 25회는 19회 뒤 429. **F-25에서 Vercel `/api/*` rewrite를 붙이면 이 값을 실제 앞단 프록시 수로 넣어야 한다.**
  - 발견 3·4·5는 미수리.
- [ ] **F-25** 실제 배포 — 준비물은 2026-08-16에 완료, 남은 것: **사용자 승인 아래 실제 배포 실행**(OCI VM에서 4절 수행 + Vercel 프로젝트 생성 + F-21 ① redirect URI).
  - 준비 완료: production compose(`infra/deployment/compose.production.yml`, Caddy TLS·persistent volume·secret은 `/etc/dayjaview/*.env` 참조만) · live 진입 `infra/operations/live_stack.py`(fixture 모드 fail-closed) · migration runner에 명시적 production 게이트 · Vercel `/api/*` rewrite + SPA fallback(`apps/web/vercel.json`) · 배포·백업/복구 runbook [operations_runbook.md](./release/operations_runbook.md). compose 스키마·Caddyfile은 로컬 docker로 검증, 계약 테스트 `tests/infra/**` 추가.
  - Redis는 배포하지 않는다(F-23 — 코드가 안 읽음). market worker 컨테이너도 없다(A-8 거래일 루프가 api 안에 있음). `TRUSTED_PROXY_HOPS=1` 근거는 runbook 4절.

## 남은 외부 관문 (에이전트가 못 여는 것)

| 관문 | 막히는 작업 | 필요한 것 |
|---|---|---|
| 주식장 개장 시간 + 키움 live 승인 | A-3 | 장중 실행, CLAUDE.md 승인 항목 2 |
| 인포스탁·뉴스 live 호출 승인 | B-8 잔여, D-14 | CLAUDE.md 승인 항목 2 |
| `.env.example` 항목 추가 | B-8 잔여 | 없음 — F-22에서 `.env*` append 쓰기가 가능한 것으로 확인됐다. 요청 시 진행 |
| 구글 콘솔 redirect URI 등록 | F-21 ① | 사용자 계정 작업 |
| 2인 블라인드 평가 | E-19 → E-20 | 사람 평가 통과 |
| 과거 주가 2005~2009 원천 | E-16 잔여 구간 (E-18·E-19의 2010년 이전 사건 outcome) | KRX Open API가 미제공(실측) — 대체 원천 확보 또는 2010년 이후로 범위 확정, 사용자 판단 |
| 배포·cloud·DNS 승인 | F-25 | CLAUDE.md 승인 항목 1 |

## 진행 순서 (remaining_work.md 기준, 완료분 제외)

```
A-3 (장중 승인 필요) → F-21 ~ F-25 (1차 출시)  [E-21 1단계 동반 가능]
→ E-16 → E-18 → E-19 → E-20 (출시 후)
→ E-21 2단계(E-16+E-17 후, E-17은 완료) → E-21 3단계(E-19 후)
```
