# ADR-009. OCI A1 Flex 단일 VM 백엔드 운영 배치

- 상태: `accepted`
- 결정일: 2026-08-14
- 적용 범위: staging, production-shadow, production의 첫 운영 배치
- 관련 문서: [system_architecture.md](../system_architecture.md), [implementation_roadmap.md](../implementation_roadmap.md)

## 배경

DAYJAVIEW는 상시 scheduler, WebSocket API, PostgreSQL·Redis, 장중 worker, 로그인 session을 사용하는 인포스탁 browser worker를 운영해야 한다. 정적 프론트 호스팅이나 요청 때만 실행되는 serverless 환경만으로는 이 수명주기 전체를 안정적으로 소유하기 어렵다.

팀은 이미 OCI `VM.Standard.A1.Flex` 4 OCPU·24GB, Ubuntu 22.04 LTS ARM64 VM을 보유하고 있다. 운영자 SSH public key 등록과 접속은 검증됐다. 다만 기존 프로젝트의 잔존 service·container·cron·파일 정리는 아직 별도 검증 대상이다.

## 결정

1. 첫 운영 플랫폼으로 기존 OCI A1 Flex VM을 재사용한다.
2. 배포는 Docker Compose를 기준으로 하며 reverse proxy, API/BFF, 역할별 worker, PostgreSQL, Redis를 같은 호스트에 둔다. React production frontend는 ADR-011에 따라 Vercel에 둔다.
3. 같은 호스트여도 process·container·network·volume·credential 경계를 유지한다.
4. staging과 production은 Compose project, port, volume, DB credential을 분리한다.
5. public ingress는 HTTPS와 제한된 SSH만 허용한다. DB·Redis·인포스탁 인증 UI는 외부에 직접 공개하지 않는다.
6. 인포스탁 수동 로그인 UI는 loopback에 bind하고 SSH tunnel로만 접근한다.
7. production secret은 Git 밖의 root 소유 `0600` env 파일 또는 OCI Vault로 주입한다. private SSH key는 운영자 단말 밖으로 복사하지 않는다.
8. PostgreSQL, session state, 허용된 원본 blob은 영구 volume에 두고 공개 출시 전 외부 암호화 backup과 restore를 시험한다.
9. 모든 컨테이너와 Playwright·Chromium 경로는 `linux/arm64` PoC를 먼저 통과해야 한다. browser worker만 호환되지 않으면 그 worker만 호환 실행 환경으로 분리한다.

## 대안

### Google Compute Engine

기술적으로 가능하지만 이미 보유한 OCI 자원을 두고 별도 비용과 운영 대상을 추가할 이유가 없어 채택하지 않았다.

### 정적 호스팅·serverless만 사용

프론트 배포에는 적합하지만 장시간 WebSocket, scheduler, stateful browser session, PostgreSQL·Redis를 포함한 전체 운영 호스트로는 맞지 않아 채택하지 않았다.

### 처음부터 여러 VM 또는 managed service로 분리

장애 격리와 고가용성에는 유리하지만 현재 부하·SLO 근거가 없다. 논리 경계는 유지하고 실제 부하·복구 지표가 분리를 요구할 때 확장한다.

## 결과

### 장점

- 추가 VM 비용 없이 현재 자원을 활용한다.
- 상시 worker와 browser session을 같은 운영 경계에서 관리할 수 있다.
- 역할별 container 구조로 이후 분리 가능성을 유지한다.

### 비용과 위험

- 단일 호스트 장애가 전체 서비스 중단으로 이어진다.
- ARM64 image와 browser runtime 호환성을 별도 검증해야 한다.
- DB와 애플리케이션의 자원 경합을 측정하고 제한해야 한다.
- 호스트 자체가 손실되면 로컬 volume도 함께 잃을 수 있으므로 외부 backup이 필수다.

## 구현 전·출시 전 검증

- [x] 운영자 SSH public key 등록과 실제 접속 확인
- [x] 임시 복구용 VM·volume 정리
- [ ] 기존 프로젝트의 service·container·cron·파일 잔존 여부 확인과 제거
- [ ] OS 보안 update와 SSH·firewall baseline 적용
- [ ] Docker Engine·Compose 설치와 재부팅 후 자동 기동 검증
- [ ] 핵심 image의 `linux/arm64` build·실행 검증
- [ ] Playwright·Chromium 로그인·storage state 저장·복원 PoC
- [ ] staging·production network·volume·credential 격리 검증
- [x] `dayjaview.duckdns.org`와 `api.dayjaview.duckdns.org` A record를 OCI VM 공인 IPv4에 연결
- [ ] `api.dayjaview.duckdns.org` TLS와 Vercel rewrite·Google OAuth callback 검증
- [ ] PostgreSQL·session state 외부 backup과 restore drill
- [ ] rollback과 재부팅 복구 시험
