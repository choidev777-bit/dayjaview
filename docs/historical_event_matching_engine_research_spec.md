# DAYJAVIEW 과거 유사사례 매칭 엔진 연구·구현 명세

- 문서 상태: 연구 시작 가능
- 최초 작성일: 2026-08-13
- 문서 목적: 다른 Codex 세션 또는 연구자가 이 문서 하나를 전달받아 데이터 감사부터 최종 엔진 구현까지 수행할 수 있게 한다.
- 엔진명: **Historical Event Matching Engine**
- 한국어명: **과거 유사사례 매칭 엔진**

---

## 0. 다음 세션에게 주는 실행 지시

이 문서를 처음부터 끝까지 읽은 뒤 연구를 시작한다.

목표는 미리 정한 유사도 공식을 구현하는 것이 아니다. 인포스탁 과거 테마 이벤트, 뉴스·공시, 당시 종목 구성, 조정 가격 데이터를 시점 기준으로 복원하고 여러 후보 방법을 동일한 조건에서 비교해 **표본 외 예측력, 사건 관련성, 안정성, 설명 가능성이 가장 좋은 방법**을 선택하고 구현하는 것이다.

다음 원칙을 지킨다.

1. 합리적인 가정으로 진행하되 모든 가정과 데이터 한계를 기록한다.
2. 데이터 수집 전 이용약관, 접근 권한, 라이선스와 재사용 범위를 확인한다.
3. 우회 접속, 접근 제한 회피, CAPTCHA 우회, 로그인 정보 추출을 하지 않는다.
4. 무작위 train/test 분할을 사용하지 않는다. 시간순 워크포워드 검증을 사용한다.
5. 미래 정보가 후보 검색, 특징 생성, 모델 선택에 들어가지 않게 한다.
6. 최종 미사용 테스트 구간은 모델 확정 후 한 번만 평가한다.
7. 수익률이 가장 높았던 모델을 자동 채택하지 않는다. 아래 정의한 종합 기준으로 선택한다.
8. 연구 과정과 결과를 재현할 수 있게 데이터 스냅샷, 코드 버전, 설정, seed, 결과를 저장한다.
9. 막히지 않는 한 사용자에게 사소한 선택을 묻지 말고 연구를 진행한다.
10. 데이터 접근 권한이나 라이선스처럼 사용자의 새로운 권한이 필요한 경우에만 중단하고 구체적으로 요청한다.

---

## 1. 제품 질문

엔진이 답해야 하는 질문은 다음과 같다.

> 오늘 발생한 세부 테마 사건과 구조적으로 비슷했던 과거 사건은 언제였는가?

> 그 과거 사건들의 당시 주도주는 다음날, 5거래일 뒤, 20거래일 뒤 어떻게 움직였는가?

> 이후 상승 모멘텀은 어느 시점까지 관측됐는가?

`원전`처럼 넓은 테마명만 일치한다고 유사한 사건으로 보지 않는다.

예:

```text
원전
├─ 해외 원전 수주 기대
├─ 최종 후보 선정
├─ 우선협상대상자 선정
├─ 본계약 체결
├─ 국내 원전 재가동
├─ SMR 투자
├─ 원전 해체
└─ 핵연료 정책
```

오늘 사건이 `체코 원전 우선협상대상자 선정`이면 다음 순서가 자연스럽다.

1. 해외 원전 수주 + 우선협상 단계
2. 해외 원전 수주 + 최종후보·본계약 같은 인접 단계
3. 해외 정부 발주 + 한국 기업 수혜 구조
4. 넓은 원전수출 사건

`국내 원전 재가동`이나 `SMR 투자`는 같은 원전 테마라도 낮은 유사도를 가져야 한다.

---

## 2. 핵심 구분

### 2.1 전체 시스템과 개별 방법

- 전체 시스템: **과거 유사사례 매칭 엔진**
- 엔진 안에서 시험하는 개별 계산법: **유사도 알고리즘 또는 매칭 모델**

엔진은 다음 일을 포함한다.

```text
현재 사건 구조화
후보 과거 사건 검색
유사도 계산 및 재정렬
중복·동일 프로젝트 정리
Top K 사례 선정
선정 완료 후 미래 성과 조회
통계·근거·표본 신뢰도 반환
```

### 2.2 가격 데이터의 역할

가격 데이터는 이후 결과의 정답이다.

```text
가격 데이터 = T+1, T+5, T+20 결과 정답
가격 데이터 ≠ 두 사건의 의미적 유사성 정답
```

두 사건의 이후 수익률이 같았다는 이유만으로 두 사건을 의미상 비슷하다고 정의하면 안 된다.

### 2.3 현재 테마 강도와 과거 성과의 분리

현재 테마 반응과 홈 정렬은 [제품 의사결정 PD-001](./product_decisions.md)의 상한형 유동시가총액 가중 방식을 사용한다.

과거 유사사례 성과는 이 방식을 사용하지 않는다. 당시 주도주 동일가중 바스켓을 사용한다.

```text
현재 테마 시장 영향 = 상한형 유동시총 가중
현재 테마 확산도 = 상승 종목 수 / 유효 구성종목 수
과거 사건 성과 = 당시 주도주 동일가중 바스켓
```

---

## 3. 연구 목표와 비목표

### 3.1 목표

1. 한국 주식 테마 사건의 point-in-time 데이터셋 구축
2. 세부 사건 구조와 발생 전 시장 상태를 함께 반영하는 후보 검색·재정렬 방법 비교
3. 의미상 타당하면서 이후 결과 분포 예측에 유용한 Top K 과거 사례 선택
4. 시간순 표본 외 평가와 과최적화 검증
5. 최종 모델, 폴백 규칙, 설명 근거를 포함한 재현 가능한 엔진 구현
6. 사용자 화면에서 이해 가능한 통계와 근거 제공

### 3.2 비목표

- 특정 종목 매수·매도 추천
- 미래 수익 보장
- 가장 높은 백테스트 수익률만을 목표로 한 전략 탐색
- 뉴스 문장 임베딩만으로 최종 사례 결정
- 현재 등록된 관련주를 모든 과거 사건에 소급 적용
- 장중 분봉이 없는 기간의 장중 경로를 추정하거나 만들어내기

---

## 4. “가장 좋은 엔진”의 정의

단일 수익률 숫자로 결정하지 않는다. 다음 네 축을 함께 평가한다.

### 4.1 사건 관련성

- 사람이 보기에 같은 종류의 사건인가
- 같은 세부 테마, 촉매, 단계, 수혜 구조를 공유하는가
- Top K에 무관한 사건이 섞이지 않는가
- 왜 비슷한지 사용자에게 설명 가능한가

### 4.2 결과 예측력

- T+1 상승확률의 Brier score와 calibration
- T+1, T+5, T+20 중앙 수익률 예측 MAE
- 수익률 구간 예측의 coverage와 pinball loss 또는 CRPS
- 시장 대비 초과수익 예측력

### 4.3 안정성

- 연도별, 테마별, 시장 국면별 성능
- 대형주 중심 사건과 소형주 중심 사건의 성능
- 표본 수 변화에 대한 민감도
- 하이퍼파라미터가 조금 바뀌어도 결과가 유지되는가
- 최신 미사용 기간에서도 성능이 유지되는가

### 4.4 제품 적합성

- 응답 속도
- 유사 근거 설명 가능성
- 적은 표본에서 안전하게 폴백하는가
- 재현 가능한가
- 운영자가 오류를 검수·수정할 수 있는가

최종 선택 규칙은 연구 시작 전에 수치화해 `model_selection_policy.md`에 고정한다. 최종 테스트 결과를 본 뒤 선택 규칙을 바꾸지 않는다.

---

## 5. 필수 데이터

### 5.1 인포스탁 원천 데이터

가능하면 원문을 보존한다.

- 테마 ID와 테마명
- 테마 설명
- 상위·하위 테마 관계 후보
- 테마 관련주
- 종목별 편입 이유
- 과거 테마 발생일
- 당시 상승·하락 이유 원문
- 당시 인포스탁 주도주
- 원문 URL
- 수집 시각
- 원문 또는 원문 해시
- 이후 수정 여부

중요:

- 현재 페이지에 보이는 최신 관련주를 과거 사건의 당시 구성종목으로 간주하지 않는다.
- 인포스탁에 실제 당시 주도주가 기록된 경우 그 목록을 과거 성과 기준으로 사용한다.
- 과거 시점에 어떤 정보가 공개돼 있었는지 복원할 수 없는 필드는 `point_in_time_safe=false`로 표시한다.

### 5.2 뉴스·공시

- 제목과 본문 또는 사용 허용 범위의 텍스트
- 게시·수정 시각
- 언론사 또는 공시기관
- 원문 URL
- 종목·기업·기관·국가 엔티티
- 동일 사건 기사 묶음 ID
- 공시 여부와 공식 출처 여부

### 5.3 가격·거래 데이터

- 종목별 조정 Open, High, Low, Close
- 거래량과 거래대금
- 상장·상장폐지·거래정지 정보
- 액면분할, 합병, 유상·무상증자 등 기업행위
- KOSPI, KOSDAQ 및 필요한 시장·업종 벤치마크
- 거래일 캘린더

조정 가격 산출기관과 조정 방식의 버전을 기록한다.

### 5.4 종목 기초정보

- 종목코드 변경 이력
- 기업 ID와 종목 ID 분리
- 상장시장
- 상장주식수
- 유동주식비율
- 시가총액
- 업종

### 5.5 장중 데이터

과거 장중 분봉이 충분하지 않다면 1차 엔진 핵심 입력에서 제외한다.

향후 실시간 유사성 연구를 위해 지금부터 다음 스냅샷을 축적한다.

- 이벤트 최초 탐지 시각
- 소재 최초 확인 시각
- 1분 또는 5분 OHLCV
- 시간대별 테마 구성과 주도주
- 시간대별 확산도
- 시간대별 상한형 유동시총 가중 수익률

---

## 6. 권장 데이터 스키마

### 6.1 `events`

```text
event_id
event_date
first_known_at
theme_id
subtheme_id
direction
catalyst_type
event_stage
novelty_type
actor_type
actor_id
geography
beneficiary_type
beneficiary_ids
project_id
event_summary
raw_reason_text
source_url
source_provider
collected_at
valid_from
valid_to
point_in_time_safe
classification_version
valid_for_statistics
```

### 6.2 `event_stocks`

```text
event_id
stock_id
role
role_source
is_infostock_leader
is_direct_beneficiary
known_at
valid_from
valid_to
```

`role` 예:

```text
leader
direct_beneficiary
related_stock
supplier
customer
```

### 6.3 `event_documents`

```text
document_id
event_id
published_at
updated_at
source
title
body_or_allowed_excerpt
url
is_official
content_hash
duplicate_cluster_id
embedding_version
```

### 6.4 `daily_prices`

```text
stock_id
trade_date
open
high
low
close
adjusted_open
adjusted_high
adjusted_low
adjusted_close
volume
trading_value
adjustment_version
```

### 6.5 `event_features`

특징은 생성 시점과 버전을 반드시 가진다.

```text
event_id
as_of
feature_version
ontology_features
text_embedding
pre_event_setup_features
market_regime_features
data_quality_flags
```

### 6.6 `event_outcomes`

```text
event_id
outcome_version
leader_count
ret_t1
ret_t5
ret_t20
abnormal_ret_t1
abnormal_ret_t5
abnormal_ret_t20
positive_t1
positive_t5
positive_t20
new_high_after_t1
new_high_after_t3
new_high_after_t5
new_high_after_t10
peak_close_day_20
max_close_return_20
min_close_return_20
```

---

## 7. 사건 구조화 기준

최소 온톨로지:

```text
Theme
SubTheme
CatalystType
EventStage
NoveltyType
Direction
ActorType
Actor
Geography
BeneficiaryType
Beneficiary
Project
Technology
BusinessRole
```

예:

```text
Theme            원전
SubTheme         원전수출
CatalystType     해외수주
EventStage       우선협상대상자 선정
NoveltyType      기존 이슈 진전
ActorType        해외정부
Actor            체코 정부
Geography        유럽
BeneficiaryType  한국 원전기업
Project          체코 신규 원전 사업
Direction        Positive
```

단계 관계도 저장한다.

```text
수주 기대
최종 후보
우선협상대상자
본계약
착공
납품
```

같은 단계, 인접 단계, 먼 단계가 다른 유사도를 갖게 한다.

---

## 8. 과거 성과 정답 생성

### 8.1 당시 주도주만 사용

현재 테마 관련주 전체를 과거에 적용하지 않는다.

과거 사건 `e`의 당시 인포스탁 주도주 집합을 `L_e`라고 한다.

### 8.2 종목별 수익률

```text
r(i,e,h) = AdjustedClose(i, T+h) / AdjustedClose(i, T0) - 1
```

`h`는 1, 5, 20번째 거래일이다.

### 8.3 사건별 동일가중 바스켓

```text
R(e,h) = (1 / |L_e|) × Σ r(i,e,h)
```

각 사건은 주도주 수와 관계없이 관측값 하나가 된다.

### 8.4 상승 사건

```text
Positive(e,h) = 1 if R(e,h) > 0 else 0
```

### 8.5 시장 대비 초과수익

기본 가격수익률과 함께 벤치마크 대비 결과도 저장한다.

```text
AbnormalReturn(e,h) = R(e,h) - BenchmarkReturn(e,h)
```

벤치마크 후보:

- 주도주가 속한 시장의 KOSPI 또는 KOSDAQ
- 연구 결과가 충분하면 업종지수

벤치마크 선택 규칙을 사건 발생 전에 결정할 수 있어야 한다.

### 8.6 표본 집계

Top K 유사사례 집합을 `N(q)`라고 한다.

```text
상승 사건 수(h) = Σ Positive(e,h), e ∈ N(q)
중앙 수익률(h) = median(R(e,h)), e ∈ N(q)
```

기간별 데이터가 없으면 해당 기간 분모에서 제외한다. 분모를 억지로 맞추지 않는다.

### 8.7 상승 모멘텀 지속

`평균 상승 지속일`은 정의가 모호하므로 기본 목표로 고정하지 않는다. 다음 후보를 각각 계산하고 연구한다.

1. `T+k 이후 새 최고 종가 발생 여부`
2. `20거래일 내 최고 종가 발생일`
3. `T0 대비 양의 종가 수익률이 처음 끊길 때까지 연속 거래일 수`
4. `T0 대비 양의 종가 수익률을 보인 거래일 수`

사용자에게 기본 노출할 후보:

```text
5일 뒤에도 새 최고 종가가 나온 사례 8 / 14건
```

최고가 대신 최고 **종가**를 기본으로 검토한다. 일봉 고가를 이용한 가상 바스켓은 종목별 고가 발생 시각이 달라 실제 동시 체결 가능한 바스켓이 아닐 수 있다.

---

## 9. Point-in-time 규칙

역사적 재생 시점 `t`에서 다음만 사용할 수 있다.

- `published_at <= t`인 뉴스·공시
- `known_at <= t`인 분류와 종목 관계
- `trade_date < t`인 완성 가격 데이터
- 해당 시점까지 존재했던 과거 이벤트
- 해당 시점에 배포돼 있던 모델과 특징 버전

사용 금지:

- 이후 수정된 인포스탁 분류를 원래부터 알았던 것처럼 사용
- 장후 확정된 당시 주도주를 장중 모델 입력에 사용
- 미래 상장폐지 여부
- 미래 관련주 편입·제외 정보
- 이후 수익률을 이용한 후보 필터링
- 전체 기간으로 학습한 임베딩을 과거 시점 평가에 그대로 사용해 생기는 미래 말뭉치 누수

임베딩 모델이 전체 외부 말뭉치로 사전학습됐더라도 연구에서 허용할 범위와 잠재 누수를 모델 카드에 기록한다.

---

## 10. 역사적 재생 연구 설계

사용자가 말한 “과거로 무작위 이동”은 **historical replay**로 구현한다.

무작위 사건 샘플링은 계산량 조절과 수동 검수에 사용할 수 있지만, 성능 검증은 시간순으로 한다.

예:

```text
재생 시점: 2021-07-15 장 마감

사용 가능:
2021-07-15까지 공개된 사건·뉴스·가격

사용 불가:
2021-07-16 이후 정보와 결과

실행:
과거 후보 검색
Top K 선택
T+1, T+5, T+20 예측 분포 저장

평가:
시간이 지난 뒤 실제 조정 종가와 비교
```

### 10.1 권장 시간 분할

실제 데이터 범위에 맞춰 조정하되 원칙은 유지한다.

```text
초기 학습 구간       가장 오래된 시점 ~ 2020
워크포워드 검증 구간 2021 ~ 2023
최종 미사용 테스트   2024 ~ 최신 완결 연도
```

연도는 예시다. 전체 이벤트 수와 테마별 표본을 감사한 뒤 확정한다.

### 10.2 Purging과 embargo

T+20 결과가 겹치는 사건을 인접 fold 양쪽에 두면 결과 기간이 겹친다.

- 학습과 검증 경계에서 최대 예측 horizon만큼 purging 또는 embargo 적용
- 같은 프로젝트의 단계별 사건이 train/test 양쪽에 과도하게 중복되지 않게 그룹 분할도 비교
- 동일 뉴스 사건의 중복 기사는 반드시 같은 fold에 둔다

### 10.3 최종 테스트 봉인

- 모델 가족, 특징, K, 가중치, 임계값을 최종 테스트 전에 확정
- 테스트 결과 확인 후 재조정하면 새 테스트 기간이 필요
- 모든 시도를 experiment registry에 기록

---

## 11. 비교할 후보 엔진

모든 후보는 같은 후보 풀, fold, 결과 정의, 평가 코드로 비교한다.

### Baseline 0. 넓은 테마 일치

```text
같은 Theme의 과거 사건 중 최신 K개
```

현재 단순 방식보다 복잡한 방법이 실제로 나은지 확인하는 최저 기준이다.

### Baseline 1. 세부 테마 정확 일치

```text
같은 Theme + SubTheme
```

### Model A. 규칙 기반 온톨로지 점수

후보 변수:

- Theme
- SubTheme
- CatalystType
- EventStage 거리
- NoveltyType
- ActorType
- Geography
- BeneficiaryType
- Direction
- 동일 Project 여부

가중치는 전문가 초기값과 학습형 값을 모두 시험한다.

### Model B. 텍스트 의미 유사도

- 사건 요약 임베딩
- 인포스탁 원문 임베딩
- 뉴스 묶음 임베딩
- cosine similarity

일반 한국어 임베딩과 금융 도메인 임베딩을 비교한다. 임베딩 단독 모델은 최종 후보가 아니라 비교 기준이다.

### Model C. 발생 전 상태 거리

후보 특징:

- 테마 최근 5·20·60일 수익률
- 당시 주도주 최근 5·20일 수익률
- 시장 대비 상대수익률
- 최근 거래대금 수준
- 52주 고점 대비 위치
- 마지막 동일 프로젝트 사건 이후 경과일
- 최근 동일 테마 사건 빈도
- KOSPI·KOSDAQ 최근 흐름과 변동성

거리 후보:

- 표준화 Euclidean
- Manhattan
- Mahalanobis
- Gower distance

### Model D. 혼합 선형 점수

```text
Score
= w1 × OntologySimilarity
+ w2 × TextSimilarity
+ w3 × SetupSimilarity
+ w4 × MarketRegimeSimilarity
```

가중치는 학습 구간과 검증 구간에서만 선택한다.

### Model E. Metric learning

- LMNN 또는 유사한 학습형 거리
- Siamese 또는 triplet 방식
- 지도 신호는 학습 구간 안에서만 생성

주의:

미래 수익률이 비슷하다는 이유만으로 의미가 다른 사건을 이웃으로 만들지 않는다. 온톨로지 관련성 제약 또는 다목적 손실을 둔다.

### Model F. Learning-to-rank 재정렬

1차 후보 검색은 안전한 온톨로지·텍스트 필터로 한다.

2차 모델이 다음을 이용해 순위를 조정한다.

- 사건 구조 일치
- 텍스트 유사도
- 발생 전 상태 유사도
- 데이터 품질
- 동일 프로젝트·중복 패널티

### Model G. 결과 분포 직접 예측 + 사례 검색

예측 모델과 사례 검색을 분리한다.

- 예측 모델: T+1 상승확률과 T+5·T+20 분포 예측
- 사례 검색: 의미상 가장 가까운 설명용 사건 검색

두 결과가 크게 충돌하면 신뢰도를 낮춘다. 이 방식은 “설명용 이웃”과 “예측용 이웃”을 억지로 같게 만드는 문제를 줄일 수 있다.

### Model H. 앙상블

표본 외에서 서로 보완적인 모델만 결합한다.

- 단순 평균
- 검증 성능 기반 가중
- stacking

복잡도가 늘어난 만큼 안정적 개선이 없으면 단순 모델을 선택한다.

---

## 12. 후보 검색과 최종 정렬 계약

### 12.1 1차 후보 필터

최소 조건:

- 현재 사건보다 과거 발생
- `valid_for_statistics=true`
- Direction 일치
- Theme 또는 온톨로지 상위 개념 연결
- 당시 주도주와 필요한 가격 존재

### 12.2 중복 제거

다음은 같은 사건 묶음으로 처리한다.

- 같은 프로젝트
- 같은 핵심 발표
- 짧은 시간 안에 반복 보도된 기사
- 문구만 다른 인포스탁 중복 기록

프로젝트가 장기간 단계적으로 진전된 경우 각 단계는 별도 사건으로 유지하되 `project_id`를 공유한다.

### 12.3 다양성

Top K가 한 프로젝트의 연속 기사로 채워지지 않게 한다.

- 프로젝트별 최대 사례 수 후보를 검증
- Maximal Marginal Relevance 같은 다양성 재정렬 비교
- 다양성 때문에 사건 관련성이 크게 낮아지면 적용하지 않음

### 12.4 폴백

엄격 유사사례가 부족할 때 단계적으로 넓힌다.

```text
동일 단계·동일 세부 테마
인접 단계·동일 세부 테마
동일 촉매·상위 테마
부분 유사사례
```

서로 다른 레벨을 한 통계에 조용히 섞지 않는다.

예:

```text
같은 유형 2건
부분 유사사례 7건
```

각 집계는 별도 표시한다.

---

## 13. 평가 지표

### 13.1 사건 검색 품질

수동 검수 표본을 만든다.

- Precision@K
- nDCG@K
- Mean Reciprocal Rank
- 무관 사례 비율
- 동일 프로젝트 중복률
- 설명 근거 적절성

최소 두 명의 평가자가 가능한 범위에서 독립 라벨링한다. 불일치율과 합의 규칙을 기록한다.

라벨 예:

```text
3  매우 유사: 세부 테마·촉매·단계·수혜 구조가 거의 동일
2  유사: 핵심 촉매와 수혜 구조 동일, 일부 단계 차이
1  부분 유사: 상위 테마만 같거나 구조 일부만 일치
0  무관
```

### 13.2 상승확률

- Brier score
- Log loss
- Reliability diagram
- Expected Calibration Error
- 예측확률 구간별 실제 상승률

정확도만 사용하지 않는다.

### 13.3 수익률 분포

- 중앙값 예측 MAE
- Pinball loss
- CRPS
- 50%·80% 예측구간 coverage
- 방향 정확도
- 시장 대비 초과수익 예측 오차

### 13.4 경제적 성과

2차 지표로만 사용한다.

- 거래비용 전·후 수익률
- 최대낙폭
- turnover
- hit rate
- Sharpe 또는 downside risk 지표

서비스는 매매 추천기가 아니므로 경제적 성과만으로 모델을 고르지 않는다.

### 13.5 안정성 슬라이스

- 연도
- Theme/SubTheme
- CatalystType
- EventStage
- 신규/진전/재부각
- KOSPI/KOSDAQ
- 대형주·중형주·소형주 중심 사건
- 강세·약세·고변동 시장
- 표본 수 구간

---

## 14. 과최적화 방지

여러 계산법을 많이 시험할수록 우연히 좋아 보이는 모델이 나온다.

필수 조치:

1. 모든 실험을 registry에 기록
2. 최종 테스트 구간 봉인
3. 시간순 워크포워드 평가
4. 하이퍼파라미터 수 제한
5. 단순 baseline 대비 통계적 개선 확인
6. block bootstrap으로 신뢰구간 산출
7. White's Reality Check 또는 적절한 multiple-testing 보정 검토
8. Probability of Backtest Overfitting을 보조 진단으로 검토
9. 성과가 특정 테마·연도 한 곳에만 몰렸는지 확인
10. 모델 복잡도 대비 개선이 작으면 단순 모델 선택

PBO는 시간순 최종 테스트를 대체하지 않는다.

---

## 15. 사용자 출력 계약

엔진 API는 최소 다음을 반환한다.

```json
{
  "query_event_id": "...",
  "engine_version": "...",
  "as_of": "...",
  "strict_match_count": 14,
  "partial_match_count": 7,
  "matches": [
    {
      "event_id": "...",
      "event_date": "2024-07-17",
      "summary": "체코 원전 최종후보 선정",
      "why_similar": ["원전수출", "해외수주", "유럽", "인접 단계"],
      "similarity_band": "high",
      "ret_t1": 0.052,
      "ret_t5": 0.184,
      "ret_t20": 0.117
    }
  ],
  "statistics": {
    "t1": {"valid_n": 14, "positive_n": 10, "median_return": 0.038},
    "t5": {"valid_n": 14, "positive_n": 9, "median_return": 0.082},
    "t20": {"valid_n": 12, "positive_n": 6, "median_return": 0.041}
  },
  "data_quality": {
    "sample_warning": false,
    "point_in_time_safe": true,
    "missing_fields": []
  }
}
```

사용자 화면 기본 표현:

```text
비슷했던 날 14건

다음날
14건 중 10건 상승
중앙 수익률 +3.8%

왜 비슷한가
원전수출 · 해외수주 · 유럽 · 인접 단계
```

유사도 `93%`는 확률이나 정확도로 오해될 수 있으므로 기본 노출하지 않는다. 필요하면 `매우 비슷함`, `비슷함`, `부분 유사` 같은 검증된 구간과 근거 태그를 사용한다.

### 15.1 표본 부족

표본을 숨기지 않는다.

```text
같은 유형 과거사례 2건
표본이 적어 참고용이에요
```

표본 부족 경고 임계값과 문구는 calibration·사용자 테스트 후 확정한다.

### 15.2 현재 진행 중 사건

현재 장중 사건에는 미래 성과가 아직 없다. 엔진은 과거 사례만 집계하며 현재 사건을 자기 후보에 포함하지 않는다.

---

## 16. 구현 구조

권장 모듈:

```text
collectors/
  infostock
  news_disclosure
  price_market

normalization/
  stock_identity
  trading_calendar
  corporate_actions
  document_deduplication

point_in_time_store/
  snapshots
  validity_intervals

ontology/
  schema
  classifier
  stage_graph

features/
  ontology_features
  text_embeddings
  pre_event_setup
  market_regime

matching/
  candidate_retriever
  scorers
  reranker
  diversity
  fallback

outcomes/
  leader_basket
  horizon_returns
  abnormal_returns
  momentum_duration

evaluation/
  historical_replay
  walk_forward
  retrieval_metrics
  forecast_metrics
  overfitting_checks

serving/
  engine_api
  explanation_builder
  versioning

monitoring/
  drift
  data_quality
  operator_corrections
```

언어·프레임워크는 기존 저장소 구조를 확인한 뒤 정한다. 연구 코드와 운영 코드를 분리하되 핵심 특징 계산과 성과 계산은 같은 검증된 라이브러리를 공유한다.

---

## 17. 필수 자동 테스트

### 데이터 테스트

- 동일 `stock_id + trade_date` 중복 없음
- 거래일 순서 정확
- 조정 가격 결측·급변 감사
- 상장폐지 종목 보존
- 종목코드 변경 후 기업 연결 정확
- 이벤트 원문과 해시 보존

### Point-in-time 테스트

- 후보 사건 날짜가 query 사건보다 항상 과거
- 특징의 `as_of`가 query 시점 이후가 아님
- 장중 평가에 장후 주도주가 들어가지 않음
- 미래 관련주 정보가 과거 snapshot에 들어가지 않음
- 결과 데이터가 후보 선택 전에 접근되지 않음

### 성과 계산 테스트

- T+1은 다음 달력일이 아니라 다음 거래일
- 동일가중 계산 정확
- 결측 종목 처리 규칙 정확
- 각 사건이 관측값 하나
- 분모가 기간별 유효 사건 수와 일치
- 수정주가와 기업행위 처리 검증

### 매칭 테스트

- 자기 사건 제외
- 중복 기사 제거
- 동일 프로젝트 과다 노출 제한
- 폴백 단계가 통계에 혼합되지 않음
- 동일 입력·모델 버전에서 결과 결정적

---

## 18. 연구 산출물

다음 파일을 최종적으로 남긴다.

1. `data_source_audit.md`
   - 출처, 권한, 기간, 누락, 수정 이력, point-in-time 안전성
2. `data_dictionary.md`
   - 모든 필드 정의와 단위
3. `event_ontology.md`
   - 테마·촉매·단계·수혜 구조
4. `outcome_definition.md`
   - T+1·T+5·T+20과 지속성 계산식
5. `research_protocol.md`
   - fold, embargo, 지표, baseline, 실험 순서
6. `model_selection_policy.md`
   - “가장 좋은 엔진” 선택 규칙
7. `experiment_registry.csv` 또는 동등한 저장소
   - 모든 실험, 코드·데이터·설정 버전, 결과
8. `research_report.md`
   - 후보 비교, 신뢰구간, 실패한 방법, 슬라이스 결과
9. `model_card.md`
   - 최종 모델, 한계, 데이터 범위, 사용 금지 조건
10. 운영 엔진 코드와 자동 테스트
11. 엔진 API 명세
12. 재현 명령과 환경 잠금 파일

실패한 실험도 삭제하지 않는다. 같은 시도를 반복하거나 좋은 결과만 골라 보고하는 것을 막는다.

---

## 19. 단계별 실행 계획

### Phase 0. 저장소와 데이터 접근 감사

- 현재 코드·문서·DB 확인
- 인포스탁 접근 방식과 이용 조건 확인
- 가격 API의 조정주가·상폐종목 지원 확인
- 사용 가능한 기간과 예상 사건 수 산출
- 결과: `data_source_audit.md`

### Phase 1. 원천 데이터 보존

- 인포스탁 테마·히스토리·주도주 수집
- 원문 URL, 수집시각, 해시 저장
- 가격·기업행위·거래일 수집
- 종목 ID 정규화
- 중복 기사·이벤트 후보 묶음

### Phase 2. 정답과 baseline 구축

- 당시 주도주 동일가중 성과 계산
- Baseline 0, 1 구현
- historical replay 실행
- 최초 데이터 품질 보고서 작성

### Phase 3. 수동 검수 세트

- 테마·연도·촉매별 층화 표본 생성
- 사건 쌍 또는 query별 Top K 관련성 라벨
- 평가자 합의 규칙 기록

### Phase 4. 후보 모델 연구

- Model A부터 순서대로 추가
- 한 번에 한 요소씩 개선 효과 확인
- 모든 실험 registry 기록
- 시간순 검증과 슬라이스 평가

### Phase 5. 모델 선택과 봉인 테스트

- 사전 고정한 정책으로 후보 선택
- 최종 테스트 한 번 실행
- 신뢰구간과 과최적화 진단
- 실패 시 테스트에 맞춰 수정하지 않고 연구 단계로 되돌아가 새 테스트 기간 확보

### Phase 6. 운영 엔진 구현

- batch 인덱스 구축
- query 사건 point-in-time 특징 생성
- 후보 검색과 재정렬
- 통계와 설명 생성
- 버전 관리, 캐시, 모니터링
- 자동 테스트와 API 계약 검증

### Phase 7. 제한적 운영 검증

- 운영자 블라인드 평가
- 장후 인포스탁 확정값과 비교
- 잘못된 사례와 분류 수정률 측정
- 모델 drift와 신규 테마 폴백 점검

---

## 20. 완료 조건

다음이 모두 충족돼야 “최종 엔진 구현 완료”로 본다.

- 데이터 출처와 사용 범위 문서화
- point-in-time 재생 가능
- 상장폐지·종목코드 변경·기업행위 처리
- 당시 주도주 성과 계산 검증
- 최소 두 baseline과 여러 후보 모델 비교
- 시간순 워크포워드 평가
- 최종 미사용 테스트 평가
- 사건 관련성 수동 검수
- 상승확률 calibration 평가
- 테마·연도·시장 국면별 안정성 평가
- 과최적화·multiple-testing 진단
- 표본 부족 폴백 구현
- 사용자용 유사 근거 생성
- API와 자동 테스트 구현
- 모델 카드와 재현 방법 작성

“백테스트 수익률이 높다” 하나만으로 완료 처리하지 않는다.

---

## 21. 주요 위험

### 데이터 권리

인포스탁 데이터 수집·저장·재배포 범위를 확인해야 한다. 허용 범위가 좁으면 원문 전체 대신 식별자, 구조화 필드, 해시, 허용된 요약만 보존하는 대안을 검토한다.

### 과거 정보 복원 실패

현재 관련주 목록만 있고 과거 snapshot이 없으면 당시 전체 테마 구성은 정확히 복원할 수 없다. 당시 주도주 원문이 있다면 과거 성과는 계산 가능하지만 발생 전 테마 상태 특징 일부는 결측 처리해야 한다.

### 생존편향

상장폐지 종목과 이름·코드 변경 종목을 누락하면 과거 성과가 과대평가될 수 있다.

### 사건 중복

같은 발표가 여러 날짜·기사로 기록되면 표본 수와 상승확률이 부풀려진다.

### 테마 다중 소속

한 종목이 여러 테마에 속한다. 현재 테마 강도와 사건 수혜 구조에서 중복 기여를 별도로 관리한다.

### 제도·시장 구조 변화

2006년 사건과 2026년 사건은 가격제한폭, 시장 참여자, 정보 전달 속도가 다르다. 연도·제도 구간별 안정성 확인이 필요하다.

### 장중 비교 한계

과거 분봉이 없으면 현재 오전 10시 흐름과 과거 오전 10시 흐름을 직접 비교할 수 없다. 완성된 일봉과 현재 장중 값을 같은 조건인 것처럼 표현하지 않는다.

---

## 22. 관련 연구

완전히 동일한 한국 테마주 유사사례 엔진 논문보다 구성요소별 연구를 조합한다.

1. A. Craig MacKinlay, **Event Studies in Economics and Finance**  
   사건 전후 가격 반응 측정의 기본 틀.  
   <https://www.bu.edu/econ/files/2011/01/MacKinlay-1996-Event-Studies-in-Economics-and-Finance.pdf>

2. Andrew W. Lo, Harry Mamaysky, Jiang Wang, **Foundations of Technical Analysis: Computational Algorithms, Statistical Inference, and Empirical Implementation**  
   과거 가격 패턴을 체계적으로 인식하고 조건부 수익률 분포를 비교하는 접근.  
   <https://www.nber.org/system/files/working_papers/w7613/w7613.pdf>

3. Xiao Ding, Yue Zhang, Ting Liu, Junwen Duan, **Deep Learning for Event-Driven Stock Prediction**  
   뉴스에서 구조화 사건을 추출하고 주가 움직임과 연결.  
   <https://www.ijcai.org/Proceedings/15/Papers/329.pdf>

4. Xiao Ding, Yue Zhang, Ting Liu, Junwen Duan, **Knowledge-Driven Event Embedding for Stock Prediction**  
   지식그래프를 결합한 사건 임베딩과 사건 유사성.  
   <https://aclanthology.org/C16-1201/>

5. Kilian Q. Weinberger, Lawrence K. Saul, **Distance Metric Learning for Large Margin Nearest Neighbor Classification**  
   학습 데이터로 이웃 검색 거리함수를 학습하는 기반 방법.  
   <https://www.jmlr.org/papers/v10/weinberger09a.html>

6. Halbert White, **A Reality Check for Data Snooping**  
   많은 모델을 반복 시험할 때 생기는 우연한 성과를 검증.  
   <https://doi.org/10.1111/1468-0262.00152>

7. David H. Bailey et al., **The Probability of Backtest Overfitting**  
   투자 백테스트 과최적화 확률 진단.  
   <https://papers.ssrn.com/sol3/Papers.cfm?abstract_id=2326253>

연구 시작 시 위 논문의 실험 설계와 한계를 다시 검토하고, 더 직접적인 한국어 금융 이벤트 검색·metric learning·event study 연구가 있는지 최신 문헌 검색을 수행한다.

---

## 23. 아직 확정하지 않을 항목

연구 전 임의로 고정하지 않는다.

- 최종 유사도 공식
- 온톨로지 변수별 가중치
- 임베딩 모델
- Top K
- 동일 프로젝트 최대 노출 수
- 발생 전 상태 기간
- 시장 국면 정의
- 폴백 임계값
- 표본 부족 경고 임계값
- 확률·수익률 결합 방식
- `평균 모멘텀 지속기간`의 채택 여부

각 항목은 연구 결과와 선택 근거를 제품 의사결정 기록에 추가한다.

---

## 24. 다음 세션 시작 체크리스트

다음 순서로 즉시 시작한다.

1. 저장소 전체와 `docs` 읽기
2. 데이터 파일, DB, 수집 코드 존재 여부 확인
3. 인포스탁 데이터 접근·이용 범위 감사
4. 가격 API 후보와 조정주가·상폐종목 지원 확인
5. 실제 사건 수, 기간, 필드 결측률 표본 조사
6. `data_source_audit.md` 작성
7. point-in-time 가능 필드와 불가능 필드 구분
8. 연구 프로토콜과 최종 테스트 구간 봉인
9. 원천 데이터 수집과 Baseline 0 구현
10. 진행 중 가정·한계·실험을 계속 문서화

첫 보고에는 다음을 포함한다.

```text
확보 가능한 데이터
확보 불가능하거나 불명확한 데이터
point-in-time 복원 가능 범위
예상 이벤트 수와 기간
가장 큰 누수·편향 위험
첫 baseline 구현 계획
사용자 권한이 추가로 필요한 사항
```

---

## 25. 한 줄 원칙

> 미래 결과로 과거의 닮음을 조작하지 말고, 과거 시점에 알 수 있던 정보만으로 의미상 타당한 이웃을 찾은 뒤 그 이웃의 이후 결과가 실제로 유용했는지 시간순 표본 외에서 검증한다.

