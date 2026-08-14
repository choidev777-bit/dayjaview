# ADR-008. 공급원별 데이터 권리와 기본 차단

- 상태: `proposed`
- 결정일: 2026-08-14
- 적용 범위: 외부 시장·테마·뉴스·기준정보·가격 데이터의 수집, 저장, 가공, 보존과 사용자 표시
- 현재 외부 게이트: `B-DATA-RIGHTS`, `B-REFDATA-KEYS`, `B-INFOSTOCK-AUTH` 미해제
- 관련 문서: [PRD.md](../PRD.md), [product_decisions.md](../product_decisions.md), [system_architecture.md](../system_architecture.md), [implementation_roadmap.md](../implementation_roadmap.md), [ADR-004](./004-realtime-state-storage.md)

## 배경

API 접근 가능, 로컬 수집 성공 또는 교육 목적 승인은 production에서 원본을 저장·가공·재배포할 권리를 자동으로 뜻하지 않는다. 공급원마다 허용된 field, 원문 보존, 파생값 생성, 사용자 표시, attribution과 보존 기간이 다를 수 있다.

현재 저장소에는 인포스탁 production 저장·가공·표시 권리와 뉴스 약관 증거가 없다. KRX·OpenDART key, 인포스탁 운영 browser state와 구체적인 가격 공급원도 준비되지 않았다. 권리를 추정해 production collector를 켜거나 문서의 예정 UI를 권리 승인으로 간주해서는 안 된다.

## 결정

1. 외부 공급원은 **기본 차단**한다. versioned source-rights record가 명시적으로 허용한 operation과 환경만 실행할 수 있다.
2. source-rights record는 최소 다음을 식별한다.
   - 공급원·제품·접근 경로와 적용 약관·계약 version
   - 확인 주체·확인 시각과 재검토·만료 조건
   - 허용 환경: local/test, research, staging-shadow, production
   - 허용 operation: collect, raw store, normalize, derive, internal review, user display, redistribute
   - 허용 field·content class와 금지 항목
   - 원본·파생물 보존·삭제 범위
   - attribution, 매체·시각·원문 link 요구
3. record가 없거나 만료·범위 불명확이면 worker는 production 수집과 사용자 표시를 시작하지 않는다. 실패를 source status와 운영 log에 남기되 빈 값을 0이나 다른 공급원 값으로 조용히 대체하지 않는다.
4. 현재 증거에서 허용되는 안전 범위는 다음과 같다. 이는 production 권리 승인이 아니라 구현 가능한 최대 범위다.

| 공급원 | 저장소의 현재 증거 | 지금 가능한 범위 | production 해제 조건 |
|---|---|---|---|
| 키움 REST·WebSocket | 제품 입력과 공식 한도·session PoC 요구만 기록됨 | synthetic fixture, adapter·schema·실패 test | 공식 이용 조건·호출/구독 한도와 저장·파생·표시 범위 기록, live 기술 PoC |
| 인포스탁 | 로컬 manifest의 교육 승인 표시는 있으나 production 범위 증거 없음 | fixture·기존 import를 이용한 DB/adapter 계약 구현 | 합법적 접근·갱신 방식, 저장·가공·표시·보존 승인과 운영자 수동 인증 |
| 뉴스 RSS·NAVER API HUB | 예정 공급원과 표시 원칙은 있으나 약관 증거 없음 | synthetic article metadata와 계약·매칭 fixture | 공급원별 수집·호출·원문 처리·보존·재배포·link 정책 승인 |
| KRX Open API·OpenDART | 무료 공식 API만 사용한다는 제품 결정, key와 field/Coverage 검증은 없음 | adapter, 설정 검증, fixture와 결측·충돌 test | 공식 key, 실제 field 의미·기준일·Coverage와 원천별 이용 범위 검증 |
| 조정 가격·거래일 | 제품 역할은 정의됐지만 실제 공급원·권리 record 없음 | synthetic/승인 fixture, point-in-time·기업행위 schema test | 공급원, 저장·연구·표시 범위와 calendar/price version 고정 |

5. fixture 기반 구현은 production 권리와 분리한다. 로컬 교육·시험 데이터나 승인되지 않은 capture를 production DB, object storage, API response 또는 공개 demo로 자동 승격하지 않는다.
6. 뉴스는 권리가 허용한 metadata와 선택된 근거만 처리한다. 사용자 화면은 자체 요약, 매체, 발행 시각과 원문 link 중심으로 구성한다. 기사 전문은 source-rights record가 처리·저장을 명시적으로 허용할 때만 사용한다.
7. 파생 metric과 LLM 요약은 원천 권리에서 자유로운 새 데이터라고 가정하지 않는다. 모든 파생물은 source ID, 수집 시각, 원문 hash, parser·calculation·prompt/model version과 적용 권리 record를 추적한다.
8. 인포스탁 원본·뉴스 원문·대형 fixture를 object storage에 두는 것은 권리 record가 raw storage를 허용할 때뿐이다. PostgreSQL metadata에는 위치·hash·수집 시각과 권리 범위를 연결한다.
9. KRX/OpenDART 기준정보는 무료 공식 API 범위만 사용한다. 검증된 유동주식비율을 얻지 못하면 총시가총액이나 임의 비율로 대체하지 않고 Coverage에서 제외해 계산·출시를 차단한다.
10. credential, browser storage state, cookie와 token은 데이터 원본과 별도 secret 경계에 두고 source-rights 증거로 저장하지 않는다.
11. 허용 범위가 축소·만료되면 새 수집과 표시를 중단하고 기록된 보존·삭제 정책을 적용한다. 감사에 남길 metadata도 해당 권리와 개인정보 정책이 허용한 최소 범위로 제한한다.
12. 실제 공급원별 허용 범위가 외부 확인되기 전에는 이 ADR을 `accepted`로 전환하지 않는다. 권리 확인은 코드나 ADR 작성만으로 완료되지 않는다.

## 검토한 대안

### API나 로그인 접근이 되면 production 사용을 허용

기술적 접근과 저장·가공·재배포 권리를 혼동한다. PRD Gate A와 현재 blocker에 반하므로 채택하지 않는다.

### 모든 공급원에 같은 원본 보존·표시 정책 적용

기사 전문, 시장 data, taxonomy와 공식 기준정보의 허용 범위가 다를 수 있다. 공급원·content class별 record를 사용한다.

### 권리가 확정될 때까지 adapter와 schema 구현도 중단

외부 승인 없이도 synthetic fixture, fail-closed 정책, provenance와 contract test는 구현할 수 있다. production 수집·공개만 차단한다.

### 원본을 모두 저장한 뒤 나중에 삭제

저장 자체가 허용되지 않을 수 있고 파생물 계보도 오염된다. collect·raw store 단계부터 허용 범위를 검사한다.

## 결과

### 장점

- 접근 성공을 권리 승인으로 오인해 restricted data를 production에 축적하는 일을 막는다.
- 공급원별 허용 범위와 파생물 계보를 감사할 수 있다.
- 외부 blocker와 무관한 adapter·fixture·failure-path 구현을 계속할 수 있다.
- 약관 변경 때 영향받는 원본·파생물·화면을 식별할 수 있다.

### 비용과 위험

- 공급원별 법적·계약 검토와 versioned 권리 record 운영이 필요하다.
- 승인 전 production 수집과 일부 화면은 의도적으로 잠긴다.
- 권리 범위가 바뀌면 보존·삭제와 파생물 재생성 작업이 생길 수 있다.
- 이 ADR만으로 실제 권리가 확보되거나 외부 gate가 해제되지 않는다.

## 근거와 현재 blocker

- [PRD.md](../PRD.md)의 4.7, 10절과 Gate A는 허용된 경로, 뉴스 원문 비재배포, 운영 수집 전 권리 확보를 제품 기준으로 둔다.
- [product_decisions.md](../product_decisions.md)의 PD-003은 기사 전문을 명시적 허용 때만 처리·저장하고 자체 요약·출처·link만 표시하도록 한다. PD-004는 인포스탁 접근 경로를 정하지만 production 권리 승인을 대신하지 않는다. PD-009는 KRX·OpenDART 무료 공식 API 범위만 허용한다.
- [system_architecture.md](../system_architecture.md)의 15.4는 공급원별 수집·저장·가공·표시 범위와 보존·삭제 정책을 요구한다.
- [implementation_roadmap.md](../implementation_roadmap.md)의 `B-DATA-RIGHTS`는 local 교육 승인 외 production 권리와 뉴스 약관 증거가 없다고 기록하며, fixture·계약 구현만 허용하고 production 수집·공개를 금지한다.
- `B-REFDATA-KEYS`와 `B-INFOSTOCK-AUTH`도 live 검증을 막는다. 이 상태를 권리 승인이나 구현 완료로 표현하지 않는다.

## accepted 전환 조건과 검증

- [ ] 각 production 공급원에 승인된 source-rights record와 증거 위치·검토 주체가 있다.
- [ ] record 누락·만료·operation 불일치 fixture에서 collector, raw store와 사용자 projection이 fail closed한다.
- [ ] 뉴스 전문은 명시적 허용 record 없이는 저장·LLM 전달·API 표시되지 않는다.
- [ ] 사용자 evidence가 자체 요약·매체·발행 시각·원문 link와 source metadata를 포함한다.
- [ ] 원본과 파생물에서 source ID·hash·수집 시각·parser/calculation/model version·rights version을 추적할 수 있다.
- [ ] 인포스탁 production 수집과 표시는 합법적 접근·갱신·보존 범위와 수동 인증 검증 전 비활성이다.
- [ ] KRX·OpenDART 실제 field·기준일·Coverage와 무료 공식 API 범위를 검증하고 대체값 금지 test가 통과한다.
- [ ] 권리 축소·만료 fixture가 새 수집·표시 중단과 승인된 보존·삭제 정책을 수행한다.
- [ ] fixture·log·Git·browser response에 credential, cookie, token과 browser state가 없다.
