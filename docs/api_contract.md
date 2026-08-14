# DAYJAVIEW API 의미 계약

- 문서 버전: `0.2-draft`
- 문서 상태: 기계 계약·로컬 검증 완료 — 프론트·백엔드 공동 승인 필요
- 최종 수정일: 2026-08-14
- 제품 기준: [PRD.md](./PRD.md)
- 시스템 기준: [system_architecture.md](./system_architecture.md)
- 화면 기준: [screen_spec.md](./screen_spec.md)
- UI 적용 계획: [ui_prototype_adaptation_plan.md](./ui_prototype_adaptation_plan.md)
- 실시간 기능 세부사항: [realtime_theme_feature_spec.md](./realtime_theme_feature_spec.md)
- 구현 로드맵: [implementation_roadmap.md](./implementation_roadmap.md)

---

## 0. 목적·범위·권한

이 문서는 DAYJAVIEW 프론트엔드와 백엔드가 공유하는 **의미 계약**을 정의한다. endpoint 이름만 나열하지 않고 식별자, 시간, 수치, 결측, 상태, 오류, 실시간 재연결의 뜻을 고정한다.

대상:

- 핵심 MVP의 read API
- 오늘·인사이트·테마 상세의 실시간 snapshot
- 온톨로지 재검증 후 열리는 유사사례 read API
- 프론트 fixture와 backend contract test의 공통 의미
- OpenAPI·AsyncAPI·JSON Schema와 공통 fixture의 기계 계약

비대상:

- 공급원별 수집 API
- 키움·인포스탁·뉴스 adapter 내부 계약
- 운영자 console의 runtime 구현
- 인증 제품 요구사항 전체
- v2 방향 예측 API
- backend·frontend runtime 구현

### 0.1 내부 토폴로지와 독립

이 계약은 모듈형 모놀리스, 역할별 독립 배포, 마이크로서비스 중 어떤 내부 토폴로지를 선택해도 유지돼야 한다. 프론트는 내부 service 이름, DB schema, queue, worker 배치를 알지 않는다.

`ADR-001`에서 service 분할 방식이 바뀌어도 다음은 바뀌지 않는다.

- public identifier의 뜻
- 상태 축의 분리
- null·0·빈 배열 의미
- REST resource와 WebSocket topic 의미
- 오류·재연결·호환성 규칙

### 0.2 문서 우선순위

충돌 시 다음 순서를 따른다.

1. [PRD.md](./PRD.md)의 제품 범위·금지사항·출시 게이트
2. [system_architecture.md](./system_architecture.md)의 상태 소유권·데이터 경계
3. [screen_spec.md](./screen_spec.md)의 사용자 표현·화면 상태
4. 이 문서의 API 의미
5. 승인된 OpenAPI·AsyncAPI·JSON Schema의 필드·형식
6. 문서와 fixture의 예시 값

기계 schema가 이 문서의 의미를 바꿔서는 안 된다. 의미 변경이 필요하면 이 문서와 schema를 같은 변경에서 갱신한다.

### 0.3 확정 수준

| 표시 | 의미 |
|---|---|
| 기준 | 구현이 따라야 할 의미 규칙 |
| 후보 | OpenAPI·AsyncAPI 검토에서 이름·경로 조정 가능 |
| 미결정 | 소유자·결정 시점이 필요한 항목 |

이 문서의 endpoint 경로는 `후보`, 식별자·상태 분리·수치 의미는 `기준`이다.

---

## 1. 공통 wire 규칙

### 1.1 기본 형식

| 항목 | 규칙 |
|---|---|
| protocol | HTTPS와 WSS만 사용 |
| REST base | `/v1` 후보 |
| body | UTF-8 JSON |
| field name | `camelCase` |
| enum | 대문자 `SNAKE_CASE` |
| timestamp | RFC 3339·ISO 8601 absolute timestamp |
| date | `YYYY-MM-DD` |
| content type | `application/json` |
| identifier | opaque string, client parsing 금지 |
| percent·ratio | JSON number, `%` 문자열 금지 |

응답에 `NaN`, `Infinity`, `-Infinity`, locale-formatted number, 쉼표가 포함된 숫자 문자열을 보내지 않는다.

### 1.2 요청 header

| header | 필수 | 의미 |
|---|---|---|
| `Accept: application/json` | REST | 지원 형식 명시 |
| Secure·HttpOnly session cookie | 제품 REST | Vercel app origin의 host-only cookie; `/api/*` rewrite가 OCI로 전달 |
| `Origin` | 상태 변경·WebSocket | 허용된 app origin `https://dayjaview.vercel.app` 검증 |
| `X-Request-Id` | 선택 | client correlation ID; 없으면 server 발급 |
| `If-None-Match` | 선택 | cache 가능한 확정 데이터 재검증 |

공급원 credential과 내부 API key를 browser에 전달하지 않는다.

### 1.3 공통 성공 envelope

모든 REST 성공 응답은 `data`와 `meta`를 가진다.

```json
{
  "data": {},
  "meta": {
    "requestId": "req_01K2B7M3M6R4J6K4J2Y7X9Q8ZW",
    "apiVersion": "1",
    "schemaVersion": "2026-08-14.1",
    "generatedAt": "2026-08-14T01:18:23.042Z"
  }
}
```

시장 read model은 `meta.marketContext`를 추가한다.

```json
{
  "marketContext": {
    "market": "KRX",
    "timeZone": "Asia/Seoul",
    "marketDate": "2026-08-14",
    "asOf": "2026-08-14T01:18:22.410Z",
    "dataStatus": "LIVE",
    "lastHealthyAt": "2026-08-14T01:18:22.410Z",
    "qualityFlags": []
  }
}
```

규칙:

- `generatedAt`: API response를 만든 시각
- `asOf`: response 계산에 포함된 가장 최신 시장 관측 기준 시각
- `generatedAt`이 새롭다고 `asOf`도 최신인 것은 아님
- `lastHealthyAt`: 마지막으로 정상 품질을 충족한 시각; `LIVE`에서는 `asOf`와 같을 수 있음
- `qualityFlags`: response 전체에 적용되는 품질 경고

### 1.4 version metadata

계산 결과에는 적용 가능한 version만 보낸다.

```json
{
  "versions": {
    "calculationVersion": "theme-metrics-2026.08.1",
    "rankingModelVersion": "theme-rank-2026.08.1",
    "membershipVersion": "membership-2026-08-14T00:10:00Z",
    "tradingCalendarVersion": "krx-calendar-2026.08.1",
    "ontologyVersion": null,
    "retrievalArtifactVersion": null
  }
}
```

`null`은 해당 결과에서 version이 필요하지만 아직 적용되지 않았거나 기능이 잠겼음을 schema가 허용한 경우에만 쓴다. 적용 자체가 없는 endpoint에서는 필드를 생략한다.

---

## 2. 식별자 계약

### 2.1 식별자 정의

| 필드 | 의미 | 수명주기 | client 규칙 |
|---|---|---|---|
| `stockId` | canonical 상장 종목 | 상장 종목 identity 동안 안정 | 종목코드로 대체 금지 |
| `symbol` | 시장 표시 종목코드 | 변경 가능 | 표시·검색 보조 |
| `themeId` | canonical theme 개념 | theme revision과 독립 | 표시명 parsing 금지 |
| `eventId` | 한 거래일의 한 촉매·움직임 수명주기 | 생성 후 불변 | 현재 사건 context |
| `matchedEventId` | 검색 결과로 선택된 과거 Event의 `eventId` | 원 Event와 동일 | 별도 새 Event를 뜻하지 않음 |
| `newsId` | 정규화된 뉴스 항목 | 생성 후 불변 | URL을 ID로 쓰지 않음 |
| `classificationVersion` | 한 Event의 분류 revision 번호 | 변경마다 단조 증가 | 최신값 덮어쓰기 추정 금지 |
| `snapshotId` | 한 read model snapshot | snapshot마다 새 값 | cache·관측 추적용 |

`matchedEventId`는 별도 ID namespace가 아니다. 현재 사건과 과거 사건의 역할을 코드에서 구분하기 위한 field name이다.

### 2.2 형식

ID는 정렬 가능 문자열을 권장하지만 형식은 server 구현 세부사항이다.

```text
thm_01K2B5Y7J6D8G2M4R7T9V1X3Z5
evt_01K2B62FQ4D1P8E9N7A3C5M6RT
stk_01JZZY9SQ0VQAF2PJ8Y0VMSV40
news_01K2B6D0S3H7N4X8F9K1W5Q2PA
```

client는 prefix·시간·길이를 parsing하거나 정렬 기준으로 사용하지 않는다.

### 2.3 관계

```text
themeId 1 ── N eventId
eventId 1 ── N classificationVersion
eventId N ── N newsId
current eventId 1 ── N matchedEventId
matchedEventId = 과거 Event의 eventId
```

### 2.4 장후 분류 변경

- `eventId`는 유지한다.
- `classificationVersion`을 증가시킨다.
- `themeId`와 `displayName`이 바뀔 수 있다.
- 이전 분류와 변경 근거를 history로 보존한다.
- response는 현재 canonical route 정보를 제공할 수 있어야 한다.

path의 `themeId`가 해당 `eventId`의 현재 또는 인정된 과거 분류와 무관하면 `RESOURCE_ID_MISMATCH` 오류를 반환한다. canonical ID를 표시 문자열로 추측하지 않는다.

---

## 3. 시간·거래일 계약

### 3.1 저장·전송·표시

| 계층 | 기준 |
|---|---|
| 영속 timestamp | UTC `timestamptz` |
| API absolute timestamp | UTC `Z` 형식으로 정규화 |
| 시장 날짜 | KRX·`Asia/Seoul` 기준 `marketDate` |
| 사용자 표시 | KST로 변환 |
| T+N | KRX 거래일 calendar 기준 |

예:

```text
API:     2026-08-14T01:18:22.410Z
화면:    2026.08.14 10:18:22 KST
거래일:  2026-08-14
```

client가 absolute timestamp에서 `marketDate`를 재계산하지 않는다. 조기 종료·휴장·calendar revision은 server가 처리한다.

### 3.2 주요 시간 필드

| 필드 | 의미 |
|---|---|
| `generatedAt` | response·message 생성 시각 |
| `asOf` | 계산에 포함된 최신 시장 관측 기준 시각 |
| `lastHealthyAt` | 정상 품질을 마지막으로 충족한 시각 |
| `publishedAt` | 공급원이 표시한 원문 발행 시각을 정규화한 값 |
| `receivedAt` | DAYJAVIEW가 원문·event를 수신한 시각 |
| `occurredAt` | 시장·상태 사건이 실제 발생한 시각 |
| `decisionAt` | 검색·분류·모델 판단 cutoff |
| `changedAt` | revision·상태 변경 시각 |
| `marketDate` | KRX 거래일 |

`publishedAt`이 불명확하면 추측값을 정상 시각처럼 보내지 않는다. `null`과 `PUBLISHED_TIME_UNKNOWN` 품질 flag를 함께 사용한다.

### 3.3 순서 규칙

- 정상 뉴스: `publishedAt <= receivedAt`
- 현재 판단 입력: `input.asOf <= decisionAt`
- 과거 검색 입력: `news.publishedAt <= decisionAt`
- response: `asOf <= generatedAt`
- 미래 시각 또는 순서 위반은 ingest 검증에서 격리

clock skew 허용 범위는 운영 설정이며 공개 계약에 임의 숫자로 고정하지 않는다.

---

## 4. 숫자·단위·결측 계약

### 4.1 단위

| 값 | wire 예시 | 화면 예시 |
|---|---:|---|
| 수익률 | `0.027` | `+2.7%` |
| 하락률 | `-0.008` | `−0.8%` |
| 비율 | `0.81` | `81%` |
| 거래 관심 배수 | `2.4` | `평소의 2.4배` |
| 일수 | `163` | `163거래일` |
| 금액 | `1250000000` | locale에 맞춰 표시 |
| count | `17` | `17종목`, context와 함께 표시 |

수익률은 퍼센트 point가 아니라 decimal return이다. 프론트 formatting 외에 재계산하지 않는다.

### 4.2 정밀도와 반올림

- server는 계산 원값 또는 승인된 저장 정밀도를 보낸다.
- 화면은 [screen_spec.md](./screen_spec.md)의 자릿수로 반올림한다.
- 합계·정렬은 화면 반올림값이 아니라 server 결과를 사용한다.
- money가 JavaScript safe integer를 넘을 수 있으면 OpenAPI에서 decimal string 또는 minor-unit string 정책을 별도 확정한다.

### 4.3 `null`, `0`, 빈 배열, 누락

| 표현 | 의미 |
|---|---|
| `0` | 실제 관측·계산 결과가 0 |
| `null` | field는 적용되지만 현재 계산·관측 불가 |
| `[]` | 조회 성공, 조건에 맞는 항목 0개 |
| field 누락 | endpoint projection에 적용되지 않거나 optional forward field |
| HTTP 오류 | 요청 자체를 신뢰 가능한 결과로 완료하지 못함 |

금지:

- 계산 불가를 `0`으로 변환
- source 장애를 `[]`로 변환
- 권한 없는 field를 `null`로 노출해 존재 여부 유출
- unknown enum을 기존 enum으로 강제 매핑

nullable metric은 가능한 경우 이유를 같이 제공한다.

```json
{
  "medianReturn": null,
  "unavailableReason": "INSUFFICIENT_OBSERVATIONS"
}
```

---

## 5. 공통 상태 계약

### 5.1 `dataStatus`

read model의 freshness·시장 데이터 상태다.

| 값 | 의미 | client 처리 |
|---|---|---|
| `PREOPEN` | 장 시작 전 준비·전일값 | 실시간으로 표시 금지 |
| `LIVE` | 정상 freshness 기준 충족 | 정상 갱신 |
| `DELAYED` | 전체 핵심 feed 지연 | 마지막 정상값·정상 시각 유지 |
| `DEGRADED` | 일부 feed·Coverage 저하 | 영향 범위와 품질 표시 |
| `CLOSED` | 해당 거래일 장 마감 | 최종값·장후 정합 상태 표시 |

### 5.2 Event 상태 축

한 enum에 생명주기·장후 정합·근거 수준을 섞지 않는다.

#### `lifecycleStatus`

```text
CANDIDATE
ACTIVE
WEAKENING
CLOSED
DISCARDED
```

공개 기본 목록은 `ACTIVE`, `WEAKENING`을 사용한다. `CANDIDATE`, `DISCARDED`는 운영·평가 projection 외에는 노출하지 않는다.

#### `reconciliationStatus`

```text
PENDING
MATCHED
UNMATCHED
```

`UNMATCHED`에서는 과거 통계 자동 연결을 금지한다.

#### `reviewStatus`

```text
null
PENDING
RESOLVED
```

`null`은 검수 작업이 생성되지 않았음을 뜻한다. `reviewStatus`와 검수 사유·담당자·내부 메모는 operator projection에서만 제공하며 일반 사용자 API에는 포함하지 않는다.

#### `evidenceStatus`

```text
SEARCHING
SINGLE_SOURCE
MULTI_SOURCE_CONFIRMED
NO_NEW_CATALYST
REEMERGENCE
AFTER_CLOSE_CONFIRMED
```

`AFTER_CLOSE_CONFIRMED`는 `lifecycleStatus`가 아니다.

#### 전이 규칙

상태 전이는 server의 소유 모듈만 수행한다. client는 표시·구독만 한다.

```text
lifecycleStatus
CANDIDATE  -> ACTIVE | DISCARDED
ACTIVE     -> WEAKENING | CLOSED
WEAKENING  -> ACTIVE | CLOSED
CLOSED     -> terminal
DISCARDED  -> terminal

reconciliationStatus
PENDING    -> MATCHED | UNMATCHED
UNMATCHED  -> MATCHED
MATCHED    -> classification revision은 가능, 상태 역전은 별도 승인 전 금지

reviewStatus
null       -> PENDING
PENDING    -> RESOLVED
```

근거는 수집된 자료가 늘면서 `SEARCHING -> SINGLE_SOURCE -> MULTI_SOURCE_CONFIRMED`로 강화될 수 있다. `NO_NEW_CATALYST`, `REEMERGENCE`, `AFTER_CLOSE_CONFIRMED` 전이의 세부 조건은 Catalyst Matching 정책이 소유한다. API는 근거 없이 client가 상태를 강화하는 것을 허용하지 않는다.

새로운 역전·재개 transition이 필요하면 Event ID 유지 여부와 audit 의미를 ADR·schema에 먼저 반영한다.

### 5.3 같은 이름의 `CLOSED`

```json
{
  "dataStatus": "CLOSED",
  "lifecycleStatus": "CLOSED",
  "reconciliationStatus": "PENDING",
  "evidenceStatus": "SINGLE_SOURCE"
}
```

위 조합은 장이 마감돼 Event도 종료됐지만 인포스탁 확정은 기다리고 있고 장중 단일 뉴스 근거가 남아 있다는 뜻이다. client는 field 이름 없이 enum 값만 비교하지 않는다.

### 5.4 분류

분류를 다음 구조로 전달한다.

```json
{
  "classification": {
    "classificationVersion": 3,
    "themeId": "thm_01K2B5Y7J6D8G2M4R7T9V1X3Z5",
    "displayName": "전력설비",
    "kind": "INFOSTOCK_THEME",
    "certainty": "CONFIRMED",
    "source": "INFOSTOCK",
    "changedAt": "2026-08-14T07:12:04.000Z"
  }
}
```

후보 enum:

```text
kind:       INFOSTOCK_THEME | UNCLASSIFIED_CLUSTER | TEMPORARY_THEME
certainty:  PROVISIONAL | CONFIRMED
source:     LIVE_ENGINE | OPERATOR | INFOSTOCK
```

이 이름은 OpenAPI 검토에서 확정한다. `reconciliationStatus`와 중복되는 결합 enum은 만들지 않는다.

### 5.5 unknown enum

- server는 기존 enum의 의미를 바꾸지 않는다.
- client는 알 수 없는 값을 crash 원인으로 만들지 않는다.
- 화면은 `상태 확인 중` 같은 안전 표현을 사용한다.
- raw unknown value와 schemaVersion을 telemetry로 보낸다.
- 안전하지 않은 기능은 숨긴다. 특히 과거 연결은 unknown 상태에서 열지 않는다.

---

## 6. Coverage와 품질

### 6.1 Coverage 구조

```json
{
  "coverage": {
    "status": "SUFFICIENT",
    "core": {
      "observedCount": 17,
      "totalCount": 21,
      "countRatio": 0.8095,
      "observedWeightRatio": 0.91
    },
    "related": {
      "observedCount": 25,
      "totalCount": 31,
      "countRatio": 0.8065
    }
  }
}
```

`coverage.status`:

```text
SUFFICIENT
PARTIAL
INSUFFICIENT
```

threshold는 server의 versioned 계산 정책이다. client가 ratio로 상태를 다시 판정하지 않는다.

규칙:

- `observedCount <= totalCount`
- denominator가 0이면 ratio는 `null`, 이유 flag 제공
- `INSUFFICIENT` 결과를 0% theme로 표시 금지
- rank·treemap 포함 여부는 server가 결정
- Core와 Related Coverage를 합쳐 하나의 숫자로 재계산 금지

### 6.2 `qualityFlags`

`qualityFlags`는 open enum 문자열 배열이다. 초기 후보:

```text
PARTIAL_COVERAGE
INSUFFICIENT_COVERAGE
STALE_MARKET_DATA
STALE_NEWS_DATA
FREE_FLOAT_UNAVAILABLE
FREE_FLOAT_STALE
FREE_FLOAT_SOURCE_CONFLICT
PROVISIONAL_BASELINE
PUBLISHED_TIME_UNKNOWN
MEMBERSHIP_VERSION_MISMATCH
OUTCOME_PARTIAL
SOURCE_DEGRADED
```

flag가 없으면 `[]`다. client는 모르는 flag를 무시하되 telemetry에 기록하고, `dataStatus`와 명시적 availability를 우선한다.

---

## 7. 오류 계약

### 7.1 오류 envelope

REST 오류는 `data` 없이 다음 형식을 사용한다.

```json
{
  "error": {
    "code": "RESOURCE_ID_MISMATCH",
    "message": "요청한 테마와 이벤트의 관계를 확인할 수 없습니다.",
    "retryable": false,
    "details": {
      "field": "themeId"
    }
  },
  "meta": {
    "requestId": "req_01K2B7M3M6R4J6K4J2Y7X9Q8ZW",
    "apiVersion": "1",
    "schemaVersion": "2026-08-14.1",
    "generatedAt": "2026-08-14T01:18:23.042Z"
  }
}
```

`message`는 사용자에게 그대로 노출하는 문구가 아니다. UI는 code와 context에 맞는 승인 문구를 사용한다.

### 7.2 HTTP status와 code

| HTTP | code 예 | 의미 |
|---:|---|---|
| 400 | `INVALID_REQUEST` | query·body 형식 오류 |
| 401 | `AUTHENTICATION_REQUIRED` | 인증 필요 |
| 403 | `FEATURE_NOT_ENTITLED` | pilot·운영 권한 없음 |
| 404 | `RESOURCE_NOT_FOUND` | 존재하지 않거나 노출 불가 |
| 409 | `RESOURCE_ID_MISMATCH` | identifier 관계 충돌 |
| 422 | `UNSUPPORTED_MARKET_DATE` | 처리할 수 없는 거래일 조건 |
| 429 | `RATE_LIMITED` | 호출 한도 |
| 500 | `INTERNAL_ERROR` | 예기치 않은 server 오류 |
| 503 | `DATA_TEMPORARILY_UNAVAILABLE` | 필요한 source·read model 불가 |

source 일부가 지연됐지만 마지막 정상 read model을 안전하게 제공할 수 있으면 HTTP 200과 `DELAYED`·`DEGRADED`를 사용한다. 신뢰 가능한 결과 자체가 없으면 503을 사용한다.

validation 오류의 `details`는 field path·reason을 제공하되 내부 stack·SQL·공급원 credential을 노출하지 않는다.

### 7.3 retry

- `retryable=true`여도 client는 exponential backoff와 jitter 사용
- 429는 `Retry-After` 우선
- 4xx non-retryable 오류 자동 반복 금지
- mutation이 추가되면 `Idempotency-Key` 계약을 별도 정의

---

## 8. Pagination·filter·sort

### 8.1 Cursor pagination

과거 목록은 opaque cursor를 사용한다.

```json
{
  "items": [],
  "page": {
    "nextCursor": null,
    "hasMore": false,
    "limit": 20
  }
}
```

- cursor 내용을 client가 해석·수정하지 않음
- 동일 query·version 안에서만 cursor 재사용
- sort는 stable tie-breaker를 포함
- `nextCursor=null`은 다음 page 없음
- mutable 실시간 ranking에는 page cursor를 사용하지 않고 제한된 전체 snapshot 제공

기본·최대 `limit` 값은 OpenAPI에서 확정한다.

### 8.2 Filter와 sort

- 지원하지 않는 filter·sort는 조용히 무시하지 않고 400
- enum query는 문서화된 값만 허용
- 유사사례 filter·sort는 URL에 복원 가능
- 사용자 표시명으로 ID filter 금지
- 결과를 수익률로 재정렬해 유사성 순서를 왜곡하지 않음

---

## 9. 인증·권한·보안

### 9.1 제품 read 인증

사용자 인증 공급자는 Google OAuth만 사용하고 서버가 `Domain` 속성 없는 Secure·HttpOnly host-only session cookie를 발급한다. browser의 REST·OAuth 요청은 Vercel app origin의 `/api/*` external rewrite를 통과한다. 모든 제품 REST endpoint는 유효한 사용자 session을 요구한다. 비로그인 요청에는 제품 데이터 일부나 stale cache를 포함하지 않고 `401 AUTHENTICATION_REQUIRED`를 반환한다.

비로그인 접근이 허용되는 것은 로그인 정적 화면, 아래 OAuth endpoint, logout, 개인정보·약관 같은 필수 정적 문서와 제품 데이터를 포함하지 않는 최소 health check뿐이다. 이메일·비밀번호 로그인, NAVER 로그인, 이메일 인증은 구현하지 않는다.

### 9.2 권한

```text
authenticated core read
historical pilot entitlement
operator read/write
internal service
```

- 유사사례 feature flag만으로 권한을 대신하지 않음
- operator field를 public schema에 섞지 않음
- `reviewStatus` 등 내부 상태 노출 범위 별도 projection
- source 원문·재배포 권리에 따라 evidence field 제한
- cookie 인증의 상태 변경 요청은 허용된 app origin 검증과 CSRF 방어를 적용
- 인증·사용자별 REST 응답은 `Cache-Control: private, no-store`이며 Vercel edge cache 대상이 아니다.

### 9.3 WebSocket 인증

browser URL query에 장기 bearer token이나 connection ticket을 넣지 않는다. 원칙:

- 인증된 `POST /v1/auth/realtime-ticket`이 수명 30초의 1회용 opaque ticket을 발급한다.
- browser는 OCI WSS 연결 직후 5초 안에 첫 `AUTH` message로 ticket을 제출한다.
- server는 Redis에서 ticket을 원자적으로 소비하고 session·origin·만료를 검증한다.
- ticket 재사용·만료·다른 session 사용은 연결을 종료한다.
- 인증 전에는 snapshot 일부를 보내지 않는다.

WSS 연결은 `wss://api.dayjaview.duckdns.org/v1/realtime`이며 `APP_BASE_URL=https://dayjaview.vercel.app` 기준 origin 검증은 필수다.

### 9.4 인증 endpoint

```text
GET  /auth/google
GET  /auth/google/callback
GET  /auth/session
POST /auth/logout
POST /v1/auth/realtime-ticket
```

- callback URI는 `https://dayjaview.vercel.app/api/auth/google/callback`이며 Google Console 등록값과 정확히 일치시킨다. Vercel rewrite가 OCI callback handler로 전달한다.
- callback의 authorization code와 OAuth `state`는 로그에 기록하지 않는다.
- 로그아웃은 현재 서버 session을 폐기하고 cookie를 만료시킨다.
- Google API를 대신 호출하는 기능이 없으므로 장기 refresh token은 저장하지 않는다.
- `returnTo`는 `APP_BASE_URL` 기준 내부 경로만 허용하며 검증 실패 시 `오늘`로 이동한다.

---

## 10. 핵심 REST endpoint 후보

경로는 OpenAPI 검토에서 변경 가능하다. field 의미는 이 문서를 따른다.

### 10.1 `GET /v1/market/session`

목적: 현재 KRX session·거래일·다음 전환과 data freshness 제공.

응답 예시:

```json
{
  "data": {
    "market": "KRX",
    "timeZone": "Asia/Seoul",
    "marketDate": "2026-08-14",
    "sessionPhase": "REGULAR",
    "sessionOpenedAt": "2026-08-14T00:00:00.000Z",
    "sessionClosesAt": "2026-08-14T06:30:00.000Z",
    "nextTransitionAt": "2026-08-14T06:30:00.000Z"
  },
  "meta": {
    "requestId": "req_01K2B7M3M6R4J6K4J2Y7X9Q8ZW",
    "apiVersion": "1",
    "schemaVersion": "2026-08-14.1",
    "generatedAt": "2026-08-14T01:18:23.042Z",
    "marketContext": {
      "market": "KRX",
      "timeZone": "Asia/Seoul",
      "marketDate": "2026-08-14",
      "asOf": "2026-08-14T01:18:22.410Z",
      "dataStatus": "LIVE",
      "lastHealthyAt": "2026-08-14T01:18:22.410Z",
      "qualityFlags": []
    }
  }
}
```

`sessionPhase` 후보:

```text
PREOPEN
REGULAR
CLOSED
HOLIDAY
```

동시호가·시간외 세분화는 제품 범위 승인 후 enum 확장한다.

### 10.2 `GET /v1/themes/rankings`

query 후보:

| query | 의미 |
|---|---|
| `limit` | 노출 개수, 상한 OpenAPI 확정 |
| `marketDate` | 기본 현재 KRX 거래일; 과거 조회 지원 여부 미결정 |

응답 예시:

```json
{
  "data": {
    "snapshotId": "snap_01K2B7HVK1BD7M2P6W4N9A8F5C",
    "streamId": "stream_01K2B80C7V3P9N5M1D6Q4R8AXZ",
    "sequence": 1842,
    "items": [
      {
        "eventId": "evt_01K2B62FQ4D1P8E9N7A3C5M6RT",
        "lifecycleStatus": "ACTIVE",
        "reconciliationStatus": "PENDING",
        "classification": {
          "classificationVersion": 1,
          "themeId": "thm_01K2B5Y7J6D8G2M4R7T9V1X3Z5",
          "displayName": "원전수출",
          "kind": "INFOSTOCK_THEME",
          "certainty": "PROVISIONAL",
          "source": "LIVE_ENGINE",
          "changedAt": "2026-08-14T00:11:04.000Z"
        },
        "rank": 1,
        "rankChange60s": 3,
        "badges": ["RISING_FAST"],
        "weightedReturn": 0.027,
        "weightMethod": "FREE_FLOAT_CAPPED",
        "advancingCount": 17,
        "validCount": 21,
        "leader": {
          "stockId": "stk_01JZZY9SQ0VQAF2PJ8Y0VMSV40",
          "symbol": "000000",
          "name": "예시 종목",
          "return": 0.142
        },
        "evidence": {
          "evidenceStatus": "SINGLE_SOURCE",
          "summary": "체코 신규 원전 관련 보도",
          "publishedAt": "2026-08-14T01:17:00.000Z"
        },
        "coverage": {
          "status": "SUFFICIENT",
          "core": {
            "observedCount": 17,
            "totalCount": 21,
            "countRatio": 0.8095,
            "observedWeightRatio": 0.91
          },
          "related": {
            "observedCount": 25,
            "totalCount": 31,
            "countRatio": 0.8065
          }
        },
        "qualityFlags": []
      }
    ]
  },
  "meta": {
    "requestId": "req_01K2B7M3M6R4J6K4J2Y7X9Q8ZW",
    "apiVersion": "1",
    "schemaVersion": "2026-08-14.1",
    "generatedAt": "2026-08-14T01:18:23.042Z",
    "marketContext": {
      "market": "KRX",
      "timeZone": "Asia/Seoul",
      "marketDate": "2026-08-14",
      "asOf": "2026-08-14T01:18:22.410Z",
      "dataStatus": "LIVE",
      "lastHealthyAt": "2026-08-14T01:18:22.410Z",
      "qualityFlags": []
    },
    "versions": {
      "calculationVersion": "theme-metrics-2026.08.1",
      "rankingModelVersion": "theme-rank-2026.08.1",
      "membershipVersion": "membership-2026-08-14T00:10:00Z"
    }
  }
}
```

규칙:

- 기본 목록에 `CANDIDATE`, `DISCARDED` 제외
- Coverage 미달 theme는 rank item에서 제외하거나 명시적 unavailable projection 사용
- `rankChange60s=null`은 비교 snapshot 없음
- `badges=[]`은 badge 없음
- 내부 score는 public 응답에서 제외
- `weightedReturn`은 API의 기술 필드명으로 유지하지만, 클라이언트가 사용자에게 표시하는 지표명은 모든 화면에서 `테마 수익률`로 고정한다.
- production의 `weightMethod`은 `FREE_FLOAT_CAPPED`만 허용한다. 검증된 유동주식비율이 없으면 다른 가중 방식으로 조용히 대체하지 않고 해당 값을 unavailable로 처리한다.

### 10.3 `GET /v1/insights/treemap`

목적: 인사이트 최초 REST snapshot과 WebSocket 복구.

응답 item 예시:

```json
{
  "eventId": "evt_01K2B62FQ4D1P8E9N7A3C5M6RT",
  "themeId": "thm_01K2B5Y7J6D8G2M4R7T9V1X3Z5",
  "displayName": "원전수출",
  "lifecycleStatus": "ACTIVE",
  "weightedReturn": 0.027,
  "advancingCount": 17,
  "validCount": 21,
  "coverageStatus": "SUFFICIENT",
  "qualityFlags": []
}
```

response envelope는 ranking과 같은 `snapshotId`, `streamId`, `sequence`, `marketContext`, version을 사용한다.

REST 최초 snapshot과 WebSocket 첫 snapshot의 `streamId`가 같을 때만 sequence를 직접 비교한다. 다르면 WebSocket의 첫 full snapshot으로 전체 교체한다.

규칙:

- `weightedReturn > 0`과 노출 기준 판정은 server 책임
- client가 ranking 목록에서 treemap 대상을 다시 계산하지 않음
- tile size와 color에 필요한 공개 값만 제공
- 기본 상위 개수는 제품·성능 검토에서 확정

### 10.4 `GET /v1/themes/{themeId}/events/{eventId}`

목적: 테마 상세 요약 read model.

응답 구조 예시:

```json
{
  "data": {
    "eventId": "evt_01K2B62FQ4D1P8E9N7A3C5M6RT",
    "marketDate": "2026-08-14",
    "lifecycleStatus": "ACTIVE",
    "reconciliationStatus": "PENDING",
    "classification": {
      "classificationVersion": 1,
      "themeId": "thm_01K2B5Y7J6D8G2M4R7T9V1X3Z5",
      "displayName": "원전수출",
      "kind": "INFOSTOCK_THEME",
      "certainty": "PROVISIONAL",
      "source": "LIVE_ENGINE",
      "changedAt": "2026-08-14T00:11:04.000Z"
    },
    "currentReaction": {
      "weightedReturn": 0.027,
      "weightMethod": "FREE_FLOAT_CAPPED",
      "advancingCount": 17,
      "validCount": 21,
      "turnoverMultiple": 2.4,
      "attentionGapTradingDays": 163
    },
    "coverage": {
      "status": "SUFFICIENT",
      "core": {
        "observedCount": 17,
        "totalCount": 21,
        "countRatio": 0.8095,
        "observedWeightRatio": 0.91
      },
      "related": {
        "observedCount": 25,
        "totalCount": 31,
        "countRatio": 0.8065
      }
    },
    "evidenceSummary": {
      "evidenceStatus": "SINGLE_SOURCE",
      "summary": "체코 신규 원전 관련 보도",
      "sourceCount": 1,
      "latestPublishedAt": "2026-08-14T01:17:00.000Z"
    },
    "leaders": [
      {
        "stockId": "stk_01JZZY9SQ0VQAF2PJ8Y0VMSV40",
        "symbol": "000000",
        "name": "예시 종목",
        "return": 0.142,
        "role": "LEADER"
      }
    ],
    "historicalAccess": {
      "status": "GATED",
      "reason": "ONTOLOGY_VALIDATION_REQUIRED"
    },
    "canonicalPath": "/v1/themes/thm_01K2B5Y7J6D8G2M4R7T9V1X3Z5/events/evt_01K2B62FQ4D1P8E9N7A3C5M6RT",
    "qualityFlags": []
  },
  "meta": {
    "requestId": "req_01K2B7M3M6R4J6K4J2Y7X9Q8ZW",
    "apiVersion": "1",
    "schemaVersion": "2026-08-14.1",
    "generatedAt": "2026-08-14T01:18:23.042Z"
  }
}
```

`historicalAccess.status` 후보:

```text
AVAILABLE
GATED
UNAVAILABLE
```

`GATED`는 서버 장애가 아니다. 사용자 진입점은 제품 gate 정책에 따라 숨긴다.

### 10.5 `GET /v1/events/{eventId}/evidence`

query 후보:

- `cursor`
- `limit`

응답 item 예시:

```json
{
  "newsId": "news_01K2B6D0S3H7N4X8F9K1W5Q2PA",
  "sourceName": "예시 언론사",
  "title": "체코 신규 원전 관련 보도",
  "publishedAt": "2026-08-14T01:17:00.000Z",
  "receivedAt": "2026-08-14T01:17:08.201Z",
  "originalUrl": "https://example.com/news/123",
  "matchBasis": ["THEME", "STOCK", "TIME"],
  "summary": "기사에서 확인된 범위의 자체 요약",
  "qualityFlags": []
}
```

응답 상단에 `evidenceStatus`와 pagination을 포함한다.

규칙:

- `SEARCHING`에서 `items=[]`은 아직 확인 중일 수 있음
- `NO_NEW_CATALYST`와 수집 장애를 구분
- source 권리상 제공 불가한 본문을 API에 포함하지 않음
- 원문 URL·매체·발행 시각을 가능한 범위에서 제공
- LLM이 만든 요약을 source 원문으로 표시하지 않음

### 10.6 사용자 관심·저장

모든 endpoint는 현재 session의 사용자만 대상으로 한다. `userId`를 path·query·body로 받지 않는다.

```text
GET    /v1/me/saved?type=ALL|THEME|STOCK|EVENT&cursor=<opaque>&limit=<n>
PUT    /v1/me/saved/themes/{themeId}
DELETE /v1/me/saved/themes/{themeId}
PUT    /v1/me/saved/stocks/{stockId}
DELETE /v1/me/saved/stocks/{stockId}
PUT    /v1/me/saved/events/{eventId}
DELETE /v1/me/saved/events/{eventId}
DELETE /v1/me
```

`PUT`은 idempotent하다. 이미 저장된 대상이면 기존 저장 상태를 성공으로 반환한다. `DELETE`도 대상이 이미 없으면 성공으로 처리해 client 재시도를 안전하게 한다.

목록 item 예시:

```json
{
  "savedType": "THEME",
  "targetId": "thm_584",
  "displayName": "스페이스X(SpaceX)",
  "savedAt": "2026-08-14T02:10:00.000Z",
  "availability": "AVAILABLE",
  "unavailableReason": null,
  "currentState": {
    "eventId": "evt_01K2B62FQ4D1P8E9N7A3C5M6RT",
    "eventState": "ACTIVE",
    "weightedReturn": 0.0342,
    "dataStatus": "LIVE",
    "asOf": "2026-08-14T02:11:20.000Z"
  }
}
```

규칙:

- `savedType`은 `THEME | STOCK | EVENT`다.
- 정렬 기본값은 `savedAt desc`, 동률은 `targetId`다.
- 저장 목록은 사용자별 projection이며 저장 여부가 공용 ranking·matching 결과를 바꾸지 않는다.
- 대상이 비활성·삭제·권한 제한이면 항목을 다른 대상으로 치환하지 않고 `availability=UNAVAILABLE`과 허용된 사유를 반환한다.
- 과거 Event detail은 유사사례 gate와 entitlement를 그대로 적용한다. 저장했다는 사실이 권한을 우회하지 않는다.
- 저장·해제 응답은 target ID, 최종 `saved` 상태, `savedAt`을 반환한다.
- `DELETE /v1/me`는 재인증 또는 최근 인증 확인 후 모든 session을 폐기하고 최소 프로필과 saved data 삭제를 시작한다. 성공 후 기존 session으로 제품 API에 접근할 수 없다.
- 알림 설정, 증권계좌, 주문·포트폴리오 field를 이 계약에 추가하지 않는다.

### 10.7 내부 운영자 API

모든 endpoint는 유효한 Google session과 내부 `OPERATOR` 역할을 함께 요구한다. 일반 사용자 session은 endpoint 존재를 알아도 `403 FEATURE_NOT_ENTITLED`를 받는다. 운영자 field를 사용자 projection에 추가하지 않는다.

endpoint 후보:

```text
GET  /v1/operator/status
GET  /v1/operator/jobs?status=<status>&cursor=<opaque>
GET  /v1/operator/jobs/{runId}
POST /v1/operator/jobs/{runId}/retry
POST /v1/operator/jobs/{runId}/resume
GET  /v1/operator/reviews?type=<type>&status=PENDING&cursor=<opaque>
GET  /v1/operator/reviews/{reviewId}
POST /v1/operator/reviews/{reviewId}/resolve
GET  /v1/operator/audit?cursor=<opaque>
GET  /v1/operator/infostock/auth-status
```

`status` 응답 범위:

- 배포 version·commit·기동 시각
- API·market·news·infostock·batch process의 기본 health
- 공급원별 마지막 성공 시각과 `RUNNING | SUCCEEDED | PARTIAL | RATE_LIMITED | AUTH_REQUIRED | FAILED`
- credential·cookie·token·내부 host 경로를 제거한 오류 code

command 규칙:

- retry·resume·resolve는 CSRF 방어, `Idempotency-Key`, 대상 현재 version을 요구한다.
- request에는 승인된 reason code와 운영자 사유를 포함한다.
- 결과는 `auditId`, 수행자, 시각, 대상, 이전/이후 revision을 반환한다.
- 분류 수정·병합·제외는 원본 row를 덮어쓰거나 hard delete하지 않는다.
- 재시도 불가능한 작업, 이미 완료된 작업과 stale version은 명시적 `409`로 거부한다.
- 인포스탁 인증 상태 API는 session state·cookie를 반환하지 않는다. 수동 재인증은 OCI loopback 인증 UI와 SSH tunnel runbook을 따른다.
- 서버 shell·파일 브라우저·임의 SQL·secret 편집 endpoint는 만들지 않는다.

---

## 11. 조건부 유사사례 REST endpoint

이 절은 온톨로지 구축·재검증·승인된 artifact·entitlement를 모두 통과한 뒤 활성화한다. v1 기준선 결과를 그대로 연결하지 않는다.

### 11.1 `GET /v1/events/{eventId}/similar-events`

query 후보:

```text
horizonTradingDays=1|5|20
sort=relevance|eventDate
cursor=<opaque>
limit=<n>
```

`sort=outcome`은 제공하지 않는다. 미래 결과로 관련성 순서를 바꾸지 않는다.

응답 예시:

```json
{
  "data": {
    "eventId": "evt_01K2B62FQ4D1P8E9N7A3C5M6RT",
    "decisionAt": "2026-08-14T01:18:22.410Z",
    "availability": "AVAILABLE",
    "summary": [
      {
        "horizonTradingDays": 1,
        "eligibleCount": 14,
        "observedCount": 14,
        "positiveCount": 10,
        "medianReturn": 0.031
      },
      {
        "horizonTradingDays": 5,
        "eligibleCount": 14,
        "observedCount": 14,
        "positiveCount": 9,
        "medianReturn": 0.082
      },
      {
        "horizonTradingDays": 20,
        "eligibleCount": 14,
        "observedCount": 12,
        "positiveCount": 6,
        "medianReturn": 0.041
      }
    ],
    "items": [
      {
        "matchedEventId": "evt_01HXYZR8C9G4T7N2K5M6P1Q3VW",
        "marketDate": "2024-11-18",
        "displayNameAtEvent": "LED",
        "normalizedCatalystSummary": "마이크로 LED 양산 발표",
        "similarityReasons": ["유사 소재 유형", "관련 종목군 중첩"],
        "outcomes": [
          {
            "horizonTradingDays": 1,
            "return": -0.003,
            "status": "OBSERVED",
            "unavailableReason": null
          },
          {
            "horizonTradingDays": 20,
            "return": 0.011,
            "status": "OBSERVED",
            "unavailableReason": null
          }
        ]
      }
    ],
    "page": {
      "nextCursor": null,
      "hasMore": false,
      "limit": 20
    }
  },
  "meta": {
    "requestId": "req_01K2B7M3M6R4J6K4J2Y7X9Q8ZW",
    "apiVersion": "1",
    "schemaVersion": "2026-08-14.1",
    "generatedAt": "2026-08-14T01:18:23.042Z",
    "versions": {
      "ontologyVersion": "ontology-2026.08.1",
      "retrievalArtifactVersion": "retrieval-2026.08.1",
      "tradingCalendarVersion": "krx-calendar-2026.08.1"
    }
  }
}
```

규칙:

- 검색 후보와 순서는 `decisionAt` 당시 정보로 고정
- outcome은 후보 선택 완료 후 결합
- `eligibleCount`, `observedCount`, `positiveCount` 구분
- 기간별 분모가 다르면 그대로 제공
- 작은 표본을 숨기지 않음
- 현재 관련주를 과거 event에 소급하지 않음
- 내부 similarity score 노출 여부는 설명 가능성·오해 위험 검토 후 결정

### 11.2 `GET /v1/events/{eventId}`

query 후보:

```text
contextEventId=<현재 eventId>
```

목적: 과거 Event 자체와 현재 사건 기준 similarity explanation·당시 outcome 제공. 이 endpoint를 유사사례에서 호출할 때 path의 `eventId` 값이 화면·client 모델에서는 `matchedEventId`다.

필수 section:

- 과거 Event 분류와 사건일
- `contextEventId` 기준 similarity reasons
- 발생 전 관심·가격·거래 상태
- 당시 membership과 leader
- T+1·T+5·T+20 관측 결과·누락 이유
- 적용된 ontology·retrieval·outcome version
- 미래 결과 비사용 고지 flag 또는 문구 key

`contextEventId`가 없으면 similarity explanation을 제공하지 않거나 명시적 null로 구분한다. 과거 Event detail 자체와 현재 사건 비교 context를 섞지 않는다.

---

## 12. WebSocket 계약

### 12.1 연결

endpoint 후보:

```text
wss://<host>/v1/realtime
```

하나의 연결에서 여러 topic을 구독한다. 연결 직후 client가 명시적으로 subscribe한다.

### 12.2 client subscribe

```json
{
  "type": "subscribe",
  "requestId": "client_001",
  "topics": [
    { "name": "theme_rank_snapshot", "params": { "limit": 10 } },
    { "name": "theme_treemap_snapshot", "params": { "limit": 12 } },
    {
      "name": "event_state_changed",
      "params": { "eventIds": ["evt_01K2B62FQ4D1P8E9N7A3C5M6RT"] }
    }
  ]
}
```

server는 승인된 topic·normalized params와 subscription ID를 응답한다.

```json
{
  "type": "subscribed",
  "requestId": "client_001",
  "subscriptionId": "sub_01K2B8A4Q9P3F6D7M1R5C2X0VW",
  "topics": ["theme_rank_snapshot", "theme_treemap_snapshot", "event_state_changed"]
}
```

### 12.3 server snapshot envelope

```json
{
  "type": "theme_rank_snapshot",
  "schemaVersion": "2026-08-14.1",
  "subscriptionId": "sub_01K2B8A4Q9P3F6D7M1R5C2X0VW",
  "streamId": "stream_01K2B80C7V3P9N5M1D6Q4R8AXZ",
  "topic": "theme_rank_snapshot",
  "sequence": 1842,
  "generatedAt": "2026-08-14T01:18:23.042Z",
  "asOf": "2026-08-14T01:18:22.410Z",
  "marketDate": "2026-08-14",
  "dataStatus": "LIVE",
  "qualityFlags": [],
  "payload": {
    "snapshotId": "snap_01K2B7HVK1BD7M2P6W4N9A8F5C",
    "items": []
  }
}
```

### 12.4 sequence 범위

`sequence`는 전 시스템 global 값이 아니다. 다음 key 안에서 단조 증가한다.

```text
streamId + topic + normalized subscription params
```

client 규칙:

1. 같은 `streamId`·topic에서 이전보다 작은 또는 같은 sequence 무시
2. 새 `streamId`이면 이전 sequence와 비교하지 않고 새 전체 snapshot 수용
3. connection 재연결 후 subscribe하고 첫 전체 snapshot으로 교체
4. sequence gap이 있어도 현재 message가 전체 snapshot이면 적용 가능
5. delta message는 초기 계약에 없음

### 12.5 topic 의미

| topic | payload | 초기 방식 |
|---|---|---|
| `theme_rank_snapshot` | 오늘 ranking 전체 노출 집합 | full snapshot |
| `theme_treemap_snapshot` | treemap 전체 노출 집합 | full snapshot |
| `event_state_changed` | 구독 Event의 최신 요약 상태 | event별 full summary |

`event_state_changed` payload는 Event별 전체 공개 요약으로 확정한다. refetch 신호나 불완전한 delta를 보내지 않는다.

### 12.6 snapshot 주기와 coalescing

- 정상 목표 주기: 약 2~3초 후보
- 내부 tick마다 client message를 만들지 않음
- 변경이 많으면 같은 주기 안에서 최신 상태로 coalesce
- 변경이 없어도 freshness 확인용 주기 snapshot 또는 heartbeat 제공
- 정확한 값은 성능·SLO 시험 후 설정으로 관리

### 12.7 heartbeat

application heartbeat 후보:

```json
{
  "type": "ping",
  "sentAt": "2026-08-14T01:18:30.000Z"
}
```

client는 `pong`을 응답하거나 protocol ping/pong을 사용한다. 방식·간격·timeout은 AsyncAPI·인프라 검토에서 확정한다.

### 12.8 재연결

1. 연결 끊김 감지
2. 화면을 즉시 LIVE로 유지하지 않고 stale 상태 전환
3. exponential backoff + jitter로 재연결
4. 인증 갱신 필요 시 갱신
5. topic 재구독
6. 첫 full snapshot 수신
7. snapshot 적용 후 LIVE·DELAYED·DEGRADED 표시

offline·background 복귀 시에도 같은 절차를 사용한다. 과도한 동시 재연결을 방지한다.

### 12.9 backpressure

- server는 client별 무제한 queue를 만들지 않음
- full snapshot topic은 보내지 못한 중간 snapshot을 버리고 최신값으로 coalesce 가능
- 느린 client는 명시적 close reason 후 재연결
- Event 상태 감사 log 전달을 WebSocket delivery 보장으로 착각하지 않음
- 중요한 영속 상태는 REST·PostgreSQL에서 복구

### 12.10 오류 message

```json
{
  "type": "error",
  "requestId": "client_001",
  "code": "INVALID_SUBSCRIPTION",
  "message": "지원하지 않는 topic 또는 parameter입니다.",
  "retryable": false
}
```

인증 실패·권한 없음·schema 불일치는 close code와 application error를 AsyncAPI에서 확정한다.

---

## 13. Cache·freshness

### 13.1 실시간 read model

- browser·CDN에서 오래 cache하지 않음
- `Cache-Control: no-store` 또는 짧은 private cache를 endpoint별 확정
- 화면 freshness는 HTTP response 시각이 아니라 `asOf`·`dataStatus`로 판단
- WebSocket 복구 REST가 오래된 CDN response를 받지 않게 구성

### 13.2 확정 과거 데이터

- immutable version·ETag 사용 가능
- ontology·retrieval·outcome version이 바뀌면 cache key도 달라짐
- 장후 revision 전 데이터와 확정 데이터를 같은 ETag로 제공하지 않음

---

## 14. 호환성·버전·폐기

### 14.1 호환 가능한 변경

- optional field 추가
- open enum에 새 값 추가 — client unknown fallback 전제
- 새 endpoint·topic 추가
- nullable field에 값 제공 시작

### 14.2 호환되지 않는 변경

- field 의미·단위 변경
- 수익률 decimal을 percent number로 변경
- optional → required
- null 의미 변경
- identifier lifecycle 변경
- sequence 범위 변경
- 기존 enum 값 삭제·의미 변경

비호환 변경은 새 major version 또는 새 field·topic으로 진행한다.

### 14.3 폐기

- deprecation 공지와 replacement 제공
- 가능한 경우 `Deprecation`, `Sunset`, `Link` header 사용
- 최소 지원 기간은 배포 주기 확정 후 결정
- production telemetry로 구버전 사용 확인
- 문서만 지우고 endpoint를 즉시 중단하지 않음

### 14.4 version 구분

| version | 대상 |
|---|---|
| `apiVersion` | public API major |
| `schemaVersion` | response·message schema revision |
| `calculationVersion` | metric 계산 |
| `rankingModelVersion` | rank 정책 |
| `membershipVersion` | theme 구성종목 |
| `ontologyVersion` | event 구조화 ontology |
| `retrievalArtifactVersion` | 유사사례 검색 artifact |
| `tradingCalendarVersion` | KRX 거래일 |

한 version 문자열로 모든 변경을 숨기지 않는다.

---

## 15. Fixture 계약

`contracts/fixtures/`의 후보 구조:

```text
market/
  session.preopen.json
  session.live.json
  session.closed.json
rankings/
  live.json
  delayed.json
  degraded-partial-coverage.json
  closed-pending-reconciliation.json
  empty.json
treemap/
  live.json
  insufficient-coverage-excluded.json
event/
  searching-evidence.json
  single-source.json
  multi-source.json
  after-close-confirmed.json
  unmatched-review-pending.json
evidence/
  none-searching.json
  no-new-catalyst.json
  source-degraded.json
similar/
  gated.json
  available.json
  empty.json
  small-sample.json
  partial-outcomes.json
realtime/
  subscribed.json
  ranking-snapshot.json
  new-stream-snapshot.json
  old-sequence.json
  reconnect-full-snapshot.json
errors/
  invalid-request.json
  not-entitled.json
  unavailable.json
```

규칙:

- fixture는 OpenAPI·AsyncAPI·JSON Schema 검증 통과
- 예시 ID는 서로 참조 가능하게 구성
- 실제 개인정보·credential·제한된 원문 포함 금지
- 날짜·sequence가 논리적으로 일관돼야 함
- 프론트 mock server와 backend contract test가 같은 fixture 사용
- production build에서 fixture 제거 검사

---

## 16. Contract test

기계 계약은 저장소 루트에서 다음 두 명령으로 검증한다.

```text
uv run python scripts/validate_contracts.py
uv run pytest tests/contracts -q
```

검증기는 외부 서비스나 secret 없이 OpenAPI, AsyncAPI 3.0 profile, JSON Schema reference, 모든 fixture, 이 문서의 JSON 예시와 시간·Coverage·Event ID·classification revision·sequence·권한 경계 invariant를 결정적으로 확인한다.

### 16.1 schema

- 모든 REST success·error example 검증
- WebSocket client·server message 검증
- enum unknown client test
- additional property 정책 명시
- nullable·required 구분

### 16.2 의미 invariant

```text
asOf <= generatedAt
publishedAt <= receivedAt, 확인 가능한 경우
observedCount <= totalCount
positiveCount <= observedCount <= eligibleCount
rank >= 1
sequence 단조 증가
eventId 장후 유지
classificationVersion 단조 증가
INSUFFICIENT Coverage를 0%로 노출하지 않음
UNMATCHED 과거 자동 연결 금지
outcome이 검색 후보 선택 입력에 존재하지 않음
```

### 16.3 소비자 계약

- 프론트가 실제 사용하는 field를 fixture로 검증
- backend가 삭제·타입 변경 시 CI 실패
- optional field가 없을 때 UI test
- null·0·[] 각각 별도 test
- DELAYED·DEGRADED·CLOSED 상태별 visual test

---

## 17. 계속 미결정인 사항

| 항목 | 결정 주체 | 결정 시점 |
|---|---|---|
| heartbeat·timeout·reconnect 최대값 | 프론트·백엔드·인프라 | 부하 시험 전 |
| 과거 조회 cache 기간 | 백엔드·인프라 | 운영 준비 |
| deprecation 최소 기간 | 제품·개발 | 첫 release policy |
| similarity score 공개 여부 | 연구·제품 | 단계 7 검수 |
| 큰 금액 decimal 표현 | 백엔드·프론트 | schema 검토 |

미결정 항목은 server·client 한쪽에서 먼저 사실상 표준으로 만들지 않는다.

---

## 18. 완료 체크리스트

### 의미 계약 초안

- [x] 문서 범위와 우선순위를 정의했다.
- [x] 내부 배포 토폴로지와 외부 계약을 분리했다.
- [x] ID의 의미·관계·수명주기를 정의했다.
- [x] UTC·KST·KRX 거래일 기준을 정의했다.
- [x] `asOf`·`publishedAt`·`receivedAt` 의미를 정의했다.
- [x] 수익률·비율·count 단위를 정의했다.
- [x] `null`·`0`·빈 배열·누락을 구분했다.
- [x] `dataStatus`와 Event 상태 축을 분리했다.
- [x] Coverage와 `qualityFlags`를 정의했다.
- [x] 공통 success·error envelope를 정의했다.
- [x] pagination·filter·sort 원칙을 정의했다.
- [x] 핵심 REST 후보와 예시를 작성했다.
- [x] 사용자 관심·저장·계정 삭제 의미 계약을 작성했다.
- [x] 일반 사용자와 분리된 운영자 API 의미 계약을 작성했다.
- [x] 조건부 유사사례 계약과 gate를 정의했다.
- [x] WebSocket snapshot·sequence·재연결·backpressure를 정의했다.
- [x] 호환성·version·폐기 원칙을 정의했다.
- [x] fixture와 contract test 범위를 정의했다.
- [x] 미결정 항목을 기록했다.

### 승인 전 남은 일

- [ ] 프론트·백엔드가 모든 예시를 공동 검토했다.
- [ ] 데이터 담당이 metric·Coverage·시간 의미를 검토했다.
- [ ] 연구 담당이 유사사례 leakage 방지 계약을 검토했다.
- [ ] 운영·보안 담당이 인증·권한·오류 노출을 검토했다.
- [ ] 미결정 항목에 소유자와 기한을 지정했다.
- [x] `contracts/openapi.yaml`을 작성·검증했다.
- [x] `contracts/asyncapi.yaml`을 작성·검증했다.
- [x] `contracts/schemas/`를 작성했다.
- [x] `contracts/fixtures/`를 작성했다.
- [x] 로컬 validator와 focused contract test를 작성했다.
- [x] secret·외부 서비스 없는 contract CI를 작성했다.
- [ ] 프론트 mock과 backend contract test가 같은 schema를 사용한다.
- [ ] 문서 상태를 `승인`으로 변경했다.

문서 초안 작성 완료는 API 확정 또는 구현 완료를 뜻하지 않는다.

---

## 19. 변경 관리

- 제품 범위·출시 gate 변경은 [PRD.md](./PRD.md)를 먼저 수정한다.
- 상태 소유권 변경은 [system_architecture.md](./system_architecture.md)와 ADR을 먼저 수정한다.
- 화면 표현 변경은 [screen_spec.md](./screen_spec.md)와 함께 검토한다.
- wire 변경은 이 문서와 OpenAPI·AsyncAPI·fixture를 한 변경으로 갱신한다.
- schema example만 고쳐 의미 충돌을 숨기지 않는다.
- v1 검색 기준선을 일반 사용자 API에 연결하지 않는다.
- v2 예측 출력은 이 API namespace와 response에 추가하지 않는다.
