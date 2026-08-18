# DAYJAVIEW 작업 체크리스트

- **용도**: 작업 항목별 완료 여부만 기록한다. 작업 내용 정의는 [remaining_work.md](./remaining_work.md)가 원본이고, 이 문서는 상태판이다.
- **갱신 규칙**: 작업을 끝내면 그 줄의 `[ ]`를 `[x]`로 바꾸고 뒤에 `— 완료일 commit해시`를 적는다. 못 끝냈으면 `[ ]`로 두고 남은 것을 한 줄로 적는다.
- **마지막 갱신**: 2026-08-18 밤 · 기준 commit `4bf2523` 이후 E-22 9종 개방 배선 추가 (**`RESEARCH_SERVE_UNVERIFIED` 스위치·가격 corpus 배포 5-2·단계 3·4 적재 스크립트 — 실행은 배포 승인 대기**) · `uv run pytest -q` 746 passed·10 skipped (2026-08-18 측정)

## 요약

| 그룹 | 완료 | 남음 |
|---|---|---|
| A. 실데이터 연결 | 8 / 8 | — |
| B. 뉴스 근거 | 4 / 4 | — |
| C. 화면 | 3 / 3 | — |
| D. 매일 자동 운영 | 3 / 3 | — |
| E. 과거 연구 | 1 / 7 | E-16, E-18 ~ E-22 |
| F. 출시 | 5 / 5 | — |

**1차 출시선(A+B+C+D+F)은 백업 암호문 외부 보관 확인(사람 항목) 하나만 남기고 전부 닫혔다.** A-3은 2026-08-18 장중 검증으로, C-13은 같은 날 임시 해법으로 닫혔고, 장후 D-13 정합도 2026-08-18 실이벤트로 확인됐다(matched 10·missedIntraday 0·unmatched 269). E는 출시 후 진행 중 — **E-17(온톨로지)은 완료**, **E-22는 단계 0~6 구현이 커밋됐고 검수 통과분(질의 8종)이 운영에 열렸다. 나머지 9종은 사람 검수팩 기입 대기, 7은 미착수.** E-21은 독립 항목으로 진행하지 않고 E-22 단계 5가 맡았다(완료).

## A. 연습용 데이터를 진짜 데이터로 바꾸기

- [x] **A-1** 인포스탁 280개 테마를 실시간 계산에 연결 — 2026-08-15 `f47f2f2`
- [x] **A-2** 종목 기준정보 실데이터 채우기 — 2026-08-15 `5d4f311` · `c9ec059` · `5581961` · `9992e5c` · `1d3a61f` · `58421c8`
  - 실키 live 수집 성공: 전일종가 2,410/2,411, 상장주식수 2,410/2,411, 유동주식비율 VERIFIED 2,254(93.5%), 280개 테마 중 99개가 Coverage SUFFICIENT 후보.
  - live 응답에서만 드러난 결함 6건 수정: 최대주주 `계` 합계 행(이중 차감), 자기주식 `-`(0주 표기), 정기보고서 `as_of`가 결산기준일과 어긋나 저장분이 안 읽히던 것, 한 종목 공시 형태로 전체 적재가 중단되던 것, 결산기준일을 보고서코드로 가정하던 것, 비유동 보유를 주주별로 골라 이미 처분한 주주가 계속 차감되던 것.
  - 유동주식비율은 공시(OpenDART) 기준으로 자기완결 계산하고, 유동시총에 곱할 상장주식수만 KRX 최신을 쓴다. 두 값이 일치할 때만 확정하던 규칙은 기준일 차이로 정상 데이터를 버려 폐기했다.
  - 잔여 157종목: MISSING 118(공시 표 형식 상이) · CONFLICT 24(스팩·대량소각 등) · STALE 15(2025년 자료만 존재). 기업행위(권리락·액면분할) 원천이 없어 장중 시점 전일 종가가 비는 경우가 있다.
  - PD-001 잔여: 가중치 상한 20·25·30·35% 백테스트 미실시. 초기값 30%로 운영 중.
- [x] **A-3** 키움 실시간 시세 장중 검증 — ①·②는 2026-08-15 `6bda408`. ③ 장중 검증 2026-08-18 완료 `cd55c3c` · `7688f24`. 10:54 KST 실장중에서 키움 접속 → 조건검색 후보 → 테마 집계 → 이벤트 생성(CANDIDATE 274·ACTIVE 4) → rankings 스냅샷 6테마 갱신까지 통과. 막고 있던 결함 2건을 고쳤다: (1) 거래일 `as_of`를 UTC 자정으로 붙여 KST 00:00 세션 생성이 매일 실패(`cd55c3c`), (2) 어댑터가 6자리 숫자만 받아 영문 섞인 KRX 단축코드 53종목이 구독 요구를 만드는 첫 tick에서 루프를 죽임(`7688f24`). 외부 관문이던 키움 지정단말기 인증은 운영 VM IP(158.180.89.244) 등록으로 해소. `data_status`는 DEGRADED가 정상 — 동시 구독 상한 180 대 명단 6,629종목이라 coverage가 COMPLETE가 될 수 없다. 11:20 KST에 ACTIVE 9·CANDIDATE 263·WEAKENING 5·CLOSED 1로 소멸 전이까지 실관측했다(fixture로는 못 보던 구간). 종가 기준 지표와 D-13 장후 정합은 15:30 이후에 확인한다. → 같은 날 17:30 장후 cron에서 D-13이 그날 장중 Event 10건을 MATCHED로 연결(missedIntraday 0·unmatched 269, run 7 SUCCEEDED)해 확인 완료.
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

- [x] **B-8** codex 미커밋분 정리 + 뉴스 수집 마무리 — 2026-08-15 `2c855fb` · `2623713` · 2026-08-16 `5ae7a92`
  - 운영 서버에 Postgres 영속 저장과 상시 뉴스 worker를 배선했다. 2026-08-16 네이버 뉴스 live 호출로 10건 저장, 중복 없는 재수집과 재부팅 후 보존을 확인했다. `.env.example`의 `NEWS_RSS_SOURCES`·뉴스 DSN·네이버/OpenAI 항목도 실제 배포 계약과 맞췄다.
- [x] **B-9** 뉴스 ↔ 실시간 테마 매칭 — 2026-08-15 `cf153bd`
- [x] **B-10** 근거 있을 때만 AI 요약 — 2026-08-15 `9f83666` · 2026-08-16 `5ae7a92`
  - live `gpt-5.6-luna` 호출이 지정 JSON schema로 정상 응답하는 것을 운영 worker 안에서 확인했다. 주말이라 공개 Event가 0건이어서 실제 기사 요약 기록은 만들지 않았고, Event가 있을 때만 호출하는 기존 근거 게이트는 유지했다.
- [x] **B-11** 근거 UI 완성 — 2026-08-15 `6c7fb83`
- [x] **(B 잔여)** 근거 REST 배선 — 2026-08-15 `92b7c94` · 2026-08-16 `5ae7a92`. `MarketDataPipeline.evidence_document`(공개 Event만, 판정 전 SEARCHING) + `SnapshotProductReadRepository.evidence`. 브라우저에서 `/evidence` 200과 근거 섹션 렌더 확인. 근거 revision·뉴스 매칭·LLM 호출 기록을 Postgres에 영속하고 API 발행 직전에 다시 읽도록 배선했다.

## C. 화면

- [x] **C-0** 시안 디자인 전면 이식 — 2026-08-15 `dd62033` (토큰 `tokens.css`, 브랜드색 `#ff6600`, 하단 탭 4개)
- [x] **C-12** 인사이트 트리맵 — 2026-08-15 `4eb51c6`
- [x] **C-13** 장중 테마 상세·근거 갱신 — 2026-08-18 `22c3f0b` 임시 해법(문서가 지정한 경로). rankings 스냅샷이 도착할 때마다 상세 캐시를 비워 REST 재조회로 최신값을 받고, 근거는 30스냅샷(발행 2초 주기 기준 약 60초)마다 비운다 — 매번 비우면 "더 보기" 페이지네이션이 리셋되기 때문. WS 구독 목록이 상위 10개뿐이라 11위 이하 테마 상세가 갱신 대상에서 빠지던 부수 문제도 함께 사라진다. 정본 해법(백엔드 `event_state_changed` 발행 — hub fan-out 또는 계약 변경 필요)은 선택 후속으로 남고, 발행이 붙으면 프런트 임시 해법을 걷어낸다.

## D. 매일 자동 운영

- [x] **D-13** 장후 정합 (같은 eventId revision) — 2026-08-15 `f63bdb7` · 2026-08-16 `5ae7a92`
  - `RECONCILE_EVENT` command + `packages/events/reconciliation.py`. 같은 날 같은 테마의 인포스탁 UP history만 MATCHED(분류 CONFIRMED/INFOSTOCK 승격), 기사 없으면 UNMATCHED, 늦은 기사는 UNMATCHED→MATCHED. 재실행 안전(결정적 message_id·종결 Event 건너뜀), state_logs에 axis 열 추가(`0004` 마이그레이션).
  - D-14 뒤에 같은 날짜로 실행되는 운영 worker와 운영자 review 배선을 완료했다. 2026-08-14 실자료로 confirmations 21건을 읽어 성공했고, 배포 전이라 장중 Event가 없었던 21건은 `INFOSTOCK_WITHOUT_INTRADAY_EVENT` 검토 항목으로 남겼다.
- [x] **D-14** 인포스탁 매일 증분 수집 자동화 — 2026-08-15 `32d1353` · 2026-08-16 `5ae7a92`
  - `packages/infostock/increment.py` + worker `collect_increment.py`(매일 장후 스케줄러가 부르는 진입점). lookback 창(기본 7일)만 수집해 S1과 같은 schema·revision·lineage로 적재. 재실행은 input_hash로 reused(idempotent), 수정은 revision, 삭제는 **창 안으로 제한한** NOT_VISIBLE revision. `0005` 마이그레이션(INCREMENTAL run의 core SKIPPED).
  - 세션 설계 확정: Daily API는 무인증 공개 endpoint(S1 전체 4,655건이 무인증으로 수집된 실측 근거). 자동 로그인 없음 — 401/403이 오면 AUTH_REQUIRED로 멈춰 운영자에게 드러남(FR-10), 429는 RATE_LIMITED.
  - DSN 게이트 테스트는 일회용 PostgreSQL 16으로 통과(revision·창 내 숨김·reused, 0004·0005 실적용). 2026-08-16 승인 후 2026-08-14 기준 live 첫 실행 성공: 게시물 6건·관계 1,185건·revision 11건 저장. 평일 17:30 KST cron을 설치하고 재부팅 후에도 등록이 남는 것을 확인했다.
- [x] **D-15** 운영자 콘솔 — 2026-08-16 `775e1c0` · `5ae7a92`
  - 계약이 이미 정의해 둔 운영자 surface 10개(status·jobs·job·retry·resume·reviews·review·resolve·audit·infostock auth-status)를 전부 구현. `packages/operator`(도메인·in-memory 저장소) + `apps/api/operator_boundary.py`(allowlist 투영).
  - command 3개는 같은 순서를 지킨다: 같은 Idempotency-Key 재요청은 저장된 receipt 재생(중복 실행 없음) → 대상 없음 404 → expectedVersion 불일치 409 `STALE_VERSION` → 허용되지 않는 전이 409 `COMMAND_NOT_ALLOWED`. 실행한 command만 revision을 올리고 audit을 남긴다. retry는 FAILED·RATE_LIMITED·AUTH_REQUIRED, resume은 PARTIAL에서만.
  - redaction: internal_context와 command 사유 원문은 어떤 응답에도 넣지 않고, 투영 값이 안전 패턴을 벗어나면 500으로 닫는다. `authorize_operator_command`가 role을 먼저 봐서 일반 사용자에게 CSRF 실패 대신 권한 없음으로 답한다.
  - 화면은 `apps/web/operator.html` + `src/operator/**`로 사용자 SPA와 다른 entry다. 두 번들은 서로를 import하지 않고 사용자 router·navigation에도 없다.
  - 운영자 job·review·audit·명령 receipt·인포스탁 인증 상태를 Postgres에 영속했다. 뉴스 수집, D-14, D-13이 실제 job·review를 기록하며 API도 같은 저장소를 읽는다. 운영 서버에서 job 3종과 pending review 21건을 확인했고 재부팅 뒤에도 보존됐다.

## E. 과거 연구 기능 (출시 후)

- [ ] **E-16** 과거 주가 corpus — **2010-01-01~2026-08-14 구간 완성·검증**, 2026-08-16 `b4aac7d`·`76b0f2b`·`f6d5863`. 코드: `packages/historical_data/`(전 필드 parser · 재개 가능 백필 · 원주가+수정주가+adjustment_version SQLite 빌더) + worker `apps/worker-batch/historical-data/` + 테스트 21건. **남은 것은 2005-03~2009-12 구간 하나이며 외부 관문(원천 부재, 아래 표)이다.** E-18의 데이터 선행은 이 corpus + E-17(완료)로 충족된다.
  - **원천 경계 실측 (probe)**: KRX Open API 일별매매는 **2010-01-01부터만 제공** — 2009-12-30(그해 폐장일)까지 빈 응답, 2010-01-04부터 데이터, KOSPI·KOSDAQ 동일. 2005~2009를 채우려면 다른 원천(정보데이터시스템 수동 CSV, 유료 벤더 등) 확보 필요 — 사용자 판단.
  - **산출물**: `research/data/daily_prices.sqlite` 1.66GB — **9,578,134 rows · 3,899종목**, 원문 봉투 12,097개 3.5GB(`research/data/krx-daily/`, 모두 gitignore·이 PC 전용). KOSPI·KOSDAQ 거래일 4,091일(주중 휴장 245일), KONEX 3,221일(2013-07-01 개장부터). adjustment_version `krx-cmpprevdd-1@2026-08-14` 단일 스탬프.
  - **검증**: ① 상장폐지 포함 — 최종일 존재 2,872종목 vs 전체 3,899종목, 2026년 전에 사라진 종목 955개(생존 편향 없음) ② factor 5,416건 · break 0 · 수정가 NULL 0 · skipped 0 ③ 실분할 대조 — 삼성전자 50:1(2018-05-04, factor 정확히 1/50) · 카카오 5:1(2021-04-15) · NAVER 5:1(2018-10-12) 전부 자동 검출되고 분할 전후 수정주가 연속(거래정지일 기세 row 포함).
  - **기록해 둘 성질**: 카카오·NAVER factor는 명목 1/5이 아니라 **KRX 실효 factor**(호가단위로 반올림된 재상장 기준가/직전종가 = 56/279, 141/704)다. KRX가 공시한 기준가 그대로이며(지어내지 않음), 명목 비율을 쓰는 일부 벤더의 수정주가와 소수점 수준에서 다를 수 있다. 수집 중 KRX가 간헐적으로 JSON 아닌 본문을 준 사례 1회(2015-01-27 실측) — fetch 한정 재시도로 흡수(`f6d5863`).
  - 재수집은 `backfill_daily_prices.py`(파일 존재 기준 재개), 재빌드는 `build_corpus.py`(전체 재생성·원자 교체·같은 입력이면 같은 출력). 2005-02 probe 빈 봉투 6개는 scratchpad로 옮겨 둠(삭제 아님).
- [x] **E-17** 사건·소재 온톨로지 — 2026-08-16 `a30ba7e`
  - `packages/ontology/` — 통제어휘 v1(소재 유형 28종·키워드 900개, `vocabulary.py`) + versioned transform(`catalyst-transform/1.0.0`). 분류 축 4개: 소재 유형(복수 라벨, primary=원문 첫 등장 유형)·방향(꼬리 동사 우선)·확실성(확정/기대·전망 — 한국어 후치 수식이라 **마지막 표지 우선**: "타결 기대감"=기대, "협상 타결 소식"=확정)·지속(재부각·모멘텀). LLM 미사용 — 결정론적 키워드 span 선점 매칭(시작 위치→긴 키워드 우선)이라 같은 입력·같은 버전이면 같은 출력.
  - 설계 절차는 작업 정의 그대로: 전수 39,696건 어절 빈도 분석 → 고정 seed(20260816) 표본 1,000건 정독 → 유형 확정. **게이트: 기타(미분류) 전수 6.0%·표본 3.1%** (기준 10% 이하 → GO).
  - **기준셋 정확도 (2026-08-16 추가)**: 설계 표본과 겹치지 않는 새 seed(20260817) 1,000건을 블라인드(원문만) 수동 라벨한 `tests/ontology/goldset_v1.tsv`(key+라벨만, 원문 미포함이라 커밋 가능) 기준 — **primary 엄격 73.2% · 허용(대안 포함) 80.0% · 유형 포함 84.1% · 방향 99.8% · 확실성 86.7%** (어휘 1.1.0). 재채점은 `apps/worker-batch/ontology/score_gold_set.py` 1회 실행. 기준셋 정독 중 드러난 부분 문자열 충돌 4종(중국산→국산, 방송사→송사, 삼성화재→화재, 10억달러→달러)은 어휘 1.1.0에서 수리하고 회귀 테스트로 고정. 최초 설계 표본 100건 자기 대조의 "~98%"는 오염된(설계에 쓴 표본) 낙관치였음 — 기준셋 수치가 공식값이다. 주의: 이후 어휘 개선을 기준셋 불일치로부터 하려면 dev/test 분할(500/500)로 나눠 절반은 측정 전용으로 보존할 것.
  - 전수 라벨링 worker `apps/worker-batch/ontology/label_theme_history.py` → `research/ontology/labels.jsonl`·`coverage_report.json` 생성(어휘 버전·내용 해시·dataset hash 동봉, 게이트 미달 시 종료 코드 2). `research/ontology/`는 인포스탁 원문이 실려 gitignore — 로컬 전용, 같은 수집본·버전이면 재생성 동일.
  - E-18 요구 형태 충족: 복수 라벨 + primary 1개(한 사건이 여러 유형 표본을 부풀리지 않게), 근거 span(원문 오프셋), 온톨로지·분류 버전 보존. 방향은 로더 `_direction`(양쪽 표지 존재 시 MIXED)과 달리 반사 문장("유가 급락 …에 상승")을 UP으로 판정한다.
  - 한계(작업 정의가 기록 요구): 인포스탁이 기록한 사건만 다루며 기록 밀도가 연도별로 크게 다르다(2005년 96건 → 2024년 5,837건). 확실성은 문장 단위라 확정+기대가 섞인 복합 사유는 마지막 표지 계열로 수렴한다.
  - **정확도 개선 3라운드 (2026-08-16, 어휘 1.1.0 → 1.2.0 · transform 1.0.0 → 1.1.0)**: 기준셋을 dev/test 500/500으로 분할(dev=짝수 행, `score_gold_set.py --subset`)하고 dev·전수 미분류만 보며 수리, test는 라운드 종료 시 1회씩만 측정. transform 수리 — ① 꼬리 정규식이 "폭등에/급등에"의 "등에"를 열거 조사로 오식별해 core를 자르던 버그 ② 확실성 표지를 끝-위치로 비교해 "검토 소식" 류 복합 표지가 "소식"을 가리게 함 ③ 주제어 폴백 2계층(행위어 부재 시에만 primary) ④ 시세동사만 있는 문장의 MARKET_SYNC 폴백. 어휘 — 키워드 약 200개 보강·이동(제재→통상), 충돌은 `apps/worker-batch/ontology/audit_keyword_contexts.py`(신규, 앞뒤 어절 빈도 감사)로 전수 검증 후 채택. **test 최종: primary 엄격 77.8% · 허용 85.4% · 유형 포함 88.8% · 방향 99.6% · 확실성 90.0% · 전수 미분류 2.6%** (개선 전 전체 1,000 기준 73.2/80.0/84.1/99.8/86.7/6.0). dev는 87.8/93.2/94.8/100/96.2 — 목표(80/87/90/99.5/90) 중 test에서 확실성·방향·미분류는 충족, primary 3종은 1.2~2.2pp 미달로 규칙 기반 잔여 한계를 보고서에 기록. 기준셋 라벨은 한 건도 수정하지 않음.
- [ ] **E-18** 과거 테마 반응 소재 TOP3 — 미착수. 선행: E-16 + E-17(완료).
- [ ] **E-19** 유사사례 검색 엔진 + 평가 — 미착수. 외부 관문: 2인 블라인드 평가.
- [ ] **E-20** 유사사례 화면 — 미착수. E-19 통과 전 노출 금지(현재 `HistoricalGatePage`가 gate-off 표시).
- [ ] **E-21** 리서치 탭 자연어 질의 — **E-22 단계 5로 이관.** 답할 질문의 정본은 [company_event_ontology_implementation_plan.md](./company_event_ontology_implementation_plan.md) 4.0절 **질의 17종**이고, 요청 방식은 자연어 입력 하나다(날짜·종목을 클릭해 고르는 조회 화면은 만들지 않는다). 기존 "3단계" 구분은 데이터 확보 순서로만 남는다. 요구사항 문서는 2026-08-15 작성 완료(PRD FR-11·6.2, screen_spec 11.7).
  - **`DAY_MOVERS` 조회 화면·공개 엔드포인트는 제거했다** — `packages/infostock/daily_read.py`의 읽기 로직은 `PostgresResearchRepository`의 `DAY_MOVERS` 경로로 흡수했고, `GET /v1/daily/movers`·`DayMoversPage.tsx`·`/movers` route·홈 진입 링크·`getDayMovers` repository 메서드·`DayMovers*` 계약 스키마·fixture를 모두 없앴다. 공개 표면은 `POST /v1/research/answers` 하나다. 답변에 dataset·parser·어휘·query 버전이 붙어 13절 조건을 채웠다. `daily_read.py` 자체는 단계 0 겹 B 대조 스크립트(`verify_answers.py`)가 쓰는 두 번째 경로라 남겼다.
- [ ] **E-22** 회사 중심 사건 온톨로지·자연어 질의 확장 — **2026-08-17 착수, 단계 0~6 구현 완료. 2026-08-18 검수 통과분 운영 개방**: 겹 A 1,150·겹 C 1,910(원본 1,000+보강 910) 전량 `HUMAN_CONFIRMED`, 해석기 promotionEligible(test 820, 유형 98.41%) — goldset·계약·계산 fixture만으로 게이트가 닫히는 **8종**(`DAY_MOVERS`·`PERIOD_SUMMARY`·`STOCK_DAY_REASON`·`STOCK_TOP_MOVES`·`STOCK_THEME_MEMBERSHIP`·`THEME_MEMBERS`·`THEME_HISTORY`·`THEME_COMPARISON`)을 `RESEARCH_VERIFIED_QUERY_TYPES`로 열고 운영 DB에 단계 1(daily mention)·단계 2(회사 master)를 적재했다. **남은 것: 사람 검수팩 기입**(`research/ontology/human_quality_review/` — goldset 13유형 top-up 62·회사 역할 240·사건 단계 314·금액 111·중복 쌍 130·daily span 90·프로젝트 30, 전부 `AI_DRAFT`) **후 나머지 9종 개방, 승인 artifact 버전 고정, 단계 7(E-19 gate).** 단계 3·4 데이터는 해당 유형이 닫혀 있는 동안 운영 적재를 보류한다(2.0.0 파서로 적재된 운영 테마 원문과의 대조 문제 포함). **2026-08-18 밤 — 9종 개방 배선 완료(사용자 결정)**: `RESEARCH_SERVE_UNVERIFIED=1` 스위치(검수 전 유형도 답하되 humanVerified=False로 화면 경고 유지), 배포 5-2(가격 corpus 1.6GB 전송+`PRICE_CORPUS_PATH` 주입)·5-3(krx 사명 색인 전송), `vm_load_company_ontology.sh`(단계 3·4 운영 적재 — 배포 뒤 실행해 파서 버전을 맞춘다; input_hash가 parserVersion을 포함해 재배포 시 2.1.0 재적재됨을 확인). 남은 실행: 배포(승인 필요)+적재 스크립트. 검수팩 기입은 그대로 남는다.
  - [x] **선행** E-17 라벨 DB 적재 — 2026-08-17 `9f5e2de`. `0007`이 `ontology` 스키마와 `catalyst_vocabularies`·`catalyst_types`·`theme_history_labels`·`theme_history_label_spans` 생성. 적재 job `load_theme_catalyst_labels.py`(버전 append·idempotent). "이 소재 유형에 과거 어떤 테마가 반응했나"를 SQL 한 번으로 계산 가능해졌다.
  - [ ] **단계 0** 질문 계약·gold set — 17종 exact enum·필수 슬롯·집계 단위·선행 단계와 재현 hash를 `query_contracts.py`로 고정했다. 겹 A `split` 포함 1,150문장은 사용자 검수를 거쳐 전부 `HUMAN_CONFIRMED`이며 test 820건의 `promotionEligible=true`를 확인했다. 겹 C 보강 910건도 전부 `HUMAN_CONFIRMED`다. 기존분과 합친 test 955건은 strict 88.17%, lenient 92.25%, type recall 94.14%, direction 99.79%, certainty 94.76%, 누락 0건이다. **남은 것: test 30건 미만인 13유형(현재 25~29건) 추가 보강.** `AI_DRAFT`·`AI_CROSS_CHECKED`는 승격 판정에 쓰지 않는다.
  - [ ] **단계 1** DailyFeaturedTheme 파싱 확장 — `0011` typed source mention, Daily 변환·idempotent 적재·worker·coverage 보고서를 구현하고 로컬 서비스 DB에 적재했다. 활성 4,655 posts·213,446 relations에 mention 213,446건이 연결됐고 `PARSE_PARTIAL` 249건, 미지원 post 0건, `missingRelations=0`, `mismatchedRelations=0`이다. **남은 것: 사람 span/role 표본 검수.** `STOCK_DAY_REASON` 실제 응답은 단계 5 범위다.
  - [x] **단계 2** 회사 정체성·alias — 2026-08-17 `62a1b26` · `f90e9c6`. `0009`가 `core.company_entities`·`company_aliases`·`company_instruments`·`company_revisions`·`company_resolution_reviews` 생성. 사명 이력의 정본은 KRX 일별매매 종목명이다(`krx_names.py`) — 인포스탁은 과거 기록을 현재 이름으로 소급 정규화해 유효기간을 만들 수 없다. 미해결 과거 주도주는 임의 연결하지 않고 검수 대상으로 남긴다.
  - [ ] **단계 3** history 회사 역할 연결 — `0010`, 역할 9종·근거 span·본문/주도주/구성종목 분리, idempotent 적재와 worker를 구현했다. 전수 39,696 histories가 DB에 이미 존재하며 직접 사건 history는 1,749건이다. **남은 것: 사람 회사 역할 표본으로 precision·macro F1 gate 통과.**
  - [ ] **단계 4** 사건 구조·단계·중복 제거 — 절 분리, 단계 12종, 회사 밖 참여자·지역, 명시 프로젝트, 금액 fact, 현실 사건/테마 반응 분리, 보수적 중복 제거, revision DB 적재를 구현하고 로컬 서비스 DB에 COPY staging·집합 INSERT로 원자 적재했다. 40,104 drafts → 20,008 unique catalysts, 7,749 auto-merged catalysts, 27 possible-duplicate pairs, 20 projects, 금액 fact 640건, 테마 반응 39,696건이며 `missingHistories=0`, `mismatchedHistories=0`, artifact hash는 `8f0032…28b2`다. 같은 artifact 재적재는 신규 행 0건으로 확인했다. 상태는 `AI_DRAFT`다. **남은 것: 프로젝트·중복 후보·단계·금액 사람 검수와 11.2절 gate 통과.**
  - [x] **단계 5** E-21 질의 17종 — 2026-08-17 `d76dd9d`. 자연어 문장 하나를 17종 QueryPlan으로 옮기는 결정론적 해석기(`query_planning.py`, LLM 미사용), 유형별 답변 계산(`query_answers.py`), Postgres 저장소(`research_postgres.py`), 공개 경계(`apps/api/research.py` + `POST /v1/research/answers`), `/research` 자연어 입력 화면, artifact 발행기(`publish_company_ontology.py`)를 만들었다.
    - **겹 A 채점 (`score_query_goldset.py`, test split 820문장)**: 질의 유형 **98.41%** · 방향 **96.38%** · 소재 유형 **100%** · 회사 해석 85.0% · 날짜 60.31%(연도를 되찾을 수 있는 행만 보면 **93.83%**). dev split은 유형 98.79%·방향 97.33%다. dev만 보며 규칙을 고치고 test는 라운드 끝에 1회 측정했다.
    - **날짜 60.31%의 상한은 61.83%다.** gold 날짜 행 191건 중 76건이 문장에 `6/29`만 적혀 있어 gold가 기대하는 연도를 원문에서 되찾을 수 없다. 해석기는 가장 가까운 과거로 읽는다.
    - **회사 해석 85%의 미스는 전부 `AMBIGUOUS_ALIAS`다.** gold는 이름 접두사 충돌을 애매하다고 보지만 해석기는 정확히 일치하는 이름을 애매하다고 보지 않는다(계획서 8.1절 — 같은 alias를 두 회사가 쓸 때만 후보를 돌려준다). 규칙을 gold에 맞추면 정확한 사명 질문이 깨져서 바꾸지 않았다.
    - **거래일 기준·발행 전 "오늘"·가르지 못한 발행일**을 전부 처리한다. Daily 조회는 `source_mention_daily.trading_date` 기준이고, 발행 전이면 직전 거래일로 답하며 실시간 값을 섞지 않는다는 사실을 답에 적는다. 한 거래일에 게시물이 여럿이면 섞였을 수 있다고 표시한다.
    - **근거 coverage 100%를 코드가 강제한다.** 근거 없는 행이 하나라도 있으면 답을 내보내지 않고 `기록 없음`으로 끝낸다(11.2절 "답변 거부").
    - **질의 원문은 저장하지 않는다.** POST 본문으로만 받고(URL·접근 로그에 남지 않게), 실패 사유는 `유형:사유` 집계만 남긴다. 응답에 내부 사유를 넣지 않는다.
    - **남은 것: 온톨로지 품질 검수.** 겹 A와 겹 C 보강 910건은 전부 `HUMAN_CONFIRMED`지만 겹 C test 최소 표본 13유형과 회사 역할·사건 단계·금액·중복 쌍 gate가 남아 `RESEARCH_VERIFIED_QUERY_TYPES`는 아직 열지 않는다. 검수와 기준을 통과한 유형만 env에 넣어 연다.
  - [ ] **단계 6** 금액·실제 결과 — 계산 경로는 구현했다 (2026-08-17 `d76dd9d`). 금액은 합산 가능한 fact만 더하고 같은 고유 사건의 중복 금액을 한 번만 센다. outcome은 `SqliteOutcomeReader`가 E-16 corpus에서 T+1·T+5·T+20 실제 수익률을 읽고, **없는 값을 0으로 바꾸지 않고 `null`로 둔다.** 2010-01-01 이전 사건은 `제품 범위 밖`으로 답한다. **남은 것: 금액 exact-match 사람 검수(11.2절 98%).** `PRICE_CORPUS_PATH` 주입·corpus 전송은 배포 5-2 단계로 배선했다(2026-08-18 밤, 실행은 배포 대기). corpus 파일이 없으면 결과 질문 gate가 닫힌 채로 있다.
  - [ ] **단계 7** 유사사례 승격 — 미착수. E-19 gate 종속.
  - **외부 관문**: gold set 사람 검수는 완료했다. 다만 겹 C의 test 최소 30건을 못 채운 13유형은 계획서 11.1절에 따라 `측정 불가`이며 해당 유형에 의존하는 질문을 열지 않는다.

## F. 출시

- [x] **F-21** 실제 구글 로그인 연결 — 2026-08-16 완료. ②(조립 함수) `4682549` · ①(redirect URI)은 구글 콘솔에 이미 등록돼 있음을 확인 · 실배포에서 teamfomc 계정 실로그인 왕복 검증(roles USER+OPERATOR).
  - 실로그인에서만 드러난 결함 2건 수리: 실구글 callback의 부가 query 거부(`6cf8586`), RFC 9207 `iss` 거부(`b356285`). 로그인 테스트 helper가 실구글 모양 callback을 쓰도록 고정.
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
- [x] **F-25** 실제 배포 — 2026-08-16 완료 (`47e29c0`·`3fb5edc` 준비, `b9bc061`·`cec46a0`·`6cf8586`·`b356285`·`5ae7a92` 실행 중 수리). **서비스 공개 상태**: https://dayjaview.vercel.app (웹) + https://api.dayjaview.duckdns.org (API, TLS 자동발급) · 실로그인·rewrite·SPA fallback 외부 검증 통과.
  - 실행 기록: 기존 maptamin VM은 SSH가 내부 방화벽에 막혀 있고 Run command 에이전트도 죽어 있어 **디스크 보존 후 삭제 → dayjaview-prod 신규 생성**(같은 A1 4/24, 새 IP → DuckDNS 갱신). Security list에 80/443 추가. 배포는 `deploy_production.sh` 원커맨드(코드 archive 전송 — VM에 GitHub 자격증명 없음), 백업 cron 자동 설치.
  - 실행 중 드러난 결함 수리: git archive가 autocrlf로 SQL을 CRLF 변환해 마이그레이션 checksum 불일치(`b9bc061`, `.gitattributes` LF 고정 + manifest 재생성) · 구글 callback 부가 query 2건(F-21 줄 참조).
  - 2026-08-16 `5ae7a92` 재배포 후 Vercel `/api/health`가 OCI health를 그대로 200으로 전달하는 것을 확인했다. 암호화 백업 2개를 생성하고 일회용 DB로 업무 테이블 43개를 실제 복원한 뒤 제거했다. VM 재부팅 뒤 API·PostgreSQL·Caddy·뉴스 worker가 자동 복구되고 데이터와 장후 cron이 보존되는 것도 확인했다.
  - **남은 것(사람 확인)**: `/etc/dayjaview/backup.passphrase`는 root 전용 0600으로 존재하지만, 그 값을 사용자의 비밀번호 관리자 등 VM 밖에 보관했는지는 에이전트가 확인할 수 없다. 월요일 개장 시 키움 첫 실가동 관찰은 A-3 ③에 남는다.
  - 준비 완료: production compose(`infra/deployment/compose.production.yml`, Caddy TLS·persistent volume·secret은 `/etc/dayjaview/*.env` 참조만) · live 진입 `infra/operations/live_stack.py`(fixture 모드 fail-closed) · migration runner에 명시적 production 게이트 · Vercel `/api/*` rewrite + SPA fallback(`apps/web/vercel.json`) · 배포·백업/복구 runbook [operations_runbook.md](./release/operations_runbook.md). compose 스키마·Caddyfile은 로컬 docker로 검증, 계약 테스트 `tests/infra/**` 추가.
  - Redis는 배포하지 않는다(F-23 — 코드가 안 읽음). market worker 컨테이너도 없다(A-8 거래일 루프가 api 안에 있음). `TRUSTED_PROXY_HOPS=1` 근거는 runbook 4절.

## 남은 외부 관문 (에이전트가 못 여는 것)

| 관문 | 막히는 작업 | 필요한 것 |
|---|---|---|
| 주식장 개장 시간 | A-3 | 개장일 장중 실가동 관찰 |
| 백업 암호문 VM 밖 보관 | F-25 후속 안전조치 | 사용자가 비밀번호 관리자 등에 직접 보관 확인 |
| gold set 겹 C 최소 표본 보강 | E-22 13유형 의존 질의 개방 | 검수팩 `research/ontology/human_quality_review/goldset_topup_candidate.tsv` 62건의 `human_*` 열 기입 (계획서 11.1.2·11.2) |
| 회사 역할·사건 단계·금액·중복 쌍 검수 | E-22 나머지 질의 9종 개방 | 검수팩 `research/ontology/human_quality_review/` — 역할 240·단계 314·금액 111·중복 130·daily 90·프로젝트 30행의 `human_*` 열 기입 후 11.2절 판정 |
| E-16 가격 corpus 배포 주입 | E-22 단계 6 결과 질문 | `research/data/daily_prices.sqlite`(1.66GB, 로컬 전용)를 운영에 두고 `PRICE_CORPUS_PATH`로 가리켜야 outcome gate가 열린다 |
| 2인 블라인드 평가 | E-19 → E-20 | 사람 평가 통과 |
| 과거 주가 2005~2009 원천 | E-16 잔여 구간 (E-18·E-19의 2010년 이전 사건 outcome) | KRX Open API가 미제공(실측) — 대체 원천 확보 또는 2010년 이후로 범위 확정, 사용자 판단 |

## 진행 순서 (remaining_work.md 기준, 완료분 제외)

```
A-3 (개장 시간 필요)        ← 외부 관문, 병렬
C-13 (장중 상세 갱신)

E-22 단계 0 최소 표본 보강·단계 1·3·4 사람 검수  ← DB 적재 완료, 외부 관문(검수)
E-22 단계 5·6 구현 완료 — 검수된 유형을 env로 열면 그대로 서비스된다
E-22 단계 7 (E-19 gate)
E-18 (선행 E-16 2010~ 구간·E-17 충족, 지금 착수 가능)
E-19 → E-20 (2인 블라인드 통과 후)
E-16 2005~2009 구간 (대체 원천 확보 여부는 사용자 판단)
```
