# ADR-003. REST 조회·복구와 WebSocket 실시간 snapshot

- 상태: `accepted`
- 결정일: 2026-08-14
- 적용 범위: browser와 API/BFF 사이의 초기 조회, 실시간 갱신, 지연·재연결 복구
- 관련 문서: [PRD.md](../PRD.md), [system_architecture.md](../system_architecture.md), [api_contract.md](../api_contract.md), [implementation_roadmap.md](../implementation_roadmap.md), [ADR-004](./004-realtime-state-storage.md), [ADR-011](./011-vercel-oci-split-deployment.md)

## 배경

오늘 순위, 트리맵과 Event 상태는 수초 단위로 바뀌지만 상세·과거 데이터와 재연결 복구는 요청 시점의 완전한 read model이 필요하다. REST polling만 사용하면 변하지 않은 응답을 반복하고, delta만 전달하면 누락·순서 역전·background 복귀 때 client 상태가 서버와 달라질 수 있다.

전송 방식은 내부 process나 저장소 구조와 독립적이어야 하며, 지연·결측을 최신값이나 0으로 보이게 해서는 안 된다.

## 결정

1. REST는 최초 화면 snapshot, 상세·과거 조회, 명시적 새로고침과 재연결 복구를 담당한다.
2. WebSocket은 실시간 순위, 트리맵과 구독 Event의 최신 상태를 전달한다. client는 초기 REST 조회 후 WebSocket을 연결한다.
3. 인증되지 않은 REST·WebSocket에는 제품 데이터를 보내지 않는다. Vercel/OCI 배치와 30초·1회용 WebSocket ticket 경계는 [ADR-011](./011-vercel-oci-split-deployment.md)을 따른다.
4. 한 WebSocket 연결에서 client가 topic과 정규화된 parameter를 명시적으로 구독한다. 사용자마다 별도 시장 계산을 만들지 않고 server가 계산한 공용 snapshot을 권한에 맞게 제공한다.
5. 초기 계약은 delta가 아니라 versioned **전체 snapshot**을 사용한다.
   - `theme_rank_snapshot`: 현재 노출 순위 집합 전체
   - `theme_treemap_snapshot`: 현재 노출 타일 집합 전체
   - `event_state_changed`: Event별 최신 full summary 또는 REST refetch signal 중 AsyncAPI에서 확정한 한 형태
6. 모든 실시간 message에는 `schemaVersion`, `subscriptionId`, `streamId`, topic, 단조 증가 `sequence`, `generatedAt`, `asOf`, `dataStatus`와 quality 정보를 둔다. server는 구독 승인 시 정규화된 parameter를 `subscriptionId`와 연결한다.
7. `sequence`는 전 시스템 global 값이 아니라 `streamId + topic + normalized subscription params` 범위에서만 비교한다.
   - 같은 범위의 이전 값 이하 message는 무시한다.
   - 새 `streamId`의 첫 전체 snapshot은 이전 sequence와 비교하지 않고 교체 적용한다.
   - sequence gap이 있어도 현재 message가 전체 snapshot이면 적용할 수 있다.
8. 연결이 끊기면 client는 화면을 계속 `LIVE`로 표시하지 않는다. stale 상태로 전환하고 exponential backoff와 jitter로 재연결한 뒤 인증 갱신, topic 재구독, 첫 전체 snapshot 교체 순서로 복구한다.
9. server는 내부 tick마다 message를 만들지 않고 설정된 주기 안에서 최신 상태로 coalesce할 수 있다. 느린 client에는 중간 전체 snapshot을 버리고 최신값을 우선하며 무제한 queue를 만들지 않는다.
10. 변경이 없어도 heartbeat 또는 freshness snapshot으로 연결과 시점을 판정한다. 정확한 snapshot·heartbeat 간격, timeout과 close code는 성능 시험 후 AsyncAPI와 설정에서 확정한다.
11. WebSocket 장애 시 제한적 REST polling은 저하 모드일 뿐 정상 전달 방식이 아니다. 복구 REST는 오래된 shared CDN cache를 받지 않도록 private/no-store 정책을 사용한다.
12. client는 snapshot을 표시만 하며 테마 수익률·순위·Coverage를 재계산하거나 불완전한 delta를 임의 병합하지 않는다.
13. SSE는 초기 기준에서 사용하지 않는다. 인증이나 인프라 제약으로 WebSocket을 운영할 수 없다는 증거가 생기면 새 ADR로 재검토한다.

## 검토한 대안

### REST polling만 사용

구현은 단순하지만 수초 단위 화면에 반복 요청과 불필요한 응답이 많고 지연 판정이 어렵다. 장애 시 제한 폴백으로만 둔다.

### Server-Sent Events

단방향 snapshot에는 가능하지만 현재 계약은 한 연결의 명시적 다중 topic 구독과 OCI direct 인증 경계를 사용한다. WebSocket이 불가능하다는 근거가 없어 채택하지 않았다.

### delta event를 client에서 병합

payload는 줄지만 gap, 재연결, schema 변경과 background 복귀 복잡도가 커진다. 초기 규모에서는 전체 snapshot의 일관성과 복구 단순성이 더 중요하므로 채택하지 않았다.

### 화면·topic마다 별도 WebSocket

인증·heartbeat·재연결과 자원 사용이 중복된다. 한 연결의 다중 구독을 채택했다.

## 결과

### 장점

- client는 첫 snapshot 교체만으로 재연결 상태를 서버와 맞출 수 있다.
- sequence gap과 느린 client가 불완전한 UI 상태를 만들 가능성이 줄어든다.
- REST·WebSocket public 의미가 내부 토폴로지와 분리된다.
- stale·DELAYED·DEGRADED 상태를 값과 별도로 표현할 수 있다.

### 비용과 위험

- 전체 snapshot 크기와 전송 주기를 함께 측정해야 한다.
- `streamId`와 구독 parameter 정규화 규칙을 기계 계약으로 고정해야 한다.
- direct WSS와 REST rewrite 두 ingress의 인증·Origin 검증이 필요하다.
- WebSocket이 끊겼을 때 polling이 정상 경로로 고착되지 않게 관측해야 한다.

## 근거와 확정 수준

- [PRD.md](../PRD.md)의 핵심 MVP와 FR-1·FR-7은 수초 단위 갱신과 지연·Coverage의 정직한 표시를 요구한다.
- [system_architecture.md](../system_architecture.md)의 3.5와 11절은 REST 조회·복구, WebSocket 실시간 전체 snapshot, sequence와 재연결 원칙을 확정한다.
- [api_contract.md](../api_contract.md)의 9.3, 12~14절은 인증, message envelope, sequence 범위, coalescing, backpressure와 호환성 의미를 정의한다.
- 정확한 endpoint payload, heartbeat 방식·간격, snapshot 주기와 close code는 아직 OpenAPI·AsyncAPI 및 SLO 검증 대상이다. 이 미확정 parameter는 REST/WebSocket 역할과 full-snapshot 복구 결정을 바꾸지 않는다.
- 현재 제품 애플리케이션과 WebSocket server는 구현되지 않았다. 이 ADR은 구현 완료를 주장하지 않는다.

## 검증 체크

- [ ] 비로그인·만료 session과 인증 전 WebSocket에서 제품 snapshot이 0건이다.
- [ ] 구독 직후와 재연결 후 첫 message가 해당 범위의 전체 snapshot이다.
- [ ] 같은 stream의 중복·역순 sequence를 client가 무시한다.
- [ ] 새 `streamId`와 sequence gap fixture가 전체 snapshot으로 안전하게 복구된다.
- [ ] 연결 끊김·background 복귀 때 stale 표시 후 재인증·재구독·교체가 수행된다.
- [ ] server가 느린 client별 무제한 queue를 만들지 않고 최신 snapshot으로 coalesce한다.
- [ ] REST 복구 응답과 WebSocket snapshot이 같은 계산·식별자·상태 계약을 사용한다.
- [ ] snapshot 크기·주기와 heartbeat timeout이 부하 시험 뒤 설정과 AsyncAPI에 기록된다.
- [ ] 인증된 private REST 복구 응답이 shared CDN cache에 남지 않는다.
