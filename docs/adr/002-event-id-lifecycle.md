# ADR-002. Event ID 수명주기와 장후 revision

- 상태: `accepted`
- 결정일: 2026-08-14
- 적용 범위: 장중 Event 생성·상태 전이·중복 판단과 장후 인포스탁 정합
- 관련 문서: [PRD.md](../PRD.md), [system_architecture.md](../system_architecture.md), [api_contract.md](../api_contract.md), [implementation_roadmap.md](../implementation_roadmap.md), [ADR-001](./001-application-modularity.md)

## 배경

장중 엔진이 처음 붙인 테마명과 `themeId`는 추정일 수 있고, 뉴스 근거와 장후 인포스탁 분류가 추가되면서 분류가 바뀔 수 있다. 이때 분류 변경을 새 사건으로 만들거나 기존 값을 덮어쓰면 장중 탐지, 장후 확정, 운영자 검수와 과거 결과를 같은 사건으로 감사할 수 없다.

반대로 같은 날 발생한 모든 움직임을 하나로 합치면 서로 다른 촉매와 독립 움직임이 섞인다. Event의 불변 identity와 변경 가능한 상태·분류를 분리해야 한다.

## 결정

1. Event 모듈만 Event 생성, 상태 전이, 현재 분류, `stateVersion`과 `classificationVersion`을 쓸 수 있다. Activation & Ranking, 뉴스, 인포스탁과 운영자 모듈은 명령이나 근거를 제출하고 결과를 구독한다.
2. `eventId`는 **한 거래일의 한 촉매·움직임 수명주기**를 식별한다. 영속 저장과 함께 한 번 발급한 뒤 바꾸지 않는다.
3. `eventId`는 정렬 가능한 opaque 문자열을 사용할 수 있지만 날짜, 테마명, `themeId`를 의미로 인코딩하지 않는다. client는 prefix·길이·발급 시각을 해석하지 않는다.
4. 같은 거래일·같은 canonical theme·같은 Catalyst cluster의 재강화는 기존 Event를 우선한다. 약화 후 versioned 설정의 회복 구간 안에서 `ACTIVE`로 돌아오면 같은 `eventId`를 유지한다.
5. 다른 촉매가 확인되거나 terminal 상태 뒤 독립 움직임이 시작되면 새 Event 후보로 처리한다. 촉매 cluster와 회복 시간의 정확한 임계값은 `rankingModelVersion`에 포함하고 fixture로 고정한다.
6. 상태 의미는 한 enum으로 합치지 않고 다음 축으로 나눈다.
   - `lifecycleStatus`: `CANDIDATE → ACTIVE | DISCARDED`, `ACTIVE → WEAKENING | CLOSED`, `WEAKENING → ACTIVE | CLOSED`
   - `reconciliationStatus`: `PENDING → MATCHED | UNMATCHED`, `UNMATCHED → MATCHED`
   - `evidenceStatus`: 근거 탐색·강화·장후 확인 수준
   - `reviewStatus`: 운영자 검수 작업의 존재와 해결 상태
7. `CLOSED`와 `DISCARDED`는 lifecycle terminal 상태다. 장후 정합은 `CLOSED`를 되돌리지 않고 별도 `reconciliationStatus`와 classification revision으로 진행한다.
8. 장후 분류가 달라져도 새 Event를 만들지 않는다. 같은 `eventId`에서 `classificationVersion`을 단조 증가시키고 이전·새 `themeId`와 표시명, source, 변경 시각, 변경 주체와 사유를 보존한다.
9. `UNMATCHED` Event는 운영자 검수 대상으로 만들고 과거 통계에 자동 연결하지 않는다. 운영자 수정·병합·제외도 원본 row 덮어쓰기나 hard delete가 아니라 reason이 있는 revision으로 기록한다.
10. 상태 갱신은 `stateVersion` optimistic concurrency로 직렬화한다. 중복 명령과 재처리는 새 Event나 같은 revision을 중복 생성하지 않아야 한다.
11. `matchedEventId`는 별도 namespace가 아니라 검색 결과로 선택된 과거 Event의 `eventId`다. 저장된 관심 Event나 과거 상세 조회도 gate·권한을 우회하지 않는다.
12. 새로운 lifecycle 역전이나 terminal Event 재개가 필요하면 구현 전에 이 ADR과 상태 schema의 audit 의미를 함께 변경한다.

## 검토한 대안

### 장중 분류와 장후 확정에 서로 다른 Event ID 사용

각 단계 구현은 단순하지만 하나의 시장 움직임에 대한 탐지·근거·분류·결과 계보가 끊긴다. PRD의 장중→장후 연속성 요구와 충돌하므로 채택하지 않았다.

### 날짜와 테마명을 조합한 자연키를 Event ID로 사용

사람이 읽기 쉽지만 이름·`themeId` revision과 같은 날 복수 촉매를 안전하게 표현하지 못한다. 자연키는 중복 판단 입력으로만 사용할 수 있고 public identity로는 사용하지 않는다.

### 현재 분류 row를 제자리에서 덮어쓰기

조회는 단순하지만 장중 snapshot과 변경 근거를 재현할 수 없다. 감사·평가 요구를 충족하지 못하므로 채택하지 않았다.

### 약화 후 모든 재상승을 새 Event로 생성

짧은 흔들림마다 사건이 분열되고 카드·근거·장후 정합이 중복된다. versioned hysteresis 안의 재강화는 같은 Event로 유지한다.

## 결과

### 장점

- 장중 탐지와 장후 확정을 하나의 불변 식별자로 추적할 수 있다.
- lifecycle, 정합, 근거, 검수 상태가 서로를 덮어쓰지 않는다.
- 분류 변경과 운영자 개입을 과거 시점 기준으로 재현할 수 있다.
- client route와 저장 항목이 표시명 변경에 깨지지 않는다.

### 비용과 위험

- 중복·재강화 판단과 optimistic concurrency test가 필요하다.
- classification history와 현재 projection을 함께 관리해야 한다.
- 촉매 cluster와 회복 임계값이 version에 포함되지 않으면 같은 입력의 Event 분할이 달라질 수 있다.
- `UNMATCHED`와 내부 `reviewStatus`를 일반 사용자 projection에서 분리해야 한다.

## 근거와 확정 수준

- [PRD.md](../PRD.md)의 제품 원칙 4.6과 FR-8은 같은 `eventId` 유지, 변경 이력과 원본 보존을 제품 기준으로 둔다.
- [system_architecture.md](../system_architecture.md)의 3.6, 5.9, 7절은 Event 단일 writer, 불변 identity, 독립 상태 축과 classification revision을 정의한다.
- [api_contract.md](../api_contract.md)의 2절과 5.2절은 public identifier와 허용 transition의 의미를 기준으로 둔다. endpoint 이름과 세부 schema는 아직 기계 계약 검토 대상이다.
- [implementation_roadmap.md](../implementation_roadmap.md)의 서비스 정의와 S0-ADR 원장은 장중 Event와 장후 revision의 동일 identity를 확정 의미로 취급한다.
- 현재 저장소에는 Event 제품 구현·migration·기계 schema가 없다. 이 ADR의 `accepted`는 경계와 의미의 채택을 뜻하며 구현 완료를 뜻하지 않는다.

## 검증 체크

- [ ] 같은 거래일의 중복 명령과 versioned 재강화 fixture가 같은 `eventId`를 만든다.
- [ ] 다른 촉매 또는 terminal 이후 독립 움직임 fixture가 새 Event 후보를 만든다.
- [ ] 허용되지 않은 lifecycle·정합 전이와 stale `stateVersion` 쓰기가 거부된다.
- [ ] 장후 자동 매칭과 운영자 검수 모두 `eventId`를 유지하고 `classificationVersion`만 증가시킨다.
- [ ] 이전 분류, source, 변경 주체·시각·사유를 재현할 수 있다.
- [ ] `UNMATCHED`는 과거 통계 자동 연결을 차단한다.
- [ ] 일반 사용자 schema에는 `reviewStatus`·내부 검수 메모가 포함되지 않는다.
- [ ] OpenAPI·AsyncAPI·JSON Schema·fixture가 같은 식별자와 상태 축을 사용한다.
