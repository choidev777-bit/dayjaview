# ADR-011. Vercel 프론트엔드와 OCI 백엔드 분리 배포

- 상태: `accepted`
- 결정일: 2026-08-14
- 적용 범위: staging preview, production frontend, production backend ingress
- 관련 문서: [system_architecture.md](../system_architecture.md), [api_contract.md](../api_contract.md), [implementation_roadmap.md](../implementation_roadmap.md)

## 배경

팀은 production용 소유 도메인이 없고, React 프론트엔드는 Vercel의 배포·preview 흐름을 사용하기로 했다. 장시간 실행되는 키움 연결, WebSocket, scheduler, 인포스탁 browser worker, PostgreSQL과 Redis는 이미 보유한 OCI A1 Flex VM에서 운영한다.

프론트와 API가 서로 다른 registrable domain을 사용하므로 브라우저가 `api.dayjaview.duckdns.org`의 cookie를 제3자 cookie로 취급하게 만들면 브라우저별 차단 정책에 따라 로그인이 깨질 수 있다.

## 결정

1. 사용자 React 프론트엔드는 `https://dayjaview.vercel.app`에 배포한다.
2. OCI API·WebSocket 공개 endpoint는 `https://api.dayjaview.duckdns.org`와 `wss://api.dayjaview.duckdns.org/v1/realtime`을 사용한다.
3. browser의 REST와 Google OAuth 요청은 `https://dayjaview.vercel.app/api/*`를 사용하고 Vercel external rewrite가 OCI HTTPS API로 전달한다.
4. session cookie는 `Domain` 속성이 없는 Secure·HttpOnly host-only cookie로 발급한다. 브라우저에는 `dayjaview.vercel.app`의 first-party cookie로 저장된다.
5. private·사용자별 REST 응답은 `Cache-Control: private, no-store`로 제공하며 Vercel edge cache에 저장하지 않는다.
6. WebSocket은 Vercel을 통과시키지 않는다. 인증된 same-app REST에서 수명 30초의 1회용 ticket을 발급하고, OCI WSS 연결 직후 첫 `AUTH` message로 제출해 Redis에서 원자적으로 소비한다.
7. 장기 bearer token, session ID 또는 ticket을 WebSocket URL query에 넣지 않는다. 인증 전에는 어떠한 제품 snapshot도 보내지 않는다.
8. OCI API는 허용 Origin을 `https://dayjaview.vercel.app`로 제한한다. preview deployment는 production API에 연결하지 않고 별도 staging API와 명시적 origin allowlist를 사용한다.
9. Railway는 사용하지 않는다.
10. 소유 도메인을 구매하면 app·API hostname을 같은 소유 도메인의 하위 도메인으로 이전하되 public API field와 도메인 식별자는 유지한다.

## DuckDNS 확인 조건

`dayjaview.duckdns.org` 등록 후 `api.dayjaview.duckdns.org`의 A/AAAA 해석과 ACME 인증서 발급을 실제로 확인한다. 하위 hostname을 안정적으로 사용할 수 없으면 backend endpoint만 `dayjaview-api.duckdns.org`로 바꾸고 나머지 경계는 유지한다.

## 결과

### 장점

- 프론트 배포와 preview는 Vercel이 담당한다.
- 상시·상태ful backend는 기존 OCI 자원을 사용한다.
- REST와 OAuth가 browser 관점에서 app origin에 유지되어 제3자 cookie 의존을 피한다.
- WebSocket은 Vercel 함수 수명과 proxy 제약을 피한다.

### 비용과 위험

- Vercel rewrite와 OCI API 두 ingress를 함께 시험해야 한다.
- Vercel·DuckDNS의 공유 도메인은 팀 소유 도메인이 아니므로 Google OAuth 일반 공개 검증에는 별도 소유 도메인이 필요할 수 있다.
- DuckDNS 장애와 hostname 정책에 의존한다.
- WebSocket ticket 발급·만료·원자적 소비 구현이 추가된다.

## 구현·출시 검증

- [ ] Vercel production URL에서 `/api/health` rewrite 성공
- [ ] Google OAuth callback과 host-only session cookie 왕복 성공
- [ ] Safari·Chrome 모바일에서 제3자 cookie 허용 없이 로그인 유지
- [ ] private REST 응답이 Vercel cache에 남지 않음
- [ ] 1회용 WebSocket ticket 재사용·만료·다른 session 사용 거부
- [ ] WebSocket 인증 전 snapshot 0건
- [ ] production Origin 외 REST mutation·WSS 연결 거부
- [ ] DuckDNS 하위 hostname DNS·Caddy ACME 발급·자동 갱신 성공

