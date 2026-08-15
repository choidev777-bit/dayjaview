# 2026-08-14 시장 replay fixture 완료 보고서

## 0. 판정 상태

**현재 상태: 수집 취소로 종료 — 계획된 완료 조건 미달, 부분 fixture로 확정**
(2026-08-15 갱신. 이전 문구는 "장중 수집 중"이었으나 실제로는 그날 취소로 끝났다.)

manifest 실측:

| | 본 수집 `data/market-replay/2026-08-14` | 보조 수집 `data/market-replay-supplemental/2026-08-14` |
|---|---|---|
| status / error | `INTERRUPTED` / `cancelled` | `INTERRUPTED` / `stopped by user` |
| 확보 구간(KST) | 09:00:00.569 ~ **10:39:31** | 10:09:17 ~ **10:39:24** |
| 계획 종료(KST) | 15:40 (`endAt`) — **미도달** | 장 마감까지 — **미도달** |
| event 수 | 1,803,564 (체결 1,561,132) | 133,965 (snapshot 132,561) |
| 종목 수 | 2,182 | 2,218 |
| 1분봉 | **0행** (`backfillMinuteBars: true`였으나 미실행) | — |

**이 데이터셋으로는 통과할 수 없는 게이트가 있다.** 장 마감 범위, 전 종목 1분봉,
run 정상 종료는 재수집 없이는 영구 미달이다. 정규장이 다시 열려야만 닫힌다.
아래 1절 표를 그 사실에 맞춰 갱신했고, PENDING(곧 끝남)과 FAIL(이 데이터셋으로
불가)과 미실행(절차를 돌리지 않음)을 구분했다.

이 보고서는 `one_time_market_replay_collection_plan.md`의 성공 조건을 실제 증거와
일대일로 대조하기 위한 문서다. 구현 코드가 존재하거나 테스트가 일부 통과했다는
이유만으로 데이터셋을 완료 처리하지 않는다. 계획이 정의한 `완료`는 달성되지
않았으며, 이 문서는 **확보된 범위 안에서 무엇이 검증 가능한지**를 확정하는
용도로 남긴다.

이 fixture의 완료는 DAYJAVIEW 제품 전체 기능이 이미 통과했다는 뜻이 아니다.
향후 구현된 서비스의 Market Gateway를 이 fixture에 연결해 오늘의 입력을 같은
순서와 시각으로 공급할 준비가 됐음을 뜻한다.

## 1. 최종 완료 게이트

| 게이트 | 통과 증거 | 현재 상태 |
|---|---|---|
| 목적·범위 설명 | 구현 계획 0~3절 | PASS |
| 수집·저장 구현 | collector, SQLite, NDJSON, manifest 소스 및 테스트 | PASS |
| 09:00 시작 범위 | 첫 REST 09:00:00.569, 첫 체결 09:00:00.863 KST | PASS |
| 장 마감 범위 | 마지막 시장 event가 15:30 이후 | **FAIL(영구)** — 10:39:31 KST 취소, 15:30 미도달 |
| 필수 장중 공급원 | 조건식, REST 4종, 0B, 0J, 0U, 구독 결정 | 09:00~10:39 PASS / 이후 데이터 없음 |
| 활성 테마 비구독 종목 보조 시세 | `ka10095` 30초 snapshot, coverage, 무오류 batch | 10:09:17~10:39:24 PASS / 09:00~10:09 공백 FAIL / 10:39 이후 없음 |
| 보조 시작 전 공백 복구 | `ka10084` 1분 market state, 전 테마 연관 master 종목 | **미실행** — 구현·1종목 smoke만 PASS. `data/market-replay-gap-recovery/` 생성되지 않음 |
| 본·보조 통합 재생 | 수신 시각 병합, 단일 sequence, payload hash 보존 | 제한 구간 PASS / 전 구간 대상 없음 |
| 전 종목 1분봉 | master 대비 완료 95% 이상, 실제 bar 종목 90% 이상 | **FAIL(영구)** — `minute_bars` 0행, 백필 미실행 |
| run 정상 종료 | `collection_runs.status=COMPLETED`, 종료 시각 존재 | **FAIL(영구)** — 본 `INTERRUPTED/cancelled`, 보조 `INTERRUPTED/stopped by user` |
| 저장 무결성 | `replay_market.py verify` exit 0 | **미실행** |
| 수집 완전성 | `replay_market.py audit` exit 0 | **미실행** — 완전성 기준이 장 마감·1분봉을 전제하므로 이 데이터셋으로는 통과 불가 |
| DB·원본·재생 동일성 | `replay_market.py prove` exit 0 | **미실행** |
| 서비스 profile 재생 | 실제 DB emit 건수/hash 증명 | **미실행** |
| WebSocket 재생 | 실제 DB의 제한 구간을 접속자가 순서대로 수신 | PASS |
| 재생 clock | 실제 DB 동일 구간을 1배속·20배속·무지연 재생 | PASS |
| 회귀 테스트 | 전체 pytest 통과 | 장중 23 PASS / 수집 종료 후 재실행 **미실행** |
| credential 부재 | audit의 exact-key 검사와 로그 검사 | **미실행** |

판정 구분: **FAIL(영구)** = 재수집 없이는 닫히지 않음(정규장 필요). **미실행** =
절차를 돌리면 결과가 바뀔 수 있음. 취소로 끝났으므로 4절 증명 절차는 어느 것도
실행되지 않았다.

## 2. 기능별 fixture 적합성 감사

| 향후 검증할 서비스 기능 | 필요한 fixture 입력 | 완료 판정 방식 |
|---|---|---|
| 후보 종목 발견 | 조건 편입·이탈, REST 4종 원본 | 필수 event와 정규장 polling gap 감사 |
| 실시간 종목 선정 | 후보 점수, 테마 확장, 구독 전체 집합·증감 | `subscription.changed`와 target 180 한도 감사 |
| 테마 집계·Coverage | 동결 인포스탁 membership, 선택 종목 0B, 비구독 종목 `ka10095` | reference hash, 0B provenance, 보조 snapshot coverage 감사 |
| CANDIDATE→ACTIVE 등 상태 전이 | occurred/received time이 보존된 체결·지수·breadth | sequence·timestamp hash 검증 후 시간 재생 |
| 급부상·주도주·순위 | 거래량·거래대금·등락률 field map | raw 0B와 canonical event 동일성 증명 |
| 시장 필터 | KOSPI·KOSDAQ 0J/0U | `001`, `101` 양쪽 존재와 장 마감 범위 감사 |
| 후보 밖 사후 사례 분석 | KOSPI·KOSDAQ 전 종목 1분봉 | master/완료/bar coverage 및 시간 범위 감사 |
| 상승 이유 대기·장애 상태 | 뉴스가 없는 입력 계약, source status/error | `SEARCHING/상승 이유 확인 중` 동작을 서비스에서 검증 |
| 장애·복구 | source status/error, REST 원본, 구독 재등록 | 기록된 운영 event를 replay하여 상태 처리 검증 |

**취소로 추가 제외된 항목(2026-08-15 갱신).** 위 표에서 09:00~10:39 구간으로
검증 가능한 것은 후보 종목 발견, 실시간 종목 선정, 테마 집계·Coverage,
CANDIDATE→ACTIVE 승격, 급부상·주도주·순위, 시장 필터, 상승 이유 대기·장애
상태다. 반면 **WEAKENING/CLOSED 소멸 전이**는 장 후반이 없어 재생할 수 없고,
**후보 밖 사후 사례 분석**은 1분봉이 0행이라 불가능하다. 이 둘은 A-3(실제 장중
실행)에서만 닫힌다.

뉴스 기사 매칭 품질, 20/60거래일 동시간대 baseline, 유동주식비율 승인값,
T+1/T+5/T+20 outcome은 오늘 하루 fixture만으로 만들 수 없으며 완료 범위에서
제외한다. 누락을 정상값으로 위장하지 않고 서비스가 `SEARCHING`,
`PROVISIONAL_BASELINE`, Coverage 부족으로 처리하는지를 검증한다.

## 3. 장중 확인 증거

2026-08-14 09:21 KST까지 확인된 내용:

- run: `market-2026-08-14-a99559a09258`, `RUNNING`
- 첫 REST 후보: 09:00:00.569 KST
- 첫 0B 체결: 09:00:00.863 KST
- 첫 조건 변화: 09:00:06.508 KST
- 첫 KOSPI·KOSDAQ 0J/0U: 09:00:29.702 KST
- REST 안전망: `ka10019`, `ka10023`, `ka10027`, `ka10032` 모두 관찰
- 시장 코드: 지수와 breadth 모두 `001`, `101` 관찰
- 실시간 구독 집합: 180종목 유지 확인
- 09:21 점검 누적 sequence: 623,000
- 공급원 오류: 0건
- 점검 당시 최신 저장 지연: 약 1.6초
- 실제 DB WebSocket 09:00:00~09:00:01: 212건, mismatch 0건
- 기대/수신 envelope SHA-256: `04342246e23b0c4ee85423df47706be705469fdfb8e8ba1e958a1833e135d92e`
- WebSocket `replay.completed.eventCount`: 212
- 같은 1.003494초 기록 구간의 1배속 재생: 1.015초, 유효 0.989배
- 같은 구간의 20배속 재생: 0.062초, 유효 약 16.2배
- 같은 구간의 무지연 재생: 0.046초
- 세 모드 모두 212건이며 기대/수신 envelope hash가 동일
- 장전 reference: 테마 상세 280개, theme index 1개, KOSPI master 2,153개,
  KOSDAQ master 1,769개 숫자 종목코드
- reference event 283건 canonical snapshot SHA-256:
  `a951c429a1eb723e4f2d8e65828a35590fea83ff29aa1d8c7d1408b497bc9906`
- 동결 DB와 현재 장전 파일 canonical 내용 동일 확인
- 테마 상세 2개(`373`, `586`)의 embedded `contentHash`는 확장 편집 후 갱신되지
  않은 stale metadata로 확인. 동결 원문과 event/reference snapshot hash는 정상이며
  최종 audit에는 경고로 보존한다.
- 09:40 연속성 중간 감사: REST 네 API 각각 82회, API별 최대 gap 30.762초;
  KOSPI·KOSDAQ 지수/시장폭별 최대 gap 14.990초; 전체 체결 stream 최대 gap
  6.251초
- sequence 968,500까지 `received_at` 역행 0건
- 장후 백필 사전 실API 검증: `ka10080` 삼성전자 조회 return code 0,
  09:00~09:43 오늘 1분봉 44행과 예상 OHLCV 필드 확인. 응답 첫 페이지 900행
  안에 오늘 정규장 최대 약 391행이 모두 포함되는 구조 확인
- 키움 공식 0B 계약 대조: 실제 체결 200건 표본에서 체결시간, 현재가,
  전일대비·등락률, 최우선 매도/매수, 체결량, 누적 거래량·대금, 시가·고가·저가,
  체결강도, 순간 거래대금, 시가총액의 필수 FID 15개 모두 확인
- 후보 발견 canonical 변환: REST `ka10019/23/27/32` 모두 후보 event 생성,
  중간 점검 고유 정상 종목 1,837개. 조건 변화는 선택 조건 중 실제 신호가 발생한
  5개에서 편입 1,561건·이탈 1,459건, 정상 종목 82개 확인
- 구독 이유 provenance 재구성: 구독 결정 1,532회·대상 인스턴스 275,760개를
  시점별 후보 TTL과 동결 membership으로 재생. 직접 후보 221,830개, 테마 확장
  53,930개, 설명 불가능한 대상 0개
- 시간 계약 균등 표본 1,068건: 0B FID `20`과 `occurred_at` 불일치 0건,
  거래일 오류 0건. 키움 공급원 시계는 수신 시계 대비 표본 -0.602~+4.874초,
  P95 +0.445초이며 1초를 넘는 역방향 clock skew는 0건. 작은 음수 편차는
  공급원/로컬 시계 차이로 보존하고 최종 보고서에 경고 통계로 기록

위 수치는 장중 중간 증거이며 최종 건수로 사용하지 않는다.

### 3.1 `ka10095` 보조 수집과 알려진 한계

- 시작: 2026-08-14 10:09:17 KST, 별도 run `market-2026-08-14-e6468ab3fc8b`
- 저장 위치: `data/market-replay-supplemental/2026-08-14/`
- 본 수집 DB 접근: `PRAGMA query_only=ON`; 본 DB에는 쓰지 않음
- 선택 계약: 최근 30분 활성 후보가 속한 동결 테마의 전체 연관 종목에서 현재 0B 구독 180종목을 제외
- 10:14 중간 점검: 활성 테마 280개, 연관 종목 2,358개, 요청 2,182개, 응답 2,181개, 22 batch, 실패 0
- 반복되는 응답 1종목 차이는 인포스탁 SPAC 테마에 남아 있지만 당일 KOSPI·KOSDAQ master에는 없는 `455910`(에스케이증권제9호스팩)이다. 공급원 오류가 아니라 stale membership이며, 재개용 sidecar 코드는 당일 master 종목으로 교집합하도록 보강했다. 이미 실행 중인 프로세스의 원본 요청·무응답 사실은 그대로 보존한다.
- 주기: 30초, 최근 60초 snapshot 4,342건, `source.error` 0건
- 통합 WebSocket 실증: 10:10:18 1초의 본 체결 233개와 보조 snapshot 400개, 합계 633개 event, mismatch 0, 기대/수신 SHA-256 `3363c7d3c61ea9d1e5f908f4c1d79fddd4be1112ee235f22c9429942be7adca9`
- 09:00:00~10:09:17에는 이 보조 API가 가동되지 않았다. 해당 구간의 본 0B·후보·지수·breadth는 존재하지만, 0B 밖 종목의 당시 `ka10095` 상태는 정확 복구할 수 없다. 장후 1분봉은 사례 분석용 근사 보완일 뿐 정확한 실시간 snapshot 대체물이 아니다.

### 3.2 `ka10084` 공백 복구 실증과 한계

- 키움증권 공식 `ka10084`는 `tm=1009`, `tic_min=1` 조회 한 번으로 09:00~10:09의 분별 현재가·등락률·최우선 매도/매수·분 체결량·누적 거래량/대금·체결강도를 제공한다.
- 10:38 실계정 smoke: `000020` 한 종목에서 공백 구간 63개 state 저장, API 실패 0, run `COMPLETED`, verify/prove/audit-recovery 모두 PASS.
- 본 DB의 후보 TTL·테마 membership·시점별 0B 구독 결정을 분마다 재생한 결과, 70분 동안 실제 sidecar 대상이었을 union은 2,278종목, 종목-분 instance는 149,847개다. 장중 수집과 API 한도를 다투지 않도록 장후 전수 실행한다.
- 복구 DB는 원본 `ka10084` 응답과 canonical `market.minute_state.recovered`를 함께 보존한다. archive의 `received_at`은 장후 실제 수집 시각이고, 통합 replay에서는 `occurred_at`을 가상 수신 시각으로 사용한다.
- 이 복구는 60초 완료 상태다. `ka10095`의 30초 중간값과 체결 없이 바뀐 호가는 복구하지 못하므로 exact full-session 판정은 계속 FAIL이다.
- 공식 계약 근거: [키움증권 REST API 공식 저장소](https://github.com/Kiwoom-Securities/Kiwoom-REST-API), [공식 Postman collection](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/postman/kiwoom-openapi.postman_collection.json).

## 4. 장 종료 후 실행할 증명 절차

**미실행.** 수집이 10:39에 취소돼 아래 절차는 하나도 돌지 않았다. 지금 돌려도
`finalize`·`audit`은 장 마감 범위와 1분봉을 전제하므로 통과하지 않는다. 절차
자체는 다음 정규장 재수집 때 그대로 쓴다.

```powershell
./scripts/check_market_capture.ps1 -TradeDate 2026-08-14
python -m pytest -q
python scripts/finalize_market_replay.py `
  --output-dir data/market-replay/2026-08-14 `
  --supplemental-output-dir data/market-replay-supplemental/2026-08-14 `
  --recovery-output-dir data/market-replay-gap-recovery/2026-08-14
python scripts/replay_market.py audit-supplement data/market-replay-supplemental/2026-08-14/market-replay.sqlite3
python scripts/replay_market.py audit-recovery data/market-replay-gap-recovery/2026-08-14/market-replay.sqlite3
python scripts/replay_market.py prove-combined `
  data/market-replay/2026-08-14/market-replay.sqlite3 `
  --supplemental-database data/market-replay-supplemental/2026-08-14/market-replay.sqlite3 `
  --recovery-database data/market-replay-gap-recovery/2026-08-14/market-replay.sqlite3
python scripts/replay_market.py socket-prove `
  data/market-replay/2026-08-14/market-replay.sqlite3 `
  --supplemental-database data/market-replay-supplemental/2026-08-14/market-replay.sqlite3 `
  --from-time 10:10:18 --to-time 10:10:18 --speed 0 --max-events 10000
```

`audit`는 내부에서 저장 무결성 검사를 포함하므로 전체 데이터의 hash·sequence,
run 완료, 필수 공급원, 시간 범위, REST gap, 분봉 coverage, credential 부재를 한 번에
검사한다. 최종화 명령은 manifest를 현재 파일에서 다시 만든 뒤 `verify`·`audit`·
`prove` 결과를 `validation-report.json` 하나에 보존한다.

실제 DB 서비스 profile 무지연 재생은 화면에 수백만 줄을 남기지 않고 sink로
보내 최종 건수와 종료 코드를 확인한다. `prove`의 `serviceReplay.count`와
`serviceReplay.envelopeSha256`을 최종 기준으로 기록한다.

WebSocket은 실제 DB에서 짧은 시간 구간을 선택해 다음을 확인한다.

1. 수신 sequence가 증가한다.
2. DB의 같은 구간 canonical envelope와 byte-equivalent이다.
3. 마지막에 `replay.completed`와 정확한 event 수가 온다.
4. 동일 구간을 다시 접속해도 같은 순서와 hash가 나온다.

## 5. 최종 증거 기록란

2026-08-15 manifest·SQLite 실측으로 기록란을 확정했다. 취소로 끝났으므로
`PENDING`(진행 중)은 남기지 않고 실측값 또는 `미실행`으로 바꿨다.

- 최종 run 상태/종료 시각: **`INTERRUPTED` / `cancelled`, 2026-08-14 10:39:31 KST**
- 전체 event 수: **1,803,564** (market.trade 1,561,132 · candidate.rest 134,098 · kiwoom.websocket.raw 98,544 · candidate.condition 4,098 · subscription.changed 2,224 · market.breadth 1,190 · market.index 1,190 · kiwoom.rest.raw 796 · reference.infostock_theme 281 · source.status 8 · reference.stock_master 2 · candidate.condition_list 1)
- 수집 종목 수: **2,182** (동시 구독 상한 `maxSubscriptions: 180`)
- 시장 event 시간 범위: **09:00:00.569 ~ 10:39:31 KST** (계획 `endAt` 15:40 미도달)
- 서비스 replay event 수: 미실행
- 전체 DB↔NDJSON envelope SHA-256: 미실행
- 서비스 replay envelope SHA-256: 미실행
- NDJSON 파일 SHA-256: 미실행
- minute bar 종목/행 수 및 시간 범위: **0종목 / 0행** — `backfillMinuteBars: true`였으나 취소로 백필 미실행
- 백필 실패·복구 종목 수: 해당 없음(백필 미실행)
- verify 결과: 미실행
- audit 결과: 미실행 (완전성 기준상 이 데이터셋으로는 통과 불가)
- prove 결과: 미실행
- pytest 결과: 장중 23 passed / 수집 종료 후 재실행 미실행
- 보조 run 상태/이벤트 수: **`INTERRUPTED` / `stopped by user`, 133,965건** (market.snapshot 132,561 · kiwoom.ka10095.raw 1,342 · supplemental.coverage 61 · source.status 1), 종목 2,218, 구간 **10:09:17 ~ 10:39:24 KST**
- 보조 operational audit: 미실행
- 보조 09:00 전체구간 exact coverage: FAIL — 10:09:17 이전 알려진 공백
- `ka10084` 공백 복구: 구현·1종목 실계정 smoke PASS / **장후 전수 미실행** — `data/market-replay-gap-recovery/` 없음
- 복구 후 exact live coverage: FAIL 유지 — 60초 해상도, quote-only 변화 복구 불가
- 본·보조 통합 WebSocket: 장중 PASS — 본 체결 233건+보조 snapshot 400건, 합계 633건, mismatch 0, 기대/수신 hash 일치
- 실제 DB WebSocket 재생 결과: PASS — 212건, mismatch 0, 기대/수신 hash 일치
- 저장 위치·보존: `data/**`는 `.gitignore` 14행으로 git에 없다. 본 5.7GB·보조 790MB·smoke 211MB가 **이 PC에만 존재**하며, 정규장에서만 재생성 가능하므로 별도 백업이 필요하다.
- **최종 판정: 계획된 완료 조건 미달. 09:00~10:39 부분 fixture로 확정하고, 소멸 전이·1분봉·장후 정합은 A-3 실제 장중 실행으로 넘긴다.**
