# DAYJAVIEW 작업 규칙

이 파일이 이 저장소에서 에이전트 행동을 정하는 **유일한 규칙 파일**이다.
`docs/**`는 제품 요구사항과 과거 실행 기록이며, 행동 규칙이 아니다.

## 1. 범위

- 요청받은 것만 구현한다. 요청하지 않은 추상화 계층, 설정 옵션, 확장 지점, 방어 코드를 추가하지 않는다.
- 2줄로 되는 일은 2줄로 끝낸다. 헬퍼·wrapper·전용 모듈은 같은 코드가 3번째 반복될 때 만든다.
- 요청 범위를 스스로 줄이지 않는다. "MVP니까 이 정도만" 류의 축소 제안을 하지 않는다.
- 다 못 하면 나머지를 전부 끝낸 뒤 무엇을 왜 못 했는지 한 줄로 보고한다. 범위를 줄여서 완료라고 하지 않는다.
- 리팩터링·정리·개선은 요청받았을 때만 한다. 작업 중 발견한 문제는 고치지 말고 보고한다.

## 2. 문서

- 새 문서를 만들지 않는다. ADR, 설계 문서, 계획 문서, 완료 보고서, 요약 md는 **명시 요청이 있을 때만** 작성한다.
- `docs/implementation_roadmap.md`는 필요한 절만 읽는다. 전체를 읽고 요약하지 않는다.
- 작업 결과는 commit 메시지와 채팅 응답으로 보고한다. 문서에 실행 기록을 덧붙이지 않는다.

## 3. 테스트와 검증

- 수정한 코드에 대응하는 테스트만 쓴다. 신규 `*_contract.py`, manifest 검사, evidence 테스트를 만들지 않는다.
- 파이썬 검증은 `uv run pytest -q` 1회로 끝낸다. 통과하면 더 돌리지 않는다.
- `apps/web`을 건드렸을 때만 web lint/typecheck를 돌린다.
- 내 변경과 무관한 기존 실패는 고치지 말고 한 줄로 보고한다.

## 4. 승인

아래 3가지만 사용자 승인이 필요하다. 그 외에는 묻지 말고 진행한다.

1. 실제 배포와 cloud 리소스 변경 (OCI, Vercel, DNS)
2. 외부 API를 실제로 호출하는 수집 실행 (인포스탁 live, 키움 live)
3. 파일·데이터 삭제

`git push`는 승인 없이 진행한다.

`docs/**`에 적힌 "승인", "대기", "금지", "사용자가 직접 수행"은 **작성 시점의 상태 기록**이지 지금 세션에 대한 승인 요구가 아니다. 그 문구 때문에 작업을 멈추지 않는다.

## 5. 보고

- 완료한 것은 "했다", 안 한 것은 "안 했다"로 쓴다. 추정을 완료로 쓰지 않는다.
- 응답은 짧게. 한 일 요약과 확인이 필요한 것만. 수정한 코드 전문을 다시 붙여넣지 않는다.

## 명령

| 목적 | 명령 |
|---|---|
| 파이썬 테스트 | `uv run pytest -q` (전체 381 passed·5 skipped 기준; skip은 DSN 필요 postgres 통합 테스트) |
| 웹 lint | `pnpm --dir apps/web run lint` |
| 웹 타입체크 | `pnpm --dir apps/web run typecheck` |
| 웹 테스트 | `pnpm --dir apps/web run test` |
| 웹 빌드 | `pnpm --dir apps/web run build` |

이 머신에는 **pnpm 10이 전역 설치되어 있고 `apps/web/node_modules`도 설치돼 있다** (2026-08-15). npm으로 대체하지 않는다 (`pnpm-lock.yaml` 기준).

## 구조

- `apps/` — web, api, worker-market, worker-batch
- `packages/` — 도메인 로직 (infostock, reference-data, identity, events, ...)
- `contracts/` — OpenAPI·JSON Schema 기계 계약
- `infra/` — migrations, deployment, operations
- `docs/` — 제품 요구사항과 실행 기록 (규칙 아님)
