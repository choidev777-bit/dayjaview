# ADR-006. 키움 Windows Market Gateway 조건부 격리

- 상태: `proposed`
- 결정일: 2026-08-14
- 적용 범위: 키움 REST·WebSocket adapter의 OS·process·network·credential 배포 경계
- 관련 문서: [PRD.md](../PRD.md), [system_architecture.md](../system_architecture.md), [implementation_roadmap.md](../implementation_roadmap.md), [ADR-001](./001-application-modularity.md), [ADR-005](./005-internal-event-delivery.md), [ADR-009](./009-oci-initial-deployment.md)

## 배경

키움 연결은 인증, 조건검색, 0B·0J 구독, REST 보완, 재연결과 session별 종목 한도를 소유한다. 장중 연결 환경이 Windows runtime을 요구할 가능성이 있지만 저장소에는 이를 확정한 호환성 PoC가 없다.

Windows 제약 때문에 전체 API·DB·Redis·worker를 Windows로 옮기면 다른 모듈의 운영 경계까지 외부 SDK에 종속된다. 반대로 키움 SDK type과 자격정보를 여러 모듈에 퍼뜨리면 공급원 교체·fixture test와 보안 격리가 어렵다.

## 결정

1. Market Gateway는 키움과 통신하는 유일한 모듈이다.
   - 인증과 session 유지
   - 조건검색 등록·편입·이탈
   - 0B·0J 구독·해제
   - REST 보완 snapshot
   - heartbeat·재연결·재구독
   - 외부 field를 canonical internal event로 정규화
   - source 지연·오류·구독 상태 기록
2. domain, API/BFF와 browser는 키움 SDK type, raw credential이나 session을 알지 않는다. Market Gateway가 versioned canonical event 계약으로 변환한다.
3. 공식 client와 장시간 연결 PoC에서 Windows가 필수로 확인되면 **Market Gateway만** 별도 Windows worker로 격리한다. API, PostgreSQL, Redis, 뉴스·인포스탁·batch worker는 [ADR-009](./009-oci-initial-deployment.md)의 OCI/Linux 경계를 유지한다.
4. Linux/ARM64에서 공식·지원되는 연결이 검증되더라도 Market Gateway의 논리·process 경계는 유지할 수 있다. OS 선택이 canonical event와 downstream module을 바꾸면 안 된다.
5. Market Gateway host는 사용자 REST·WebSocket ingress가 아니다. 사용자 browser를 직접 연결하지 않고 private TLS/VPN network의 authenticated ingestion endpoint 또는 검증된 Redis Streams 경계로만 backend와 통신한다.
6. Windows host와 OCI 사이의 정확한 transport와 배포 위치는 PoC에서 선택한다. 어떤 선택이든 다음을 만족해야 한다.
   - mutual authentication 또는 private network 접근 통제
   - schema·크기·시간·sequence 검증
   - credential·raw session 비전달
   - 재전송에 안전한 idempotency key
   - 지연·disconnect를 명시적 상태로 전달
7. 한 키움 session에는 하나의 active Market Gateway writer만 둔다. 재시작·leader 전환이 같은 체결이나 구독 명령을 중복 반영하지 않도록 session·type·stock·trade timestamp·source sequence를 사용한다.
8. 200종목 제약은 Subscription Manager가 우선순위로 관리하고 Gateway는 승인된 구독 집합을 적용한다. slot 부족을 숨기지 않고 Coverage·quality 상태로 downstream에 전달한다.
9. 연결 종료 시 제한된 backoff로 재연결·재구독하고 REST snapshot으로 현재 상태를 보완한다. 이전 연결의 오래된 event가 새 session 상태를 되돌리지 않게 session·sequence 경계를 바꾼다.
10. Windows worker가 중단되거나 private link가 끊기면 마지막 정상값과 시각을 유지하되 `LIVE`로 표시하지 않는다. downstream은 `DEGRADED` 상태와 전체 재계산·snapshot 절차로 복구한다.
11. 키움 credential은 Gateway 실행 환경의 secret store에만 주입하고 Git, fixture, log, browser bundle, canonical event와 다른 worker에 포함하지 않는다.
12. 저장된 market capture와 replay는 이 ADR의 배포 PoC를 대신하지 않는다. S0-ADR에서는 실제 capture·replay를 실행하지 않는다.

## 검토한 대안

### 전체 backend를 Windows에 배치

키움 runtime과 같은 OS에서 실행하기 쉽지만 PostgreSQL·Redis·browser worker와 API까지 공급원 제약에 결합한다. Windows가 필요해도 Gateway만 분리한다.

### API/BFF process에 키움 adapter 포함

호출 경로는 짧지만 공급원 장애·재연결이 사용자 API와 같은 process를 재시작하게 만들고 credential 경계가 넓어진다. 채택하지 않았다.

### browser가 키움에 직접 연결

credential·session과 공급원 계약을 client에 노출하고 공용 계산·구독 한도를 통제할 수 없다. 금지한다.

### 검증 전에 Windows host 위치와 transport 확정

현재 공식 runtime 호환성과 network 지연·운영 증거가 없다. 인터페이스와 보안 경계만 먼저 고정하고 위치·transport는 PoC로 선택한다.

## 결과

### 장점

- 키움 OS·SDK 제약이 나머지 제품 코드와 배포를 오염시키지 않는다.
- 공급원 장애를 독립 재시작하고 canonical fixture로 downstream을 개발할 수 있다.
- credential 접근 범위와 public attack surface를 줄인다.
- Linux 경로가 가능해져도 domain·API 계약을 바꿀 필요가 없다.

### 비용과 위험

- Windows가 필요하면 별도 host, private network와 배포·관측 경로가 추가된다.
- network 단절, duplicate delivery와 clock·sequence 경계를 시험해야 한다.
- session singleton과 재구독 중 slot churn을 관리해야 한다.
- 정확한 host와 transport가 미확정이므로 live 운영 완료를 주장할 수 없다.

## 근거와 확정 수준

- [PRD.md](../PRD.md)의 FR-1과 Gate B는 조건검색, 0B, 재연결, 200종목 전환과 REST 보완 PoC를 출시 조건으로 둔다.
- [system_architecture.md](../system_architecture.md)의 5.3과 13.3은 Market Gateway 단일 adapter와 Windows 필요 시 별도 worker 경계를 정의한다.
- [system_architecture.md](../system_architecture.md) 21.2는 Windows Gateway의 배포 위치를 PoC 후 확정할 가설로 남긴다. 공식 runtime과 host가 검증되지 않았으므로 상태는 `proposed`다.
- [ADR-001](./001-application-modularity.md)은 Windows가 필요할 때 Gateway만 별도 OS 실행 경계로 두도록 정한다.
- [implementation_roadmap.md](../implementation_roadmap.md)의 `B-MARKET-FIXTURE`는 저장 capture가 최종 fixture가 아니며 실제 replay 실행을 이 작업 범위에서 금지한다.

## accepted 전환 조건과 검증

- [ ] 공식 키움 환경에서 인증, 장시간 WebSocket, 조건 편입·이탈, 0B 등록·해제·재연결 PoC를 통과한다.
- [ ] 200종목 한도에서 우선순위 전환, slot churn과 REST 보완·Coverage가 fixture와 live 검증에서 일치한다.
- [ ] 선택한 OS·host·private transport와 운영 소유자가 기록된다.
- [ ] canonical event contract가 Windows·fixture adapter에서 같은 schema·idempotency·ordering 의미를 가진다.
- [ ] 중복·역순·이전 session event가 최신 종목 상태를 되돌리지 않는다.
- [ ] Gateway·private link 중단이 `DEGRADED`, 재연결·재구독, 전체 재계산으로 복구된다.
- [ ] public ingress에서 Windows host와 ingestion endpoint에 직접 접근할 수 없다.
- [ ] credential·session이 canonical event, log, fixture와 다른 process에 나타나지 않는다.
