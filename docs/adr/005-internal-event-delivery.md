# ADR-005. 내부 event 전달, outbox와 idempotency

- 상태: `proposed`
- 결정일: 2026-08-14
- 적용 범위: 같은 process와 process 간 domain event·command 전달, DB 변경 후 발행, 재처리·순서 보장
- 관련 문서: [system_architecture.md](../system_architecture.md), [implementation_roadmap.md](../implementation_roadmap.md), [ADR-001](./001-application-modularity.md), [ADR-002](./002-event-id-lifecycle.md), [ADR-004](./004-realtime-state-storage.md)

## 배경

DAYJAVIEW의 API, 실시간 처리, 뉴스, 인포스탁과 batch는 역할별로 독립 재시작할 수 있어야 한다. DB row 변경과 process 간 event 발행을 별도 작업으로 처리하면 commit만 되고 message가 사라지거나, message가 중복 전달돼 Event·뉴스·revision이 중복 생성될 수 있다.

현재 기준은 Redis를 실시간 공유·fan-out 계층으로 사용하고 PostgreSQL을 영속 기준으로 사용한다. 다만 Redis Streams와 Pub/Sub의 정확한 사용 범위는 다중 process PoC 뒤 확정할 가설로 남아 있다.

## 결정

1. 내부 비동기 message는 최소한 다음 versioned envelope를 사용한다.

```text
eventType
eventVersion
messageId
occurredAt
receivedAt
producer
correlationId
causationId
payload
```

2. 같은 process 안에서는 function call 또는 in-process dispatcher를 사용할 수 있다. 이 최적화가 message 의미, version, idempotency key나 관측 가능한 상태 전이를 바꾸면 안 된다.
3. process 간 상태 반영이 필요한 message의 기본 전달 후보는 Redis Streams다. consumer 재시작과 일시 장애 뒤 다시 읽어야 하는 command·domain event를 단순 Pub/Sub에만 의존하지 않는다.
4. Pub/Sub는 최신 snapshot fan-out처럼 중간 알림이 유실돼도 PostgreSQL·Redis 최신 상태나 전체 snapshot으로 복구 가능한 경로에만 사용할 수 있다. Streams와 Pub/Sub의 최종 topic별 배치는 PoC와 부하 시험 뒤 고정한다.
5. Event, classification revision, 뉴스 근거처럼 PostgreSQL 변경과 외부 전달이 함께 필요한 작업은 transactional outbox를 사용한다.

```text
domain row 변경 + outbox row 기록
같은 PostgreSQL transaction commit
outbox publisher가 전달 계층에 발행
consumer가 idempotency key로 처리
```

6. transaction이 실패한 상태를 먼저 발행하지 않는다. commit 뒤 publisher가 중단돼도 outbox row에서 재시도할 수 있어야 한다. Redis 전달 성공을 domain commit의 기준으로 삼지 않는다.
7. 전달은 at-least-once 재처리를 전제로 한다. consumer는 다음 key를 사용해 동일 side effect를 한 번의 최종 결과로 수렴시킨다.
   - 내부 event: `messageId`
   - market event: session·type·stock·trade timestamp·source sequence
   - 외부 수집: source ID·canonical URL·content hash
   - batch: job name·business date·input version
   - API mutation: idempotency key 또는 domain natural key
8. idempotency는 message acknowledge만 중복 제거하는 것이 아니다. 중복 Event, 뉴스, classification revision, operator command 결과가 만들어지지 않도록 domain write와 함께 검증한다.
9. 전체 global ordering은 제공하지 않는다. 순서가 필요한 범위를 명시한다.
   - 종목 상태: 종목별 source sequence와 `receivedAt`
   - theme snapshot: stream 범위의 단조 증가 `sequence`
   - Event 상태: `stateVersion`
   - 분류: `classificationVersion`
10. 오래된 message는 해당 aggregate의 version 검사로 거부하거나 이미 반영된 성공으로 수렴시킨다. 서로 다른 aggregate 간 도착 순서에 의미를 부여하지 않는다.
11. 장기 감사가 필요한 사실은 PostgreSQL append/history에 남긴다. Redis Stream 보존 기간이나 Pub/Sub delivery를 감사 log로 취급하지 않는다.
12. message schema는 version 관리하고 producer·consumer 호환성을 contract fixture로 검증한다. 알려지지 않은 version을 조용히 현재 payload로 해석하지 않는다.

## 검토한 대안

### process 간에도 동기 function call만 사용

초기 PoC에는 단순하지만 역할별 독립 재시작·격리와 맞지 않는다. 같은 process 최적화로만 허용한다.

### 모든 전달을 Redis Pub/Sub로 처리

최신 화면 fan-out에는 적합하지만 subscriber 중단 중 중요한 상태 전이가 사라질 수 있다. 영속 상태 전이의 유일한 전달 수단으로는 채택하지 않는다.

### DB commit 전에 message 발행

지연은 줄 수 있지만 consumer가 존재하지 않는 상태를 관측하고 보상하기 어렵다. transaction + outbox 순서를 채택한다.

### 처음부터 별도 대형 message broker 도입

현재 단일 OCI 호스트와 팀 규모에서 추가 운영 대상의 필요성이 입증되지 않았다. Redis와 PostgreSQL 경계로 검증하고 처리량·복구 요구가 충족되지 않을 때 ADR로 재검토한다.

## 결과

### 장점

- process crash 지점과 무관하게 DB 상태와 전달을 다시 수렴시킬 수 있다.
- 중복 전달을 정상 조건으로 다뤄 Event·revision 중복을 막는다.
- 역할별 process를 분리하면서 PostgreSQL을 영속 기준으로 유지한다.
- topic별 순서 범위를 좁혀 불필요한 global serialization을 피한다.

### 비용과 위험

- outbox publisher, retry, idempotent consumer와 실패 fixture가 추가된다.
- Redis Streams·Pub/Sub를 잘못 나누면 유실 또는 불필요한 보존 비용이 생긴다.
- idempotency key 충돌과 aggregate version 경합을 관측해야 한다.
- schema version 호환성 검증이 producer·consumer 배포 순서에 포함된다.

## 근거와 확정 수준

- [system_architecture.md](../system_architecture.md)의 9절은 envelope, Redis Streams 권장, PostgreSQL outbox, idempotency와 ordering 범위를 정의한다.
- [system_architecture.md](../system_architecture.md) 21.2는 Redis Streams와 Pub/Sub의 정확한 사용 범위를 PoC 후 확정할 가설로 남긴다. 따라서 이 ADR은 `proposed`다.
- [ADR-001](./001-application-modularity.md)은 역할별 process와 outbox·idempotency 경계를 이미 채택했고, [ADR-004](./004-realtime-state-storage.md)은 PostgreSQL과 Redis의 책임을 고정한다.
- [implementation_roadmap.md](../implementation_roadmap.md)은 첫 다중 process 통합 전에 outbox와 idempotency failure test를 요구한다.
- 현재 broker, outbox schema와 consumer 구현은 없다.

## accepted 전환 조건과 검증

- [ ] 같은 process와 Redis adapter가 같은 versioned envelope fixture를 처리한다.
- [ ] commit 전·commit 후·Redis 발행 전후 process 중단 fixture에서 유실된 영속 상태나 유령 message가 없다.
- [ ] 동일 message와 batch를 반복 전달해도 Event·뉴스·revision side effect가 한 번의 최종 결과로 수렴한다.
- [ ] stale `stateVersion`·`classificationVersion` message가 현재 상태를 되돌리지 않는다.
- [ ] Redis 중단 뒤 outbox 재시도와 consumer 재개가 전체 상태를 복구한다.
- [ ] Pub/Sub 유실 경로는 최신 read model 또는 전체 snapshot으로 복구 가능함이 증명된다.
- [ ] topic별 Streams·Pub/Sub 선택, retention과 consumer 운영 기준이 PoC 증거와 함께 기록된다.
- [ ] schema compatibility contract가 producer·consumer 독립 배포 순서를 통과한다.
