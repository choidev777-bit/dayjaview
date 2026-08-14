# DAYJAVIEW Stage 0 기계 계약

이 디렉터리는 docs/api_contract.md의 기계 판독 기준이다.

- openapi.yaml은 확정된 HTTP surface를 정의한다.
- asyncapi.yaml은 인증된 WebSocket message와 full snapshot topic을 정의한다.
- schemas/stage0.schema.json은 공통 JSON Schema 2020-12 vocabulary다.
- fixtures/manifest.json은 모든 fixture를 공통 schema 하나에 연결하고 순서가 있는 invariant series를 기록한다.
- meta/asyncapi-stage0.schema.json은 이 저장소가 사용하는 AsyncAPI 3.0 profile을 고정한다.

이 계약에는 backend나 frontend runtime 구현이 없다. 일반 사용자 schema에는
operator review field가 없으며, historical endpoint는 외부 연구 gate가 해제될
때까지 feature gate 상태를 유지한다.

## 로컬 검증

저장소 루트에서 다음 명령을 실행한다.

    uv run python scripts/validate_contracts.py
    uv run pytest tests/contracts -q

validator는 OpenAPI, AsyncAPI profile, 모든 reference와 fixture, prose 계약의
JSON 예시, 문서 간 의미 invariant를 검증한다. 네트워크를 호출하지 않으며
secret이나 외부 서비스가 필요하지 않다.
