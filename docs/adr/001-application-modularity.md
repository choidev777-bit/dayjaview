# ADR-001. 모듈형 모놀리스와 역할별 실행 경계

- 상태: `accepted`
- 결정일: 2026-08-14
- 적용 범위: DAYJAVIEW production 애플리케이션 구조
- 관련 문서: [system_architecture.md](../system_architecture.md), [implementation_roadmap.md](../implementation_roadmap.md)

## 배경

DAYJAVIEW에는 사용자 API, 장중 실시간 계산, 키움 연동, 뉴스 수집·매칭, 인포스탁 수집, 장전·장후 batch, 연구·모델 승격 기능이 필요하다. 이 기능들은 부하·장애·실행 주기가 다르지만 현재 팀과 첫 운영 인프라는 하나의 OCI VM이다.

결정의 목적은 기능을 줄이는 것이 아니다. 모든 기능과 도메인 경계를 구현하면서 현재 근거가 없는 네트워크 분산만 피하고, 실제 분리 근거가 생기면 안전하게 추출할 수 있게 만드는 것이다.

## 결정

1. production 코드는 하나의 저장소와 모듈형 모놀리스 구조로 관리한다.
2. 도메인 모듈은 자기 데이터의 writer, application service, repository interface를 소유한다. 다른 모듈이 내부 테이블에 직접 쓰지 않는다.
3. 다음 역할은 별도 process·container entrypoint로 실행하고 독립 재시작 가능하게 한다.
   - API/BFF·WebSocket
   - 실시간 시장 처리
   - 뉴스 수집·근거 매칭
   - 인포스탁 browser 수집
   - scheduler·장전·장후 batch
   - 운영자 작업
4. 공용 코드는 schema·계약·순수 계산·공통 인프라 adapter로 제한한다. 도메인 규칙을 여러 실행 역할에 복제하지 않는다.
5. 프로세스 간 계약은 버전된 내부 event와 command/query interface로 정의하고, DB 변경과 event 발행에는 outbox·idempotency를 적용한다.
6. 키움 연결이 Windows를 요구하면 Market Gateway는 처음부터 별도 OS 실행 경계로 둔다.
7. 프론트는 내부 process·DB schema·queue 배치를 알지 않고 REST·WebSocket 계약에만 의존한다.
8. 다음 근거 중 하나가 생기면 해당 모듈만 별도 마이크로서비스로 분리한다.
   - 독립 확장 요구가 지속됨
   - 장애가 다른 기능에 반복 전파됨
   - 별도 OS·보안 경계가 필요함
   - 별도 팀 소유권과 배포 주기가 생김
   - 측정된 SLO·복구 목표를 현재 배치로 충족할 수 없음

## 검토한 대안

### 단일 process 모놀리스

구현은 단순하지만 장중 처리, browser 수집, batch 장애를 독립 재시작하거나 자원 제한하기 어렵다. 역할별 실행 격리가 필요하므로 채택하지 않았다.

### 모듈형 모놀리스와 역할별 process·container

도메인 경계와 실행 격리를 유지하면서 코드·schema·계약이 빠르게 변하는 단계의 분산 운영 비용을 억제한다. 현재 인프라와 팀 구성에서 가장 적합하므로 채택했다.

### 전면 마이크로서비스

독립 확장과 팀 자율성에는 유리하지만 현재는 여러 VM, 별도 팀, 독립 SLO 근거가 없다. 단일 OCI 호스트에서 전면 분리해도 호스트 장애는 공유하면서 네트워크 실패, 분산 트랜잭션, 인증, 배포·schema 호환 부담만 늘어난다. 현재 채택하지 않으며 분리 조건 충족 시 모듈별로 재검토한다.

## 결과

### 반드시 지킬 것

- 기능·화면·데이터 범위를 축소하지 않는다.
- 코드베이스가 하나라는 이유로 모듈 간 직접 DB 쓰기와 순환 의존을 허용하지 않는다.
- 역할별 container에 health check, graceful shutdown, resource limit, 독립 로그를 둔다.
- 외부 API와 event schema를 내부 함수 형태에 종속시키지 않는다.
- 서비스 분리가 필요한 증거를 부하·장애·팀 소유권·OS·보안·SLO로 기록한다.

### 허용되는 후속 조정

- PoC 결과에 따라 역할별 container의 resource limit과 replica 수를 조정할 수 있다.
- 자원 사용량이 매우 작은 batch entrypoint를 같은 image로 build할 수 있지만 실행 process와 책임은 분리한다.
- 별도 서비스 추출 시 외부 API 계약과 도메인 식별자는 유지한다.

## 검증 체크

- [ ] 저장소 구조에서 모듈별 public interface와 금지 의존성이 자동 검사된다.
- [ ] 각 역할이 별도 entrypoint·container로 기동된다.
- [ ] 한 worker의 재시작이 API process 재시작을 요구하지 않는다.
- [ ] 도메인 간 직접 table write가 없다.
- [ ] OpenAPI·AsyncAPI·내부 event schema가 버전 관리된다.
- [ ] outbox·idempotency failure test가 통과한다.
