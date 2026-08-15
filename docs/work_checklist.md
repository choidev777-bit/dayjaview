# DAYJAVIEW 작업 체크리스트

- **용도**: 작업 항목별 완료 여부만 기록한다. 작업 내용 정의는 [remaining_work.md](./remaining_work.md)가 원본이고, 이 문서는 상태판이다.
- **갱신 규칙**: 작업을 끝내면 그 줄의 `[ ]`를 `[x]`로 바꾸고 뒤에 `— 완료일 commit해시`를 적는다. 못 끝냈으면 `[ ]`로 두고 남은 것을 한 줄로 적는다.
- **마지막 갱신**: 2026-08-16 · 기준 commit `775e1c0` · `uv run pytest -q` 485 passed·7 skipped

## 요약

| 그룹 | 완료 | 남음 |
|---|---|---|
| A. 실데이터 연결 | 7 / 8 | A-3 |
| B. 뉴스 근거 | 4 / 4 | (live 수집 실행·`.env.example` 항목만, B-8 참조) |
| C. 화면 | 2 / 2 | — |
| D. 매일 자동 운영 | 3 / 3 | — |
| E. 과거 연구 | 0 / 6 | E-16 ~ E-21 |
| F. 출시 | 0 / 5 | F-21 ~ F-25 |

**1차 출시선(A+B+C+D+F) 기준으로 A 1건 · F 5건이 남았다.** E는 출시 후 — 다만 **E-21 1단계는 1차 출시에 붙일 수 있고, E-17은 외부 관문이 없어 지금도 착수 가능하다.**

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

- [ ] **E-16** 과거 주가 corpus — 미착수. `research/`, `packages/historical_data` 없음. A-2 키로 백필 시작 가능(1.5만 회 호출, 며칠 소요).
- [ ] **E-17** 사건·소재 온톨로지 — 미착수. **외부 관문 없음** — 재료(history 39,696건)가 이미 `data/infostock/import/**`에 있고 주가·KRX 키 불필요. 지금 착수 가능.
- [ ] **E-18** 과거 테마 반응 소재 TOP3 — 미착수. 선행: E-16 + E-17.
- [ ] **E-19** 유사사례 검색 엔진 + 평가 — 미착수. 외부 관문: 2인 블라인드 평가.
- [ ] **E-20** 유사사례 화면 — 미착수. E-19 통과 전 노출 금지(현재 `HistoricalGatePage`가 gate-off 표시).
- [ ] **E-21** 리서치 탭 자연어 질의 — 미착수. 요구사항은 2026-08-15 작성 완료(PRD FR-11·6.2, screen_spec 11.7). 3단계로 나뉨 — 1단계(선행 없음, 1차 출시에 붙일 수 있음) / 2단계(선행 E-16+E-17) / 3단계(선행 E-19).

## F. 출시

- [ ] **F-21** 실제 구글 로그인 연결 — `create_production_*` 조립 함수 없음. redirect URI 구글 콘솔 등록 필요(사용자와 함께).
- [ ] **F-22** 운영자 계정 부트스트랩 — 코드 경로는 있음. 배포 env에 `OPERATOR_BOOTSTRAP_GOOGLE_EMAILS` 설정 필요.
- [ ] **F-23** 보안 점검 — `tests/security/**` 없음.
- [ ] **F-24** 품질 점검 — `docs/release/qa_report.md` 없음.
- [ ] **F-25** 실제 배포 — 외부 관문: 사용자 승인.

## 남은 외부 관문 (에이전트가 못 여는 것)

| 관문 | 막히는 작업 | 필요한 것 |
|---|---|---|
| 주식장 개장 시간 + 키움 live 승인 | A-3 | 장중 실행, CLAUDE.md 승인 항목 2 |
| 인포스탁·뉴스 live 호출 승인 | B-8 잔여, D-14 | CLAUDE.md 승인 항목 2 |
| `.env.example` 항목 추가 | B-8 잔여 | 사용자가 직접 수행(에이전트는 `.env*` 쓰기 불가) |
| 2인 블라인드 평가 | E-19 → E-20 | 사람 평가 통과 |
| 배포·cloud·DNS 승인 | F-21, F-25 | CLAUDE.md 승인 항목 1 |

## 진행 순서 (remaining_work.md 기준, 완료분 제외)

```
A-3 (장중 승인 필요) → F-21 ~ F-25 (1차 출시)  [E-21 1단계 동반 가능]
→ E-16 → E-17 → E-18 → E-19 → E-20 (출시 후)
→ E-21 2단계(E-16+E-17 후) → E-21 3단계(E-19 후)

E-17은 외부 관문이 없어 위 순서와 무관하게 병렬 착수 가능.
```
