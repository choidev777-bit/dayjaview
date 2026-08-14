# ADR-007. 과거 유사사례 검색의 온톨로지 재검증 출시 게이트

- 상태: `accepted`
- 결정일: 2026-08-14
- 적용 범위: 과거 유사사례 연구 artifact의 승인, 운영 API 연결과 사용자 노출
- 현재 외부 게이트: `B-HISTORY-GATE` 미해제
- 관련 문서: [PRD.md](../PRD.md), [system_architecture.md](../system_architecture.md), [api_contract.md](../api_contract.md), [implementation_roadmap.md](../implementation_roadmap.md)

## 배경

M-TXT v1은 문자 n-gram 기반 과거 사례 검색의 기준선으로 연구됐지만 최종 제품 검색기가 확정됐다는 증거가 아니다. 관련성 평가는 1인 검수에 의존했고, 이웃의 T+1 결과는 기저율을 개선하지 못했으며 예측구간도 실제 변동을 과소포착했다.

온톨로지와 혼합 검색은 아직 새 봉인 구간에서 재검증되지 않았다. v1 결과를 일반 사용자 API에 바로 연결하거나 미래 outcome으로 후보를 고르면 검증 상태를 제품 약속으로 바꾸고 미래 정보 누수를 만든다.

## 결정

1. M-TXT v1, `K=20`, `leader_ew_v1`은 비교 가능한 내부 기준선으로 보존한다. 재검증 전에는 연구와 승인된 제한 파일럿에만 사용할 수 있고 일반 사용자 유사사례 경로에 연결하지 않는다.
2. 사용자 노출 후보는 같은 candidate pool과 fold에서 다음을 비교한다.
   - M-TXT v1
   - versioned ontology 구조 매칭
   - M-TXT와 ontology의 혼합 검색·재정렬
3. 검색 입력은 `decisionAt` 당시 알 수 있던 point-in-time 정보로 제한한다. 현재 관련주를 과거 사건에 소급하지 않고 현재 진행 Event를 자기 후보에 포함하지 않는다.
4. T+1·T+5·T+20 outcome은 후보와 순서를 고정한 **뒤** 결합한다. Outcome repository를 retrieval score 입력 interface에 제공하지 않고 `sort=outcome`을 만들지 않는다.
5. 관련성 평가는 2인 이상이 outcome을 보지 않은 상태에서 blind로 수행하고 P@5, nDCG@5와 무관 사례 비율을 같은 기준으로 비교한다. 불일치 조정 결과를 보존한다.
6. ontology 또는 혼합 방식은 M-TXT보다 관련성이 개선되거나 통계적으로 동률이면서 설명 가능성·운영성이 명확히 좋아야 채택할 수 있다.
7. 기존 2026-01-01~2026-08-11 봉인 구간은 새 모델의 최종 채택 근거로 재사용하지 않는다. 후보·parameter를 고정한 뒤 2026-09 이후의 새 미사용 봉인 구간에서 한 번 평가한다.
8. 검색 관련성 승인과 방향 예측 가능성은 별도 결론이다. v2 예측기는 별도 입력, contract, registry alias와 출시 gate를 가지며 Historical Matching API에 포함하지 않는다.
9. 채택 결과는 dataset, ontology, retrieval engine·config, trading calendar와 outcome version을 가리키는 immutable artifact로 publish한다. mutable 연구 출력이나 승인되지 않은 `latest` 결과를 production에서 읽지 않는다.
10. 운영 출시 순서는 다음과 같다.

```text
연구 후보
offline 평가
2인 blind 관련성 검수·불일치 조정
새 미사용 봉인 구간 평가
model card·selection report
승인
immutable artifact publish
staging shadow
feature flag 제한 공개
```

11. 사용자 요청은 다음 조건을 모두 만족할 때만 과거 사례 API와 route를 연다.
    - 승인된 immutable artifact
    - server-side gate와 feature flag
    - 해당 사용자의 historical entitlement
    - Event·데이터 quality가 과거 연결을 허용
12. feature flag만으로 권한을 대신하지 않는다. 저장된 Event도 gate와 entitlement를 우회하지 않는다. 조건이 없으면 theme detail은 `historicalAccess=GATED` 또는 허용된 `UNAVAILABLE` 상태를 반환하고 진입점을 숨긴다.
13. 노출 결과는 관련성 순서, 왜 비슷한지의 근거 태그, 당시 주도주의 실제 관측값과 기간별 `eligibleCount`·`observedCount`·`positiveCount`·중앙값을 제공한다. 표본 부족·가격 누락을 0으로 바꾸지 않는다.
14. 과거 관측값을 현재 사건의 상승 확률, 예상 수익률, 적중률이나 추천으로 표현하지 않는다.
15. 새 artifact로 바꿀 때 v1과 이전 승인 artifact를 덮어쓰지 않고 새 version을 발급한다. gate 조건이나 검색·예측 경계를 완화하려면 PRD와 이 ADR을 먼저 변경한다.

## 검토한 대안

### M-TXT v1을 즉시 일반 사용자에게 노출

관련 과거 사례 검색의 기준선 가치는 있지만 최종 제품 검색기와 2인 검수·새 봉인 구간 증거가 없다. PRD의 사용자 노출 전 게이트와 충돌하므로 채택하지 않았다.

### 기존 봉인 구간으로 ontology를 반복 선택

같은 구간을 개발·선택·최종 평가에 재사용하면 봉인 의미가 사라진다. 새 미사용 구간 1회 평가를 요구한다.

### outcome이 좋은 사례를 우선 정렬

사용자에게 인상적인 결과를 보일 수 있지만 미래 정보가 retrieval에 들어간다. 관련성 선택 뒤 outcome을 결합한다.

### UI feature flag만으로 차단

직접 API, 저장 항목과 오래된 client가 gate를 우회할 수 있다. server artifact gate·entitlement와 함께 강제한다.

## 결과

### 장점

- 검증된 관련성 검색과 검증되지 않은 예측을 분리한다.
- outcome leakage와 반복 봉인 구간 선택을 구조적으로 차단한다.
- 어떤 ontology·engine·data·outcome으로 결과가 생성됐는지 재현할 수 있다.
- gate가 닫힌 동안에도 corpus·ontology·engine과 gate-off 제품 경로를 병렬 구현할 수 있다.

### 비용과 위험

- 2인 blind 검수, 불일치 조정과 새 시간 구간을 기다려야 한다.
- artifact registry, entitlement와 server-side gate 구현이 필요하다.
- gate가 열리기 전 일반 사용자 MVP 화면에서는 과거 사례 영역이 잠긴다.
- 관련성 개선이 없으면 ontology/hybrid를 채택하지 못할 수 있으며 이는 정상적인 연구 결과다.

## 근거와 현재 blocker

- [PRD.md](../PRD.md)의 2.1~2.2, 4.3~4.4, FR-5~FR-6과 Gate E가 v1의 제한, leakage 금지와 재검증 조건을 제품 기준으로 확정한다.
- [system_architecture.md](../system_architecture.md)의 3.7과 12절은 검색·예측 분리, outcome 후결합과 immutable artifact 출시 흐름을 정의한다.
- [api_contract.md](../api_contract.md)의 9.2, 10.4와 11절은 `GATED` 상태, entitlement와 조건부 endpoint 의미를 정의한다.
- [implementation_roadmap.md](../implementation_roadmap.md)의 `B-HISTORY-GATE`에 따르면 현재 v1 코드, 가격 corpus와 registry가 없고 기존 2026 봉인 구간은 재사용할 수 없다. 2인 평가와 새 미사용 봉인 구간 평가·artifact 승인도 완료되지 않았다.
- `accepted`는 이 출시 차단 정책이 확정됐다는 뜻이다. 검색기나 gate 통과가 완료됐다는 뜻이 아니다.

## 검증 체크

- [ ] gate가 닫히면 일반 사용자 route와 유사사례 API가 결과 0건이 아니라 명시적 `GATED` 상태로 잠긴다.
- [ ] feature flag, entitlement 또는 승인 artifact 하나라도 없으면 직접 API와 저장 Event가 우회하지 못한다.
- [ ] retrieval code path가 outcome field·repository를 후보 선택 전에 받을 수 없다.
- [ ] M-TXT·ontology·hybrid가 같은 candidate pool·fold와 blind label로 비교된다.
- [ ] 2인 평가, 불일치 조정과 새 미사용 봉인 구간 1회 평가 기록이 있다.
- [ ] 승인 artifact가 dataset·ontology·retrieval·calendar·outcome version과 hash를 고정한다.
- [ ] 기간별 분모·누락·표본 부족과 당시 leader만을 사용하는 outcome test가 통과한다.
- [ ] 사용자 문구와 schema에 확률·예상 수익률·추천 또는 outcome 정렬이 없다.
- [ ] v2 prediction artifact·contract·alias가 Historical Matching 운영 경로와 분리된다.
