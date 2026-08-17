# DAYJAVIEW 회사 중심 사건 온톨로지·자연어 질의 확장 계획

- 문서 상태: 구현 기준 계획안
- 작성일: 2026-08-16
- 갱신일: 2026-08-17 (선행 단계·gold set 최소 표본 기준 추가, DailyFeaturedTheme 파싱 확장을 단계 1로 이동, 질의 유형 17종 확정, 열린 질의 전환 조건 신설, gold set 세 겹·라벨 출처 표시 신설, 거래일·발행일 어긋남 3일 기록)
- 대상: E-17 사건 온톨로지 확장, DailyFeaturedTheme 구조화, E-21 회사 중심 자연어 질의
- 관련 문서:
  - [Product Requirements Document](./PRD.md)
  - [화면 명세](./screen_spec.md)
  - [남은 작업](./remaining_work.md)
  - [인포스탁 DB 구현 계획](./infostock_db_implementation_plan.md)
  - [과거 유사사례 매칭 엔진 연구·구현 명세](./historical_event_matching_engine_research_spec.md)
  - [ADR-002 Event ID 수명주기와 장후 revision](./adr/002-event-id-lifecycle.md)
  - [ADR-007 과거 유사사례 검색의 온톨로지 재검증 출시 게이트](./adr/007-historical-matching-release-gate.md)

---

## 1. 목적

현재 E-17 온톨로지는 인포스탁 테마 history 원인문을 다음 네 축으로 분류한다.

- 소재 유형
- 상승·하락 방향
- 확정·기대 여부
- 지속·재부각 여부

이 구조는 “정책 소재에 어떤 테마가 반응했는가” 같은 테마 중심 질문에는 쓸 수 있다. 그러나 “한화에어로스페이스가 직접 체결한 계약만 알려줘” 같은 회사 중심 질문에는 부족하다. 원인문 끝의 주도주 목록에서 회사 이름을 찾을 수는 있지만, 회사가 사건 주체인지 단순 주도주인지 구분하지 못하기 때문이다.

이 계획의 목적은 회사·사건·테마·근거·실제 결과를 명시적으로 연결해 E-21 자연어 리서치가 다음 원칙으로 회사 질문에 답하게 만드는 것이다.

1. 회사 이름이 아니라 안정적인 회사 식별자를 사용한다.
2. 회사가 사건에서 맡은 역할을 구분한다.
3. 동일 사건의 테마·날짜별 반복 기록을 중복 집계하지 않는다.
4. 답변 수치와 목록은 결정론적 질의로 계산한다.
5. 모든 주장에 원문 근거와 데이터 버전을 붙인다.
6. 근거가 부족하면 추측하지 않고 `기록 없음`, `질문 해석 실패`, `제품 범위 밖`으로 답한다.

---

## 2. 현재 기준선

### 2.1 이미 구현된 자산

| 자산 | 현재 상태 | 재사용 원칙 |
|---|---|---|
| 테마 history | 39,696건, 2005~2026년 | 사건 관측 원천으로 유지 |
| E-17 분류 | 28개 소재 유형, 방향·확실성·지속, 근거 span | 기존 결과를 덮어쓰지 않고 새 버전으로 확장 |
| E-17 test | primary 엄격 77.8%, 허용 85.4%, 유형 포함 88.8%, 방향 99.6%, 확실성 90.0% | 알려진 품질 기준선으로 보존 |
| 종목 식별 | `core.infostock_stocks.stock_id`, 6자리 종목코드 | 새 종목 ID를 만들지 않음 |
| 종목명 관측 | `core.infostock_stock_name_observations` | 회사 alias 후보의 원천으로 사용 |
| 과거 주도주 | `core.infostock_theme_history_leaders`가 `stock_id`에 연결 | 역할을 항상 `LEADER`로 보존 |
| 과거 관련주 | `core.infostock_theme_history_memberships`가 `stock_id`에 연결 | `LEADER`와 혼합하지 않음 |
| DailyFeaturedTheme | 게시물 4,655건, 본문 4,655건, 관계 144,961건, 2007-09-17~2026-08-14 | 회사·날짜 질문의 1순위 원천. 원문(`raw_body`)이 보존돼 있으므로 재파싱으로 확장한다 |
| Daily 종목 연결 | `core.infostock_daily_relations.stock_id` | 종목 링크가 있는 관계는 재사용 |
| 이벤트 저장소 | `event.events`, 불변 `event_id`, revision 원칙 | 장중 시장 Event 의미를 유지 |
| 자연어 질의 계약 | 닫힌 질의 유형, 슬롯 채우기, 결정론적 계산, 근거 필수 | 자유 SQL·무근거 RAG를 도입하지 않음 |

### 2.2 실제로 부족한 것

기존 DB에 종목 ID와 주도주 관계가 있으므로 “회사 정보가 전혀 없다”는 뜻은 아니다. 부족한 것은 다음 의미 계층이다.

1. `stock_id`는 상장 종목 식별자다. 법인·발행사와 동일한 개념이 아니다.
2. 현재 종목명 관측은 이름의 출처와 관측 시각을 보존하지만 공식 사명 변경 유효기간이나 합병·분할 관계를 확정하지 않는다.
3. 주도주 관계는 저장돼 있지만 사건 주체·계약 당사자·수혜주·피해주·대상 회사 관계는 없다.
4. E-17 라벨 산출물은 테마 history를 분류하지만 `stock_id`·회사 역할·정규화된 사건 ID를 포함하지 않는다.
4-1. 그 라벨은 `research/ontology/labels.jsonl` 파일에만 있고 DB에 없다. migration 0001~0006 어디에도 라벨 테이블이 없으므로, 회사 확장 이전에 소재 유형을 조건으로 거는 질의 자체가 불가능하다.
5. 한 문장에 여러 사건이 있어도 현재는 문장 전체에 유형·확실성을 붙인다.
6. 계약 단계, 상대방, 국가, 프로젝트, 금액·통화가 구조화되지 않았다.
7. 같은 현실 사건이 여러 테마나 여러 날짜에 반복돼도 고유 사건과 후속 진행을 구분하지 않는다.
8. DailyFeaturedTheme는 테마·종목·설명 관계만 파싱됐고 사건 온톨로지는 적용되지 않았다. 더 중요한 것은 파싱이 원문 대부분을 버린다는 점이다 — 섹션 설명은 첫 줄 하나만 `DESCRIPTION` 관계로 남고 `▷` 상세 문단이 사라지며, 종목 등락률 표는 값으로 분해되지 않은 채 `THEME_STOCK.raw_text` 한 덩어리로 들어간다.
9. 테마 방향을 특정 회사의 실제 주가 방향으로 해석할 수 없다.

### 2.3 한화에어로스페이스 실측 예시

현재 로컬 온톨로지 라벨에서 `한화에어로스페이스` 문자열은 테마 기록 156건에 등장한다. 이 중 회사명이 사건 사유 본문에 직접 등장한 것은 13건이며, 나머지는 대부분 문장 끝 주도주 목록에 등장한다.

따라서 단순 문자열 검색은 다음 두 기록을 같은 회사 사건으로 섞는다.

- 회사가 직접 계약을 체결하거나 유상증자를 발표한 기록
- 지정학·정책 같은 테마 전체 사건에서 주도주로 열거된 기록

본 계획은 이 둘을 각각 `ACTOR` 또는 `ISSUER`와 `LEADER`로 분리한다.

### 2.4 DailyFeaturedTheme가 테마 history보다 나은 원천이다

"이 종목이 이날 왜 올랐는가"를 기준으로 두 원천을 같은 종목(신성델타테크)에 대해 실측했다.

| | 테마 history | DailyFeaturedTheme |
|---|---|---|
| 전체 규모 | 39,696건 · 4,384 사건일 | 4,655 게시물 · 2007-09-17~2026-08-14 |
| 거래일 커버 | 초기 연도 희소(2005년 68일) | 연 244~252일, 거의 전 거래일 |
| 해당 종목 등장 | 주도주 62일 · 관련주 716건 | 122일 |
| 사유 서술 | 한 줄 요약 | 섹션당 `▷` 상세 문단 3~5개(언론·증권사 리포트 인용) |
| 그날 실제 등락률 | 없음 | 있음 — 해당 종목 103일에 종가·등락률·거래량 |

주도주 필드는 2022년 무렵부터 채워지므로 테마 history로는 그 이전 구간의 주도 여부를 판정할 수 없다(해당 종목 2020~2022년 주도주 기록 0건, 관련주만 241건). DailyFeaturedTheme는 2007년부터 같은 밀도를 유지한다.

또한 이 구간에서는 등락률이 게시물 안에 있으므로 E-16 가격 corpus 결합 없이도 "얼마나 올랐는가"에 답할 수 있다. E-16 결합은 게시물이 다루지 않은 종목·기간과 사건 이후 추적에 필요하다.

---

## 3. 목표와 비목표

### 3.1 목표

- 회사명·과거 사명·종목코드·DART 법인코드를 하나의 회사 정체성으로 해석한다.
- 회사가 사건에서 맡은 역할을 근거 span과 함께 저장한다.
- 사건을 주체·행동·대상·단계·상대방·지역·프로젝트·금액으로 구조화한다.
- 동일 보도의 중복과 프로젝트의 후속 단계를 구분한다.
- 테마 history와 DailyFeaturedTheme에 같은 온톨로지 계약을 적용한다.
- 회사 중심 닫힌 질의 유형을 E-21에 추가한다.
- 답변마다 집계 단위, 표본 수, 기간, 근거 사건, 데이터 버전을 표시한다.
- 회사별 실제 결과 질문은 역사 가격·outcome이 준비된 범위에서만 연다.
- **최종적으로는 관계를 이어 타는 열린 질의를 연다.** 닫힌 17종은 도착점이 아니라 첫 단계다(3.3절).

### 3.2 비목표

- 자연어를 자유 SQL로 변환하지 않는다 — **단계 4 완료 전까지의 제약이며, 3.3절 전환 조건을 충족하면 해제 대상이다.**
- 검색된 원문만 보고 LLM이 사실·숫자·순위·확률을 만들지 않는다.
- 주도주로 등장했다는 사실을 사건 수혜주나 회사 자체 사건으로 자동 승격하지 않는다.
- 테마 상승·하락을 개별 종목 수익률로 바꾸지 않는다.
- 이름이 비슷하다는 이유만으로 회사를 자동 병합하지 않는다.
- 미래 주가·매수·매도·목표가 질문에 답하지 않는다.
- 유사사례는 E-19의 2인 블라인드 평가와 새 봉인 구간 검증 전에 열지 않는다.
- 원천 row나 기존 E-17 산출물을 덮어쓰거나 삭제하지 않는다.

### 3.3 열린 질의로 가는 전환 조건

닫힌 17종은 안전장치이지 목표가 아니다. 조합 질문은 미리 유형을 정해두는 방식으로 낼 수 없다 — 조합이 무한하기 때문이다.

```text
"폴란드와 계약한 회사 중 2차전지 테마에도 속한 곳은?"
"이 회사 경쟁사가 수주한 사건은?"
"정책 소재로 올랐다가 3개월 안에 실적 소재로 또 오른 테마는?"
```

지금 닫아 두는 이유는 열림이 부정확해서가 아니라 **탈 관계가 아직 없어서**다. 관계가 정의돼 있고 질문을 정확히 옮기면 열린 질의도 답이 데이터에서 그대로 따라 나온다. 현재 결손은 셋이다.

| 열린 질의에 필요한 것 | 현재 |
|---|---|
| 관계가 정의돼 있음 | 없음 — 회사 역할·사건 구조·상대방·금액이 단계 2~4 산출물이다 |
| 라벨이 정확함 | primary 유형 test split 77.8% |
| 질문 옮기기를 검증할 수 있음 | 미착수 |

다음을 모두 충족하면 열린 질의 도입을 검토한다. 충족 전에는 검토하지 않는다.

- 단계 4까지 완료돼 회사·역할·사건·단계·프로젝트·금액이 관계로 저장된다.
- 11.2절 기준으로 회사 역할 precision 95% 이상, 사건 단계 정확도 90% 이상, 중복 병합 pair precision 98% 이상을 유지한다.
- 열린 질의 결과에도 집계 단위·표본 수·근거 사건·데이터 버전이 붙는다.
- 질문을 옮긴 결과(생성된 질의)를 사람이 읽고 검증할 수 있는 형태로 남긴다.
- 열린 질의가 실패하면 닫힌 17종으로 내려가는 경로가 있다.

열린 질의를 열어도 17종은 지우지 않는다. 정확도가 측정된 지름길로 남긴다.

### 3.4 기존 E-21 단계와의 관계

이 확장이 기존 E-21 전체를 막지는 않는다.

- E-21 1단계의 현재 시장·테마·종목 질문은 기존 계산 값으로 먼저 출시할 수 있다.
- E-21 2단계의 소재 유형·과거 테마 집계도 현재 E-17 범위에서 별도 출시할 수 있다.
- 본 계획의 회사 직접 사건·역할·금액·Daily 질문은 E-21 2단계의 확장 범위로 순차 개방한다.
- E-21 3단계 유사사례 질문은 회사 온톨로지 완성 여부와 별개로 E-19 gate를 계속 적용한다.

---

## 4. 고객 질문 계약

### 4.0 확정된 질의 유형 17종 (2026-08-17 제품 결정)

답할 질문은 아래 17종으로 닫는다. 전부 구현 대상이며 축소하지 않는다. 묶음은 범위가 아니라 **순서**다 — 앞 묶음이 뒤 묶음의 자료·검증을 만든다.

**17종은 최종형이 아니라 1단계다.** 3.3절 전환 조건을 충족하면 관계를 이어 타는 열린 질의를 열고, 17종은 정확도가 측정된 지름길로 남긴다. 운영하며 자주 들어오는 질문을 보고 유형을 늘리는 것도 같은 이유로 정상 경로다.

**요청 방식은 자연어 입력 하나다(2026-08-17 제품 결정).** 날짜·종목·테마를 클릭해 고르는 조회 화면은 별도로 만들지 않는다. 17종 전부 8절 실행 구조를 거친다.

| # | 질의 ID | 고객 질문 | 필수 슬롯 | 집계 단위 | 선행 단계 | 묶음 |
|---|---|---|---|---|---|---|
| 1 | `DAY_MOVERS` | "이날 뭐가 올랐어·빠졌어?" | 날짜 | Daily 섹션 | 단계 1 | 1차 |
| 2 | `PERIOD_SUMMARY` | "이번 주 시장 어땠어?" | 날짜 범위 | Daily 섹션 | 단계 1 | 2차 |
| 3 | `STOCK_DAY_REASON` | "이 종목 이날 왜 올랐어·빠졌어?" | 종목, 날짜 | Daily 섹션 | 단계 1·2 | 1차 |
| 4 | `STOCK_TOP_MOVES` | "이 종목 최근 크게 움직인 날은?" | 종목, 기간 | Daily 종목 행 | 단계 1·2 | 2차 |
| 5 | `STOCK_THEME_MEMBERSHIP` | "이 종목 어떤 테마에 속해? 왜?" | 종목 | 현재 테마 구성 | 단계 2 | 1차 |
| 6 | `STOCK_COOCCURRENCE` | "같이 움직이는 종목은?" | 종목, 기간 | 고유 catalyst 공동 등장 | 단계 4 | 3차 |
| 7 | `THEME_MEMBERS` | "이 테마에 어떤 종목이 있어? 왜?" | 테마 | 현재 테마 구성 | 없음 | 2차 |
| 8 | `THEME_HISTORY` | "이 테마 과거에 뭘로 움직였어?" | 테마 | 원천 기록 | 선행 단계 | 2차 |
| 9 | `THEME_COMPARISON` | "두 테마 중 최근 어디가 셌어?" | 테마 2개 이상, 기간 | Daily 테마 등락률 | 단계 1 | 3차 |
| 10 | `THEME_FREQUENCY` | "자주 나온 테마는?" | 기간 | 고유 사건 | 단계 4 | 3차 |
| 11 | `CATALYST_THEME_REACTION` | "이 소재에 어떤 테마가 반응했어?" | 소재 유형 | 테마 반응 | 선행 단계 | 3차 |
| 12 | `CATALYST_FREQUENCY` | "이 소재 과거 몇 번 나왔어?" | 소재 유형, 기간 | 고유 사건 | 단계 4 | 3차 |
| 13 | `CATALYST_CERTAINTY` | "기대감이었어, 확정이었어?" | 소재 유형 또는 사건 | 고유 catalyst | 단계 4 | 3차 |
| 14 | `CATALYST_CONTINUATION` | "처음이야, 다시 나온 거야?" | 소재 유형 또는 테마 | 고유 catalyst | 단계 4 | 3차 |
| 15 | `COMPANY_DIRECT_EVENT` | "회사가 직접 한 일만" | 회사 | 고유 catalyst | 단계 3 | 3차 |
| 16 | `COMPANY_VALUE_SUMMARY` | "1조 넘는 수주 몇 건?" | 회사 또는 소재, 금액 조건 | 중복 제거된 금액 fact | 단계 4 | 3차 |
| 17 | `COMPANY_HISTORICAL_OUTCOME` | "그 사건 이후 흐름은?" | 회사 또는 사건, 기간 | outcome 관측 | 단계 6 | 3차 |

모든 유형은 **상승과 하락을 대칭으로 다룬다**. Daily 종목 행의 22.8%가 하락이며 섹션 머리글의 20.1%가 하락 사유다. 상승만 답하면 절반을 버린다.

1~4번과 9번이 쓰는 등락률은 원문 표에 적힌 값이라 오차가 없다. 11~14번은 자동 분류에 기대므로 유형 정확도 77.8%, 확실성 90.0%가 그대로 답의 상한이다. 방향 판정만은 99.6%다.

### 4.0.1 "오늘" 처리 규칙

DailyFeaturedTheme는 장 마감 후 발행된다. 그날 게시물이 아직 없는 시각에 오늘을 물으면 **실시간 값으로 대체하지 않는다**. 자료가 없다고 말하고 직전 거래일 답을 제시한다.

- 응답: `기록 없음` + "아직 오늘 특징테마가 발행되지 않았습니다" + 직전 거래일 결과
- 장중 실시간 계산값과 Daily 발행분을 같은 답에 섞지 않는다. 두 값의 출처와 확정 시점이 다르다.

### 4.1 회사 질의 상세

**구현 대상은 4.0절 17종뿐이다.** 그중 회사 질의는 세 개이며 아래가 그 상세 규칙이다. 각 유형은 허용 슬롯, 집계 단위, 필수 근거를 고정한다.

| 질의 ID | 4.0절 번호 | 고객 질문 예시 | 기본 집계 단위 | 필요한 구조 | 출시 조건 |
|---|---|---|---|---|---|
| `COMPANY_DIRECT_EVENT` | 15 | “회사가 직접 발표한 사건만 알려줘.” | 고유 catalyst | `ACTOR`·`ISSUER` 역할 | 회사 역할 완료 |
| `COMPANY_VALUE_SUMMARY` | 16 | “확정 수주액 합계는 얼마야?” | 중복 제거된 금액 fact | 금액·통화·단계·중복 제거 | 수치 exact-match gate 통과 |
| `COMPANY_HISTORICAL_OUTCOME` | 17 | “수주 뒤 T+5 실제 반응은 어땠어?” | 회사 또는 당시 주도주 outcome | E-16 가격·기업행위·benchmark | E-16 및 outcome gate 통과 |

### 4.1.1 17종에 넣지 않은 회사 질의 후보

2026-08-16 초안이 회사 질의 12종을 늘어놓았으나, 2026-08-17 제품 결정이 **전체를 17종으로 닫으면서** 그중 3종만 남았다. 나머지 9종은 폐기가 아니라 **대기**다. 세 갈래로 갈린다.

| 후보 ID | 처리 | 근거 |
|---|---|---|
| `COMPANY_DAILY_FEATURED` | **3번 `STOCK_DAY_REASON`이 흡수** | 같은 질문이다 — "이 종목이 이날 왜 나왔나". 집계 단위도 Daily 섹션으로 같다 |
| `COMPANY_COOCCURRENCE` | **6번 `STOCK_COOCCURRENCE`가 흡수** | 같은 질문이다. 회사 축과 종목 축을 따로 둘 이유가 없다 |
| `COMPANY_EVENT_STAGE` | **13번 `CATALYST_CERTAINTY`가 부분 흡수** | 확정·기대 구분은 13번이 답한다. `BID`·`SIGNED` 같은 단계 해상도는 17종 밖이다 |
| `COMPANY_APPEARANCE` | 17종 밖 · 유형 확대 후보 | 단계 3 산출물로 계산은 되지만 질문 유형으로 열지 않았다 |
| `COMPANY_THEME_ASSOCIATION` | 17종 밖 · 유형 확대 후보 | 5번은 **현재** 테마 구성이라 과거 연결 이력을 답하지 못한다 |
| `COMPANY_CATALYST_DISTRIBUTION` | 17종 밖 · 유형 확대 후보 | 단계 4 필요 |
| `COMPANY_COUNTERPARTY` | 17종 밖 · 열린 질의 후보 | 상대방·국가·프로젝트를 이어 타는 질문이라 3.3절 대상이다 |
| `COMPANY_IMPACT_HISTORY` | 17종 밖 · 유형 확대 후보 | 단계 4 + 회사 영향 축 필요 |
| `COMPANY_SIMILAR_CASE` | 17종 밖 · E-19 gate | 단계 7 |

이 9종은 `query_contracts.py` enum에 넣지 않는다. 넣으면 구현·gold set·채점 대상이 26종으로 벌어지고, 4.0절 제품 결정과 어긋난다. 여는 경로는 둘뿐이다 — 4.0절대로 **운영 중 실패 사유 집계를 보고 유형을 늘리거나**, 3.3절 전환 조건을 채워 **열린 질의를 열거나**.

### 4.2 집계 단위 규칙

“몇 건”이라는 표현은 다음 단위를 화면에 함께 표시한다.

- `원천 기록`: 테마 history row 또는 Daily 섹션 수
- `테마 반응`: 한 날짜·한 테마에서 관측된 반응 수
- `고유 사건`: 중복 보도를 합친 `catalyst_id` 수
- `프로젝트`: 여러 단계의 사건을 묶은 `project_id` 수

기본 회사 사건 집계는 `catalyst_id`를 사용한다. 원천 기록 수를 고유 사건 수처럼 표시하지 않는다.

### 4.3 공통 답변 구조

모든 회사 질문은 기존 FR-11 답변 블록을 유지한다.

1. 해석된 회사·기간·역할·소재·단계·지역 슬롯
2. 한 문장 요약
3. 집계 단위가 표시된 수치 요약
4. 근거 사건 목록
5. 제외·미제시 항목과 이유
6. dataset·company master·ontology·query 버전

---

## 5. 목표 개념 모델

### 5.1 회사와 종목을 분리한다

사용자가 질문하는 “회사”와 거래되는 “종목”은 별도 엔티티다.

- `Company`: 법인·발행사 정체성
- `Instrument`: 종목코드가 붙은 상장 증권
- 기존 `core.infostock_stocks`: 초기 Instrument 정본
- `CompanyInstrument`: 회사와 종목의 유효기간 관계
- `CompanyAlias`: 현재·과거 사명과 출처·유효기간

하나의 회사가 여러 종목을 가질 수 있고, 종목코드·사명은 시간에 따라 달라질 수 있다. 내부 `company_id`는 외부 코드나 이름을 의미로 인코딩하지 않는 안정적인 식별자를 사용한다.

### 5.2 현실 사건과 시장 반응을 분리한다

한 계약이 방산·우주항공·항공기부품 세 테마에 같은 날 기록될 수 있다. 계약은 현실 사건 하나지만 시장 반응은 세 개다.

- `Catalyst`: 현실에서 발생한 원자적 사건
- `ThemeReaction`: 특정 날짜·테마가 그 사건에 반응한 관측
- `SourceMention`: 인포스탁 history, Daily 섹션, 뉴스 등 원천 언급
- `Project`: 기대·입찰·본계약·납품 같은 여러 사건 단계를 묶는 장기 대상

같은 보도의 복제는 하나의 `catalyst_id`로 합친다. 기대에서 본계약으로 진행된 것은 같은 사건으로 덮어쓰지 않고 별도 catalyst를 만들고 같은 `project_id`와 `ADVANCES` 관계로 연결한다.

### 5.3 관계 구조

```mermaid
erDiagram
    COMPANY ||--o{ COMPANY_ALIAS : has
    COMPANY ||--o{ COMPANY_INSTRUMENT : issues
    INFOSTOCK_STOCK ||--o{ COMPANY_INSTRUMENT : identifies
    PROJECT ||--o{ CATALYST : progresses_through
    CATALYST ||--o{ CATALYST_COMPANY_ROLE : involves
    COMPANY ||--o{ CATALYST_COMPANY_ROLE : participates
    CATALYST ||--o{ CATALYST_PARTICIPANT : has
    ACTOR_ENTITY ||--o{ CATALYST_PARTICIPANT : participates
    CATALYST ||--o{ SOURCE_MENTION : evidenced_by
    CATALYST ||--o{ THEME_REACTION : causes_or_explains
    INFOSTOCK_THEME_HISTORY ||--o{ THEME_REACTION : observes
    DAILY_RELATION ||--o{ SOURCE_MENTION : supplies
    CATALYST ||--o{ CATALYST_VALUE : reports
```

### 5.4 일반 참여자

계약 상대방이 해외 정부·기관·개인일 수 있으므로 모든 참여자를 회사 테이블에 넣지 않는다. `ActorEntity`는 `COMPANY`, `GOVERNMENT`, `PUBLIC_INSTITUTION`, `PERSON`, `INTERNATIONAL_ORGANIZATION`, `COUNTRY`, `OTHER` 유형을 가진다. 회사형 참여자는 `company_id`를 함께 가리키고, 비회사 참여자는 별도 정규화 이름·alias·근거를 가진다.

`CatalystParticipant`는 주체·상대방·발표자·규제기관·대상을 표현한다. “폴란드와 체결한 계약”은 geography 문자열 검색만 하지 않고 폴란드 정부·기관 참여자 또는 정규화된 국가 관계로 찾는다.

### 5.5 회사 역할

역할은 한 회사에 여러 개 붙을 수 있으며 각 역할에 근거 span이 필요하다.

| 역할 | 의미 | 자동 생성 기준 |
|---|---|---|
| `ACTOR` | 발표·투자·개발·행동 주체 | 문장 주어와 행동 근거가 명시됨 |
| `ISSUER` | 실적·증자·상장 등 자본시장 사건의 발행사 | 회사명과 발행 행위가 명시됨 |
| `CONTRACTOR` | 계약을 수주·체결한 공급자 | 계약 행위와 회사명이 명시됨 |
| `COUNTERPARTY` | 계약·협력 상대방 | 상대 역할이 명시됨 |
| `TARGET` | 인수·제재·소송·규제 대상 | 대상 관계가 명시됨 |
| `BENEFICIARY` | 원문이 수혜를 명시한 회사 | “수혜” 근거가 직접 존재함 |
| `ADVERSELY_AFFECTED` | 원문이 피해·부담을 명시한 회사 | 부정 영향 근거가 직접 존재함 |
| `LEADER` | 원천의 당시 주도주 목록 | 기존 history leader row에서 결정론적으로 생성 |
| `RELATED` | 관련주·구성종목으로만 확인됨 | 기존 membership 또는 Daily 관계 |

`LEADER`나 `RELATED`를 근거 없이 `BENEFICIARY`로 바꾸지 않는다.

`COMPANY_DIRECT_EVENT`의 기본 역할은 `ACTOR`, `ISSUER`, `CONTRACTOR`, `TARGET`이다. `BENEFICIARY`, `ADVERSELY_AFFECTED`, `LEADER`, `RELATED`는 사용자가 해당 역할을 요청했을 때만 포함한다.

### 5.6 사건 필드

최소 사건 revision은 다음 필드를 가진다.

```text
catalyst_id
revision_no
occurred_on
known_at
primary_catalyst_type
catalyst_types
event_stage
certainty
novelty_type
action
object
project_id
geography_ids
officiality
continuation
ontology_version
transform_version
dataset_hash
content_hash
```

방향은 현실 사건 자체가 아니라 `ThemeReaction`과 `CatalystCompanyRole.impact`에 각각 둔다.

- 테마 반응: `UP`, `DOWN`, `MIXED`, `UNKNOWN`
- 회사 영향: `POSITIVE`, `NEGATIVE`, `MIXED`, `UNKNOWN`
- 실제 종목 결과: 별도 `Outcome`

### 5.7 단계와 프로젝트

단계는 사건 유형별 통제어휘와 인접 관계를 가진다. 수주·계약의 초기 단계는 다음과 같다.

```text
RUMOR
REVIEW
DISCUSSION
BID
SHORTLIST
PREFERRED_BIDDER
SIGNED
EXECUTING
COMPLETED
DELAYED
CANCELLED
UNSPECIFIED
```

단계가 다르면 별도 catalyst다. 같은 프로젝트의 진행인지 확인된 경우에만 `project_id`를 공유한다.

### 5.8 금액·수량 fact

금액 합계 질문은 원문 문자열을 직접 더하지 않는다. 다음 단위로 정규화한다.

```text
fact_type
reported_value
normalized_value
unit
currency
value_basis
effective_on
evidence_span
```

초기 `fact_type`은 `CONTRACT_VALUE`, `INVESTMENT_VALUE`, `CAPACITY`, `QUANTITY`, `STAKE_PERCENT`로 제한한다. 범위값·최대값·총사업비·회사 몫을 구분하지 못하면 합계에서 제외하고 미제시 사유를 표시한다.

---

## 6. 저장 모델 계획

정확한 DDL은 1단계 구현에서 확정하되 책임은 다음처럼 나눈다.

### 6.1 `core` 기준정보

- `core.company_entities`
- `core.company_aliases`
- `core.company_instruments`

기존 `core.infostock_stocks`와 `core.infostock_stock_name_observations`를 Instrument 원천으로 재사용한다. `company_entities`에 종목명 관측을 복사해 별도 정본을 만들지 않는다.

### 6.2 `ontology` 사건 구조

- `ontology.catalysts`
- `ontology.catalyst_revisions`
- `ontology.actor_entities`
- `ontology.actor_aliases`
- `ontology.catalyst_participants`
- `ontology.projects`
- `ontology.project_aliases`
- `ontology.geographies`
- `ontology.source_mentions`
- `ontology.source_mention_*` source별 typed link
- `ontology.catalyst_company_roles`
- `ontology.theme_reactions`
- `ontology.catalyst_values`
- `ontology.catalyst_relations`
- `ontology.artifacts`

모든 분류 결과는 revision append 방식으로 쓴다. 현재 projection을 편의상 만들 수 있지만 이전 revision을 삭제하지 않는다.

`geographies`는 ISO 국가 코드와 통제된 권역 계층을 사용한다. 회사 국적만 보고 사건 지역을 추정하지 않는다.

### 6.3 원천 연결

`source_mentions`에 검증할 수 없는 `source_kind + source_id` 다형 참조를 두지 않는다. 공통 mention metadata와 source별 typed link table을 분리해 실제 FK를 유지한다.

- `INFOSTOCK_THEME_HISTORY`
- `INFOSTOCK_DAILY_DESCRIPTION`
- `INFOSTOCK_DAILY_THEME_STOCK`
- `NEWS_CATALYST_EVIDENCE`
- `MANUAL_REVIEW`

예를 들어 history·Daily·news는 각각 `source_mention_history`, `source_mention_daily`, `source_mention_news` bridge에서 원천 PK를 참조한다. mention 하나에는 source link가 정확히 하나만 존재해야 한다.

각 mention은 source revision·원문 hash·문자 오프셋·수집 시각을 가진다. 원문을 ontology 테이블에 중복 저장하지 않는다.

### 6.4 기존 `event.events`와의 관계

`event.events`는 한 거래일의 한 테마·촉매 시장 움직임을 식별한다. `ontology.catalysts`는 여러 테마가 공유할 수 있는 현실 사건을 식별한다. 둘을 합치지 않는다.

장중 Event나 과거 테마 반응이 현실 사건에 정합되면 `catalyst_id`를 classification revision에 연결한다. 장후 분류 변경은 ADR-002대로 기존 `event_id`를 유지한다.

---

## 7. 변환 파이프라인

### 7.1 회사 master 구축

식별 우선순위는 다음과 같다.

1. 원천 종목 링크의 6자리 종목코드
2. 기존 `stock_id` 연결
3. DART `corp_code`와 종목코드 매핑
4. 해당 시점에 유효한 exact alias
5. 미해결

공백·법인 표기·시장 접미사 같은 안전한 정규화만 자동 적용한다. 편집거리나 임베딩으로 회사를 자동 연결하지 않는다. fuzzy 후보는 운영자 검수 제안으로만 사용한다.

**사명 이력의 정본은 KRX 일별매매의 종목명이다.** 인포스탁은 과거 기록의 종목명을 현재 이름으로 소급 정규화한 코드가 있어(012450은 2006년 기록에도 "한화에어로스페이스"로 적혀 있다) 그 원천만으로는 alias 유효기간을 만들 수 없다. E-16이 받아 둔 KRX 봉투(2010-01-04~)는 거래일마다 그날의 이름을 담으므로 이름의 시작·끝 거래일이 나온다. 인포스탁 관측은 KRX가 모르는 이름과 등장 횟수에만 쓴다. KRX 수집 시작 이전 구간은 첫 이름에 한해 인포스탁 관측으로 앞을 늘린다 — 뒤 이름을 늘리면 소급 정규화된 이름이 과거까지 유효해진다.

### 7.2 테마 history 처리

1. 기존 `parse_cause_sentence`로 주도주 괄호와 방향 꼬리를 분리한다.
2. 사유 본문을 복합 사건 절로 나눈다.
3. 기존 history leader는 `LEADER` 역할로 연결한다.
4. 각 절에서 회사·기관·국가·행동·대상·단계·금액 span을 추출한다.
5. 소재 유형·확실성·지속을 절 단위로 다시 계산한다.
6. 한 history row가 여러 catalyst를 가리키면 각각 별도 mention을 만든다.
7. 원 history row의 방향은 각각의 `ThemeReaction`에 보존한다.

### 7.3 DailyFeaturedTheme 처리

1. 현재 `DESCRIPTION`과 `THEME_STOCK` 관계를 재사용하되, 먼저 파싱 손실을 복구한다. `raw_body`를 다시 읽어 섹션 헤드라인과 `▷` 상세 문단을 각각 별도 관계로 보존한다. 지금은 섹션당 첫 줄만 남는다.
2. 종목 등락률 표를 종가·등락률·거래량·시가·고가·저가 값으로 분해해 저장한다. 표 열 구성이 다른 형식군은 분해하지 않고 원문만 보존한다.
3. 한 종목이 한 게시물의 여러 섹션에 등장하면 관계마다 해당 섹션 설명을 붙인다. 지금은 두 번째 관계부터 `description`이 비는 경우가 있다.
4. `DESCRIPTION`을 테마 섹션의 사건 텍스트로 취급한다.
5. `THEME_STOCK`은 `RELATED` 관계로 시작하며 원문이 역할을 명시할 때만 승격한다. 섹션 상세 문단에 종목명이 직접 등장하는 경우가 승격 근거다.
6. 자유 서술형 `테마시황`은 문장·절 분리 뒤 개별 catalyst mention으로 만든다.
7. 현재 `PARSE_PARTIAL` 1,294건을 형식군별로 분류하고, 지원하지 않는 형식은 원문 보존 후 서빙 대상에서 제외한다.
8. 긴 글 전체를 하나의 사건으로 분류하지 않는다.

재파싱은 `core.infostock_daily_post_revisions.raw_body` 저장분으로 수행한다. 외부 재수집을 하지 않는다.

### 7.4 규칙과 LLM의 책임

다음은 규칙 기반으로 처리한다.

- 종목코드와 source link 해석
- exact alias 연결
- 기존 주도주·관련주 역할
- 날짜·금액·통화의 명시 패턴
- 방향 꼬리와 확실성 표지
- 기존 통제어휘 키워드

LLM은 규칙으로 해석하지 못한 비정형 절의 구조 추출에만 선택적으로 사용한다.

- 출력은 허용된 ID·enum만 사용한다.
- 모든 값에 evidence span이 필요하다.
- 원문에 없는 회사·금액·단계는 생성할 수 없다.
- 계산·집계·중복 제거 점수·수익률은 생성하지 않는다.
- validation 실패 출력은 버리고 `UNRESOLVED`로 남긴다.
- model·prompt·schema·input hash를 보존한다.

### 7.5 사건 중복 제거와 수명주기

중복 후보 키는 다음 요소를 사용한다.

- 사건 날짜 또는 명시된 유효일
- 주체·상대방 회사 ID
- 행동·대상
- 프로젝트 ID
- 단계
- 정규화된 금액·수량
- 원문 fingerprint

원칙은 precision 우선이다.

- 같은 원문 복제나 같은 날 여러 테마의 동일 서술은 자동 병합 가능하다.
- 날짜가 다르거나 단계가 다르면 기본적으로 별도 catalyst다.
- 비슷한 수익률이나 같은 테마라는 이유로 병합하지 않는다.
- 애매한 후보는 `POSSIBLE_DUPLICATE`로 남기고 자동 집계에서 분리한다.
- 운영자 병합·분리 결과는 새 revision과 사유를 남긴다.

### 7.6 artifact 발행

서빙 가능한 artifact는 다음 버전과 hash를 고정한다.

- source dataset hash
- company master version
- alias version
- ontology vocabulary version
- transform version
- dedup policy version
- query contract version
- 생성 시각과 코드 commit

`latest` 같은 mutable 경로를 일반 사용자 API가 직접 읽지 않는다.

---

## 8. 자연어 질의 실행 구조

```text
질문 분류
회사·기간·역할·소재·단계 슬롯 해석
닫힌 QueryPlan 생성
결정론적 repository 질의
집계와 근거 목록 생성
문장 renderer
FR-11 답변 블록 반환
```

### 8.1 회사 슬롯 해석

회사는 다음 순서로 해석한다.

1. 종목코드 exact match
2. 현재 회사명 exact match
3. 질문 기간과 겹치는 과거 alias exact match
4. 복수 후보 반환
5. 후보가 없으면 질문 해석 실패

복수 후보를 LLM이 임의로 고르지 않는다. 사용자가 선택한 `company_id`를 해석 블록에 표시한다.

### 8.2 QueryPlan 예시

질문: “2024년 한화에어로스페이스가 직접 체결한 해외 계약만 보여줘.”

```json
{
  "queryType": "COMPANY_DIRECT_EVENT",
  "companyId": "<resolved-company-id>",
  "period": {"from": "2024-01-01", "to": "2024-12-31"},
  "roles": ["CONTRACTOR"],
  "catalystTypes": ["ORDER_CONTRACT"],
  "eventStages": ["SIGNED"],
  "geography": "NON_KR",
  "countUnit": "CATALYST"
}
```

LLM은 이 plan에 없는 필터를 추가하거나 SQL을 작성하지 않는다.

### 8.3 답변 실패

기존 세 가지 public 실패 사유를 유지하고 내부 세부 사유를 매핑한다.

| 내부 사유 | public 사유 | 처리 |
|---|---|---|
| `UNKNOWN_COMPANY` | 질문 해석 실패 | 회사 후보 또는 종목코드 입력 제안 |
| `AMBIGUOUS_ALIAS` | 질문 해석 실패 | 유효기간·종목코드 포함 후보 제시 |
| `NO_MATCHING_EVENT` | 기록 없음 | 기간·역할 범위 확대 제안 |
| `UNSUPPORTED_QUERY_TYPE` | 제품 범위 밖 | 지원 질문 예시 제시 |
| `OUTCOME_GATE_CLOSED` | 제품 범위 밖 | 과거 사건 목록만 대안으로 제시 |
| `SIMILARITY_GATE_CLOSED` | 제품 범위 밖 | 소재·단계 집계 질문 제시 |
| `INSUFFICIENT_EVIDENCE` | 기록 없음 | 불확실 기록을 수치에 포함하지 않았다고 명시 |

### 8.4 캐시와 재현성

질문 문자열이 아니라 해석된 QueryPlan을 cache key로 사용한다. key에는 company master·ontology·dataset·query contract version을 포함한다. 같은 plan과 같은 artifact는 같은 정렬·수치·근거 목록을 반환해야 한다.

---

## 9. 구현 단계

### 선행 단계. 기존 E-17 라벨 DB 적재

이 계획의 모든 단계는 기존 E-17 결과를 DB에서 join할 수 있다고 전제한다. 현재는 그렇지 않다. `label_theme_history.py` 산출물은 `research/ontology/labels.jsonl`에만 있고, 라벨을 담는 테이블이 migration에 없다. 따라서 회사 확장 이전에 테마 중심 질의조차 실행할 수 없다.

할 일:

- `ontology` 스키마와 테마 history 라벨 테이블을 추가한다. 행 단위는 `(history_id, vocabulary_version, transform_version)`이다.
- 유형 목록, primary 유형, 방향, 확실성, 지속 여부, evidence span을 저장한다. span은 `raw_text` 문자 오프셋 의미를 그대로 유지한다.
- 통제어휘 자체(`type_id`, `name_ko`, `description_ko`, `vocabulary_content_hash`)를 참조 테이블로 함께 적재한다.
- 적재는 덮어쓰기가 아니라 버전 append다. 같은 `history_id`에 이전 어휘 버전 라벨이 남아 있어야 한다.
- 적재 job은 재실행해도 같은 결과가 되어야 한다(idempotent).
- `dayjaview_api_reader`에 SELECT 권한만 부여한다.

완료 조건:

- "이 소재 유형에 과거 어떤 테마가 반응했는가"를 SQL 한 번으로 계산할 수 있다.
- 라벨 행마다 어휘 버전·변환 버전·content hash가 붙어 어떤 어휘로 분류했는지 재현된다.
- 파일 산출물과 DB 적재 결과의 행 수·분포가 일치한다.

### 단계 0. 질문 계약과 gold set 고정

질문 목록 자체는 2026-08-17 제품 결정으로 확정됐다(4.0절 17종, "오늘" 처리 규칙 포함). 이 단계에 남은 것은 그 목록을 기계 계약으로 고정하고 채점용 gold set을 만드는 일이다.

할 일:

- 4.0절 17종의 질의 ID·슬롯·집계 단위를 기계 enum으로 고정한다. **enum은 정확히 17개다** — 4.1절 회사 질의 3종은 그 안에 있고, 4.1.1절 후보 9종은 넣지 않는다.
- 유형마다 상승·하락 양쪽 표본을 넣는다. Daily 종목 행의 22.8%가 하락이므로 상승만 뽑으면 하락 답을 검증하지 못한다.
- 직접 사건, 주도주 전용, 과거 사명, 복합 사건, 금액, Daily 형식을 층화 표집한다.
- 발행 전 시각에 오늘을 묻는 회귀 fixture를 넣는다 — 직전 거래일로 대체하고 실시간 값을 섞지 않는지 본다.
- 표본군마다 최소 건수를 채운다. 무작위 표집만으로는 희소 구간이 측정 불가 상태로 남는다 — 기존 E-17 gold set 1,000건(dev 500·test 500)에서 인수합병 primary는 전체 245건(0.6%)이라 test split 기대 편입량이 약 3건이고, 구조조정·신용 171건과 콘텐츠 성과 170건도 약 2건씩이다. 이 수량으로는 해당 구간 정확도를 보고할 수 없다.
- 개발용과 최종 측정용 gold set을 분리한다.
- 회사 역할·사건 단계·중복 쌍은 2인 독립 라벨과 불일치 조정을 사용한다.
- 한화에어로스페이스처럼 “이름 등장”과 “회사 자체 사건”이 크게 다른 회귀 fixture를 포함한다.

완료 조건:

- 17종 전부에 기대 QueryPlan과 기대 답변 수치가 고정된다.
- test split은 transform 개선에 사용하지 않는다.

### 단계 1. DailyFeaturedTheme 파싱 확장

회사 질문 중 가장 많이 들어올 "이 종목이 이날 왜 올랐는가"에 가장 좋은 원천은 테마 history가 아니라 DailyFeaturedTheme다(2.4절 실측). 이 단계는 앞 단계의 회사 모델에 의존하지 않고 파서 확장만으로 끝나므로 회사 정체성보다 먼저 둔다.

할 일:

- 테마 섹션 본문을 문단 단위로 전부 보존한다. 현재 파서는 섹션당 첫 줄만 `DESCRIPTION` 관계로 남기고 `▷` 상세 문단을 버린다.
- 종목 등락률 표를 값으로 분해한다. 현재 `THEME_STOCK.raw_text`는 종가·등락률·거래량·시가·고가·저가가 탭으로 이어 붙은 한 덩어리라 수치 조건 질의에 쓸 수 없다.
- 한 종목이 한 게시물에서 여러 섹션에 등장할 때 각 관계에 해당 섹션 설명을 붙인다. 현재는 두 번째 관계부터 `description`이 비는 경우가 있다.
- `PARSE_PARTIAL` 1,294건을 형식군별로 감사하고 지원 형식을 늘린다.
- Daily section·description·theme-stock 관계를 catalyst mention으로 변환한다.
- Daily 전용 coverage·role·span 보고서를 만든다.

재수집은 필요 없다. `core.infostock_daily_post_revisions.raw_body`에 원문 HTML이 그대로 보존돼 있으므로 기존 저장분을 다시 파싱한다.

완료 조건:

- `STOCK_DAY_REASON`(3번) 질문이 날짜·테마·근거와 함께 그날 종가·등락률을 반환한다. 회사 역할은 단계 3이 붙이므로 이 단계의 완료 조건이 아니다.
- 섹션 상세 문단이 근거 span과 함께 조회된다.
- 부분 파싱 원문을 성공으로 오표시하지 않는다.

#### 단계 1에서 앞당겨 만든 것과 그 처리

`DAY_MOVERS`(질의 1번)를 읽기 모델·REST·화면까지 먼저 만들었다(`ab02b80`). 계획서 어느 단계의 할 일도 아니었고, 자연어 입력 결정 이전이라 날짜를 고르는 조회 화면 형태였다. 다음과 같이 정리한다.

| 산출물 | 처리 |
|---|---|
| `packages/infostock/daily_read.py` | 단계 5의 `DAY_MOVERS` QueryPlan 조회 함수로 흡수한다 |
| 응답 데이터 구조·계약 스키마 | 답변 블록의 일부로 유지하되 4.3절 필수 항목을 채운다 |
| 섹션·테마·종목 렌더링 | 답변 표시에 재사용한다 |
| `GET /v1/daily/movers` 공개 엔드포인트 | 밖으로 열지 않는다. 자연어 질의 하나만 공개 표면으로 둔다 |
| 날짜 선택 화면·홈 진입 링크 | 제거한다. 요청 방식은 자연어 입력 하나다(4.0절) |

현재 이 응답에는 dataset·parser 버전이 없어 13절 조건을 어긴다. 단계 5에서 답변 블록을 만들 때 함께 채운다. 지금 붙이면 형태가 바뀌며 두 번 하게 된다.

### 단계 2. 회사 정체성과 alias

할 일:

- 기존 `stock_id`를 Instrument로 재사용한다.
- 회사·alias·회사-종목 유효기간 모델을 추가한다.
- KRX 종목코드와 DART `corp_code`를 외부 식별자로 연결한다.
- 과거 사명·상장폐지·합병·분할을 revision으로 저장한다.
- 미해결 과거 주도주 90건은 임의 연결하지 않고 검수 대상으로 남긴다.

완료 조건:

- 현재 이름·과거 이름·종목코드가 같은 `company_id`로 재현된다.
- 특정 시점에 유효하지 않은 alias로 자동 연결되지 않는다.

### 단계 3. history 회사 역할 연결

할 일:

- 기존 history leader와 membership을 회사에 연결한다.
- 사유 본문 회사 mention과 주도주 목록 mention을 분리한다.
- `ACTOR`, `ISSUER`, `CONTRACTOR`, `COUNTERPARTY`, `TARGET`, `BENEFICIARY`, `ADVERSELY_AFFECTED`, `LEADER`, `RELATED`를 추출한다.
- 역할별 evidence span과 resolution status를 저장한다.

완료 조건:

- `COMPANY_DIRECT_EVENT`(15번) 질의를 결정론적으로 계산할 수 있다.
- 11.4절 검증 예시 4문항이 서로 다른 답을 낸다 — 특히 "언급된 기록 수"와 "직접 행동한 고유 사건 수"가 갈린다. 이 둘은 질의 유형이 아니라 같은 저장 구조를 세는 단위 차이이며, 유형으로 여는 것은 4.1.1절 대기 항목이다.
- `LEADER`만 있는 기록이 회사 자체 사건 집계에 들어가지 않는다.

### 단계 4. 사건 구조·단계·중복 제거

할 일:

- 복합 문장 절 분리와 catalyst revision을 구현한다.
- 단계·프로젝트·상대방·지역·금액을 추출한다.
- 같은 보도 중복과 프로젝트 후속 단계를 구분한다.
- 원천 기록·테마 반응·고유 사건·프로젝트 집계 단위를 모두 제공한다.

완료 조건:

- 수주 기대와 본계약이 별도 catalyst로 보존되고 같은 프로젝트로 연결된다.
- 동일 계약의 여러 테마 기록이 고유 사건 집계에서 한 번만 계산된다.

### 단계 5. E-21 질의 17종

할 일:

- 질의 classifier와 slot resolver를 구현한다. 17종 전부이며 회사 질의 3종(4.1절)이 그 안에 있다.
- QueryPlan별 repository 함수를 만든다. `DAY_MOVERS`는 단계 1에서 만든 `daily_read.py`를 흡수한다.
- 답변 수치·근거·미제시 사유를 API 계약에 추가한다.
- 모든 답변에 dataset·parser·어휘·query 버전을 붙인다(13절 조건).
- 자연어 입력 하나만 공개 표면으로 둔다. 단계 1에서 앞당겨 만든 조회 화면과 공개 엔드포인트는 제거한다.
- **Daily 조회 기준을 발행일에서 거래일로 바꾼다**(아래 단서).
- `/research` 예시 질문을 현재 열린 유형만 반영하도록 갱신한다.
- 직전 회사·기간 슬롯을 이어받을 때 해석 블록에 표시한다.
- **3.3절 전환 조건 5개의 충족 여부를 표로 갱신한다**(13절 마지막 조건). 이 단계에서 단계 4 완료·11.2절 수치·근거 표시·질의 검증 형태가 모두 확정되므로, 그 시점의 실제 값으로 3.3절 표를 고친다. 이후 단계 6·7에서 값이 바뀌면 같은 표를 다시 고친다.

완료 조건:

- 같은 QueryPlan이 같은 답을 반환한다.
- 질문 해석 실패와 기록 없음이 구분된다.
- 회사 역할·집계 단위·표본 수가 화면에 표시된다.
- 17종 어디에도 걸리지 않는 질문이 `질문 해석 실패`로 끝나고, 그 사유가 유형 확대 판단에 쓸 수 있게 집계된다(질의 원문은 저장하지 않는다).
- 밀린 발행분 3일이 각각 제 거래일로 갈라져 조회된다.

#### 거래일과 발행일이 어긋나는 3일

`answer_review_queue`의 이중 대조가 찾은 결함이다. `core.infostock_daily_post_revisions.published_date`는 인포스탁 `sendDate`이며 거의 항상 거래일과 같지만, 밀린 발행분을 하루에 몰아 올린 날이 있다. 그날 `DAY_MOVERS`는 여러 거래일 내용을 한 답에 섞는다.

| 발행일 | 게시물 | 제목이 가리키는 거래일 |
|---|---|---|
| 2012-01-06 | 2건 | 1/5, 1/6 |
| 2012-08-30 | 3건 | 8/28, 8/29, 8/30 |
| 2018-08-22 | 2건 | 08/21, 08/22 |

전체 발행일 4,651일 중 **3일**이고 2019년 이후로는 0일이다. 세 건 모두 제목 `[M/D]`로 거래일을 정확히 가를 수 있다.

처리 규칙:

- 제목에 `[M/D]`가 있으면 그 날짜를 거래일로 쓰고, 없으면 발행일을 그대로 쓴다. 확인 가능한 1,862건 중 1,859건(99.8%)이 두 값이 같으므로 이 규칙은 나머지 날짜의 결과를 바꾸지 않는다.
- 제목 날짜 표기는 2019년부터 사라졌다(2014~2017년 100% → 2019년 이후 0%). 따라서 앞으로 같은 일이 생기면 제목으로 복구할 수 없다.
- 그래서 **한 발행일에 게시물이 여럿인데 거래일로 가르지 못하면 답변에 그 사실을 표시한다.** 지금 수집본에서는 이 조건이 한 번도 걸리지 않지만, 조용히 섞이는 것만은 막는다.

### 단계 6. 수치·실제 결과 질문

할 일:

- 금액·통화·범위·회사 귀속분을 정규화한다.
- E-16 일봉과 기업행위 보정 결과를 회사·종목·사건 시점에 연결한다.
- 테마 방향과 개별 종목 실제 수익률을 별도 필드로 표시한다.
- 표본 수·eligible count·observed count·누락 사유를 보존한다.

완료 조건:

- 금액 합계와 수익률 집계가 중복 사건을 포함하지 않는다.
- 가격 누락을 0으로 바꾸지 않는다.
- 결과 질문은 E-16 gate를 통과한 범위에서만 열린다.

### 단계 7. 유사사례와 운영 승격

할 일:

- 회사·역할·단계·프로젝트 특징을 E-19 검색 후보에 추가한다.
- M-TXT·ontology·hybrid를 동일 fold에서 비교한다.
- 승인 artifact만 production registry에 승격한다.
- 낮은 confidence·미해결 alias·중복 후보를 운영자 검수로 보낸다.

완료 조건:

- E-19의 2인 블라인드와 새 봉인 구간 gate를 통과한다.
- 미승인 artifact나 mutable `latest`를 API가 읽지 않는다.

### 단계 요약과 결정 주체

| 순서 | 단계 | 무엇이 열리는가 | 결정·수행 주체 |
|---|---|---|---|
| 1 | 선행 | 테마 중심 질의가 SQL로 가능해진다 | 구현 |
| 2 | 0 | 답할 질문 목록과 채점 기준이 확정된다 | **제품 결정** + 라벨 검수 인력 |
| 3 | 1 | 이날 이 종목이 왜 올랐는지를 상세 소재와 실제 등락률로 답한다 | 구현 |
| 4 | 2 | 사명이 바뀌어도 같은 회사로 해석된다 | 구현 |
| 5 | 3 | 회사 자체 사건과 주도주 전용 기록이 분리된다 | 구현 |
| 6 | 4 | 중복 집계가 사라지고 계약 단계가 구분된다 | 구현 |
| 7 | 5 | 회사 질문에 근거 있는 답이 나간다 | 구현 |
| 8 | 6 | 금액 합계와 실제 수익률 질문이 열린다 | 구현 (E-16 gate 종속) |
| 9 | 7 | 유사사례 검색에 회사·역할이 반영된다 | 구현 (E-19 gate 종속) |

선행 단계·단계 0·단계 1은 서로 의존하지 않으므로 병행할 수 있다. 단계 1은 파서 확장만으로 끝나고 회사 모델을 전제하지 않는다. 단계 2 이후는 앞 단계 완료 조건을 전제한다.

단계 0에는 사람이 정답을 붙이는 작업이 포함되므로 착수 전에 검수 담당과 분량을 확정한다. LLM 초안 후 사람 검수 방식을 쓰더라도 최종 라벨 책임은 사람에게 남긴다.

---

## 10. 코드·산출물 위치

구현 시 다음 위치를 기준으로 한다.

`+`는 이미 만든 것, `-`는 아직 없는 것이다(2026-08-17 워킹트리 기준). E-17에서 온 `vocabulary.py`·`transform.py`·`labeling.py`·`models.py`·`postgres.py`·`label_theme_history.py`는 이 확장의 기반이므로 목록에 다시 적지 않는다.

```text
packages/ontology/
+ company_entities.py        단계 2
+ company_postgres.py        단계 2
+ krx_names.py               단계 2
+ company_roles.py           단계 3
- participants.py            단계 4
- projects.py                단계 4
- event_structure.py         단계 4
- event_dedup.py             단계 4
- query_contracts.py         단계 0 — 17종 enum. 단계 0 잔여의 첫 항목이다

apps/worker-batch/ontology/
+ load_theme_catalyst_labels.py   선행
+ build_query_goldset.py          단계 0 겹 A
+ build_goldset_supplement.py     단계 0 겹 C 보강
+ build_blind_review_input.py     단계 0 겹 C 눈가림 입력
+ merge_blind_review.py           단계 0 겹 C 독립 판정 대조
+ verify_answers.py               단계 0 겹 B 두 경로 대조
+ score_gold_set.py               채점(E-17에서 이어짐, review_status 처리 추가)
+ audit_keyword_contexts.py       어휘 충돌 감사(E-17에서 이어짐)
+ build_krx_name_windows.py       단계 2
+ build_company_master.py         단계 2
+ label_company_events.py         단계 3
- label_daily_events.py           단계 1 잔여 — Daily 관계를 catalyst mention으로
- publish_company_ontology.py     단계 5

tests/ontology/
+ goldset_v1.tsv             겹 C 원본 1,000건 (5열·HUMAN_CONFIRMED)
+ goldset_supplement.tsv     겹 C 보강 910건 (6열)
+ query_goldset.tsv          겹 A 1,150문장 (split 열)
+ test_goldset.py            세 gold set 무결성
+ test_krx_names.py          단계 2
+ test_company_entities.py   단계 2
+ test_company_postgres.py   단계 2
+ test_company_roles.py      단계 3
+ test_company_role_postgres.py  단계 3
- test_event_structure.py    단계 4
- test_event_dedup.py        단계 4
- test_company_queries.py    단계 5

research/ontology/          (gitignore — 원문이 실려 로컬 전용)
+ krx_name_windows.json      단계 2
+ company_master_report.json 단계 2
+ company_labels.jsonl       단계 3
+ company_role_report.json   단계 3
+ answer_review_queue.json   단계 0 겹 B 불일치 큐
+ goldset_score{,_dev,_test}.json  채점 결과
- daily_coverage_report.json 단계 1 잔여
- company_ontology_artifact.json  단계 5

infra/migrations/
+ 0007_theme_catalyst_labels.sql   선행
+ 0008_daily_relation_details.sql  단계 1
+ 0009_company_identity.sql        단계 2
+ 0010_history_company_roles.sql   단계 3
- 단계 4의 catalyst·participant·project 테이블은 그 뒤 번호로 나간다
```

`0007_theme_catalyst_labels.sql`은 선행 단계의 기존 E-17 라벨 적재용이며, 회사 확장 migration보다 앞선다. 적재 job은 `apps/worker-batch/ontology/load_theme_catalyst_labels.py`로 두고 `label_theme_history.py`의 파일 산출 책임과 분리한다.

단계 1의 Daily 파싱 확장은 기존 `packages/infostock/daily.py`의 `parse_daily_html_body`를 고치고, 늘어난 필드를 `packages/infostock/models.py`의 `DailyRelation`에 더한다. 새 Daily 파서 모듈을 만들지 않는다. 관계 유형과 시세 컬럼은 `0008_daily_relation_details.sql`이 담으며, 회사 정체성 migration은 그 뒤 `0009`가 된다. `0009`는 단계 2 범위인 회사·alias·회사-종목·이력·검수만 담는다. 단계 3의 history 회사 역할 테이블은 `0010_history_company_roles.sql`이고, 단계 4의 catalyst·participant·project 테이블은 그 뒤 번호로 나간다.

현재 `packages/ontology`를 확장한다. 같은 분류 책임을 별도 최상위 패키지로 복제하지 않는다.

tracked gold set에는 source key와 라벨만 둔다. 저작권 원문과 key join 결과는 gitignore된 `research/ontology/`에만 둔다.

---

## 11. 검증 계획과 출시 gate

### 11.1 gold set 구성

다음 표본군을 분리해 포함한다.

- 회사가 사건 주체인 문장
- 회사가 주도주 목록에만 있는 문장
- 회사가 수혜·피해 대상으로 명시된 문장
- 과거 사명·동명이인·이름 변형
- 한 문장에 여러 회사·여러 사건이 있는 문장
- 기대·입찰·본계약·취소 단계
- 금액·통화·범위 표현
- 같은 사건의 여러 테마·날짜 중복
- Daily 정형 표와 자유 서술

각 표본군은 test split 기준 **최소 30건**을 채운다. 30건 미만인 구간은 정확도를 보고하지 않고 `측정 불가`로 표시하며, 해당 구간에 의존하는 질문 유형은 열지 않는다.

설계·개선용 dev와 최종 측정용 test를 분리한다. test 불일치를 보고 어휘나 규칙을 수정한 뒤 같은 test 점수를 최종값으로 다시 사용하지 않는다.

기존 E-17에서 이 분리 효과가 실측됐다. 어휘 v1.2.0 기준 primary 엄격 정확도는 dev split 87.8%, test split 77.8%로 10.0%p 차이가 난다. dev를 보며 어휘를 고친 결과이므로 **77.8%가 실제 성능**이다. 회사 온톨로지에서도 dev 점수를 승격 근거로 쓰지 않는다.

### 11.1.1 gold set 세 겹과 규모

정답지는 하나가 아니라 세 겹이며 채점 대상이 다르다. C가 A·B의 바닥이다 — C가 틀리면 A·B가 맞아도 답이 틀린다.

| 겹 | 채점 대상 | 규모 | 산출 | dev/test 표시 |
|---|---|---|---|---|
| A. 질문 해석 | 문장 → 질의 유형·슬롯 | 1,150문장 | `tests/ontology/query_goldset.tsv` | `split` 열 |
| B. 답변 수치 | QueryPlan 결과가 맞나 | 유형당 8건, 약 136건 | `verify_answers.py` 대조 + `research/ontology/answer_review_queue.json` | 해당 없음 |
| C. 온톨로지 라벨 | 소재 유형·방향·확실성 | 기존 1,000건 + **910건 보강** | `goldset_v1.tsv` + `goldset_supplement.tsv` | 행 번호 짝/홀 |

A 표본군은 22개다 — 상승·하락 대칭이 필요한 5종(`DAY_MOVERS`, `PERIOD_SUMMARY`, `STOCK_DAY_REASON`, `STOCK_TOP_MOVES`, `THEME_COMPARISON`)은 방향별로 나누고 나머지 12종은 하나씩이다. 군마다 test 30 + dev 15에 실패·난이도 케이스 160문장(17종 밖 질문 60, 과거 사명·동명이인·상대 날짜·복수 슬롯 80, 발행 전 "오늘" 20)을 더한다.

**A의 dev/test는 파일 안 `split` 열로 표시한다.** C가 쓰는 짝/홀 행 규칙(`score_gold_set.py --subset`)은 1:1 비율만 표현할 수 있어 A의 30:15에 쓸 수 없다. 실패·난이도 160문장은 회귀 고정용이라 전부 `test`다 — 이 문장들을 보며 규칙을 고치면 회귀로서 값어치가 없어진다.

C의 보강분은 실측으로 정한 규모다. test 500건 표본에서 유형당 30건을 못 채우는 유형이 28종 중 23종이며(콘텐츠 성과 기대 2.1건, 구조조정·신용 2.2건, 인수합병 3.1건 …), dev까지 채우는 데 필요한 추가 라벨이 844건으로 계산됐다. 실제 표집은 희소 유형 하한을 맞추느라 910건이 됐다.

B는 사람이 전건을 보지 않는다. 같은 답을 두 경로로 계산해(제품 읽기 모델 ↔ 원문 재파싱) 일치하면 통과시키고 불일치만 검수 큐로 올린다. 그래서 B에는 고정 정답 파일이 없고 대조 스크립트와 불일치 큐가 그 자리를 대신한다. 두 경로가 같은 버그를 공유하면 못 잡는다는 한계는 C가 받는다.

### 11.1.2 라벨 출처 표시와 승격 제한

2026-08-17 제품 결정으로 **AI 초안을 사람 검수 없이 먼저 쓴다.** 속도를 얻는 대신 정답지가 AI의 오차를 그대로 물려받으므로, 그 상태로 잰 정확도를 승격 근거로 쓰면 안 된다. 다음 규칙으로 이 선택을 되돌릴 수 있게 유지한다.

- 세 겹 모두 행마다 `review_status`를 둔다. 값은 셋이다.

| 값 | 뜻 | 승격 판정 |
|---|---|---|
| `AI_DRAFT` | 현재 transform의 자기 출력이거나 스크립트가 만든 초안 | 쓰지 않는다 |
| `AI_CROSS_CHECKED` | 독립 판정(원문만 본 별도 경로)이 같은 답을 낸 행 | **쓰지 않는다** |
| `HUMAN_CONFIRMED` | 사람이 원문을 보고 확정한 행 | 이것만 센다 |

- `AI_CROSS_CHECKED`는 "사람이 나중에 봐도 된다"는 뜻이지 "맞다"는 뜻이 아니다. 두 경로의 오차가 상관되면 같이 틀린다. 검수 큐 우선순위를 낮추는 용도이며 게이트에서는 `AI_DRAFT`와 같이 취급한다.
- 채점 결과 JSON에 표본군별 `humanConfirmedRatio`와 `reviewStatusCounts`를 반드시 넣는다.
- **11.2절 승격 기준은 `HUMAN_CONFIRMED` 행만으로 판정한다.** 검수된 행이 없는 표본군은 정확도를 `측정 불가`로 표시하고 해당 질문 유형을 열지 않는다.
- 화면과 답변에는 그 유형이 아직 사람 검증 전임을 표시한다.
- 사람 검수는 나중에 행 단위로 올릴 수 있다. 비율이 오르면 잠긴 유형이 자동으로 열린다.

**`goldset_v1.tsv` 1,000건은 예외다.** 이 파일은 `review_status` 열이 생기기 전 형식(5열)이고, E-17에서 원문만 보고 붙인 블라인드 수동 라벨이다. `score_gold_set.py`가 5열 행을 `HUMAN_CONFIRMED`로 읽어 채점한다(`_read_goldset`). 따라서 C겹은 이미 검수된 1,000건을 가지고 있고, 보강 910건만 검수 대기다. 새로 만드는 파일은 6열 형식을 쓴다.

즉 `AI_DRAFT`·`AI_CROSS_CHECKED`만으로도 개발·회귀 테스트는 굴러가지만 **출시 게이트는 통과하지 못한다.** 게이트를 열려면 검수가 필요하다.

### 11.1.3 질문 문장 초안의 한계

A의 질문 문장을 AI가 만들면 만든 쪽 말투에 치우친다. 실제 사용자는 더 짧고 불완전하게 친다.

```text
초안이 쓸 법한 문장   "신성델타테크의 2026년 6월 29일 상승 사유는?"
사용자가 실제로 칠 문장 "신성델타 왜 올랐어"  "신성델타테크 6/29"
```

따라서 A는 출시 전 최소 기준이지 최종형이 아니다. 운영을 시작하면 `질문 해석 실패`로 끝난 질문의 사유 집계(단계 5 완료 조건)를 근거로 표본을 교체·보강한다. 질의 원문은 저장하지 않으므로 집계된 실패 사유와 유형만 쓴다.

### 11.2 최소 자동 승격 기준

| 항목 | 기준 | 실패 처리 |
|---|---|---|
| 코드 기반 회사 연결 precision | 100% | import 실패 |
| alias 자동 연결 precision | 99.5% 이상 | 애매한 alias 자동 연결 중지 |
| 회사 역할 precision | 95% 이상 | 미달 역할 서빙 제외 |
| 핵심 역할 macro F1 | 90% 이상 | 규칙·gold set 재검토 |
| 사건 단계 정확도 | 90% 이상 | 단계 질문 잠금 |
| 금액·통화 exact match | 98% 이상 | 합계 질문 잠금 |
| 중복 병합 pair precision | 98% 이상 | 자동 병합 범위 축소 |
| 중복 병합 pair recall | 90% 이상 | 누락 경고와 운영 검수 |
| evidence span 유효성 | 100% | 해당 fact 폐기 |
| 사용자 답변 근거 coverage | 100% | 답변 거부 |
| QueryPlan 계산 fixture | 100% | 배포 차단 |
| 동일 artifact 재현성 | hash 일치 | 배포 차단 |

precision을 recall보다 우선한다. 연결하지 못한 기록은 허용하지만 잘못된 회사에 연결한 기록은 자동 서빙하지 않는다.

위 기준은 모두 11.1.2절에 따라 `HUMAN_CONFIRMED` 행만으로 판정한다. `AI_DRAFT`만 있는 표본군은 어떤 수치가 나오든 통과로 보지 않는다.

### 11.3 테스트 계층

- unit: 이름 정규화, alias 유효기간, 절 분리, 역할, 단계, 금액, span
- invariant: 한 role fact에 회사와 근거가 반드시 존재
- property: 입력 순서가 바뀌어도 canonical 결과가 같음
- integration: PostgreSQL import·revision·idempotency·FK
- regression: 한화에어로스페이스 직접 사건과 주도주 전용 기록 분리
- query contract: 질문, 해석 슬롯, 집계, 정렬, 근거 사건 exact match
- leakage: 사건 분류·중복 판단에 미래 가격을 사용하지 않음
- rights: 허용된 원문만 근거로 제공하고 원문 전체 재배포 금지
- performance: 전체 corpus 기준 회사 질의 P95 2초 이내

### 11.4 사용자 검증 예시

아래 질문이 서로 다른 답을 내야 한다.

1. “한화에어로스페이스가 언급된 기록은 몇 건인가?”
2. “한화에어로스페이스가 직접 행동한 고유 사건은 몇 건인가?”
3. “한화에어로스페이스가 주도주로만 등장한 테마 반응은 몇 건인가?”
4. “확정된 해외 수주만 몇 건인가?”

각 답변은 원천 기록·테마 반응·고유 사건 중 어떤 단위를 센 것인지 명시해야 한다.

---

## 12. 위험과 대응

| 위험 | 영향 | 대응 |
|---|---|---|
| 과거 사명 오연결 | 다른 회사 사건 혼입 | 시점 유효 alias와 종목코드 우선, fuzzy 자동 연결 금지 |
| 주도주를 사건 주체로 오해 | 회사 자체 사건 수 과대계상 | `LEADER`를 독립 역할로 고정 |
| 복합 문장 전체 분류 | 회사·단계·확실성 혼합 | 절 단위 catalyst 생성 |
| 같은 계약 중복 집계 | 빈도·금액 과대계상 | `catalyst_id`와 project lifecycle 분리 |
| 후속 단계를 중복으로 병합 | 기대와 체결 이력 소실 | 단계 변경은 별도 catalyst와 `ADVANCES` 관계 |
| Daily 긴 본문 오분류 | 관련 없는 회사·사건 연결 | 섹션·문장·절 분리, 미지원 형식 제외 |
| 테마 방향을 회사 수익률로 오해 | 잘못된 투자 해석 | ThemeReaction·CompanyImpact·Outcome 분리 |
| LLM 근거 없는 구조 생성 | 허위 회사·금액 | enum·span validation, 실패 출력 폐기 |
| test set 반복 사용 | 품질 과대평가 | dev/test 분리와 새 봉인 표본 |
| 질문 로그 축적 | 제품 privacy 위반 | 질의 원문 저장 금지, 실패 사유 집계만 허용 |

---

## 13. 최종 완료 조건

- [ ] 기존 E-17 라벨이 버전과 함께 DB에 적재돼 SQL로 join된다.
- [ ] gold set 각 표본군이 test split 기준 30건 이상이거나 `측정 불가`로 표시된다.
- [ ] gold set 행마다 `review_status`가 있고(`goldset_v1.tsv` 5열 형식은 11.1.2절 예외), 승격 판정이 `HUMAN_CONFIRMED` 행만 쓴다.
- [ ] 겹 A gold set에 `split` 열이 있고 test split을 규칙 개선에 쓰지 않는다.
- [ ] 회사·종목·alias·유효기간이 분리돼 있다.
- [ ] 기존 `stock_id`와 history leader·Daily relation을 재사용한다.
- [ ] 회사 역할과 근거 span이 revision으로 저장된다.
- [ ] 회사 자체 사건과 주도주 전용 기록이 분리된다.
- [ ] 현실 사건과 테마 반응이 서로 다른 식별자를 가진다.
- [ ] 같은 보도 중복과 프로젝트 후속 단계가 구분된다.
- [ ] DailyFeaturedTheme가 섹션·사건 단위로 온톨로지화된다.
- [ ] Daily 섹션 상세 문단이 보존되고 종목 등락률이 값으로 분해돼 있다.
- [ ] Daily 조회가 발행일이 아니라 거래일 기준이며, 가르지 못하는 날은 답변에 표시된다.
- [ ] 4.0절 질의 유형 17종이 전부 열려 있고 상승·하락을 대칭으로 답한다.
- [ ] 발행 전 시각의 "오늘" 질문이 직전 거래일로 답하고 실시간 값을 섞지 않는다.
- [ ] 닫힌 회사 질의 유형과 QueryPlan이 기계 계약으로 고정된다.
- [ ] 답변 수치는 결정론적으로 계산되고 표본 수·집계 단위가 표시된다.
- [ ] 모든 사용자 답변에 근거 사건과 artifact version이 있다.
- [ ] 질문 해석 실패·기록 없음·제품 범위 밖이 구분된다.
- [ ] 미래 예측·매매 판단은 계속 차단된다.
- [ ] 3.3절 열린 질의 전환 조건이 충족 여부와 함께 최신 상태로 유지된다.
- [ ] 회사 결과 질문은 E-16, 유사사례 질문은 E-19 gate를 각각 지킨다.
- [ ] 11절 품질 gate와 전체 관련 테스트가 통과한다.

이 조건을 모두 충족해야 “회사 이름을 검색할 수 있다”가 아니라 “회사의 역할과 사건을 이해해 근거 있는 자연어 답변을 제공한다”고 판단한다.
