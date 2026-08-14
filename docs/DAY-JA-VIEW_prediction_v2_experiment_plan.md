# DAY-JA-VIEW 조건부 지속성 예측 v2 연구·실험 계획서

- 문서 버전: `v2.0-draft`
- 작성일: 2026-08-13
- 적용 대상: DAY-JA-VIEW 과거 유사사례 엔진 이후의 별도 예측 연구
- 문서 성격: Claude가 다음 세션에서 그대로 수행할 수 있는 독립 실행형 연구 명세
- 선행 산출물:
  - `docs/historical_event_matching_engine_research_spec.md`
  - `docs/DAY-JA-VIEW_엔진_research_report.md`
  - `docs/DAY-JA-VIEW_엔진_model_card.md`
  - `docs/PRD.md`
  - `docs/realtime_theme_feature_spec.md`

---

## 0. Claude에게 주는 실행 지시

이 문서와 위 선행 산출물을 처음부터 끝까지 읽은 뒤 연구를 시작한다.

이 연구의 목적은 미리 정한 모델로 좋은 결과를 만드는 것이 아니다. 현재 확보 가능한 point-in-time 데이터에서 **사건 구조, 발생 전 가격 상태, 탐지 시점의 시장 반응, 시장 국면을 조건으로 했을 때 미래의 지속 또는 반전과 관련된 재현 가능한 표본 외 신호가 존재하는지** 검증하는 것이다.

다음 원칙을 반드시 지킨다.

1. v1이 실패한 범위를 정확히 한정한다.
   - v1이 부정한 것은 `텍스트로 검색한 과거 이웃의 원시 상승비율 → T+1 방향확률` 방식이다.
   - v1 결과만으로 모든 형태의 직접 예측이 불가능하다고 결론내리지 않는다.
2. **설명용 사례 검색과 예측 모델을 분리**한다.
   - v1 `M-TXT·K=20`은 설명용 검색 기준으로 유지한다.
   - 예측 모델은 별도 입력·목표·평가 규칙을 갖는다.
3. v1 서빙 로직과 사용자 출력은 이번 연구 중 변경하지 않는다.
4. 결과가 좋아질 때까지 실험 정의를 바꾸지 않는다.
5. 모든 성공·실패·중단 실험을 registry에 기록한다.
6. 미래 정보, 사후 확정 주도주, 사후 수정된 테마 구성, 미래 시장 상태가 입력에 들어가지 않게 한다.
7. 과거 장중 스냅샷이 없으면 일봉으로 장중 사용 상황을 흉내 내지 않는다.
8. 이미 열어 본 2026-01-01~2026-08-11 v1 봉인 구간을 v2의 최종 증거로 사용하지 않는다.
9. 새 봉인 구간은 2026-09-01 이후로 만들며 봉인 해제 조건을 사전에 고정한다.
10. 예측 개선이 확인되지 않아도 실패를 숨기지 않는다. 정직한 `NO-GO`가 유효한 완료 결과다.
11. 자동매매, 종목 추천, 수익 보장 문구를 만들지 않는다.
12. 데이터 수집 권한과 라이선스를 먼저 확인한다. 접근 제한 우회, CAPTCHA 우회, 로그인 정보 추출, 허용되지 않은 원문 재배포를 하지 않는다.

저장소에 선행 보고서가 말하는 코드·DB·실험 산출물이 실제로 없으면 이를 즉시 기록한다. 없는 구현이나 데이터를 있다고 가정하거나 가짜 결과를 생성하지 않는다. 권한·데이터가 필요한 경우 정확한 파일, 테이블, 기간, 필드를 적어 사용자에게 요청한다.

---

## 1. 연구 배경과 v1에서 확정된 사실

v1은 동일한 후보 풀과 시간순 평가 조건에서 다음 세 검색 방법을 비교했다.

- `B0`: 같은 테마의 최신 과거 사건
- `M-TXT`: 원인문 문자 2~3gram TF-IDF cosine
- `M-K05`: BM25와 키워드 RRF

확정된 결과는 다음과 같다.

1. 사건 관련성은 `M-TXT`가 가장 좋았다.
   - nDCG@5 `0.857`
   - B0 대비 `+0.195`
   - 95% CI `[+0.063, +0.326]`
   - 무관 사례 비율 `1%`
2. 이웃의 T+1 상승비율은 세 방법 모두 기저율을 넘지 못했다.
3. 수축 보정을 적용해도 예측 개선은 사실상 0이었다.
4. 이웃 수익률 범위는 실제 결과 분산보다 좁았다.
5. 2026 봉인 구간에서 `M-TXT`는 B0보다 좋아진 것이 아니라, B0 대비 10% 이내의 비열등 조건만 통과했다.
6. 발생 전 상태를 사용하는 `M-SETUP`, 혼합 모델 `M-MIX`, 결과 분포 직접 예측 모델은 실행되지 않았다.
7. 지수 데이터가 없어 초과수익을 계산하지 못했다.
8. 기존 outcome은 일봉·장마감 기준이며 장중 사용에 적합하지 않다.

따라서 v2의 출발점은 다음과 같다.

> `의미상 비슷한 사건`과 `이후 수익률이 비슷한 사건`은 같은 개념이 아니다.

> v1 검색기는 사례 설명을 담당하고, v2 예측기는 현재 조건에서의 결과 분포를 직접 추정해야 한다.

---

## 2. 제품 문제와 연구 목적

DAY-JA-VIEW 사용자는 테마가 움직이는 시점에 다음을 판단하려 한다.

- 이 움직임이 장중 또는 다음 거래일까지 이어질 가능성이 있는가?
- 이미 재료가 가격에 대부분 반영됐는가?
- 한 종목의 일시적 급등인가, 테마 전반으로 확산되는가?
- 현재 반응은 같은 유형의 과거 사건보다 강하거나 약한가?
- 방향을 말할 근거가 부족한 사건인가?

v2의 제품 목적은 모든 사건에 상승확률을 붙이는 것이 아니다.

> **조건이 충분한 일부 사건에서만 지속·반전 가능성을 기저율보다 더 잘 구분하고, 나머지 사건에는 판단 보류를 반환할 수 있는지 검증한다.**

연구 목적은 다음 세 가지다.

1. 직접 예측 모델이 강한 비예측 baseline보다 표본 외 Brier score를 안정적으로 개선하는지 확인한다.
2. 개선이 전체 평균이 아니라 특정 사건·시장 조건에서만 존재하는지 확인한다.
3. 개선이 확인될 경우에만 교정된 확률 또는 방향성 밴드의 제한적 제품 노출을 검토한다.

---

## 3. 핵심 연구 질문

### RQ1. 직접 예측 신호

사건 구조, 발생 전 상태, 현재 반응, 시장 국면을 결합하면 T+1 순초과수익의 방향을 기저율보다 잘 예측할 수 있는가?

### RQ2. 증분 정보

다음 정보 묶음 중 무엇이 실제 표본 외 개선을 만드는가?

- 사건 구조와 뉴스
- 발생 전 가격·거래 상태
- 탐지 이후 현재까지의 반응
- 시장·업종 국면
- 과거 유사사례의 수축 집계

### RQ3. 조건부 예측

전체 사건에는 신호가 없더라도 다음 하위집단에는 신호가 있는가?

- 신규 공식 재료
- 기존 이슈의 확정적 진전
- 단순 재부각
- 직접 수혜와 간접 관련주
- 선반영이 낮거나 높은 사건
- 확산도가 높거나 낮은 사건
- 강세·약세·고변동 시장

### RQ4. 선택적 예측

모델이 불확실한 사건에 답하지 않을 때, 고신뢰 표본의 정확도·교정·경제적 결과가 안정적으로 개선되는가?

### RQ5. 예측 목표의 적합성

T+1 종가 방향보다 다음 목표가 더 안정적인가?

- 다음 날 시가까지의 overnight gap
- 다음 날 종가까지의 연속 수익률
- T+5 순초과수익
- T+5 안에 추가 고점 발생
- 일정 수익 도달 전 일정 손실 도달 여부

RQ5의 보조 목표를 많이 시험해 우연히 좋은 결과를 고르지 않는다. 최종 모델 선택의 1차 목표는 §8에 고정한다.

---

## 4. 사전 가설

### H0. 귀무가설

전체 입력을 사용하는 직접 예측 모델은 강한 가격·시장 baseline보다 T+1 Brier score를 개선하지 못한다.

### H1. 사건 구조의 증분 가치

`CatalystType`, `EventStage`, `NoveltyType`, 발표 주체, 수혜 직접성, 공식성, 정량적 규모를 추가하면 가격·시장 baseline보다 T+1 예측력이 개선된다.

### H2. 선반영과 현재 반응의 상호작용

동일한 사건 구조라도 최근 상승폭, 52주 고점 위치, 거래 관심, 고가 유지, 확산도에 따라 지속 또는 반전 확률이 달라진다.

### H3. 신규성과 정보 확정도

새롭고 확정도가 높은 정보는 반복·재부각 정보와 다른 후속 반응 분포를 가진다.

### H4. 과거 이웃 집계의 제한적 증분 가치

원시 이웃 상승비율은 신호가 없지만, 의미 유사도·시점·표본 품질로 가중하고 기저율로 수축한 이웃 특징은 직접 예측 모델의 보조 특징으로 증분 가치가 있을 수 있다.

### H5. 선택적 예측

전체 사건 강제 예측보다 고신뢰 사건만 남긴 고정 coverage 평가에서 Brier score와 순초과수익이 개선된다.

각 가설은 반증 가능하게 유지한다. 결과를 본 뒤 가설 문구나 1차 지표를 바꾸지 않는다.

---

## 5. 범위와 비범위

### 5.1 포함

- point-in-time 데이터 감사
- 예측 시점과 실행 가능한 가격 기준 재정의
- 일봉 기반 장후 예측 연구
- 장중 shadow 예측을 위한 prospective snapshot 수집
- 지수·업종 대비 초과수익 outcome
- 사건·가격·거래·시장·유사사례 특징
- 단순 baseline, 규제 선형 모델, 제한된 비선형 모델 비교
- 시간순 walk-forward와 새 봉인 구간 평가
- 확률 교정과 선택적 예측
- 실패·표본 부족·데이터 누락 기록

### 5.2 제외

- 자동매매
- 매수·매도 추천
- 목표가 또는 수익 보장
- 사용자가 실제 체결할 수 없었던 가격을 이용한 성과 주장
- 과거 일봉으로 장중 의사결정을 가상 복원하는 행위
- 2026-01-01~08-11 구간을 새로운 최종 테스트라고 부르는 행위
- 최종 테스트 결과를 본 뒤 모델·특징·임계값 수정
- 무제한 하이퍼파라미터 탐색
- 외부 LLM의 일반 금융 지식을 정답 라벨로 사용
- 데이터 권한이 불명확한 수집

---

## 6. 두 개의 연구 트랙

장후와 장중은 사용할 수 있는 정보와 실행 가격이 다르므로 하나의 데이터셋으로 섞지 않는다.

### Track A. 장후 예측

사용 시점:

```text
T0 장 마감 후
→ 당일 확정된 사건·테마 반응·주도주 정보 사용
→ T+1 또는 T+5 결과 예측
```

목적:

- 오버나잇 또는 단기 추적에 참고할 수 있는 조건부 분포 연구
- 기존 2022~2026 일봉 자료를 활용한 historical replay

허용 입력:

- T0 장 마감까지 공개·확정된 정보
- T0 일봉 OHLCV와 당일 확산도
- T0 장 마감 시점에 알 수 있었던 주도주

금지 입력:

- T+1 이후 인포스탁 수정
- 미래 주도주 확정
- 미래 테마 편입·제외
- T+1 이후 뉴스

Track A 결과는 장중 예측 성과라고 표현하지 않는다.

### Track B. 장중 shadow 예측

사용 시점:

```text
장중 event_detected_at
→ 시스템 처리 지연 후 decision_at
→ 당시 화면에 표시할 수 있었던 snapshot만 사용
→ 이후 장마감·다음날 결과 평가
```

목적:

- 실제 DAY-JA-VIEW 장중 사용 상황의 전향적 검증
- 사용자에게 노출하지 않는 shadow prediction 축적

필수 저장값:

```text
event_id
detected_at
first_known_at
decision_at
feature_snapshot_id
feature_version
model_version
prediction_created_at
candidate_stocks_at_decision
leader_basket_at_decision
executable_price_rule
market_snapshot_id
news_snapshot_id
data_quality_flags
```

과거에 이 snapshot이 존재하지 않으면 Track B를 백필하지 않는다. 2026-09-01 이후부터 전향적으로 저장한다.

---

## 7. 관측 단위와 시점 정의

### 7.1 관측 단위

기본 관측 단위는 `query event × decision point`다.

- 같은 사건의 중복 기사와 같은 발표의 반복 등록은 하나의 `duplicate_cluster_id`로 묶는다.
- 같은 사건을 여러 시점에 재평가하면 서로 다른 decision point로 저장하되, 학습·평가 시 같은 사건 그룹임을 유지한다.
- 같은 날짜·같은 테마의 사실상 동일한 사건을 독립 표본으로 세지 않는다.

### 7.2 Track A 시점

```text
decision_at = T0 정규장 마감 후 데이터 확정 시각
entry_reference = T0 조정종가
```

실제 제품에 적용할 때 장 마감 동시 체결 가능성을 과장하지 않도록 별도 비용·슬리피지 시나리오를 적용한다.

### 7.3 Track B 시점

기본 규칙은 PoC에서 시스템 지연을 측정한 뒤 결과를 보기 전에 확정한다.

권장 기본 구조:

```text
decision_at = detected_at + 실제 P95 처리·표시 지연
entry_reference = decision_at 이후 첫 실행 가능 구간의 가격
```

1분봉만 있으면 다음 완성 1분봉 VWAP 또는 보수적인 가격을 사용한다. 호가가 있으면 매수·매도 방향별 실행 가능 호가를 사용한다. 여러 규칙 중 성과가 좋은 것을 사후 선택하지 않는다.

### 7.4 주도주 바스켓 시점

Track A는 T0 마감 시점에 실제로 알 수 있었던 주도주를 사용한다.

Track B는 `decision_at` 당시 가격·거래대금·고가 유지율 등으로 계산된 `leader_basket_at_decision`을 사용한다. 장후 인포스탁이 확정한 주도주를 장중 예측 대상 바스켓으로 소급 적용하지 않는다.

장후 확정 주도주와 장중 추정 주도주의 차이는 별도 품질 지표로 보고한다.

---

## 8. 결과 변수 정의

### 8.1 1차 목표

1차 예측 목표는 다음과 같다.

```text
y_primary = 1 if NetAbnormalReturn(T+1) > 0 else 0
```

```text
NetAbnormalReturn(T+1)
= LeaderBasketReturn(decision_at → T+1 close)
- BenchmarkReturn(decision_at → T+1 close)
- RoundTripCost
```

- Track A의 `decision_at`은 T0 마감이다.
- Track B의 `decision_at`은 실제 탐지·표시 지연을 반영한다.
- 같은 모델에서 Track A와 Track B 표본을 섞지 않는다.

### 8.2 벤치마크

사건 발생 전에 고정한 규칙을 사용한다.

1. 주도주 시장 구분에 따른 KOSPI 또는 KOSDAQ
2. 업종지수 품질과 과거 가용성이 충분하면 업종지수 보조 결과
3. 시장·업종 결과를 모두 보고하되 좋은 쪽만 선택하지 않음

베타 조정 시장모형을 추가할 경우 추정창과 최소 관측일을 outcome 명세에 사전 고정한다.

### 8.3 거래비용

실제 적용 시점의 수수료·세금·평균 스프레드·슬리피지 근거를 문서화한다.

정확한 비용을 하나로 고정하기 어렵다면 결과 확인 전에 다음 세 시나리오를 고정한다.

```text
cost_low
cost_base
cost_high
```

1차 판정은 `cost_base`로 한다. 세 비용 수준을 모두 보고하며 가장 유리한 비용만 고르지 않는다.

### 8.4 2차 목표

- `NetAbnormalReturn(T+1)` 연속값
- `NetAbnormalReturn(T+5)` 연속값과 방향
- 다음 날 시가까지의 순초과 gap
- T+5 안의 추가 고점 여부
- MFE5, MAE5
- 사전 고정한 이익·손실 장벽 중 먼저 도달한 쪽

2차 목표는 원인 분석과 향후 연구용이다. 1차 목표가 실패했는데 2차 목표 하나가 우연히 좋아 보인다는 이유만으로 v2를 GO 처리하지 않는다. 별도 v2.1 프로토콜로 재검증한다.

### 8.5 결측과 거래 불가

- 거래정지, 상장폐지, 가격 누락을 성과가 나쁜 표본이라는 이유로 삭제하지 않는다.
- 의도한 진입 시점에 거래 불가능하면 `not_executable`로 기록한다.
- 상한가 잠김, 유동성 부족 등 체결 가능성 문제를 별도 플래그로 저장한다.
- 표본 제외 규칙은 결과 계산 전에 고정하고 제외 건수와 사유를 보고한다.

---

## 9. 데이터 감사

모델링 전에 `v2_data_audit.md`를 작성한다.

### 9.1 기존 데이터 확인

- 사건 39,643건의 기간·필드·원문·수정 이력
- 엄격 outcome 16,575건의 연도별·테마별 분포
- `first_known_at`, `published_at`, `known_at`, `collected_at` 신뢰도
- 주도주가 언제 어떤 근거로 확정됐는지
- 사건 중복클러스터와 동일 프로젝트 연결
- 7.2M 가격행의 수정주가·기업행위 버전
- 무가격 466종목과 lead 기준 86종목의 영향
- 상장폐지·종목코드 변경·합병·분할 처리
- 테마 구성과 관련주 역할의 valid_from/valid_to
- 시장·업종 지수의 가용 기간
- 장중 event snapshot과 분봉·체결 데이터 존재 여부

### 9.2 필드별 품질표

각 필드에 다음을 기록한다.

```text
field_name
definition
source
available_from
available_to
known_at_semantics
missing_rate_by_year
revision_history
point_in_time_safe
allowed_track
license_or_permission
```

### 9.3 필수 중단 조건

다음 상황에서는 해당 트랙을 중단하고 이유를 보고한다.

- decision 시점을 신뢰할 수 없음
- 주도주 바스켓이 사후 정보로만 존재함
- 벤치마크 가격이 없어 1차 outcome을 계산할 수 없음
- 미래 테마 구성과 당시 구성을 분리할 수 없음
- 수정주가·상장폐지 처리가 결과를 심각하게 왜곡함
- 필요한 데이터의 사용 권한이 없음

Track B 데이터가 없다는 이유로 전체 연구를 중단하지 않는다. Track A를 계속하고 Track B prospective 수집기를 구현한다.

---

## 10. 특징 집합

모든 특징에는 `as_of`, `feature_version`, `source_version`, `point_in_time_safe`를 저장한다.

### F0. 데이터 품질과 기본 범주

- Theme, SubTheme
- KOSPI/KOSDAQ
- 사건 시각·요일·월
- 표본 가용성
- 주도주 수
- 데이터 품질 플래그

종목 ID, 프로젝트 ID, 언론사 ID를 그대로 고차원 암기 특징으로 사용하지 않는다. 사용할 경우 재등장 엔티티에만 성과가 생기는지 별도 검증한다.

### F1. 사건 구조

- CatalystType
- EventStage
- NoveltyType
- Direction
- ActorType, Actor
- Geography
- BeneficiaryType
- 직접 수혜·간접 수혜
- 동일 Project 여부와 이전 단계
- 공식 발표·언론 보도·추정 여부
- 정보 확정도
- 정량적 규모 존재 여부
- 계약·정책 규모 ÷ 수혜기업 매출 또는 시가총액
- 루머 → 협상 → 선정 → 계약 등의 단계 변화

정량값을 신뢰할 수 없으면 억지로 추출하지 않고 `unknown`과 품질 플래그를 사용한다.

### F2. 텍스트와 의미

- v1 M-TXT 표현
- 사건 요약 임베딩
- 원인문 임베딩
- 뉴스 묶음 임베딩
- 금융 도메인 감성
- 수혜 대상별 감성
- 최근 동일 기업·테마 뉴스 대비 신규성
- 이전 기사와의 최대 유사도

검색 관련성용 임베딩과 수익 예측용 텍스트 특징을 구분한다. 수익 정답에 맞춰 학습되는 텍스트 모델은 오직 학습 fold 안에서 학습한다.

### F3. 발생 전 상태

- 테마 최근 5·20·60일 수익률
- 시장 대비 5·20·60일 초과수익
- 당시 후보 주도주 최근 5·20일 수익률
- 52주 고점 대비 위치
- 최근 변동성
- 최근 거래대금과 동일 시각 기준 거래 관심
- 마지막 같은 테마 사건 이후 경과일
- 최근 5·20·60일 동일 테마 사건 빈도
- 최근 같은 프로젝트 단계 변화
- 최근 관련주 신규 편입·제외

### F4. 현재 반응

Track A:

- T0 시가 gap
- T0 종가 수익률
- 고가 대비 종가 위치
- 저가 대비 종가 위치
- T0 거래대금 증가율
- 주도주 집중도
- 상승 종목 비율과 확산도
- +5%, +10% 이상 종목 수

Track B:

- decision_at까지의 수익률
- 시가 gap
- 현재가의 당일 고가·저가 내 위치
- 고점 후 되돌림
- 실시간 거래대금 증가율
- 확산도와 확산 속도
- 주도주 집중도
- 테마 순위 변화 속도
- 탐지 후 경과시간
- 장 시작 후 경과시간

Track B에는 T0 종가·당일 최종 고가·당일 최종 거래대금을 절대 사용하지 않는다.

### F5. 시장 국면

- KOSPI·KOSDAQ 당일 및 최근 수익률
- 시장 변동성
- 전체 상승·하락 종목 비율
- 소형주·대형주 상대강도
- 해당 업종 수익률과 거래 관심
- 시장 breadth
- 최근 급등 테마의 동시 개수

### F6. 과거 유사사례 특징

v1 M-TXT가 decision 시점 이전에 검색한 과거 사건만 사용한다.

- 유효 이웃 수
- Top K 의미 유사도 분포
- 이웃 발생일의 recency
- 사건 단계 일치율
- 신규성 일치율
- 직접 수혜 일치율
- 이웃 T+1·T+5 결과의 평균·중앙값·분산
- 유사도·recency 가중 outcome
- 기저율로 수축한 상승비율
- 이웃 결과의 불일치 정도

원시 `상승 n / 유효 n`을 모델 확률로 사용하지 않는다. F6는 다른 특징과 함께 쓰는 후보 입력이며, 증분 가치가 없으면 제거한다.

### F7. 상호작용 후보

다음 상호작용은 경제적 가설이 있으므로 사전 지정한다.

- 신규성 × 최근 테마 수익률
- EventStage × 정량적 규모
- 공식성 × 직접 수혜
- 확산도 × 주도주 고가 유지
- 선반영 × 현재 거래 관심
- 시장 위험선호 × 소형주 중심 테마
- 이웃 outcome × 현재 setup 차이

모든 조합을 무차별 생성하지 않는다.

---

## 11. 예측 모델 후보

표본 크기와 과최적화 위험을 고려해 단순 모델부터 진행한다.

### P0. 전역 기저율

학습 fold의 전체 상승률을 반환한다.

### P1. 조건부 기저율

학습 fold 안에서 다음 그룹의 부분 풀링 기저율을 계산한다.

- Theme 또는 상위 Theme
- CatalystType
- EventStage
- NoveltyType
- 시장 국면

희소 그룹은 전역 기저율로 수축한다.

### P2. 가격·시장 baseline

F0 + F3 + F4 + F5만 사용하는 규제 로지스틱 회귀다.

이 모델이 v2의 **강한 1차 비교 기준**이다. 사건 텍스트나 과거 사례를 쓰지 않아도 현재 가격 상태만으로 어느 정도 예측할 수 있으므로, 전체 모델은 이를 넘어야 한다.

### P3. 사건 구조 모델

F0 + F1 + 제한된 F2를 사용하는 규제 로지스틱 회귀 또는 계층형 모델이다.

### P4. 전체 선형 모델

F0~F7 중 사전 승인된 특징을 결합한 elastic-net 로지스틱 회귀다.

### P5. 제한된 비선형 모델

CatBoost, LightGBM 또는 동급의 트리 부스팅 중 하나를 주 모델 후보로 고정한다.

- 깊이·학습률·트리 수 탐색 범위를 사전에 제한
- early stopping은 학습 내부 시간순 validation에서만 수행
- 범주형 누수 방지
- 중요도와 SHAP은 설명용이며 인과효과로 해석하지 않음

여러 트리 라이브러리를 모두 넓게 탐색해 가장 좋은 결과만 고르지 않는다.

### P6. 텍스트 증분 모델

P4 또는 P5에 다음 중 사전 고정한 표현 하나를 추가한다.

- 한국어 일반 임베딩
- 금융 도메인 적응 임베딩
- 학습 fold에서만 학습한 수익 타깃 적응 텍스트 점수

임베딩 교체만으로 예측력이 생긴다고 가정하지 않는다.

### P7. 유사사례 증분 모델

선택된 P4 또는 P5에 F6만 추가한다. `P7 - base predictor`의 차이로 과거 사례 집계의 증분 가치를 측정한다.

### P8. 앙상블

P4와 P5가 서로 다른 시간·테마 슬라이스에서 보완적이고 둘 다 독립적으로 P2를 개선한 경우에만 검토한다. 그렇지 않으면 실행하지 않는다.

### 실행 보류 모델

다음은 v2 1차 연구에서 기본적으로 실행하지 않는다.

- 대형 딥러닝 시계열 모델
- end-to-end LLM 수익률 예측
- 수천 개 기술지표 자동 생성
- 결과가 비슷한 사건을 의미와 무관하게 이웃으로 만드는 metric learning
- reinforcement learning 매매 정책

단순 모델에서 증분 신호가 확인되지 않으면 복잡한 모델로 신호를 만들어내려 하지 않는다.

---

## 12. 실험 매트릭스

각 실험은 고정된 코드, 데이터 snapshot, fold, seed 목록, 특징 버전, outcome 버전으로 실행한다.

| 실험 ID | 목적 | 모델/입력 | 1차 비교 |
|---|---|---|---|
| `V2-R00` | v1 재현 | M-TXT 이웃 집계 | 기존 보고서 |
| `V2-D00` | 데이터·PIT 감사 | 모델 없음 | 완료 조건 |
| `V2-O00` | outcome 검증 | raw/abnormal/net abnormal | 수동 표본 |
| `V2-B00` | 전역 기저율 | P0 | 기준 |
| `V2-B01` | 조건부 기저율 | P1 | P0 |
| `V2-B02` | 강한 가격 baseline | P2 | P1 |
| `V2-M10` | 사건 구조 증분 | P3 | P1 |
| `V2-M20` | 전체 선형 | P4 | P2 |
| `V2-M30` | 제한 비선형 | P5 | P2·P4 |
| `V2-A10` | 텍스트 증분 | P6 | 선택된 P4/P5 |
| `V2-A20` | 유사사례 증분 | P7 | 선택된 P4/P5 |
| `V2-S10` | 확률 교정 | 최종 후보 | 미교정 후보 |
| `V2-S20` | 선택적 예측 | 고정 coverage | 전체 coverage |
| `V2-L00` | 2026 seen 진단 | 동결 후보 | 비확증 참고 |
| `V2-H00` | 새 봉인 테스트 | 최종 동결 모델 | P2·P1 |

`V2-L00` 결과는 모델 선택, 특징 제거, 임계값 변경에 사용하지 않는다. 이미 본 기간이므로 오직 과거 v1과의 연속성 진단으로만 보고한다.

---

## 13. 시간 분할과 누수 방지

### 13.1 개발 walk-forward

엄격 outcome 가용성이 2022년부터라는 현재 정보를 기준으로 다음 기본 fold를 사용한다.

```text
Fold 1: train 2022       → validate 2023
Fold 2: train 2022-2023  → validate 2024
Fold 3: train 2022-2024  → validate 2025
```

2022년 안에 충분한 학습 이전 기간이 없으면 Fold 1은 보조로 표시하고 Fold 2~3을 주 개발 근거로 사용한다. 실제 데이터 기간이 달라지면 결과를 보기 전에 `v2_research_protocol.md`에서 합리적으로 조정하고 버전을 고정한다.

### 13.2 2026 seen 구간

```text
2026-01-01 ~ 2026-08-11
```

이 구간은 v1에서 이미 열람됐다.

- v2의 독립 최종 테스트가 아님
- 모델 선택 금지
- 하이퍼파라미터 선택 금지
- probability threshold 선택 금지
- 보고서에는 `legacy seen diagnostic`로만 표시

최종 모델 구조와 선택 정책을 2022~2025로 고정한 뒤 한 번만 진단 실행할 수 있다.

### 13.3 새 봉인 구간

시작일:

```text
2026-09-01
```

봉인 해제는 다음 두 조건을 모두 충족한 뒤 시행한다.

1. 최소 6개월 경과
2. 1차 outcome이 성숙한 유효 사건 최소 2,500건 확보

T+5·T+20 보조 outcome은 각각의 horizon이 모두 성숙한 뒤 평가한다. 표본이 부족하면 기간을 연장하며 목표 표본 수를 사후 낮추지 않는다.

### 13.4 purging과 embargo

- 최대 평가 horizon만큼 fold 경계에서 purging 또는 embargo
- 같은 `duplicate_cluster_id`는 여러 fold에 분산 금지
- 같은 날 같은 테마의 결합 사건은 그룹 단위 처리
- 반복 decision point는 사건 그룹 단위 bootstrap
- 결과 기간이 겹친 표본의 상관을 반영

### 13.5 point-in-time 금지 목록

- 미래 기사와 수정 기사
- 장후 확정 정보를 장중 입력으로 소급
- 미래 주도주·관련주
- 미래 시장·업종 수익률
- 전체 기간으로 표준화한 값
- 전체 기간으로 학습한 target encoder
- test outcome을 이용한 calibration
- test 성과를 보고 선택한 confidence threshold

---

## 14. 학습·튜닝·교정 규칙

1. 결측 처리, 표준화, 범주 인코딩은 각 train fold에서만 적합한다.
2. 클래스 가중치는 Brier calibration을 해칠 수 있으므로 기본적으로 사용하지 않는다. 사용할 경우 사전 실험으로 별도 등록한다.
3. seed는 고정 목록을 사용하고 가장 좋은 seed만 보고하지 않는다.
4. 하이퍼파라미터 탐색 공간은 `v2_model_selection_policy.md`에 기록한 뒤 실행한다.
5. calibration은 Platt, isotonic, beta calibration 중 최대 두 방법만 비교한다.
6. calibration 데이터는 모델 학습 데이터와 시간순으로 분리한다.
7. 최종 선택은 평균 성능뿐 아니라 fold 분산과 calibration을 함께 본다.
8. 결측 자체가 정보일 수 있으므로 결측 indicator를 유지하되 미래 수집 상태를 암기하지 않는지 확인한다.
9. 수익률이 비슷해지도록 검색 이웃을 학습시키는 경우 의미 관련성 제약을 둔다. 설명용 검색 품질을 훼손하는 예측 최적화는 금지한다.

---

## 15. 평가 지표

### 15.1 1차 통계 지표

- Brier score
- Brier Skill Score against P2
- P2와의 paired 차이에 대한 95% block-bootstrap CI

```text
BrierSkill = 1 - Brier(model) / Brier(P2)
```

`BrierSkill > 0`은 P2보다 좋다는 뜻이다.

### 15.2 보조 분류 지표

- Log loss
- ROC-AUC
- PR-AUC
- 방향 정확도
- 상승확률 decile별 실제 상승률

정확도 하나로 결론내리지 않는다.

### 15.3 교정 지표

- reliability diagram
- calibration intercept와 slope
- Expected Calibration Error
- 예측확률 구간별 표본 수

### 15.4 연속 수익률 지표

- MAE
- pinball loss
- 50%·80% 구간 coverage
- CRPS 가능 시 사용
- 예측 구간 폭

### 15.5 경제적 보조 지표

- 비용 전·후 순초과수익
- 최대낙폭
- hit rate
- turnover
- 평균 MFE·MAE
- 체결 불가 비율

경제적 성과는 보조 지표다. 거래 시뮬레이션 성과 하나만으로 확률 모델을 채택하지 않는다.

### 15.6 선택적 예측 지표

다음 고정 coverage를 모두 보고한다.

```text
10%, 20%, 30%, 50%, 100%
```

- coverage별 Brier
- coverage별 calibration
- coverage별 방향 정확도
- coverage별 비용 후 순초과수익
- risk-coverage curve

좋아 보이는 coverage 하나만 골라 보고하지 않는다. 제품에서 필요한 최소 coverage는 최종 테스트 전에 제품 결정 문서로 고정한다.

### 15.7 안정성 슬라이스

- 연도·분기
- Theme/SubTheme
- CatalystType
- EventStage
- 신규·진전·재부각
- 공식·비공식
- 직접·간접 수혜
- 선반영 구간
- 확산도 구간
- KOSPI/KOSDAQ
- 대형주·중형주·소형주 중심
- 강세·약세·고변동 시장
- 장중 탐지 시각
- 표본 수와 데이터 품질

슬라이스는 모델 선택용 무한 탐색이 아니라 실패 위치를 찾는 진단이다.

---

## 16. 모델 선택 정책과 GO/NO-GO

상세 수치와 비교 순서는 첫 모델 실행 전에 `v2_model_selection_policy.md`에 봉인한다.

### Gate 0. 데이터 무결성

다음을 모두 충족해야 한다.

- point-in-time 규칙 자동 테스트 통과
- outcome 수동 표본 검산 통과
- 기업행위·거래정지·상폐 처리 검증
- 사건 중복 처리 검증
- 미래 주도주·미래 테마 구성 누수 없음

실패하면 모델 성과와 무관하게 `NO-GO`다.

### Gate 1. 개발 구간 증분 예측력

최종 후보는 P2 대비:

- 평균 Brier Skill이 양수
- paired block-bootstrap 95% CI의 하한이 0보다 큼
- log loss가 악화되지 않음
- 개선 방향이 주요 walk-forward fold에서 일관됨

단 한 fold 또는 한 테마가 전체 개선을 만드는 경우 보류한다.

### Gate 2. 교정

- 예측확률이 실제 빈도와 단조롭게 대응
- P2보다 ECE가 악화되지 않음
- 극단 확률에 충분한 표본이 없음에도 0%·100%에 가까운 값을 출력하지 않음

### Gate 3. 선택적 예측

- 사전 고정 coverage에서 confidence가 높을수록 오차가 줄어드는 관계가 존재
- 일부 표본 제거만으로 우연히 만들어진 결과가 아님
- coverage와 표본 수가 제품에서 해석 가능

### Gate 4. 경제적 현실성

- 실제 decision price 기준
- `cost_base` 적용 후 결과 보고
- 체결 불가 표본 포함 규칙 준수
- 시장·업종 대비 초과수익 확인

Gate 4는 통계 Gate 1~3을 대신하지 않는다.

### Gate 5. 새 봉인 테스트

새 봉인 구간에서 다음을 확인한다.

- 최종 동결 모델의 Brier Skill이 P2 대비 양수
- paired block-bootstrap 95% CI의 하한이 0보다 큼
- calibration이 허용 범위 안에 있음
- 개발 구간에서 선택한 coverage 규칙이 그대로 작동
- 특정 사건·월·테마 하나에 결과가 집중되지 않음

점 추정치는 개선됐지만 95% CI가 0을 포함하면 `GO-PROBABILITY` 또는 `GO-BAND-ONLY`로 판정하지 않고 `RESEARCH-ONLY`로 유지한다.

봉인 결과를 본 뒤 모델을 수정하면 해당 결과는 폐기하고 다음 새 봉인 기간을 확보한다.

### 최종 판정

#### `GO-PROBABILITY`

Gate 0~5를 모두 통과하고, 사용자에게 확률을 노출할 수 있을 정도로 교정과 표본 수가 충분하다.

#### `GO-BAND-ONLY`

순위·방향 구분력은 있으나 확률 교정이 충분하지 않다. `지속 우위 / 판단 보류 / 반전 위험` 같은 밴드만 검토하고 수치 확률은 금지한다.

#### `RESEARCH-ONLY`

일부 슬라이스 신호가 있으나 독립 검증이나 표본 수가 부족하다. shadow 상태를 유지한다.

#### `NO-GO`

강한 baseline 대비 개선이 없거나 새 봉인 테스트에서 재현되지 않는다. 방향 예측을 제품에서 제외하고 v1 관측 요약만 유지한다.

---

## 17. 과최적화와 다중검정 방지

1. 모든 실험을 registry에 기록한다.
2. 1차 목표와 P2 비교를 유일한 주 결론으로 둔다.
3. 모델 후보 수와 하이퍼파라미터 범위를 제한한다.
4. 같은 데이터로 특징 선택과 최종 성능 평가를 반복하지 않는다.
5. 날짜·사건 그룹 block bootstrap을 사용한다.
6. 주요 모델 비교군에 다중검정 보정을 적용한다.
7. White's Reality Check 또는 적절한 대안과 PBO를 보조 진단으로 검토한다.
8. 평균 성과뿐 아니라 fold 분산과 최악 구간을 보고한다.
9. 결과가 작은 종목·희소 테마·특정 시장 국면에만 몰리는지 확인한다.
10. 복잡한 모델의 개선이 단순 모델 대비 미미하면 단순 모델을 선택한다.
11. 실패한 모델과 불리한 비용 가정을 삭제하지 않는다.

---

## 18. 사용자 출력 원칙

연구 중에는 v2 예측을 사용자에게 노출하지 않는다.

통과 후에도 다음 두 정보를 분리한다.

```text
[과거 관측]
비슷한 과거 사건 20건 중 11건 상승

[조건부 모델 추정]
현재 사건·가격·시장 조건을 함께 반영한 결과
```

과거 상승비율을 모델 확률처럼 표현하지 않는다.

사용 금지 문구:

- 내일 오를 확률
- 매수 신호
- 목표 수익률
- 안전한 구간
- 과거에도 올랐으니 이번에도 상승
- AI가 추천한 종목

허용 후보 문구는 사용자 테스트와 법률·컴플라이언스 검토 후 확정한다.

예측 근거가 부족한 경우 반드시 다음 상태를 지원한다.

```text
판단 보류
현재 조건에서는 방향 우위가 확인되지 않았습니다.
```

모든 출력에는 다음을 연결할 수 있어야 한다.

- prediction as_of
- 모델 버전
- 데이터 버전
- 사용한 특징 범주
- 표본·coverage 경고
- 관측 통계와 모델 추정의 구분

---

## 19. 구현 구조 권장안

실제 저장소 구조를 먼저 확인한 뒤 조정한다. 없는 경로를 있다고 가정하지 않는다.

```text
engine_v2/
  audit/
    point_in_time
    timestamp_quality
    survivorship
    leader_leakage

  outcomes/
    benchmarks
    execution_prices
    costs
    net_abnormal_returns

  features/
    event_structure
    text
    pre_event_setup
    current_reaction
    market_regime
    analog_features
    snapshots

  predictors/
    global_base
    conditional_base
    logistic
    hierarchical
    boosted_tree
    calibration
    selective

  replay/
    walk_forward
    purging
    metrics
    bootstrap
    slices

  shadow/
    snapshot_writer
    prediction_logger
    maturity_tracker
    holdout_seal

  serving/
    prediction_contract
    abstention
    explanations
    versioning
```

v1 검색기와 v2 예측기는 공용 검증 라이브러리를 사용할 수 있지만 버전과 출력 계약은 분리한다.

---

## 20. 필수 자동 테스트

### 데이터·시점

- 모든 특징의 `as_of <= decision_at`
- 모든 뉴스의 `published_at <= decision_at`
- 모든 종목 관계의 `known_at <= decision_at`
- Track B에 T0 최종 일봉 값이 들어가지 않음
- 후보 유사사례 날짜가 query보다 과거
- 미래 확정 주도주가 장중 basket에 들어가지 않음
- 동일 사건 중복이 fold를 넘지 않음

### outcome

- T+1은 다음 달력일이 아니라 다음 거래일
- entry와 exit 가격 규칙 검증
- 지수와 종목 거래일 정렬
- 조정주가·기업행위 검증
- 비용 차감 방향 검증
- 이벤트 하나당 정의된 관측값 수 검증
- 거래 불가 처리 검증

### 모델

- train 밖 fit 금지
- 전체 기간 표준화 금지
- test target encoding 금지
- calibration set 분리
- seed 재현
- probability 범위와 결측 처리
- 동일 입력·버전에서 동일 출력

### 평가

- P0·P1·P2 결과 재현
- Brier와 Skill 계산 검산
- block bootstrap 그룹 검증
- coverage별 표본 수 합계
- 슬라이스 합계와 전체 표본 일치

### shadow·봉인

- prediction이 outcome보다 먼저 생성됨
- prediction 수정 이력 append-only
- 봉인 기간 outcome 조회 차단
- 봉인 해제 이벤트 감사 로그

---

## 21. 단계별 실행 계획

### Phase 0. 저장소·데이터·권한 감사

1. 저장소 전체 구조 확인
2. v1 코드, DB migration, 실험 registry, run JSON 확인
3. 데이터 소스와 사용 권한 확인
4. 시점 필드와 주도주 확정 시점 감사
5. 지수·업종·분봉 가용성 확인
6. 최신 1차 문헌 검색과 `v2_literature_review.md` 작성
7. `v2_data_audit.md` 작성

완료 조건:

- Track A 가능 여부 확정
- Track B 과거 재생 가능 여부 확정
- 누락 데이터와 정확한 요청 목록 확정

### Phase 1. outcome과 실행 가격

1. `v2_outcome_definition.md` 작성
2. benchmark와 비용 규칙 고정
3. Track A outcome 계산
4. 수동 표본 검산
5. raw return과 abnormal return 차이 분석
6. 장중 snapshot이 없으면 Track B writer 구현 시작

완료 조건:

- outcome 단위테스트 통과
- 표본 제외 사유·건수 보고

### Phase 2. feature snapshot

1. `v2_feature_dictionary.md` 작성
2. F0~F7 PIT 계산
3. 누락률·분포·이상치 확인
4. 미래정보 adversarial audit
5. 고차원 엔티티 암기 여부 확인

완료 조건:

- 모든 학습 특징에 as_of와 version 존재
- Track별 허용 특징 분리

### Phase 3. baseline

1. P0
2. P1
3. P2
4. walk-forward 성능과 calibration 보고

P2가 약한 dummy baseline이 되지 않도록 가격·시장 특징을 완성한다.

### Phase 4. 직접 예측 후보

1. P3 사건 구조
2. P4 전체 선형
3. P5 제한 비선형
4. 사전 고정 ablation
5. fold·테마·시장 슬라이스

Gate 1의 가능성이 전혀 없으면 복잡한 추가 모델을 중단하고 원인을 기록한다.

### Phase 5. 증분 특징과 선택적 예측

1. P6 텍스트 증분
2. P7 과거사례 증분
3. calibration
4. coverage grid 선택적 예측
5. 비용 후 보조 평가

### Phase 6. 선택 정책 봉인

1. 후보 하나 또는 NO-GO 선택
2. `v2_model_selection_policy.md` 최종 서명·해시
3. 코드·데이터·feature·outcome 버전 동결
4. 2026 seen 구간 1회 진단
5. 진단 결과로 수정 금지

### Phase 7. prospective shadow

1. 2026-09-01 이후 snapshot·prediction append-only 저장
2. 사용자 비노출
3. 데이터 품질과 누락만 모니터링
4. outcome 성과를 이용한 모델 변경 금지
5. 봉인 해제 조건 충족 대기

데이터 수집 장애를 수정할 수는 있지만 예측 결과를 보고 모델을 바꾸면 버전을 종료하고 새로운 봉인 구간을 시작한다.

### Phase 8. 봉인 해제와 최종 판정

1. 해제 조건 확인
2. H00 단 한 번 실행
3. Gate 0~5 판정
4. `v2_research_report.md`
5. `v2_model_card.md`
6. `v2_go_no_go.md`

---

## 22. 필수 산출물

1. `v2_literature_review.md`
   - 검색식, 포함·제외 기준, 한국시장 적용 한계
2. `v2_data_audit.md`
   - 출처, 권한, 기간, 누락, 수정 이력, PIT 안전성
3. `v2_outcome_definition.md`
   - decision, 실행 가격, benchmark, 비용, 결측 처리
4. `v2_feature_dictionary.md`
   - 모든 특징의 정의·단위·시점·버전
5. `v2_research_protocol.md`
   - fold, purging, seed, bootstrap, 실험 순서
6. `v2_model_selection_policy.md`
   - Gate, 모델 비교, calibration, coverage, GO/NO-GO
7. `v2_experiment_registry.csv`
   - 모든 시도와 실패
8. `v2_runs/*.json`
   - 코드·데이터·설정·결과·환경 해시
9. `v2_research_report.md`
   - 전체 결과, 불확실성, 슬라이스, 실패
10. `v2_model_card.md`
   - 허용 용도, 금지 용도, 데이터 범위, 알려진 한계
11. `v2_go_no_go.md`
    - `GO-PROBABILITY`, `GO-BAND-ONLY`, `RESEARCH-ONLY`, `NO-GO`
12. shadow prediction 저장소와 maturity tracker
13. 자동 테스트와 재현 명령

---

## 23. 첫 진행 보고 형식

연구 시작 후 첫 보고에는 다음을 포함한다.

```text
1. 실제로 존재하는 코드·DB·artifact
2. Track A에 사용할 수 있는 데이터
3. Track B historical snapshot 존재 여부
4. 신뢰 가능한 decision timestamp 범위
5. 당시 알 수 있었던 주도주 복원 가능 범위
6. 시장·업종 지수 가용성
7. 상폐·기업행위·무가격 종목 영향
8. 합법적으로 사용할 수 없는 데이터
9. 예상 Track A 유효 표본 수
10. 2026-09 prospective 봉인 구현 가능 여부
11. 사용자에게 필요한 정확한 권한·파일·결정
12. 다음 즉시 실행할 baseline 계획
```

막연하게 “데이터가 필요하다”고 하지 말고 정확한 테이블·필드·기간·권한을 적는다.

---

## 24. 예상되는 주요 실패 원인과 대응

### 시점 불일치

현재 사건을 오전에 보지만 outcome이 종가 진입 기준이면 제품 상황과 맞지 않는다.

대응:

- Track A와 Track B 분리
- 장중은 prospective snapshot만 사용

### 사후 주도주 누수

당일 종가까지 오른 종목을 사후 주도주로 고른 뒤 오전에 알았던 것처럼 사용하면 성과가 부풀려진다.

대응:

- `leader_basket_at_decision` 저장
- 장후 확정 주도주와 분리

### 시장 상승을 사건 효과로 오인

대응:

- net abnormal return을 1차 outcome으로 사용

### 선반영 미측정

대응:

- 최근 5·20·60일 수익률, 고점 위치, 반복 기사, 같은 프로젝트 빈도

### 문구 암기

대응:

- 시간순 평가
- 엔티티 재등장/미등장 슬라이스
- 사건 구조와 텍스트 ablation

### 표본 희소성

대응:

- 부분 풀링
- 단순 모델 우선
- 판단 보류
- 과도한 세부 테마 모델 금지

### 다중검정

대응:

- 고정 실험 매트릭스
- 1차 지표 하나
- 모든 실험 registry
- 새 봉인 구간

### 확률 오해

대응:

- calibration Gate 통과 전 비노출
- 관측 통계와 모델 추정 분리
- band-only와 abstention 지원

---

## 25. 연구 완료 조건

다음 항목이 모두 충족되어야 v2 연구가 완료된다.

- v1 실패 범위가 유지되고 왜곡되지 않음
- 검색기와 예측기 분리
- Track A/Track B 시점 분리
- PIT 데이터 감사 완료
- 실행 가능한 가격과 순초과수익 outcome 검증
- P0·P1·P2 baseline 구현
- 사전 고정 모델·ablation 실행
- calibration·coverage 평가
- 시간순 walk-forward와 block-bootstrap
- 2026 seen 구간 비확증 처리
- 2026-09 이후 새 봉인 구간 확보
- 새 봉인 테스트 단 한 번 실행
- GO/NO-GO 판정
- 재현 가능한 코드·설정·데이터 버전
- 실패·미실행 항목 포함 보고서
- 사용자 노출 허용·금지 조건을 담은 모델 카드

좋은 수익률 숫자가 나오는 것만으로 완료 처리하지 않는다.

---

## 26. 관련 연구와 적용 원칙

다음 연구는 v2 가설과 방법론을 설계하는 출발점으로 사용한다.

1. A. Craig MacKinlay, **Event Studies in Economics and Finance**
   - 사건 전후 절대수익과 시장 대비 초과수익을 분리하는 기본 근거
   - <https://www.bu.edu/econ/files/2011/01/MacKinlay-1996-Event-Studies-in-Economics-and-Finance.pdf>
2. Zheng Tracy Ke, Bryan T. Kelly, Dacheng Xiu, **Predicting Returns With Text Data**
   - 수익률 목표에 맞춘 지도형 텍스트 특징과 fresh/stale news 구분
   - <https://www.nber.org/papers/w26186>
3. Shihao Gu, Bryan Kelly, Dacheng Xiu, **Empirical Asset Pricing via Machine Learning**
   - 모멘텀·유동성·변동성과 비선형 상호작용의 표본 외 비교
   - <https://www.nber.org/papers/w25398>
4. Xiao Ding, Yue Zhang, Ting Liu, Junwen Duan, **Knowledge-Driven Event Embedding for Stock Prediction**
   - 단어 유사도를 넘어 주체·행동·객체와 지식 관계를 결합하는 사건 표현
   - <https://aclanthology.org/C16-1201/>
5. Halbert White, **A Reality Check for Data Snooping**
   - 반복 모델 탐색의 우연한 성과 보정
   - <https://doi.org/10.1111/1468-0262.00152>
6. David H. Bailey et al., **The Probability of Backtest Overfitting**
   - 백테스트 과최적화 보조 진단
   - <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253>

이 연구들의 해외시장 결과를 DAY-JA-VIEW 또는 한국 테마주의 예측력 증거로 인용하지 않는다. 특징·가설·검증 방식의 후보를 제공할 뿐이며, 모든 제품 주장은 DAY-JA-VIEW의 point-in-time 한국시장 데이터와 새 봉인 구간 결과로 결정한다.

연구 시작 시 더 직접적인 한국어 금융 뉴스·KRX 테마주·사건 단계·장중 지속성 연구가 있는지 최신 1차 문헌을 검색하고 `v2_literature_review.md`에 검색식, 포함·제외 기준, 핵심 한계와 함께 기록한다. 문헌에서 성과가 좋았다는 이유만으로 사전 실험 매트릭스를 무제한 확장하지 않는다.

---

## 27. 최종 원칙

> 비슷한 과거 사건의 승률을 미래 확률로 오해하지 않는다.

> 사용자가 실제로 판단한 시점에 알 수 있었던 정보와 실제로 체결할 수 있었던 가격만 사용한다.

> 모든 사건에 답하는 모델보다, 답할 근거가 있는 사건과 없는 사건을 구분하는 모델을 우선한다.

> 강한 baseline과 새 봉인 데이터에서 재현되지 않는 예측 신호는 제품 기능이 아니다.
