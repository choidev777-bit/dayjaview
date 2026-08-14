# ADR-004. memory·Redis·PostgreSQL 실시간 상태 책임

- 상태: `accepted`
- 결정일: 2026-08-14
- 적용 범위: 시장 입력, 최신 종목·테마 상태, Event·revision, read model과 원본 blob의 저장·복구
- 관련 문서: [PRD.md](../PRD.md), [system_architecture.md](../system_architecture.md), [implementation_roadmap.md](../implementation_roadmap.md), [ADR-005](./005-internal-event-delivery.md), [ADR-009](./009-oci-initial-deployment.md)

## 배경

고빈도 체결과 수초 단위 테마 snapshot을 매 요청마다 PostgreSQL에서 계산하거나 모든 tick을 동기 row write하면 hot path와 영속 저장이 서로 병목이 된다. 반대로 process memory나 Redis만 기준으로 삼으면 재시작·장애 뒤 Event identity, revision과 감사 기록을 복구할 수 없다.

상태의 수명과 복구 요구에 따라 process memory, Redis, PostgreSQL과 객체 저장소의 책임을 나눈다.

## 결정

1. PostgreSQL 16 이상을 제품의 **영속 기준 저장소**로 사용한다.
   - 원천 metadata와 수집 계보
   - 종목·테마·membership·기준정보 revision
   - Event, 상태·분류 history와 중요 append log
   - 뉴스 metadata·매칭·근거
   - 계산·모델·온톨로지 version과 승인된 운영 artifact metadata
   - 과거 outcome, 운영 read model과 사용자 최소 데이터
2. process memory는 해당 process가 즉시 계산하는 latest-by-key hot state와 짧은 작업 상태에만 사용한다. process 종료 뒤 보존되거나 다른 instance와 일치한다고 가정하지 않는다.
3. Redis는 다음의 공유 실시간 계층이다.
   - 최신 종목 상태와 테마 snapshot
   - process 간 짧은 수명의 전달
   - WebSocket fan-out
   - cooldown, rate limit, 짧은 cache와 실행 조정
4. Redis는 원본, Event history, classification revision 또는 장기 감사 기록의 기준 저장소가 아니다. Redis 손실 후 PostgreSQL checkpoint와 외부 상태 재조회로 재구축할 수 있어야 한다.
5. 단일 process PoC에서는 memory를 사용할 수 있다. 둘 이상의 process가 최신 상태를 공유하거나 API instance를 늘리는 시점에는 Redis를 사용한다. 이 최적화가 public 상태 의미나 복구 절차를 바꾸면 안 된다.
6. 고빈도 tick마다 PostgreSQL row를 동기 저장하지 않는다. 실시간 hot path는 memory/Redis에서 갱신하고, 중요 Event 전이와 revision은 PostgreSQL transaction으로 영속화한다.
7. 최신 실시간 상태는 다음처럼 배치한다.

| 데이터 | 기준 위치 | 보존·복구 원칙 |
|---|---|---|
| 현재 종목 가격·등락률·거래대금·freshness | memory + Redis | 외부 snapshot·checkpoint로 재구축 |
| 현재 테마 metric·Coverage·quality | Redis | dirty-theme 전체 재계산 가능 |
| Event·상태·분류 revision | PostgreSQL | append/history 보존, Event 모듈만 write |
| 중요 Event log | PostgreSQL | 장기 감사 |
| 1분 운영 snapshot·checkpoint | PostgreSQL 또는 시계열 partition | 비동기 저장, 정확한 보존 기간은 별도 정책 |
| 허용된 대형 원본·replay blob | versioned object storage | PostgreSQL에 위치·hash·수집 시각·권리 범위 |

8. 원본 blob은 공급원 권리가 허용한 경우에만 저장한다. local 개발 디렉터리는 adapter 뒤에서 사용할 수 있지만 production은 versioning·보존·암호화를 지원하는 객체 저장소로 교체한다.
9. schema·writer 권한을 모듈별로 분리한다. API/BFF는 공급원 SDK를 호출하거나 Event table을 직접 쓰지 않고 read model을 읽는다. 운영 writer에 DDL 권한을 주지 않는다.
10. 실시간 process 재시작은 다음 순서로 복구한다.
    1. PostgreSQL에서 당일 `ACTIVE`·`WEAKENING` Event와 최신 checkpoint 로드
    2. Market Gateway 재연결과 우선 종목 재구독
    3. 외부 REST snapshot으로 최신 상태 보완
    4. 전체 metric 재계산
    5. 새 `streamId`·sequence의 전체 snapshot 발행
11. Redis나 PostgreSQL 장애 때 마지막 정상값과 정상 시각을 보존하되 최신처럼 표시하지 않는다. PostgreSQL writer 장애 중 새 영속 전이를 성공 처리하거나 Redis-only 상태를 확정값으로 승격하지 않는다.
12. checkpoint 주기·보존 기간, Redis failover 구성, PostgreSQL partition과 read replica 도입 시점은 PoC/SLO 뒤 정한다. 이 parameter는 각 저장소의 책임을 바꾸지 않는다.

## 검토한 대안

### 모든 tick과 snapshot을 PostgreSQL에 동기 저장

영속성은 단순하지만 write amplification과 lock·I/O 경합이 실시간 경로를 지배할 수 있다. 중요 전이와 비동기 checkpoint만 PostgreSQL에 둔다.

### Redis를 영속 기준 저장소로 사용

실시간 read는 빠르지만 장기 감사, revision, 관계 무결성과 복구 기준을 맡기기 어렵다. Redis는 잃어도 재구축 가능한 상태로 제한한다.

### process memory만 사용

단일 PoC에는 가능하지만 worker/API 분리와 재시작에서 상태가 갈라진다. 다중 process 전에 Redis 공유 계층을 도입한다.

### 원본 blob을 PostgreSQL row에 직접 저장

작은 metadata 조회는 단순하지만 대형 원본·fixture의 보존·삭제·암호화 정책이 DB 운영과 결합된다. blob은 object storage, 계보 metadata는 PostgreSQL로 분리한다.

## 결과

### 장점

- hot path와 감사 가능한 영속 상태의 부하·장애를 분리한다.
- process·Redis 손실 뒤 동일 Event와 version을 기준으로 재구축할 수 있다.
- 저장 위치가 데이터 수명과 권리 정책에 맞춰진다.
- 역할별 process를 분리해도 public read model 의미가 유지된다.

### 비용과 위험

- checkpoint와 전체 재계산 경로를 실제로 시험해야 한다.
- Redis와 PostgreSQL 사이 전달에는 outbox·idempotency가 필요하다.
- 마지막 정상값, `asOf`와 data status가 잘못 결합되면 stale 값이 최신처럼 보일 수 있다.
- object storage 제품과 보존 기간은 아직 확정되지 않았다.

## 근거와 확정 수준

- [system_architecture.md](../system_architecture.md)의 3.2~3.4, 5.5, 8절과 14.3절이 저장소별 책임과 복구 순서를 확정한다.
- [PRD.md](../PRD.md)의 4.5, FR-8과 데이터 원칙은 결측을 0으로 만들지 않고 원본·version·관측 이력을 보존하도록 요구한다.
- [implementation_roadmap.md](../implementation_roadmap.md)은 PostgreSQL 영속 기준, Redis snapshot·전달·fan-out, memory hot state와 객체 저장소 원본 보존을 Stage 0 확정 의미로 기록한다.
- 정확한 checkpoint 주기, 보존 기간, Redis failover와 partition 구성은 [system_architecture.md](../system_architecture.md) 21.2의 PoC 후 가설이다.
- 현재 DB migration, Redis 구성, object storage와 제품 worker는 없다. 이 ADR은 저장 경계의 채택만 기록한다.

## 검증 체크

- [ ] process 종료와 Redis 초기화 뒤 PostgreSQL checkpoint·외부 snapshot으로 상태를 재구축한다.
- [ ] 복구 후 새 `streamId`의 전체 snapshot이 발행되고 client가 이전 sequence를 재사용하지 않는다.
- [ ] Event·classification revision은 PostgreSQL transaction 성공 전 확정·발행되지 않는다.
- [ ] 고빈도 tick 경로가 tick마다 PostgreSQL 동기 row write를 하지 않는다.
- [ ] Redis-only 데이터 삭제가 Event history·원본 계보·장후 revision을 잃게 하지 않는다.
- [ ] PostgreSQL writer 장애와 Redis 장애 fixture가 서로 다른 `DEGRADED` 상태와 마지막 정상 시각을 만든다.
- [ ] DB 계정과 repository interface가 schema별 write ownership을 강제한다.
- [ ] 원본 blob 저장·보존·삭제는 공급원 권리 metadata가 허용한 경우에만 수행된다.
- [ ] checkpoint 주기와 보존 기간은 부하·복구 시험 결과와 함께 별도 설정·정책으로 기록된다.
