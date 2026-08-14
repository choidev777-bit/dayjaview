# 2026-08-14 1회성 시장 수집·완전 재연 구현 계획

## 0. 이 작업을 지금 하는 이유

다음 영업일인 2026-08-17이 대체공휴일이어서 이번 주말에 DAYJAVIEW를 구현해도 실제 정규장이 움직이는 상태에서 서비스를 시험할 수 없다. 오늘 2026-08-14이 주말 전 마지막 정규장이다. 따라서 오늘 09:00부터 장 마감까지 DAYJAVIEW가 실시간으로 받았어야 할 시장 입력을 한 번만 원형에 가깝게 저장하고, 구현이 끝난 뒤 서비스의 시계를 2026-08-14로 되돌려 같은 입력 순서와 간격으로 공급해야 한다.

이 수집의 목적은 시세 자료를 일반적인 분석용으로 쌓는 것이 아니다. 저장 데이터에 서비스의 Market Gateway를 연결하여 다음 흐름을 다시 실행하고 검증하기 위한 **결정적 시장 replay fixture**를 만드는 것이다.

```text
2026-08-14 시장 원본 입력
→ 후보 종목 발견
→ 정밀 체결 구독
→ 테마 집계·Coverage
→ CANDIDATE/ACTIVE/WEAKENING/CLOSED
→ 순위·급부상·주도주
→ 오늘/인사이트/테마 상세 화면
→ 장중 상태 및 장애 처리 검증
```

수집은 오늘 하루 한 번만 수행한다. 상시 운영 수집기나 주문 시스템을 만드는 작업이 아니며 주문 API는 구현하거나 호출하지 않는다.

### 0.1 현재 실행 상태

- 구현: 완료
- live doctor: 운영 OAuth, WebSocket LOGIN, 조건식 목록, KOSPI master 조회 성공
- 운영 smoke capture: 20초 동안 597 event 기록, sequence·hash·timestamp 검증 통과
- 본 수집: 2026-08-14 09:00부터 background process가 장중 데이터를 수집 중
- 보조 수집: 10:09:17부터 활성 테마 연관 종목 중 0B 비구독 종목을 `ka10095`로 30초마다 별도 DB에 수집 중
- 공백 복구: 장 종료 후 공식 read-only `ka10084`로 09:00~10:09의 테마 연관 종목별 1분 market state를 별도 DB에 복구 예정
- 본 수집 종료: 15:40 이후 전 종목 1분봉 백필과 최종 검증이 끝나야 완료

2026-08-14 09:03 실데이터 확인 결과:

- 첫 체결 저장: 09:00:00.863 KST
- 첫 REST 후보 저장: 09:00:00.569 KST
- 첫 조건검색 변화 저장: 09:00:06.508 KST
- 첫 KOSPI·KOSDAQ 지수/breadth 저장: 09:00:29.702 KST
- 저장 처리 지연: 점검 당시 1초 미만
- 공급원 오류: 0건

따라서 위의 본 수집 상태는 현재 `장중 수집 중`이다.

상태 확인은 `scripts/check_market_capture.ps1`을 사용한다. 문서의 구현 완료와 오늘 데이터셋의 수집 완료를 혼동하지 않는다.

## 1. 성공의 정의

다음 조건을 모두 만족해야 이 작업이 완료된 것이다.

1. 원본 이벤트가 발생 순서, 공급원 시각, 수신 시각, 종목, 원본 payload와 함께 append-only DB에 남는다.
2. 동일 DB를 replay하면 이벤트 순서가 바뀌지 않고, 고정 clock 및 1배속·가속·무지연 모드로 재생할 수 있다.
3. 서비스가 표준 JSON event envelope를 WebSocket 또는 NDJSON으로 소비할 수 있다.
4. 조건검색·REST 안전망·0B 체결·0J 시장지수·0U 시장 breadth·구독 변경·연결 장애를 서로 구분할 수 있다.
5. 장후 전 종목 1분봉을 백필해 실시간 후보군 밖의 종목과 신규 테마 누락을 사후 조사할 수 있다.
6. DB 무결성 검사에서 sequence gap, payload hash 불일치, 잘못된 거래일, 미완료 실행을 발견할 수 있다.
7. credential이나 access token이 DB·JSONL·로그에 기록되지 않는다.
8. 0B 구독 대상, `ka10095` 보조 스냅샷, `ka10084` 공백 복구 state를 replay 시각 순으로 합쳐 단일 단조 증가 sequence의 서비스 입력으로 재생할 수 있다.

`완전 재연`은 키움에서 실제로 수신한 입력과 당시 수집기가 내린 구독 결정을 손실 없이 다시 공급한다는 뜻이다. 키움 한 세션의 200종목 제한 때문에 시장 전체 체결 틱을 뜻하지 않는다. 시장 전체는 장후 1분봉으로 보완한다.

다만 구현 도중 `ka10095`가 활성 테마의 0B 비구독 종목을 보완하는 데 필요하다는 점을 10:09에 확인했으므로 이 보조 입력은 09:00부터 10:09:17까지 존재하지 않는다. 공식 `ka10084`는 조회 시각을 지정해 체결가·등락률·최우선 호가·체결량·누적 거래량/대금·체결강도를 1분 단위로 돌려주므로 장후 이 구간을 실시간 입력보다 강한 근거로 복구한다. 그러나 30초 중간 상태와 체결 없이 바뀐 호가는 되살릴 수 없다. 따라서 최종 감사에서 `knownGapBeforeStart=true`, `recoveryResolutionSeconds=60`, `supplemental_exact_full_session_coverage=FAIL`을 숨김없이 남긴다. 본 수집의 0B·후보·지수·breadth 입력은 09:00부터 정상 보존돼 있다.

`ka10084` 계약은 키움증권 공식 REST API 저장소의 [공식 API 자료](https://github.com/Kiwoom-Securities/Kiwoom-REST-API)와 [공식 Postman collection](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/postman/kiwoom-openapi.postman_collection.json)을 기준으로 확인했다.

## 2. 이번 수집의 범위

### 2.1 장전 동결 자료

- 인포스탁 테마 ID·명칭·관련 종목·편입 이유·히스토리의 당시 파일과 hash
- KOSPI·KOSDAQ 종목 master
  - 종목코드, 종목명, 시장, 업종, 상장주식수, 전일종가, 종목 상태, 회사 분류
- 수집 설정
  - 거래일, 시작·종료 시각, poll 주기, 구독 한도, 조건식 목록
- source·schema·collector version

### 2.2 장중 실시간 자료

| 입력 | 획득 방식 | 보존 내용 |
|---|---|---|
| 조건식 목록 | `CNSRLST` | 계정에 존재하는 조건식과 선택 여부 |
| 조건검색 | `ka10173/CNSRREQ` | 최초 결과와 편입·이탈 원본 |
| 가격급등락 | `ka10019` | 30초 안전망 응답 전체 |
| 거래량급증 | `ka10023` | 30초 안전망 응답 전체 |
| 등락률상위 | `ka10027` | 30초 안전망 응답 전체 |
| 거래대금상위 | `ka10032` | 30초 안전망 응답 전체 |
| 선택 종목 체결 | `0B` | 현재가, 등락률, 체결량, 누적 거래량·대금, OHLC, 최우선 호가, 체결강도, 시총 등 원본 field map |
| 활성 테마 비구독 종목 시세 | `ka10095` | 0B 180종목 한도 밖의 활성 테마 연관 종목에 대해 현재가, 등락률, 누적 거래량·대금, OHLC, 체결강도 등을 30초 스냅샷으로 별도 저장 |
| 보조 수집 시작 전 공백 복구 | `ka10084` | 테마 membership과 당일 master의 교집합 전체에 대해 09:00~10:09의 1분별 현재가, 등락률, 최우선 호가, 체결량, 누적 거래량·대금, 체결강도를 장후 별도 저장 |
| 시장지수 | `0J` | KOSPI `001`, KOSDAQ `101` 실시간 원본 |
| 시장 breadth | `0U` | 상승·보합·하락 종목 수와 시장 누적 거래량·대금 |
| 구독 결정 | 내부 event | 대상 종목 전체, 추가·해제, 이유, slot 수 |
| 운영 상태 | 내부 event | 연결·로그인·PING·재연결·API 오류·지연 |

조건검색 결과와 네 REST 순위의 합집합을 후보로 사용한다. 최근 후보를 우선하고, 후보가 속한 인포스탁 테마의 관련 종목으로 0B 대상을 확장하되 180종목을 넘지 않는다. 20 slot은 급격한 후보 유입과 재등록을 위해 남긴다. 조건식이 계정에 없거나 제품용 조건식인지 확인되지 않아도 REST 안전망 수집은 계속한다.

### 2.3 장후 자료

- 최종 run 상태와 종료 시각
- KOSPI·KOSDAQ 종목별 2026-08-14 1분 OHLCV (`ka10080`)
- 분봉 백필 성공·실패 종목과 오류
- 최종 manifest와 무결성 검사 결과

분봉 백필은 전 종목 틱 대체물이 아니다. 후보를 놓친 원인을 찾고 테마 탐지 임계값을 분 단위로 사후 비교하기 위한 보조 자료다.

### 2.4 의도적으로 제외

- 주문·계좌·잔고 API
- 전체 호가잔량
- 뉴스 원문·뉴스 매칭
- 인포스탁 장후 브라우저 수집 자동화
- 상시 scheduler와 production 배포

뉴스가 없으므로 replay에서 상승 이유 기능은 `SEARCHING/상승 이유 확인 중`과 뉴스 공급원 장애 처리를 검증한다. 실제 기사 매칭·요약 품질은 별도 fixture가 필요하다.

## 3. 오늘 한 번의 수집만으로 해결되지 않는 입력

다음 값은 오늘 장중 자료만으로 새로 만들 수 없다.

- 최근 20거래일 동일 시각 누적 거래대금 중앙값
- 관심 공백용 최근 60거래일 동일 시각 누적 거래대금
- 승인된 유동주식비율
- 검증된 Core/Related 역할
- 과거 사례 T+1·T+5·T+20 outcome

수집 DB는 이 값들을 있는 척 채우지 않는다. 기존 승인 데이터가 없으면 replay 시 `PROVISIONAL_BASELINE` 또는 Coverage/기준정보 부족 상태로 처리해야 한다. 오늘 사건을 새로운 과거 사례로 사용할 때는 이후 T+1·T+5·T+20 조정종가를 별도 추가한다.

## 4. 저장 계약

기본 산출물은 `data/market-replay/2026-08-14/` 아래에 둔다.

```text
market-replay.sqlite3     질의·검증·replay 기준 DB
events.ndjson             사람이 복구 가능한 append-only 원본 사본
manifest.json             설정·건수·hash·완료 상태
collector.log             credential을 제외한 운영 로그
```

SQLite는 WAL 모드와 batch insert를 사용한다. 이 하루짜리 독립 fixture는 로컬 PostgreSQL 실행 여부에 의존하지 않아야 한다. 이후 운영 DB로 import할 수 있도록 event envelope를 고정한다.

### 4.1 공통 event envelope

```text
sequence                  수집 run 안에서 단조 증가
run_id
event_type
source
occurred_at               공급원 시각, 알 수 없으면 received_at
received_at               수집기가 받은 UTC 시각
stock_code                해당할 때만
source_sequence           공급원이 줄 때만
payload                   원본 JSON
payload_sha256
schema_version
```

sequence와 `received_at`이 replay의 기준이다. 동일 수신 시각에서는 sequence가 순서를 결정한다.

### 4.2 별도 테이블

- `collection_runs`: 실행 설정, 시작·종료·상태, 오류
- `events`: 모든 장전·장중 원본과 내부 결정
- `minute_bars`: 장후 전 종목 1분 OHLCV
- `integrity_checks`: 검사명, 통과 여부, 상세

## 5. 구현 순서

### Phase 1 — 저장·인증 기반

1. `.env.local`을 process 환경변수보다 낮은 우선순위로 읽는다.
2. 키움 OAuth token을 메모리에서만 발급한다.
3. SQLite schema와 NDJSON 이중 기록 writer를 만든다.
4. payload canonical hash와 manifest writer를 만든다.

### Phase 2 — 장전 snapshot

1. 인포스탁 import 파일을 DB event로 동결한다.
2. `ka10099`로 KOSPI·KOSDAQ master를 저장한다.
3. 조건식 목록과 실제 적용 조건식을 저장한다.
4. 09:00 전에는 시장 event 처리 준비 상태로 대기한다.

### Phase 3 — 장중 수집

1. WebSocket LOGIN과 PING echo를 유지한다.
2. `0J/0U`를 등록한다.
3. 조건식 최대 8개를 실시간 요청한다.
4. 09:00부터 네 REST 안전망을 분산 polling한다.
5. 후보 합집합과 인포스탁 membership으로 최대 180개의 `0B` 구독 집합을 갱신한다.
   - 조건 이벤트 burst는 최소 1초 단위로 묶고, 선택 종목 집합이 같으면 재등록하지 않는다.
6. 모든 응답과 구독 결정을 먼저 저장하고 파생 처리를 수행한다.
7. 연결이 끊기면 backoff 후 로그인·조건검색·지수·현재 0B 집합을 재등록한다.
8. 별도 sidecar가 본 DB를 read-only로 따라가며 활성 후보→테마 membership을 계산하고, 현재 0B 구독 집합 밖의 종목만 `ka10095` 100종목 batch로 조회한다. sidecar는 본 DB의 sequence 공간과 쓰기 잠금을 공유하지 않는다.

### Phase 4 — 장후 전수 보완

1. 15:40 이후 실시간 수집을 정상 종료한다.
2. 종목 master의 KOSPI·KOSDAQ 전 종목에 `ka10080` 1분봉을 호출한다.
3. 대상 거래일 행만 `minute_bars`에 idempotent하게 저장한다.
4. 실패 종목은 기록하고 재실행 시 미완료 종목만 이어받는다.
5. `ka10084`로 테마 연관 master 종목의 09:00~10:09 one-minute state를 별도 gap-recovery DB에 멱등 저장한다.
6. gap-recovery event의 `received_at`은 실제 장후 수집 시각, `occurred_at`과 `payload.replayAt`은 해당 과거 1분의 종료 시각으로 분리한다.

### Phase 5 — replay와 검증

1. DB 검사 CLI로 hash·sequence·거래일·run 상태를 확인한다.
2. NDJSON 출력 replay를 무지연으로 실행해 event 수와 최종 hash를 비교한다.
3. WebSocket replay server를 실행해 소비자가 같은 envelope를 받는지 확인한다.
4. 1배속·가속·시작/종료 구간·event type 필터를 시험한다.

## 6. 실행 명령

장전 실제 수집:

```powershell
./scripts/run_market_capture.ps1 -TradeDate 2026-08-14 -Mode real
```

2026-08-14 장전 live doctor에서 계정 조건식 70개를 확인했다. 이 실행 wrapper는 그중 오늘 후보 발견과 직접 관련된 기존 조건식 `7 당일상승`, `12 당일주도주`, `19 18%이상`, `25 고가돌파`, `35 13%상승 종목`, `54 장초반`, `56 장중`, `71 1000억 종목`을 명시적으로 선택한다. 조건식 정의 자체는 이번 1회 수집 작업에서 변경하지 않는다.

직접 실행:

```powershell
python scripts/collect_market_replay.py capture `
  --mode real --trade-date 2026-08-14 `
  --start-at 09:00:00 --end-at 15:40:00 `
  --poll-seconds 30 --max-subscriptions 180 `
  --condition-id 7 --condition-id 12 --condition-id 19 `
  --condition-id 25 --condition-id 35 --condition-id 54 `
  --condition-id 56 --condition-id 71 `
  --backfill-minute-bars
```

검사:

```powershell
python scripts/replay_market.py verify data/market-replay/2026-08-14/market-replay.sqlite3
```

전체 수집 completeness audit:

```powershell
python scripts/replay_market.py audit data/market-replay/2026-08-14/market-replay.sqlite3
```

`verify`는 저장 무결성을 확인하고, `audit`는 정규장 시간 범위·필수 공급원·체결·전 종목 분봉·credential 부재까지 확인한다. 최종 완료 판정에는 둘 다 통과해야 한다.

DB·append-only NDJSON·manifest와 서비스용 재생 스트림의 건수 및 연쇄 해시 증명:

```powershell
python scripts/replay_market.py prove data/market-replay/2026-08-14/market-replay.sqlite3
```

`prove`는 전체 이벤트를 메모리에 올리지 않고 순차 처리한다. DB와 NDJSON의
canonical envelope 건수·SHA-256, manifest의 sequence/payload hash와 원본 파일
hash를 비교하고, 서비스에 전달될 canonical 이벤트만의 별도 건수·해시도 남긴다.

수집 프로세스 종료 후 최종 manifest 재생성 및 전체 증거 보고서 작성:

```powershell
python scripts/finalize_market_replay.py `
  --output-dir data/market-replay/2026-08-14 `
  --supplemental-output-dir data/market-replay-supplemental/2026-08-14 `
  --recovery-output-dir data/market-replay-gap-recovery/2026-08-14
```

이 명령은 `COMPLETED` run에만 동작한다. 현재 파일로 manifest를 한 번 재생성한 뒤
`verify`·`audit`·`prove`를 수행하여 `validation-report.json`을 원자적으로 쓴다.
검사 하나라도 실패하면 보고서는 보존하지만 종료 코드는 실패다.

무지연 NDJSON replay:

```powershell
python scripts/replay_market.py emit data/market-replay/2026-08-14/market-replay.sqlite3 --speed 0
```

기본 `service` profile은 서비스가 소비할 canonical runtime event만 방출한다. 수집 원본과 장전 reference까지 포함한 DB 전체를 감사할 때는 `--profile all`을 사용한다.

WebSocket replay:

```powershell
python scripts/replay_market.py serve data/market-replay/2026-08-14/market-replay.sqlite3 --speed 20 --port 8765
```

실제 DB의 제한 구간 WebSocket 왕복 동일성 증명:

```powershell
python scripts/replay_market.py socket-prove `
  data/market-replay/2026-08-14/market-replay.sqlite3 `
  --profile service --from-time 09:00:00 --to-time 09:00:01 `
  --speed 0 --max-events 10000
```

`socket-prove`는 로컬 loopback에서 DB 기대 스트림과 실제 WebSocket 수신 스트림의
순서·건수·canonical envelope SHA-256 및 `replay.completed`를 비교한다.

수집 중 상태 확인:

```powershell
./scripts/check_market_capture.ps1 -TradeDate 2026-08-14
```

활성 테마 비구독 종목 보조 수집 시작:

```powershell
./scripts/run_market_snapshot_supplement.ps1 -TradeDate 2026-08-14 -Mode real
```

본 DB와 보조 DB를 하나의 서비스 WebSocket 입력으로 결합해 검증:

```powershell
python scripts/replay_market.py socket-prove `
  data/market-replay/2026-08-14/market-replay.sqlite3 `
  --supplemental-database data/market-replay-supplemental/2026-08-14/market-replay.sqlite3 `
  --from-time 10:10:18 --to-time 10:10:18 --speed 0 --max-events 10000
```

장 종료 후 보조 수집 전용 감사:

```powershell
python scripts/replay_market.py audit-supplement `
  data/market-replay-supplemental/2026-08-14/market-replay.sqlite3
```

본 수집 종료 후 09:00~10:09 보조 공백을 1분 market state로 복구:

```powershell
./scripts/run_market_gap_recovery.ps1 -TradeDate 2026-08-14 -Mode real
python scripts/replay_market.py audit-recovery `
  data/market-replay-gap-recovery/2026-08-14/market-replay.sqlite3
```

복구 DB까지 포함한 통합 replay에서는 일반 live/sidecar event는 `received_at`, 복구 state는 역사적 `occurred_at`을 replay clock으로 사용한다. 통합 envelope의 sequence는 다시 1부터 단조 증가하며 원 payload와 `payloadSha256`은 바꾸지 않는다.

장후 분봉 백필이 중단되거나 일부 종목이 실패했을 때의 복구:

```powershell
python scripts/repair_market_backfill.py `
  --output-dir data/market-replay/2026-08-14 `
  --mode real
```

복구 명령은 stock master와 `backfill.minute.completed` 이벤트를 비교해 미완료
종목만 다시 조회한다. 실시간 수집 run이 아직 `RUNNING`이면 실행을 거부한다.
확인된 비정상 종료로 DB 상태만 `RUNNING`에 남은 경우에만
`--allow-stale-running`을 명시한다. 일부 종목이 계속 실패하면 run을
`COMPLETED`로 표시하지 않으며, 복구 후에도 `verify`와 `audit`를 다시 통과해야
최종 성공이다.

## 7. 중단·복구 원칙

- 같은 거래일 DB가 있으면 새 run을 추가하되 기존 event를 삭제하지 않는다.
- 프로세스 비정상 종료는 `RUNNING` 상태로 남아 검사에서 실패한다.
- 재실행은 새 `run_id`로 시작한다. 여러 run은 수신 시각과 sequence로 구분한다.
- queue 적체·DB 쓰기 실패는 조용히 drop하지 않고 수집을 실패시킨다.
- REST 한도 오류는 전체 수집을 즉시 종료하지 않고 원본 오류 event와 backoff를 기록한다.
- WebSocket 재연결 동안 REST 안전망은 계속 동작한다.
- 분봉 API 실패가 한 종목이라도 남으면 수집 run을 성공 처리하지 않는다.
- 장후 백필 실패는 `repair_market_backfill.py`로 미완료 종목만 멱등 재시도한다.

## 8. 완료 증거

- 단위 테스트: envelope hash, 거래시각 변환, 후보 추출, 구독 선정, DB idempotency
- fixture 통합 테스트: 가짜 REST·WebSocket 입력을 수집한 뒤 replay 결과가 byte-equivalent payload인지 확인
- live doctor: OAuth, master 조회, WebSocket LOGIN, 조건식 목록 조회 성공
- 장후 manifest: run `COMPLETED`, event 종류별 건수, 구독 종목 수, 분봉 성공/실패 수
- verify 결과: sequence/hash/trade-date/run-completion 전부 통과
- prove 결과: DB·NDJSON·manifest·서비스 replay stream 건수/hash 전부 일치

실제 장이 끝나기 전에는 오늘 전체 데이터 수집 완료를 주장할 수 없다. 구현 완료와 오늘 fixture 수집 완료는 별도 상태로 보고한다.
