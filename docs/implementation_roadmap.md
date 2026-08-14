# DAYJAVIEW 최종 구현 로드맵

- 문서 버전: `1.1`
- 문서 상태: 실행 계획
- 최종 수정일: 2026-08-14
- 제품 기준: [PRD.md](./PRD.md)
- 화면 기준: [screen_spec.md](./screen_spec.md)
- 시스템 기준: [system_architecture.md](./system_architecture.md)
- 디자인 원본: [dayjaview-prototype](https://github.com/nangom/dayjaview-prototype)
- 검토한 디자인 원본 커밋: `65324878db4ef92bb29c7fce21e63a1031c3be17`

---

## 실행 원장

- 감사 기준 시각: `2026-08-14 12:27 KST`
- 감사 기준 commit: `91bd3b0ce764e4b8a3783de9be2eb3229f80d4eb`
- 실행 원장 위치: 이 절 하나만 사용한다. 아래의 기존 단계 설명은 요구사항 상세 참고이며 별도 원장이 아니다.

### 서비스 정의

1. Google 로그인 뒤에만 제품 데이터를 제공하는 국내 주식 테마 분석 서비스다.
2. 키움 후보 탐색과 선택 종목 체결로 장중 강해지는 기존 테마를 수초 단위로 찾는다.
3. 테마 수익률·확산·주도주·Coverage·freshness를 서로 다른 의미로 정확히 표시한다.
4. 상승 이유는 저장된 기사 근거가 확인된 범위에서만 제시하고 근거가 없으면 생성하지 않는다.
5. 장중 Event를 장후 인포스탁 확정·revision과 같은 `eventId`로 연결한다.
6. 과거 유사사례는 온톨로지 재검증을 통과한 artifact만 관련성 순서와 실제 관측 결과로 제공한다.
7. 관심 테마·종목·이벤트는 계정에 저장하되 공용 시장 계산에는 영향을 주지 않는다.
8. 매수·매도 추천, 자동매매, 미래 확률·예상 수익률, 제품 analytics는 범위 밖이다.

### 현재 구현 상태와 근거

| 영역 | 사실 기반 판정 | 근거 |
|---|---|---|
| Git | `main`과 `origin/main`은 `6c2637e`에서 일치한다. Stage 0 통합은 별도 `codex/int-s0-foundation` branch에서 수행했고 검증된 task commit과 repair commit을 non-fast-forward merge했다. | `git rev-parse main origin/main`, `git ls-remote`, merge ancestry·graph 점검 |
| 사용자 변경 | 추적 파일 `PROMPTS.md` 삭제 1건이 미커밋 상태다. 사용자 소유로 간주해 복원·수정·stage하지 않는다. | `git diff --name-status` → `D PROMPTS.md` |
| 제품 애플리케이션 | 프론트엔드, API, worker 서비스, DB migration과 배포 runtime은 아직 없다. Stage 0에서 기계 계약, 공통 fixture, package lock과 계약 CI만 추가됐으며 제품 기능 구현 완료를 뜻하지 않는다. | `apps/`, runtime `infra/` 부재; `contracts/**`, `pyproject.toml`, `uv.lock`, `.github/workflows/contracts.yml` 존재 |
| 문서 | PRD·화면·시스템 기준은 유지된다. ADR-002~008이 경계의 확정·제안·외부 gate를 분리했고 API 문서의 saved decimal return과 일반 사용자 `reviewStatus` 모순은 기계 계약과 함께 제거됐다. API 문서는 여전히 프론트·백엔드 공동 승인 전 `0.2-draft`다. | `docs/adr/002-*`~`008-*`; `docs/api_contract.md`; contract validator |
| 인포스탁 API 수집 | 로컬 import는 280/280 테마, 히스토리 39,696건, 실패 0건으로 완료됐지만 PostgreSQL 원천 DB·revision·Daily browser worker는 없다. | ignored `data/infostock/import/manifest.json`; tracked `scripts/collect_infostock.py` |
| 시장 capture | 본 capture와 보조 capture가 약 10:40 KST에 각각 `INTERRUPTED`; gap recovery는 시작되지 않았다. 최종 replay fixture가 아니다. | `scripts/check_market_capture.ps1` read-only 결과 |
| 기존 코드 | 추적 구현은 Infostock API collector와 2026-08-14 capture/replay 도구뿐이다. 모델 카드가 가리키는 `backend/app/engine/**`와 연구 artifact는 저장소에 없다. | `scripts/**`, `tests/**`, `git ls-files` |
| 자동 검증 | Stage 0 통합·repair 후 계약 validator, contract test 11개, market replay test 20개, 전체 offline test 35개가 통과한다. 저장 capture·gap recovery·replay는 실행하지 않았다. | `uv run python scripts/validate_contracts.py`; `uv run pytest tests/contracts -q`; `uv run pytest tests/test_market_replay.py -q`; `uv run pytest -q` |
| 외부 설정 | 값 노출 없이 존재 여부만 확인했다. 키움·NAVER API HUB·OpenAI·Google OAuth secret은 설정돼 있고 KRX·OpenDART key, operator bootstrap email, 암호화된 Infostock browser state는 없다. | `.env.local` key presence 검사, state path 존재 검사 |

### 핵심 MVP 완료 조건

- 비로그인 REST·WebSocket은 제품 데이터 없이 거부되고, Google 로그인·logout·session 만료·안전한 `returnTo`가 검증된다.
- 사용자별 관심 저장·해제·기기 간 동기화·IDOR 차단·계정 삭제가 검증된다.
- 실제 또는 승인된 adapter 입력으로 현재 테마 수익률·확산·주도주·Coverage·지연 상태가 결정적으로 계산된다.
- `ACTIVE/WEAKENING/CLOSED`, sequence, 재연결 full snapshot과 동일 `eventId`의 장후 revision이 검증된다.
- 뉴스 근거가 없을 때 LLM을 호출하거나 상승 이유를 생성하지 않으며 모든 요약에 source metadata가 연결된다.
- 오늘·테마 상세·관심·인사이트가 같은 계약과 fixture를 사용하고 모바일·데스크톱·키보드 기본 검증을 통과한다.
- 일반 사용자와 `OPERATOR` route·API·field가 서버 권한으로 분리되고 command가 revision·audit으로 남는다.
- 과거 검색은 미래 outcome을 입력으로 받지 않고, 재검증 승인 전 사용자 route와 API가 잠긴다.
- 관련 contract, lint, typecheck, unit/integration/E2E, affected build와 fixture smoke test가 통과한다.
- Record & Replay 실행·판정은 구현 완료 후 사용자가 직접 수행하며 Codex Stage는 실행하지 않았다고 기록한다.
- 모든 작업 소유 변경이 commit되고 최종 통합 branch가 GitHub에 push된다.
- 외부 권리·secret·시장 시간·수동 평가·배포 승인이 남으면 코드 완료와 외부 검증 대기를 분리해 보고한다.

### 필수 검증 명령

| 코드 | 명령 |
|---|---|
| `V-DIFF` | `git diff --check`와 작업 소유 경로의 `git diff --cached --name-status` |
| `V-CONTRACT` | `uv run python scripts/validate_contracts.py` 및 `uv run pytest tests/contracts -q` |
| `V-PY` | `uv run ruff check <owned-paths>`; `uv run mypy <owned-paths>`; `uv run pytest <targeted-paths> -q` |
| `V-WEB` | `pnpm --dir apps/web lint`; `pnpm --dir apps/web typecheck`; `pnpm --dir apps/web test --run`; `pnpm --dir apps/web build` |
| `V-STACK` | `docker compose -f infra/deployment/compose.local.yml config`와 fixture 기반 health smoke test |
| `V-ALL` | `V-CONTRACT`, 전체 Python test/lint/typecheck, `V-WEB`, `V-STACK`, 핵심 fixture E2E; 저장 market replay는 제외 |

각 작업은 위 명령 중 관련 명령과 더 좁은 targeted test를 모두 실행한다. 명령이 아직 없는 첫 기반 작업은 그 명령을 함께 만들고 같은 commit에서 통과시킨다.

### 작업 원장

| ID | Stage | 단 하나의 목표와 검증 가능한 종료 조건 | 선행 의존성 | 소유 파일·디렉터리 | 병렬 | 모델 | 필수 검증 | branch | 상태·blocker |
|---|---:|---|---|---|---|---|---|---|---|
| `S0-ADR` | 0 | 기존 확정 의미를 바꾸지 않고 ADR-002~008과 기존 ADR의 현재 증거를 정리한다. 각 ADR에 상태·결정·대안·결과·검증이 있고 상위 문서와 충돌하지 않으면 종료한다. | 이 원장 commit | `docs/adr/**` | `S0-CONTRACT`, `S0-REPLAY-UNIT`와 가능 | `gpt-5.6-sol / max` | `V-DIFF`, ADR 상태·링크 점검 | `codex/s0-adr-boundaries` | 완료; thread `019ffe5b-1a95-7bb0-a407-55215c83ac04`, remote `330a4115d884bba1fa124e78a622d4b77cac5567`; 11개 ADR section·local link·`V-DIFF` 독립 통과 |
| `S0-CONTRACT` | 0 | API 의미 계약을 OpenAPI·AsyncAPI·JSON Schema·공통 fixture·CI로 실행 가능하게 만든다. 모든 fixture와 예시가 schema/invariant를 통과하고 현재 문서의 두 모순이 제거되면 종료한다. | 이 원장 commit | `contracts/**`, `docs/api_contract.md`, `scripts/validate_contracts.py`, `tests/contracts/**`, `pyproject.toml`, `uv.lock`, `.github/workflows/contracts.yml` | `S0-ADR`, `S0-REPLAY-UNIT`와 가능 | `gpt-5.6-sol / xhigh` | `V-CONTRACT`, `V-DIFF` | `codex/s0-machine-contracts` | 완료; thread `019ffe5b-042d-7db2-a553-3285908d5fde`, remote `b18fb462eaf763b583631ca8453947b20024426f`; validator HTTP 30·WS 9·schema 79·fixture 43·문서 예시 21, contract test 11개 통과 |
| `S0-REPLAY-UNIT` | 0 | gap-recovery의 분 경계 candidate TTL 의미를 명세와 일치시켜 현재 실패 1건만 수술적으로 고친다. 전체 offline unit test가 통과하고 실제 replay·ignored data를 실행/변경하지 않으면 종료한다. | 이 원장 commit | `scripts/collect_market_gap_recovery.py`, `tests/test_market_replay.py` | `S0-ADR`, `S0-CONTRACT`와 가능 | `gpt-5.6-sol / xhigh` | `python -m pytest tests/test_market_replay.py -q`, `python -m pytest -q`, `V-DIFF` | `codex/s0-replay-minute-boundary` | 완료; thread `019ffe5b-1cb3-7440-9adb-fd3ad2e060b1`, remote `9d60fbfcea9b34252e8445de5b8f1ecb64eafd05`; targeted 20개·branch offline 24개, 통합 전체 35개 통과; replay 미실행 |
| `S1-WEB` | 1 | 계약 fixture로 로그인·오늘·테마 상세·관심·인사이트의 route/state shell을 구축한다. gate off 유사사례와 operator 비노출, 반응형·키보드 상태 test와 build가 통과하면 종료한다. | `INT-S0` | `apps/web/**` | Stage 1 다른 작업과 가능 | `gpt-5.6-sol / xhigh` | `V-WEB`, `V-CONTRACT`, `V-DIFF` | `codex/s1-web-fixture-shell` | 완료; thread `019ffe83-b454-7b20-b697-932460757368`, remote `ded32882fab31fdcc153fde11ae4ca3e8d8f3a07`; owned 32개, Web test 32개·lint·typecheck·production build·fixture boundary와 contract 검증 독립/통합 통과 |
| `S1-IDENTITY` | 1 | Google OAuth server session, 사용자/saved library, 계정 삭제, operator 역할 경계를 fixture 환경에서 구현한다. 인증·CSRF·open redirect·IDOR·role test가 통과하면 종료한다. | `INT-S0` | `apps/api/**`, `packages/identity/**`, `infra/migrations/*identity*`, `tests/identity/**` | Stage 1 다른 작업과 가능 | `gpt-5.6-sol / max` | `V-PY`, auth contract test, `V-DIFF` | `codex/s1-identity-library` | 완료; thread `019ffe83-b406-70a1-bcbc-0ed7a9bda13d`, remote `7983da71de78f5a2012e2be0d24a7d6b9e0ab82f`; owned 23개, ruff·mypy 15 files·auth/security test 33개와 contract 검증 독립/통합 통과; `B-OPERATOR` live bootstrap은 유지 |
| `S1-INFOSTOCK` | 1 | 기존 수집된 280/280 테마 원본 전체와 `DailyFeaturedTheme` 과거 전체를 PostgreSQL에 보존·정규화한다. 테마 master·설명·원본 식별자·source metadata, history 39,696건, related stocks 6,629건, leader와 당시/현재 membership을 적재하고 raw/normalized projection의 `collected_at`·`as_of`·hash·`parser_version`·`revision`·`lineage`를 보존한다. history duplicate 4건과 leader code missing 90건을 명시적 quality status로 유지한다. Daily는 목록 전체 pagination·기간 backfill, title/date/source ID/canonical URL, 본문 원문과 theme/stock/description 관계, raw snapshot, idempotent upsert, 수정·삭제·누락·parse failure 상태까지 포함해야 최초 전체 DB가 완료다. S6에는 같은 schema/revision 모델의 일일 증분 자동화만 남긴다. | `INT-S0` | `packages/infostock/**`, `apps/worker-batch/infostock/**`, `infra/migrations/*infostock*`, `tests/infostock/**` | Stage 1 다른 작업과 가능 | `gpt-5.6-sol / max` | `V-PY`, PostgreSQL 16 full-import·idempotency·quality integration, `V-DIFF` | `codex/s1-infostock-store` | 부분 완료; thread `019ffe83-b42d-7963-a8ed-dd0fef4b6bb2`, remote `90f8db749a6ca46df59dec57cc12a898acd8bb9e`, owned 27개. Theme DB는 PostgreSQL 16.15에서 280/280·history 39,696·related 6,629·leader 65,526·historical membership 652,241·raw 165,275,696 bytes·idempotency/revision/integrity 통과. Daily 실제 확보분은 page 1·목록 5·본문 1·관계 232·nextPage 2뿐이고 source ID/URL 5건·본문 4건이 누락되어 `BLOCKED`; repair thread `019fff44-2ee3-7162-8cc0-d7ccc6466b38`은 owned 결함 0의 no-op genuine blocker로 종료. `B-INFOSTOCK-AUTH`·`B-DATA-RIGHTS` 유지, 실제 전체 DB 완료로 표시하지 않음 |
| `S1-CALC` | 1 | 순수 domain에서 유동시총 상한 가중, 중앙값, 확산, 관심 배수·공백, Coverage와 상태 전이를 결정적으로 구현한다. 경계·결측·property test와 versioned fixture가 통과하면 종료한다. | `INT-S0` | `packages/domain/**`, `packages/calculations/**`, `tests/domain/**`, `tests/calculations/**` | Stage 1 다른 작업과 가능 | `gpt-5.6-sol / max` | `V-PY`, `V-CONTRACT`, `V-DIFF` | `codex/s1-domain-calculations` | 완료; thread `019ffe83-b452-73f1-84e1-96aadfdc3c71`, remote `36b911f00c82c3612143e2437567ef7aa2b170e5`; owned 21개, ruff·mypy 10 files·domain/calculation test 45개와 contract 검증 독립/통합 통과 |
| `S2-REFDATA` | 2 | KRX Open API·OpenDART 무료 원천 adapter와 point-in-time 유동주식비율·거래일·조정가격 기준정보를 구현한다. fixture 계약·충돌/결측/중복차감 test가 통과하고 live 미검증이 분리되면 종료한다. | `INT-S1` | `packages/reference-data/**`, `apps/worker-batch/reference-data/**`, `infra/migrations/*reference*`, `tests/reference-data/**` | Stage 2 다른 작업과 가능 | `gpt-5.6-sol / max` | `V-PY`, source contract fixtures, `V-DIFF` | `codex/s2-reference-data` | 생성 대기; pushed `INT-S1` SHA를 exact base로 사용. 코드/fixture 구현은 가능하나 `B-REFDATA-KEYS` live 검증과 Infostock Daily blocker를 완료로 위장하지 않음 |
| `S2-MARKET` | 2 | 키움 read-only Market Gateway와 후보·구독 180/200 slot·재연결·스냅샷 보완을 canonical event로 구현한다. fixture/adapter contract·slot churn·old input test가 통과하면 종료한다. | `INT-S1` | `apps/worker-market/**`, `packages/adapters/kiwoom/**`, `tests/market-gateway/**` | Stage 2 다른 작업과 가능 | `gpt-5.6-sol / max` | `V-PY`, adapter contract, `V-DIFF` | `codex/s2-market-gateway` | 생성 대기; pushed `INT-S1` SHA를 exact base로 사용. fixture/adapter 구현은 가능하고 실제 장중 검증은 분리 |
| `S2-REALTIME` | 2 | 단일 Event writer, hot state, dirty-theme 집계, hysteresis, outbox, versioned read snapshot을 구현한다. lifecycle/idempotency/recovery/Coverage test가 통과하면 종료한다. | `INT-S1` | `packages/realtime/**`, `packages/events/**`, `infra/migrations/*event*`, `tests/realtime/**`, `tests/events/**` | Stage 2 다른 작업과 가능 | `gpt-5.6-sol / max` | `V-PY`, `V-CONTRACT`, `V-DIFF` | `codex/s2-realtime-events` | 생성 대기; pushed `INT-S1` SHA를 exact base로 사용. Daily 실제 backfill과 독립된 event/realtime 코드 foundation 작업만 수행 |
| `S3-API` | 3 | 인증된 핵심 REST와 realtime-ticket WebSocket을 기계 계약 위에 구현한다. 비로그인 데이터 0건, 1회용 ticket, sequence/full snapshot, saved/operator 분리 contract test가 통과하면 종료한다. | `INT-S2` | `apps/api/**`, `tests/api/**`, `tests/realtime-api/**` | Stage 3 다른 작업과 가능 | `gpt-5.6-sol / max` | `V-PY`, `V-CONTRACT`, API/WSS smoke, `V-DIFF` | `codex/s3-api-realtime` | 계획 |
| `S3-WEB-LIVE` | 3 | fixture adapter와 동일 컴포넌트에 실제 REST/WSS client, cache purge, sequence/reconnect, saved flow를 연결한다. mock/staging contract parity와 `V-WEB`가 통과하면 종료한다. | `INT-S2` | `apps/web/**` | Stage 3 다른 작업과 가능 | `gpt-5.6-sol / xhigh` | `V-WEB`, `V-CONTRACT`, client integration, `V-DIFF` | `codex/s3-web-live-adapter` | 계획 |
| `S3-REPLAY-ADAPTER` | 3 | 저장 capture를 제품 canonical market event 입력으로 바꾸는 bounded adapter와 fixture test를 구현한다. ordering/hash/clock/failure test만 통과시키고 capture 또는 replay 실행은 하지 않으면 종료한다. | `INT-S2` | `packages/adapters/market-replay/**`, `tests/market-replay-adapter/**` | Stage 3 다른 작업과 가능 | `gpt-5.6-sol / xhigh` | `V-PY`, synthetic fixture only, `V-DIFF` | `codex/s3-replay-adapter` | 계획; 실제 replay 금지 |
| `S3-INFRA-LOCAL` | 3 | API·worker·PostgreSQL·Redis의 local/CI Compose와 ARM64-compatible image 골격을 만든다. config 검증, local fixture health, secret 미포함 검사가 통과하면 종료한다. | `INT-S2` | `infra/deployment/**`, `infra/images/**`, `infra/operations/local*`, `tests/infra/**` | Stage 3 다른 작업과 가능 | `gpt-5.6-sol / xhigh` | `V-STACK`, image/config scan, `V-DIFF` | `codex/s3-local-stack` | 계획; 배포하지 않음 |
| `S4-EVIDENCE-BE` | 4 | 허용 공급원 수집·중복제거·양방향 매칭·grounded LLM·근거 revision을 한 backend pipeline으로 구현한다. 근거 없음=no call/no cause, PIT와 source metadata test가 통과하면 종료한다. | `INT-S3` | `apps/worker-news/**`, `packages/news/**`, `packages/catalyst/**`, `packages/llm/**`, `infra/migrations/*news*`, `tests/evidence/**` | `S4-EVIDENCE-WEB`과 가능 | `gpt-5.6-sol / max` | `V-PY`, mocked provider contracts, `V-CONTRACT`, `V-DIFF` | `codex/s4-grounded-evidence` | 계획; 공급원 권리 확인 대기 |
| `S4-EVIDENCE-WEB` | 4 | SEARCHING부터 AFTER_CLOSE_CONFIRMED까지 근거 상태와 출처 UI를 구현한다. 원인 미생성·원문 link·지연/장애 상태 component test와 build가 통과하면 종료한다. | `INT-S3` | `apps/web/**` | `S4-EVIDENCE-BE`와 가능 | `gpt-5.6-sol / xhigh` | `V-WEB`, evidence fixtures, `V-DIFF` | `codex/s4-evidence-ui` | 계획 |
| `S5-TREEMAP-BE` | 5 | Coverage를 통과한 양수 ACTIVE/WEAKENING 상위 12개 full snapshot topic을 구현한다. 동일 `weightedReturn`, sequence/coalescing/closed test가 통과하면 종료한다. | `INT-S4` | `packages/realtime/treemap/**`, `apps/api/realtime/treemap*`, `tests/treemap-server/**` | `S5-TREEMAP-WEB`과 가능 | `gpt-5.6-sol / xhigh` | `V-PY`, `V-CONTRACT`, `V-DIFF` | `codex/s5-treemap-server` | 계획 |
| `S5-TREEMAP-WEB` | 5 | 실제 값만으로 움직이는 접근 가능한 treemap을 구현한다. 12-tile, no-toggle, stale/reduced-motion/keyboard/layout-throttle test와 build가 통과하면 종료한다. | `INT-S4` | `apps/web/**` | `S5-TREEMAP-BE`와 가능 | `gpt-5.6-sol / xhigh` | `V-WEB`, treemap fixtures, `V-DIFF` | `codex/s5-treemap-ui` | 계획 |
| `S6-RECONCILE` | 6 | 암호화 session state의 Daily browser worker와 장후 같은-event reconciliation을 구현한다. atomic state, AUTH_REQUIRED, revision, UNMATCHED, retry/idempotency test가 통과하면 종료한다. | `INT-S5` | `apps/worker-batch/infostock-daily/**`, `packages/infostock/reconciliation/**`, `packages/events/reconciliation/**`, `tests/reconciliation/**` | `S6-OPERATOR`와 가능 | `gpt-5.6-sol / max` | `V-PY`, browser fixture contract, `V-DIFF` | `codex/s6-after-close-reconcile` | 계획; 수동 login state 없음 |
| `S6-OPERATOR` | 6 | 일반 사용자와 분리된 operator status/job/review/audit API와 화면을 구현한다. USER 403, secret redaction, CSRF/idempotency/stale-version/revision test와 build가 통과하면 종료한다. | `INT-S5` | `apps/api/operator/**`, `apps/web/src/operator/**`, `packages/operator/**`, `tests/operator/**` | `S6-RECONCILE`와 가능 | `gpt-5.6-sol / max` | `V-PY`, `V-WEB`, security contracts, `V-DIFF` | `codex/s6-operator-console` | 계획; live operator bootstrap 대기 |
| `S7-HISTORY-DATA` | 7 | point-in-time 역사 corpus, 당시 leader 동일가중 outcome, 거래일·기업행위·결측 lineage를 재현 가능하게 구축한다. 데이터 감사와 leakage/outcome test가 통과하면 종료한다. | `INT-S6` | `research/data/**`, `packages/historical-data/**`, `tests/historical-data/**`, `docs/research/data_*` | `S7-ONTOLOGY`와 가능 | `gpt-5.6-sol / max` | `V-PY`, PIT/leakage test, `V-DIFF` | `codex/s7-historical-corpus` | 계획; 가격 corpus/artifact 부재 |
| `S7-ONTOLOGY` | 7 | versioned 사건 온톨로지와 point-in-time transform·annotation 계약을 구현한다. 통제어휘, unknown/복합 사건, deterministic transform test가 통과하면 종료한다. | `INT-S6` | `research/ontology/**`, `packages/ontology/**`, `tests/ontology/**`, `docs/research/event_ontology.md` | `S7-HISTORY-DATA`와 가능 | `gpt-5.6-sol / max` | `V-PY`, schema/invariant test, `V-DIFF` | `codex/s7-event-ontology` | 계획 |
| `S8-MATCHING` | 8 | M-TXT v1을 재현하고 ontology/hybrid 후보를 같은 fold에서 비교하는 leakage-safe engine·blind review package·registry를 구현한다. 자동 평가와 artifact reproducibility가 통과하면 종료한다. | `INT-S7` | `research/matching/**`, `packages/historical-matching/**`, `tests/historical-matching/**`, `docs/research/matching_*` | 불가; 단일 연구 책임 | `gpt-5.6-sol / max` | `V-PY`, PIT·retrieval·registry test, `V-DIFF` | `codex/s8-matching-validation` | 계획; 2인 평가와 새 봉인 구간은 외부 gate |
| `S9-HISTORY-BE` | 9 | 승인된 immutable 검색 artifact만 유사사례 API에 서빙하고 outcome은 선택 후 결합한다. gate/entitlement/분모/정렬/leakage contract test가 통과하면 종료한다. | `INT-S8` + 외부 연구 gate | `apps/api/historical/**`, `packages/historical-serving/**`, `tests/historical-api/**` | `S9-HISTORY-WEB`과 가능 | `gpt-5.6-sol / max` | `V-PY`, `V-CONTRACT`, leakage test, `V-DIFF` | `codex/s9-historical-serving` | gate 전 생성 금지 |
| `S9-HISTORY-WEB` | 9 | 승인 후에만 유사사례 집계·목록·과거 Event 상세를 노출한다. 기간별 분모·중앙값·누락·비예측 문구와 gate-off test가 통과하면 종료한다. | `INT-S8` + 외부 연구 gate | `apps/web/**` | `S9-HISTORY-BE`와 가능 | `gpt-5.6-sol / xhigh` | `V-WEB`, historical fixtures, `V-DIFF` | `codex/s9-historical-ui` | gate 전 생성 금지 |
| `S10-SECURITY` | 10 | auth/권한/secret/입력/의존성 경계를 감사하고 재현 test·finding만 남긴다. 차단 finding 0건 또는 독립 repair Goal이 생성되면 종료한다. | `INT-S9` | `tests/security/**`, `docs/release/security_audit.md` | Stage 10 다른 감사와 가능 | `gpt-5.6-sol / max` | security test, secret scan, dependency audit, `V-DIFF` | `codex/s10-security-audit` | 계획; 제품 파일 직접 수리 금지 |
| `S10-QA` | 10 | replay를 제외한 계약·unit/integration/E2E·접근성·성능 검증을 실행하고 증거를 남긴다. 차단 실패 0건 또는 독립 repair Goal이 생성되면 종료한다. | `INT-S9` | `tests/e2e/**`, `tests/performance/**`, `docs/release/qa_report.md` | Stage 10 다른 감사와 가능 | `gpt-5.6-sol / xhigh` | `V-ALL`, replay 미실행 명시, `V-DIFF` | `codex/s10-release-qa` | 계획; 제품 파일 직접 수리 금지 |
| `S10-OPS` | 10 | OCI/Vercel 배포 골격, backup/restore/rollback/runbook을 release-ready 상태로 만든다. config/image/local recovery smoke와 secret 분리 검사가 통과하면 종료한다. | `INT-S9` | `infra/**`, `docs/release/operations_runbook.md`, `tests/infra/**` | Stage 10 다른 감사와 가능 | `gpt-5.6-sol / xhigh` | `V-STACK`, config/backup fixture smoke, `V-DIFF` | `codex/s10-operations-ready` | 계획; 실제 배포·cloud 변경 금지 |

### Stage별 통합 순서

| 통합 Goal | 입력과 유일한 목표 | 모델 | branch | 통합 검증·다음 행동 | 상태 |
|---|---|---|---|---|---|
| `INT-S0` | `S0-ADR`, `S0-CONTRACT`, `S0-REPLAY-UNIT`의 commit만 깨끗한 worktree에 병합 | `gpt-5.6-sol / xhigh` | `codex/int-s0-foundation` | `V-CONTRACT`, 전체 offline pytest, ledger 증거 갱신·push 후 Stage 1 작업과 `INT-S1` 생성 | 통합 검증 완료; merged artifact `e90c72e`에 task 3개와 dependency repair가 포함됨; 이 evidence commit push와 후속 Goal 생성만 남음 |
| `INT-S1` | Stage 1 네 기반 결과 통합 | `gpt-5.6-sol / xhigh` | `codex/int-s1-app-base` | Python/Web/PostgreSQL 실수집 검증 후 Stage 2 생성 | 코드 foundation 통합 검증 완료; audited merge artifact `fbfebbbbbe1cb489a8a0de467936702454846c16`. 이 원장 evidence commit을 origin에 push한 SHA가 Stage 2 exact base다. Infostock Theme DB는 완료, Daily 최초 전체 backfill은 외부 blocker로 부분 완료이며 Stage 2 task에 그대로 전달 |
| `INT-S2` | 기준정보·Market Gateway·Realtime/Event 결과 통합 | `gpt-5.6-sol / max` | `codex/int-s2-realtime-core` | deterministic 계산·recovery·adapter contract 검증 후 Stage 3 생성 | 계획 |
| `INT-S3` | API·실제 web adapter·replay adapter·local stack 통합 | `gpt-5.6-sol / max` | `codex/int-s3-first-vertical` | fixture end-to-end·auth·WSS·health smoke; replay 실행 없이 Stage 4 생성 | 계획 |
| `INT-S4` | 근거 backend와 UI 통합 | `gpt-5.6-sol / xhigh` | `codex/int-s4-evidence` | no-evidence/no-LLM·PIT·source UI 검증 후 Stage 5 생성 | 계획 |
| `INT-S5` | treemap server와 UI 통합 | `gpt-5.6-sol / xhigh` | `codex/int-s5-treemap` | today와 값 일치·sequence·a11y·성능 fixture 검증 후 Stage 6 생성 | 계획 |
| `INT-S6` | 장후 정합과 operator 통합 | `gpt-5.6-sol / max` | `codex/int-s6-reconciliation` | same-event revision·USER 403·audit·AUTH_REQUIRED fixture 검증 후 Stage 7 생성 | 계획 |
| `INT-S7` | 역사 corpus와 ontology 통합 | `gpt-5.6-sol / max` | `codex/int-s7-research-base` | PIT/outcome/ontology 재현성 검증 후 Stage 8 생성 | 계획 |
| `INT-S8` | matching engine 결과 검증과 gate 판정만 수행 | `gpt-5.6-sol / max` | `codex/int-s8-matching-gate` | 2인 blind 평가·2026-09 이후 새 봉인 구간·승인 artifact 없으면 Stage 9를 생성하지 않고 정확한 blocker 기록 | 계획 |
| `INT-S9` | 승인된 history serving과 UI 통합 | `gpt-5.6-sol / xhigh` | `codex/int-s9-historical-product` | gate/분모/정렬/leakage/E2E 검증 후 Stage 10 생성 | 외부 gate 대기 |
| `INT-S10` | release 감사·ops 결과 통합과 최종 코드 완료 판정 | `gpt-5.6-sol / max` | `codex/int-s10-release-ready` | `V-ALL`, 차단 finding 0, secret 0, replay 미실행 확인, ledger·최종 branch commit/push; 실제 외부 검증은 별도 보고 | 계획 |

통합 Goal은 최종 메시지가 아니라 commit diff와 검증 로그를 직접 확인한다. 결함을 발견하면 그 결함만 소유하는 repair Goal을 만들고 통합 Goal에서 새 기능을 구현하지 않는다. 각 통합 Goal은 다음 Stage 작업들과 다음 통합 Goal을 만든 뒤 종료한다.

### Stage 0 통합 증거

- 공통 base: `6c2637e88f1c4c671b2c83a9b4caf0cbbb334f9f`; 검증 시 `main`과 `origin/main`도 이 SHA에서 일치했다.
- 원격 task SHA와 ancestry·소유 경로:
  - `S0-ADR`: `codex/s0-adr-boundaries` → `330a4115d884bba1fa124e78a622d4b77cac5567`, `docs/adr/**`만 변경.
  - `S0-CONTRACT`: `codex/s0-machine-contracts` → `b18fb462eaf763b583631ca8453947b20024426f`, 계약 task 소유 경로 55개만 변경.
  - `S0-REPLAY-UNIT`: `codex/s0-replay-minute-boundary` → `9d60fbfcea9b34252e8445de5b8f1ecb64eafd05`, 지정 구현·test 2개만 변경.
- 통합 중 새 `pyproject.toml`이 기존 replay import 의존성을 누락한 결함을 발견했다. 독립 repair Goal `REPAIR-S0-CONTRACT-DEPS`, thread `019ffe77-a834-7343-b154-3f63ab72e8fc`, branch `codex/repair-s0-contract-runtime-deps`, remote `4bd3c107031b0289f775454417739405d4a02f99`가 `pyproject.toml`·`uv.lock`만 수정해 `httpx==0.28.1`, `websockets==17.0.1`을 고정했다.
- 감사 가능한 merge 순서: ADR `50d9ec2` → 계약 `f31b747` → replay `eeabce7` → dependency repair `e90c72e`.
- 통합 검증: contract validator `HTTP 30 / WebSocket 9 / schema 79 / fixture 43 / 문서 JSON 예시 21`; `tests/contracts` 11개, `tests/test_market_replay.py` 20개, 전체 offline pytest 35개 통과; `git diff --check` 통과; 원격 SHA 4개와 merged ancestry·총 64개 task 경로 소유권 일치.
- 저장 capture, gap recovery, replay, final replay와 judge replay는 실행하지 않았다. secret·ignored/generated market·Infostock data도 읽거나 변경하지 않았다.
- 해결된 내부 blocker: 기존 gap-recovery 분 경계 실패와 통합 시 발견한 uv runtime dependency 누락. 외부 `B-*`는 해제 증거가 없어 아래 상태를 그대로 유지한다.
- Stage 1 dependency: 네 task는 원장상 서로 독립이며 병렬 생성 가능하다. 이 Stage 0 evidence를 포함해 push되는 `codex/int-s0-foundation` commit 하나를 모두의 exact base로 사용하고, 해당 SHA를 각 `/goal` prompt와 `INT-S0` 최종 보고에 기록한다. `INT-S1`에는 네 task의 실제 thread ID를 전달한다.
- 언어 연쇄 규칙: 이후 모든 Stage task·통합 Goal은 사용자 대상 답변, UI 문구, 오류·빈 상태·로딩·권한 안내와 제품/연구/운영 문서를 한국어 기본으로 작성하고 `ko-KR` 표시 규칙을 사용한다. 코드 식별자, API/JSON Schema field, URI, CLI와 library 이름은 영어를 유지한다.

### Stage 1 통합 증거

- 공통 구현 base: `b22c6ec70ab40c1041c8f8446e08e168e57f377c`. 최신 `origin/codex/int-s0-foundation`의 후속 `34dc19460ee69065d0730a1231dbbce404ac9d96`은 `docs/implementation_roadmap.md`만 수정한 Infostock 범위 보정임을 확인하고 merge `fd38587e19bd2f2b5dc2a635e0c72e0a22ecd6bc`로 먼저 보존했다. 네 task의 implementation ancestry·ownership 검증은 공통 base 그대로 유지했다.
- 원격 task SHA와 ancestry·소유 경로:
  - `S1-WEB`: thread `019ffe83-b454-7b20-b697-932460757368`, `codex/s1-web-fixture-shell` → `ded32882fab31fdcc153fde11ae4ca3e8d8f3a07`, `apps/web/**` 32개만 변경.
  - `S1-IDENTITY`: thread `019ffe83-b406-70a1-bcbc-0ed7a9bda13d`, `codex/s1-identity-library` → `7983da71de78f5a2012e2be0d24a7d6b9e0ab82f`, 지정 23개 경로만 변경.
  - `S1-INFOSTOCK`: thread `019ffe83-b42d-7963-a8ed-dd0fef4b6bb2`, `codex/s1-infostock-store` → `90f8db749a6ca46df59dec57cc12a898acd8bb9e`, 지정 27개 경로만 변경.
  - `S1-CALC`: thread `019ffe83-b452-73f1-84e1-96aadfdc3c71`, `codex/s1-domain-calculations` → `36b911f00c82c3612143e2437567ef7aa2b170e5`, 지정 21개 경로만 변경.
- 독립 검증: Web test 32개와 lint/typecheck/build, Identity 33개와 ruff/mypy, Infostock 25개와 PostgreSQL 16.15 실수집 통합 1개, Domain/Calculations 45개와 ruff/mypy, 각 `V-CONTRACT`·`V-DIFF`가 통과했다. Web의 병렬 실행 중 접근성 test timeout 1건은 isolated·sequential 재실행과 전체 32개 재실행에서 통과해 contention으로 판정했다.
- Infostock 실제 corpus: Theme DB `COMPLETE`; theme 280/280, history 39,696, related stock 6,629, leader 65,526, historical membership 652,241, source snapshot 284, unique blob 283, raw UTF-8 165,275,696 bytes. history source duplicate 4건·duplicate group head 4건, leader code missing 90건, historical membership code missing 7,498건, legacy `memberStocks` field missing 274건, source hash unverified 2건을 삭제·숨김·추정하지 않고 품질 상태로 보존했다. 동일 입력 재실행·revision·rollback·참조 무결성이 통과했다.
- Daily 실제 corpus: `DailyFeaturedTheme`는 DB 범위에서 제외하지 않는다. 확보분은 page 1의 목록 5건, 본문 1건, 관계 232건, logical raw snapshot 2건, 날짜 2026-08-07~2026-08-13, `nextPage=2`, `coverageComplete=false`다. source ID·canonical URL 5건, 본문 4건이 누락됐고 parse partial 1건이며 page 2 이후 전체 기간·게시물 총량은 인증 수집 없이 알 수 없다. FULL run은 Theme DB `COMPLETE`와 Daily `BLOCKED`를 독립 보고하고 전체 상태를 `PARTIAL`로 유지한다.
- repair 기록: 초기 same-directory finalize 시도 thread `019ffeca-a1f7-7151-97d0-6cd0018a36b9`와 `019ffece-0955-7782-9ff4-e8b8c9496bf6`은 구현·commit·push 없는 no-op으로 종료했다. 정식 `REPAIR-S1-INFOSTOCK-DAILY-BACKFILL` thread `019fff44-2ee3-7162-8cc0-d7ccc6466b38`, branch `codex/repair-s1-infostock-daily-backfill`, base `90f8db749a6ca46df59dec57cc12a898acd8bb9e`는 owned 결함 0, 환경 입력·session·rights evidence 부재를 재확인하고 no-op genuine blocker로 종료했으며 remote repair branch는 없다. setup 지연으로 생긴 중복 thread `019fff4a-ca78-7093-9dae-58110cd07320`은 변경·commit·push 0건으로 중단·archive했고 결과를 수용하지 않았다.
- 감사 가능한 merge 순서: Web `c31eb2acb812926be84214a0525296cf941d6aa9` → Identity `8eeadd7bd731ca9c8355b783c9e77af0650f724a` → Infostock `7833c38d0dbdc8026744e07e1b698ede376f5d9c` → Calculations `fbfebbbbbe1cb489a8a0de467936702454846c16`. 각 merge의 second parent가 위 원격 SHA와 일치하고 conflict·소유권 위반은 없었다.
- 통합 검증: contract validator `HTTP 30 / WebSocket 9 / schema 79 / fixture 43 / 문서 JSON 예시 21`, `tests/contracts` 11개, Identity 33개, Infostock scoped 25개, Domain/Calculations 45개, Web 32개와 lint/typecheck/build가 통과했다. 실제 corpus와 빈 PostgreSQL 16.15 DSN을 붙인 전체 `uv run pytest -q`는 `139 passed`·skip 0으로 통과했고 DB run은 Theme DB `COMPLETE`, Daily `BLOCKED`, 전체 `PARTIAL`을 재확인했다. `git diff --check`, 네 remote SHA·ancestry·merged ownership 감사도 통과했다.
- 저장 capture, gap recovery, replay, final replay, judge replay와 신규 login/live browser 수집은 실행하지 않았다. 검증용 disposable PostgreSQL 컨테이너만 생성·제거했고 원본 수집본은 읽기 전용으로 사용했다.
- Stage 2 dependency: `codex/int-s1-app-base`의 이 evidence를 포함해 push되는 commit을 `S2-REFDATA`, `S2-MARKET`, `S2-REALTIME`, `INT-S2`의 exact base로 사용한다. 세 Stage 2 구현은 서로 독립이며 코드/fixture 범위에서 병렬 가능하다. `B-INFOSTOCK-AUTH`·`B-DATA-RIGHTS`와 Daily 실제 전체 backfill 미완은 그대로 연쇄 전달하며 production/full-DB 완료로 위장하지 않는다.

### 외부 의존성·blocker

| ID | 현재 증거 | 필요한 해제 조건 | blocker와 무관하게 가능한 범위 |
|---|---|---|---|
| `B-REFDATA-KEYS` | `KRX_API_KEY`, `OPENDART_API_KEY`가 비어 있다. | 무료 공식 key 제공과 실제 field/Coverage 검증 | adapter, 설정 검증, fixture·실패 상태·계약 test |
| `B-INFOSTOCK-AUTH` | 암호화된 browser state와 `INFOSTOCK_SESSION_STATE_PATH`가 없고 ID/비밀번호 자동 입력은 금지다. 실제 Daily는 page 1·목록 5·본문 1·nextPage 2에서 중단됐으며 entrypoint는 `AUTH_REQUIRED`, exit 2, `liveRequestAttempted=false`로 fail-closed다. | 운영자의 SSH tunnel 수동 로그인, 승인된 session state와 합법적 전체 pagination·본문 backfill 확인 | Theme DB import, Daily schema/checkpoint/parser, encryption·AUTH_REQUIRED fixture test |
| `B-DATA-RIGHTS` | 로컬 manifest에 교육 승인 표시는 있으나 `INFOSTOCK_RIGHTS_EVIDENCE_PATH`와 production 저장·가공·표시 권리 증거가 없다. Daily repair는 `RIGHTS_UNVERIFIED`로 종료했다. | 공급원별 허용 범위·보존·재배포 승인 기록 | fixture와 계약 기반 구현, 기존 허용 수집본 read-only 감사; production 수집·공개는 금지 |
| `B-MARKET-FIXTURE` | 본·보조 capture가 중단됐고 gap recovery가 없다. | 구현 완료 후 사용자가 수집·재생 명령과 결과를 직접 판정 | synthetic fixture, adapter/unit/integration test; 저장 replay 실행 금지 |
| `B-HISTORY-GATE` | v1 코드·가격 corpus·registry가 없고 기존 2026 봉인 구간은 재사용 금지다. | 2인 이상 blind 평가, 불일치 조정, 2026-09 이후 새 미사용 봉인 구간 1회 평가, artifact 승인 | corpus/ontology/engine/eval code와 gate-off product path |
| `B-OPERATOR` | `OPERATOR_BOOTSTRAP_GOOGLE_EMAILS`가 비어 있다. | verified 운영자 계정과 실제 role bootstrap 검증 | role/403/audit fixture test |
| `B-DESIGN-REVIEW` | 디자이너 asset/font 권리와 제품·접근성 수동 검수 증거가 없다. | 권리 확인과 사람 검수 | 계약 fixture 기반 UI와 자동 접근성 test |
| `B-DEPLOY` | DNS·SSH 일부만 검증됐고 application/TLS/backup/rollback은 미검증이다. | 사용자의 배포 승인, 계정 권한, staging·shadow·production 수동 gate | Compose/Caddy/Vercel 설정, local/CI smoke와 runbook; cloud mutation 금지 |

### 최종 완료 조건

`INT-S10`은 핵심 MVP 코드, 인증·권한 경계, 계약/fixture/프론트/백엔드 일치, 결측·지연·Coverage, 근거 없는 원인 금지, relevant build/lint/typecheck/unit/integration/E2E/smoke 통과, secret 미노출, 모든 소유 변경 commit, 최종 branch push를 직접 검증해야 한다. Record & Replay는 실행하지 않았다고 명시한다. `B-*`가 남아 있으면 코드 완료와 외부 검증 대기를 분리하며, 실패·미검증 항목을 완료로 표시하지 않는다.

## 1. 목적

이 문서는 디자이너 프로토타입을 DAYJAVIEW 제품 화면으로 전환하고, 백엔드·실시간 데이터·뉴스 근거·과거 유사사례까지 연결해 최종 출시하는 전체 작업 순서를 정의한다.

핵심 원칙:

1. 백엔드 전체 완료를 기다리지 않는다.
2. 시스템 아키텍처 초안을 먼저 고정한다.
3. UI 적용 계획과 API 계약을 아키텍처 기준으로 병렬 작성한다.
4. 프론트엔드는 실제 API와 같은 fixture로 개발한다.
5. 프론트엔드와 백엔드는 계약을 기준으로 병렬 개발한다.
6. 기능별 수직 통합을 반복한다.
7. v1 유사사례 엔진은 기준선으로만 사용한다.
8. 온톨로지 재검증을 통과하기 전 유사사례를 일반 사용자에게 노출하지 않는다.
9. 과거 관측값을 현재 사건의 예측·추천으로 표현하지 않는다.
10. DNS·SSH 같은 가역적 provisioning은 구현 전에 끝낼 수 있지만, 실행 코드가 없는 빈 애플리케이션은 배포하지 않는다.

---

## 2. 최종 완료 상태

다음 사용자 경험이 실제 데이터로 동작하면 핵심 제품 구현 완료다.

```text
실제 장중 체결 수신
테마 활성화와 순위 계산
오늘 화면에서 강한 테마 발견
테마 상세에서 이유·주도주·확산 확인
인사이트에서 실시간 테마 맵 확인
장후 인포스탁 확정과 변경 이력 확인
온톨로지 재검증을 통과한 과거 유사사례 확인
당시 주도주의 실제 결과 확인
관심 테마·종목·이벤트를 계정에 저장하고 다시 확인
```

최종 완료 조건:

- [ ] 실제 체결로 오늘 화면과 인사이트가 갱신된다.
- [ ] 테마 수익률·확산·Coverage·데이터 상태가 정확하다.
- [ ] 상승 이유에는 근거 상태·매체·발행 시각·원문이 연결된다.
- [ ] 근거가 없을 때 원인을 생성하지 않는다.
- [ ] 장중 Event ID와 장후 확정 결과가 연결된다.
- [ ] 관심 테마·종목·이벤트가 Google 계정에 저장·동기화되고 삭제된다.
- [ ] 온톨로지 재검증을 통과한 검색기만 사용자 화면에 사용된다.
- [ ] 유사사례의 기간별 분모·중앙값·누락 사유가 정확하다.
- [ ] 모바일·데스크톱·접근성·성능·보안 검증을 통과한다.
- [ ] 스테이징 shadow 운영과 팀 검수를 통과한다.
- [ ] 기본 작업 상태·서버 로그와 장애 대응 절차가 준비된다.

---

## 3. 전체 단계

| 단계 | 결과 | 프론트·백엔드 관계 | 상태 |
|---|---|---|---|
| 0 | 시스템 아키텍처·UI 적용 계획·API 계약 | 공동 | 진행 중 |
| 1 | fixture 기반 프론트엔드 셸 | 프론트 중심 | 미착수 |
| 2 | 데이터·실시간 백엔드 기반 | 백엔드 중심, 1단계와 병렬 | 미착수 |
| 3 | 오늘·테마 상세 첫 수직 통합 | 공동 | 미착수 |
| 4 | 뉴스 근거 연결 | 공동 | 미착수 |
| 5 | 인사이트 트리맵 연결 | 공동 | 미착수 |
| 6 | 장후 확정과 품질 루프 | 백엔드·운영 중심 | 미착수 |
| 7 | 온톨로지 구축과 검색 재검증 | 연구 트랙, 일부 병렬 | 미착수 |
| 8 | 유사사례 화면 활성화 | 7단계 통과 후 | 미착수 |
| 9 | 통합 검증과 출시 준비 | 공동 | 미착수 |
| 10 | shadow 운영과 단계적 출시 | 공동 | 미착수 |

한 단계는 필수 체크리스트와 완료 게이트가 모두 충족돼야 `완료`로 변경한다.

---

## 4. 병렬 작업 구조

### 프론트엔드 트랙

- Google 로그인 화면·auth guard·안전한 `returnTo`·로그아웃 시 client cache 폐기
- 관심 화면, 테마·종목·이벤트 저장·해제와 계정 삭제 UI
- 일반 사용자 shell과 분리된 `OperatorScreen`, 작업·검수·audit 상태 fixture
- 디자인 토큰과 공통 컴포넌트 추출
- 앱 셸과 라우팅
- fixture 기반 오늘·인사이트·테마 상세
- LIVE·DELAYED·CLOSED·Coverage·빈 상태
- API 클라이언트와 WebSocket 연결
- 접근성·반응형·시각 회귀 테스트

### 백엔드 트랙

- 인포스탁 원천 DB와 이력
- 종목·테마 membership
- 조정 가격·거래일·기준정보
- 키움 WebSocket과 구독 슬롯
- 테마 증분 계산과 Coverage
- REST·WebSocket API
- 뉴스 수집·근거 구조화
- 장후 확정과 분류 변경 이력

### 인프라·배포 트랙

- 기존 OCI A1 Flex VM의 이전 프로젝트 잔존 항목 점검·정리
- Ubuntu·SSH·firewall 보안 baseline
- Docker Compose와 역할별 container 배치
- `linux/arm64` image와 Playwright·Chromium 호환성 PoC
- staging·production network·volume·credential 분리
- Vercel `dayjaview.vercel.app` 배포와 OCI `api.dayjaview.duckdns.org` DNS·TLS
- Vercel `/api/*` external rewrite·Google OAuth callback·host-only cookie 검증
- OCI direct WSS의 30초·1회용 connection ticket 인증
- PostgreSQL·인포스탁 session state 외부 backup·restore
- 재부팅 복구·rollback runbook

### 연구 트랙

- 사건 온톨로지
- point-in-time 변환 규칙
- M-TXT v1 기준선 재현
- 온톨로지·혼합 검색 비교
- 2인 이상 블라인드 평가
- 새 미사용 봉인 구간 평가

### 디자인·제품 검수 트랙

- 프로토타입 시각 언어 유지 여부
- 화면 정보 우선순위
- 문구와 오인 위험
- 빈 상태·지연 상태·표본 부족 상태
- 모바일 실제 사용성

병렬 작업의 시스템 경계는 [system_architecture.md](./system_architecture.md), 통신 연결점은 **버전된 API 계약과 fixture**다.

---

## 5. 단계 0 — 아키텍처·계획·계약

### 5.1 `system_architecture.md`

전체 시스템 경계와 상태 소유권을 정의하는 기준 문서다. 초안은 작성됐으며 팀 검수와 ADR 확정이 남았다.

핵심 결정:

- `ADR-001` 확정: 모든 기능을 유지하는 모듈형 모놀리스 코드베이스와 역할별 독립 프로세스·컨테이너
- 독립 확장·반복 장애·별도 OS·보안 또는 팀 소유권 근거가 생긴 모듈만 마이크로서비스로 분리
- `ADR-009` 확정: 기존 OCI A1 Flex 4 OCPU·24GB, Ubuntu 22.04 ARM64 VM을 초기 운영 플랫폼으로 사용
- 초기 물리 배치는 단일 VM이지만 API·실시간·수집·batch의 process·container 경계 유지
- PostgreSQL 영속 기준 저장소
- Redis 실시간 snapshot·process 간 전달·WebSocket fan-out
- 객체 저장소 원본 보존
- REST 초기·상세 조회, WebSocket 실시간 snapshot
- Event 모듈 단일 상태 writer
- 검색·outcome·v2 예측 경계 분리
- 연구 artifact 승인 후 운영 반영

완료 체크:

- [x] 시스템 컨텍스트와 신뢰 경계가 정의됐다.
- [x] 논리 모듈과 책임이 정의됐다.
- [x] `ADR-001` 애플리케이션 토폴로지가 승인됐다.
- [x] PostgreSQL·Redis·memory·객체 저장소 역할이 정의됐다.
- [x] REST·WebSocket 역할이 정의됐다.
- [x] Event ID 수명주기와 상태 writer가 정의됐다.
- [x] 장전·장중·뉴스·장후 흐름이 정의됐다.
- [x] 연구·검색·예측 경계가 정의됐다.
- [x] 초기 배포와 Windows Market Gateway 경계가 정의됐다.
- [x] 초기 OCI 운영 플랫폼과 단일 호스트 제약이 정의됐다.
- [x] 운영자 SSH public key 접속이 검증됐다.
- [x] 장애·복구·보안·최소 운영 기록 기준이 정의됐다.
- [ ] 필수 ADR 초안이 작성됐다.
- [ ] 프론트·백엔드·데이터·운영 담당이 검토했다.
- [ ] PoC 가설과 미결정 항목의 소유자가 지정됐다.
- [ ] 문서 상태가 `승인`으로 변경됐다.

완료 증거:

- 승인된 [system_architecture.md](./system_architecture.md)
- `docs/adr/`의 필수 ADR
- 아키텍처 검수 기록

### 5.2 `ui_prototype_adaptation_plan.md`

디자이너 산출물을 제품 화면으로 바꾸는 기준 문서다. 초안은 작성됐으며 공동 검수가 남았다.

필수 내용:

- 디자인 원본 저장소와 기준 커밋
- 유지할 자산: 로고, 색상, 타이포, 카드, 라운드, 여백, 모션
- 교체할 구조: 홈 순위 휠, 단일 `App.jsx`, 하드코딩 통계
- 기존 6개 화면과 새 화면의 매핑
- `오늘 / 인사이트 / 테마 / 관심` 앱 셸
- 화면·기능·공통 컴포넌트 구조
- 모바일·태블릿·데스크톱 규칙
- 아이폰 목업 프레임의 데모 모드 격리
- 접근성 기준
- LIVE·DELAYED·CLOSED·빈 상태·오류 상태
- 온톨로지 재검증 전 유사사례 잠금 규칙
- 화면별 완료 조건

완료 체크:

- [x] 기준 저장소와 커밋이 기록됐다.
- [x] 디자이너 원본과 분리된 팀 소유 비공개 production 저장소 정책이 승인됐다.
- [x] 유지·수정·폐기 항목이 구분됐다.
- [x] 기존 화면과 목표 화면 매핑이 있다.
- [x] 목표 컴포넌트 구조가 정의됐다.
- [x] 반응형과 데모 프레임 정책이 정의됐다.
- [x] 모든 데이터 상태와 빈 상태가 정의됐다.
- [ ] 디자이너·프론트 담당·제품 담당이 검토했다.
- [x] 결정되지 않은 항목이 명시됐다.

완료 증거:

- 승인된 `docs/ui_prototype_adaptation_plan.md`
- 화면 매핑 표
- 주요 화면 와이어 또는 업데이트된 디자인 링크

### 5.3 `api_contract.md`

프론트엔드와 백엔드의 공통 의미 계약이다. 초안은 작성됐으며 기계 계약 생성과 공동 검수가 남았다.

필수 내용:

- `themeId`, `eventId`, `matchedEventId`
- KST·UTC·KRX 거래일 기준
- `asOf`, `publishedAt`, `receivedAt` 의미
- `LIVE`, `DELAYED`, `CLOSED` 등 데이터 상태
- Event 생명주기·장후 정합·상승 이유 근거 상태의 분리
- `UNMATCHED`와 운영자 검수 상태의 분리
- Catalyst 상태
- Coverage 구조와 `qualityFlags`
- `null`, `0`, 빈 배열, 누락 필드의 차이
- 수익률 소수 원값과 화면 백분율 변환
- 페이지네이션·정렬·필터
- 공통 오류 형식
- 인증·권한 범위
- API·스키마·계산 버전
- 하위 호환성과 폐기 정책
- WebSocket sequence·재연결·전체 snapshot 규칙

초기 REST 후보:

```text
GET /v1/market/session
GET /v1/themes/rankings
GET /v1/insights/treemap
GET /v1/themes/{themeId}/events/{eventId}
GET /v1/events/{eventId}/evidence
```

초기 WebSocket event 후보:

```text
theme_rank_snapshot
theme_treemap_snapshot
event_state_changed
```

경로와 이름은 계약 검토에서 확정한다. 이 목록은 초안이다.

완료 체크:

- [x] 모든 ID의 수명주기와 관계가 정의됐다.
- [x] 시간·거래일 기준이 정의됐다.
- [x] 상태 enum과 전이 규칙이 정의됐다.
- [x] `null`과 `0` 의미가 정의됐다.
- [x] REST 요청·응답 예제가 있다.
- [x] WebSocket snapshot과 재연결 규칙이 있다.
- [x] 오류·페이지네이션·버전 정책이 있다.
- [ ] 프론트·백엔드 담당자가 같은 예제를 검토했다.
- [x] PRD와 화면 명세의 의미와 충돌하지 않는다.

완료 증거:

- 승인된 `docs/api_contract.md`
- 예시 요청·응답
- 변경 이력

### 5.4 기계 판독 계약

Markdown 계약과 함께 다음 파일을 만든다.

```text
contracts/
├─ openapi.yaml
├─ asyncapi.yaml
├─ schemas/
└─ fixtures/
```

필수 fixture:

- 정상 LIVE
- DELAYED
- CLOSED
- Coverage 충분·부분·미달
- 상승 이유 확인 중
- 뉴스 한 건 추정
- 복수 뉴스 확인
- 장후 인포스탁 확정
- 장후 인포스탁 미매칭·검수 대기
- 활성 테마 없음
- 계산 불가와 `null`
- 재연결 후 전체 snapshot

완료 체크:

- [ ] `openapi.yaml`이 자동 검증된다.
- [ ] `asyncapi.yaml`이 자동 검증된다.
- [ ] JSON Schema와 문서 예제가 일치한다.
- [ ] 모든 핵심 상태 fixture가 있다.
- [ ] 프론트 mock server가 fixture를 읽는다.
- [ ] 백엔드 contract test가 같은 schema를 사용한다.
- [ ] CI에서 계약 위반이 실패 처리된다.

### 단계 0 완료 게이트

- [ ] 시스템 아키텍처 승인
- [ ] 필수 ADR 승인 또는 구현 가능한 `accepted/proposed` 상태 확정
- [ ] UI 적용 계획 승인
- [ ] API 계약 승인
- [ ] OpenAPI·AsyncAPI 검증 통과
- [ ] fixture 기반 mock 응답 확인
- [ ] 프론트·백엔드 병렬 작업 범위와 담당자 확정

---

## 6. 단계 1 — 프론트엔드 기반 재구축

디자이너 프로토타입의 시각 언어를 유지하고 내부 구조를 제품용으로 교체한다.

### 작업

- [x] 프로토타입은 참고 원본으로 유지하고 별도 production 코드 구조로 이식
- 작업 브랜치와 코드 소유권 설정
- Vite와 취약 의존성 업데이트
- 단일 `App.jsx` 분리
- 실제 라우터 도입
- 색상·타이포·간격·라운드·그림자·모션 토큰 추출
- 공통 카드·배지·상태·바텀시트·skeleton 컴포넌트 생성
- `TodayScreen`, `InsightScreen`, `ThemeScreen`, `SavedScreen`과 별도 `OperatorScreen` 생성
- 유사사례 화면은 feature flag로 잠금
- 아이폰 목업은 별도 demo wrapper로 이동
- API client와 mock adapter 분리
- fixture 기반 상태 전환 구현

권장 구조:

```text
src/
├─ app/
├─ screens/
├─ components/
├─ features/
├─ api/
├─ contracts/
├─ design/
└─ mocks/
```

### 완료 체크

- [ ] `App.jsx`에 모든 화면·데이터·스타일이 집중되지 않는다.
- [ ] URL로 화면과 식별자를 복원할 수 있다.
- [ ] 디자인 토큰이 한곳에서 관리된다.
- [ ] 프로토타입의 브랜드·시각 언어가 유지된다.
- [ ] 홈 순위 휠이 제품용 테마 카드 목록으로 교체됐다.
- [ ] 하단 앱 셸이 화면 명세와 일치한다.
- [ ] 관심 화면의 유형 필터·빈 상태·저장 실패·사용 불가 상태가 fixture로 재현된다.
- [ ] 일반 사용자 navigation·검색·sitemap에 운영자 route가 없다.
- [ ] 모든 핵심 fixture 상태가 화면에서 재현된다.
- [ ] 유사사례는 feature flag로 잠겨 있다.
- [ ] 모바일 bare mode와 데스크톱 제품 레이아웃이 분리됐다.
- [ ] 데모 기기 프레임이 운영 앱 레이아웃에 영향을 주지 않는다.
- [ ] 키보드·스크린리더 기본 탐색이 가능하다.
- [ ] build·lint·unit test가 통과한다.

### 완료 증거

- Storybook 또는 화면 카탈로그
- fixture별 화면 캡처
- 프론트 테스트 결과
- 디자이너 검수 기록

---

## 7. 단계 2 — 백엔드 기반 구현

단계 1과 병렬 진행한다.

### 배포 기반

- [x] 기존 OCI A1 Flex VM 재사용 결정과 SSH public key 접속 검증
- 이전 프로젝트의 systemd service·Docker container·image·volume·cron·작업 파일 잔존 여부 점검과 정리
- Ubuntu 보안 update, SSH public key only, 운영자 IP 제한, host firewall 적용
- Docker Engine·Compose와 reverse proxy 설치
- staging·production Compose project, network, volume, DB credential 분리
- API·worker·PostgreSQL·Redis의 `linux/arm64` image build·실행
- Playwright·Chromium 로그인·storage state 저장·복원 ARM64 PoC
- production secret을 Git 밖 root 소유 `0600` env 파일 또는 OCI Vault로 주입
- 영구 volume과 외부 암호화 backup 구성
- Vercel `dayjaview.vercel.app` production 배포와 `/api/*` OCI external rewrite
- [x] DuckDNS `dayjaview.duckdns.org`와 `api.dayjaview.duckdns.org`를 OCI VM 공인 IPv4에 연결하고 A record 해석 검증
- DuckDNS `api.dayjaview.duckdns.org` Caddy TLS·Google OAuth callback 연결
- 30초·1회용 WebSocket ticket 발급·원자적 소비·인증 전 차단
- 재부팅 자동 기동, health check, rollback, backup restore 시험

### 데이터 기반

- Google subject 기반 최소 사용자·session·saved theme·stock·event schema와 migration
- 사용자별 unique constraint, 소유권 검사와 계정 삭제
- 인포스탁 데이터 사용·갱신 권리 확인
- 인포스탁 네이버 연동 로그인의 운영자 전용 `bootstrap`·`refresh` 명령 구현
- browser storage state 암호화·원자적 저장과 영구 volume 복원
- scheduler 기반 백그라운드 수집과 `AUTH_REQUIRED` 전환
- 원본 snapshot·해시·파서 버전 저장
- 테마·종목·membership·이력 구축
- 현재 관련주와 과거 당시 주도주 분리
- KRX 거래일과 조정 가격 구축
- KRX Open API·OpenDART 기반 유동주식비율 산출식·필드 의미·Coverage 검증
- 발행주식수·자기주식·최대주주·특수관계인·공식 제한 지분의 기준일 정렬과 중복 차감 방지
- 기준정보 원본·공시 접수번호·적용일·산출 버전·제외 사유 저장
- 기업행위·거래정지·가격 결측 처리

### 실시간 기반

- 키움 인증과 WebSocket 장시간 유지 PoC
- 조건검색 편입·이탈 수신
- 0B 등록·해제·재연결
- 최대 200종목 구독 우선순위 관리
- 종목 실시간 상태 저장
- 종목-테마 역색인
- dirty 테마 증분 계산
- 상한형 유동시가총액 가중 테마 수익률
- 중앙값·상승 확산·거래 관심
- Coverage와 `qualityFlags`
- Event ID 생성과 상태 전이

### API 기반

- Google OAuth callback, server session, auth middleware, logout 구현
- 로그인 화면·OAuth·최소 health check 외 모든 제품 REST·WebSocket 인증 강제
- 관심 목록·저장·해제·계정 삭제 REST 구현
- Google `OPERATOR` bootstrap, 역할 middleware와 `/v1/operator` API 구현
- OpenAPI 기반 REST 구현
- AsyncAPI 기반 WebSocket 구현
- sequence·전체 snapshot·재연결
- API·계산·membership·baseline 버전 반환
- contract test
- 기본 작업 상태와 서버 로그

### 완료 체크

- [ ] 비로그인 REST·WebSocket 요청이 제품 데이터 없이 거부된다.
- [ ] Google 로그인 후 안전한 원래 내부 route로 복귀한다.
- [ ] session 만료·로그아웃 시 client cache와 실시간 snapshot이 제거된다.
- [ ] 같은 대상을 반복 저장·해제해도 중복·오류가 없다.
- [ ] 다른 사용자의 저장 항목을 ID 추측으로 조회·수정할 수 없다.
- [ ] 계정 삭제 후 session·프로필·저장 항목이 제거된다.
- [ ] 일반 사용자 session의 운영자 route·API 접근이 `403`으로 차단된다.
- [ ] 운영자 API 응답·로그에 secret·cookie·token·credential 원문이 없다.
- [ ] 이전 프로젝트 잔존 service·container·cron·파일이 없음을 확인했다.
- [ ] OCI host·SSH·firewall 보안 baseline을 적용했다.
- [ ] 모든 필수 image와 browser worker가 ARM64에서 동작한다.
- [ ] staging과 production의 network·volume·credential이 분리됐다.
- [x] DuckDNS app·API hostname이 OCI VM 공인 IPv4로 해석된다.
- [ ] Vercel app URL·DuckDNS API TLS·external rewrite·Google OAuth callback이 동작한다.
- [ ] VM 재부팅 뒤 production stack과 영구 state가 복구된다.
- [ ] PostgreSQL·인포스탁 session state backup을 새 환경에 복원했다.
- [ ] 최초 수동 로그인으로 암호화된 인포스탁 state가 생성된다.
- [ ] worker 재시작·재배포 뒤 영구 state로 로그인 세션이 복원된다.
- [ ] 로그인 만료 시 자동 로그인을 반복하지 않고 `AUTH_REQUIRED`가 된다.
- [ ] 운영자 refresh 후 다음 예약 실행에서 정상 수집이 복구된다.
- [ ] 인증 실패 페이지가 마지막 정상 state를 덮어쓰지 않는다.
- [ ] 인포스탁 원본과 정규화 데이터가 재현 가능하다.
- [ ] 현재 관련주와 과거 주도주가 분리됐다.
- [ ] 조정 가격·거래일·기준정보 버전이 있다.
- [ ] 키움 연결과 재연결이 동작한다.
- [ ] 200종목 초과 시 우선순위 해제가 동작한다.
- [ ] 영향받는 테마만 증분 계산된다.
- [ ] Coverage 미달이 0으로 계산되지 않는다.
- [ ] Event ID와 상태 이력이 저장된다.
- [ ] REST 응답이 OpenAPI를 통과한다.
- [ ] WebSocket payload가 AsyncAPI를 통과한다.
- [ ] 같은 입력과 버전에서 같은 결과가 나온다.
- [ ] 핵심 계산 단위 테스트가 통과한다.

### 단계 2 완료 게이트

- [ ] OCI staging에서 배포·재부팅·rollback·restore smoke test가 통과한다.
- [ ] 실제 장중 체결로 최소 한 테마가 활성화된다.
- [ ] 메모리 계산 결과를 API와 WebSocket으로 받을 수 있다.
- [ ] 장애·지연·Coverage 상태를 재현할 수 있다.

---

## 8. 단계 3 — 오늘·테마 상세·관심 첫 수직 통합

### 첫 배포 게이트

코드가 없는 상태에서는 DNS 예약과 SSH 접속 검증까지만 수행한다. 프론트 앱 셸, 제품 데이터를 포함하지 않는 `/api/health`, PostgreSQL·Redis 연결, Google OAuth 기본 흐름과 첫 fixture/API 화면이 함께 실행되는 시점에 첫 OCI staging·Vercel preview 배포를 수행한다. `production` 공개 배포는 아래 단계 3 완료 게이트와 출시 단계 검증을 통과한 뒤 수행한다.

첫 통합 범위:

```text
실제 체결
테마 계산
REST·WebSocket
오늘 화면
테마 상세
관심 화면
```

### 작업

- fixture adapter를 실제 API adapter로 교체
- `themeId`, `eventId` 라우팅 연결
- 홈 초기 REST snapshot 로드
- WebSocket 변경 snapshot 연결
- 오래된 sequence 무시
- 재연결 후 전체 snapshot 복구
- 테마 카드의 순위·수익률·확산·주도주·상태 표시
- 테마 상세의 현재 반응·Coverage·주도주 표시
- 테마·종목·이벤트 저장·해제와 관심 목록의 현재 상태 연결
- session 만료·계정 삭제 뒤 관심 cache 제거
- LIVE·DELAYED·CLOSED 상태 연결
- mock과 실제 응답의 시각 결과 비교

### 완료 체크

- [ ] 실제 체결이 화면 숫자를 바꾼다.
- [ ] 0B 수신부터 홈 반영까지 P95 3초 이내다.
- [ ] 홈과 상세의 같은 필드 값이 일치한다.
- [ ] 한 기기에서 저장한 항목이 다른 로그인 session에서도 동일하게 보인다.
- [ ] 관심 목록에서 원래 상세 화면으로 식별자를 유지해 이동한다.
- [ ] Coverage 부족이 `데이터 갱신 중`으로 표시된다.
- [ ] 연결 중단 시 마지막 정상 화면과 시각이 유지된다.
- [ ] 재연결 후 전체 상태가 복구된다.
- [ ] 장 마감 시 최종 snapshot이 고정된다.
- [ ] fixture와 실제 API가 같은 컴포넌트를 사용한다.
- [ ] 통합 테스트와 저장 체결 replay가 통과한다.

### 단계 3 완료 게이트

- [ ] 오늘·테마 상세·관심 핵심 여정이 실제 데이터로 동작
- [ ] 성능 목표 충족
- [ ] 프론트·백엔드 contract test 통과
- [ ] 디자이너·제품 담당 실제 장중 화면 검수

---

## 9. 단계 4 — 뉴스 근거 연결

### 작업

- 허용된 RSS와 NAVER API HUB 공급원 확정
- 장중 특징주 뉴스 polling
- URL·제목·매체·발행 시각 중복 제거
- 뉴스 저장소와 cursor
- 종목·기업·정책·기술 Entity 추출
- 테마 상태 변화 시 로컬 뉴스 조회
- 새 기사 저장 시 활성 테마 역매칭
- 로컬 근거가 없을 때만 보완 검색
- 관련성 기준 통과 기사만 LLM 구조화·요약
- 모델·프롬프트·입력 기사 버전 저장
- 매체·발행 시각·원문 링크 제공

### 화면 상태

- `상승 이유 확인 중`
- `뉴스 기반 추정`
- `복수 뉴스 확인`
- `확인된 신규 소재 없음`
- `기존 소재 재부각`
- `인포스탁 기준 확정`

### 완료 체크

- [ ] 뉴스가 사용자 검색 없이 자동 수집된다.
- [ ] 같은 기사가 중복 저장되지 않는다.
- [ ] 테마와 기사 양방향 매칭이 동작한다.
- [ ] 근거가 없으면 LLM을 호출하지 않는다.
- [ ] LLM이 외부 검색을 수행하지 않는다.
- [ ] 요약마다 기사 ID·매체·발행 시각·원문이 있다.
- [ ] 수집 장애와 실제 기사 부재가 내부적으로 구분된다.
- [ ] 미래 기사가 이전 시점 근거로 소급되지 않는다.
- [ ] 뉴스-테마 매칭 검수 지표가 수집된다.
- [ ] 화면의 근거 상태가 API 상태와 일치한다.

### 단계 4 완료 게이트

- [ ] 근거 없는 원인 생성 0건
- [ ] 팀 표본 검수 통과
- [ ] 이용약관·재배포 정책 확인

---

## 10. 단계 5 — 인사이트 트리맵

기준 문서: [realtime_theme_treemap_implementation_plan.md](./realtime_theme_treemap_implementation_plan.md)

### 작업

- 기존 WebSocket에 `theme_treemap` topic 추가
- Core Coverage를 통과한 상승 테마 상위 12개 선택
- `weightedReturn`으로 면적·색상 계산
- 500ms 값 반영과 최대 1초 레이아웃 갱신
- keyed DOM 갱신
- stale·재연결·CLOSED 처리
- reduced motion 처리
- 타일에서 동일 Event의 테마 상세 이동

### 완료 체크

- [ ] 상승률 단일 지표만 사용한다.
- [ ] 지표 토글이 없다.
- [ ] 실제 체결이 없으면 타일이 움직이지 않는다.
- [ ] Coverage 미달이 0% 타일로 표시되지 않는다.
- [ ] 값·면적·색상이 같은 `weightedReturn`을 사용한다.
- [ ] 최대 12개만 표시된다.
- [ ] 오래된 sequence가 무시된다.
- [ ] 장 마감 후 애니메이션이 중지된다.
- [ ] 클릭·Enter·Space가 같은 상세 이동을 수행한다.
- [ ] 30분 성능 테스트가 통과한다.

### 단계 5 완료 게이트

- [ ] 오늘 화면과 트리맵의 동일 테마 수익률 일치
- [ ] 정상 연결에서 1초 이내 숫자 반영
- [ ] 메모리·DOM 노드 누수 없음

---

## 11. 단계 6 — 장후 확정과 품질 루프

### 작업

- 장 마감 시 활성 Event를 CLOSED로 전환
- 장중 최종 지표 저장
- 인포스탁 장후 업데이트 수집
- 인증된 background worker의 예약 실행과 `AUTH_REQUIRED` 복구 검증
- 장중 임시 분류와 확정 분류 매칭
- 같은 Event ID 유지
- 이름·themeId 변경 이력 저장
- 매칭 실패 Event를 UNMATCHED로 분류
- 운영자 검수 대기열
- 수집 상태·작업 목록·retry·resume·배포 version 운영자 화면
- 인포스탁 `AUTH_REQUIRED`와 OCI SSH tunnel 재인증 runbook 연결
- 분류 수정·병합·제외의 reason·revision·audit 저장
- 장중 탐지와 장후 결과 비교 지표

### 완료 체크

- [ ] 장중 기록이 장후 동기화로 삭제되지 않는다.
- [ ] 장중·장후가 같은 Event ID를 사용한다.
- [ ] 기본 화면은 장후 확정명을 사용한다.
- [ ] 이전 이름과 분류가 이력에 남는다.
- [ ] 인포스탁 지연 시 `장후 확정 대기`가 표시된다.
- [ ] 인포스탁 세션 만료와 운영자 수동 재인증 흐름을 staging에서 검증했다.
- [ ] UNMATCHED가 자동 통계에 들어가지 않는다.
- [ ] 탐지 누락률·오탐률·주도주 일치율을 계산할 수 있다.
- [ ] 운영자 수정과 근거가 기록된다.
- [ ] 일반 사용자에게 운영자 route·field·작업 상태가 노출되지 않는다.
- [ ] retry·resume 중복 요청이 같은 작업을 중복 실행하지 않는다.
- [ ] 서버 terminal과 secret 편집 기능이 존재하지 않는다.

### 단계 6 완료 게이트

- [ ] 실제 거래일 전체 수명주기 replay 통과
- [ ] 장중 화면과 장후 확정 화면 팀 검수 통과
- [ ] 변경 이력 감사 가능

---

## 12. 단계 7 — 온톨로지 구축과 유사사례 재검증

v1은 최종 엔진이 아니다. M-TXT 기준선으로 보존한다.

### 온톨로지 구축

최소 구조:

- 사건 주체
- 행동·촉매 유형
- 진행 단계
- 국가·지역
- 수혜 구조
- 공식성
- 신규성·재부각
- 정량 규모와 단위
- 근거 출처와 시점

### 비교 실험

1. M-TXT v1
2. 온톨로지 구조 매칭
3. M-TXT와 온톨로지 혼합 검색·재정렬

### 평가 규칙

- 동일 후보풀·fold·outcome 사용
- 가격 결과를 검색 정답으로 사용 금지
- T+1·T+5·T+20은 검색 완료 후 결합
- 2인 이상 블라인드 관련성 평가
- P@5·nDCG@5·무관 사례 비율 비교
- 라벨러 불일치와 조정 기록
- 후보·파라미터를 최종 평가 전에 고정
- 기존 2026-01-01~2026-08-11 봉인 구간 재사용 금지
- 2026-09 이후 확보되는 새 미사용 구간에서 한 번 평가
- 검색 관련성과 방향 예측력을 별도 결론으로 기록

### 채택 조건

온톨로지 또는 혼합 방식이 다음 중 하나를 만족해야 한다.

- M-TXT보다 관련성을 유의하게 개선
- 관련성은 통계적으로 동률이며 설명성·운영성이 명확히 개선

방향 예측은 강한 기준선과 교정 평가를 별도로 통과하지 못하면 계속 제품에서 제외한다.

### 완료 체크

- [ ] 온톨로지 버전과 통제어휘가 고정됐다.
- [ ] point-in-time 변환 규칙이 있다.
- [ ] 실제 데이터 감사 전에 수익률에 맞춰 분류를 조정하지 않았다.
- [ ] M-TXT v1이 재현됐다.
- [ ] 세 후보가 같은 조건에서 비교됐다.
- [ ] 2인 이상 블라인드 평가가 완료됐다.
- [ ] 불일치 조정 기록이 있다.
- [ ] 검색 선택 전에 미래 가격이 사용되지 않았다.
- [ ] 새 미사용 봉인 구간에서 한 번만 평가했다.
- [ ] 엔진·데이터·온톨로지·outcome 버전이 발급됐다.
- [ ] 검색 관련성과 방향 예측력 결론이 분리됐다.
- [ ] 모델 카드와 연구 보고서가 갱신됐다.

### 단계 7 완료 게이트

- [ ] 채택 모델이 사전 정책을 통과
- [ ] 사용자/팀 관련성 검수 통과
- [ ] 재현 가능한 코드·설정·데이터 버전 확보
- [ ] 일반 사용자 노출 승인

---

## 13. 단계 8 — 유사사례 화면 활성화

단계 7 통과 전 구현 가능 범위:

- 잠긴 화면 셸
- 빈 상태
- 표본 부족 상태
- fixture 기반 내부 검토

단계 7 통과 후 연결할 API 후보:

```text
GET /v1/events/{eventId}/similar-events
GET /v1/events/{eventId}?contextEventId={currentEventId}
```

두 번째 경로의 path `eventId`는 유사사례 목록에서 받은 `matchedEventId` 값이다.

### 화면 요구사항

- 관련성 순서
- 기간별 실제 유효 분모
- 상승 사건 수
- 중앙 수익률
- 왜 비슷한지 태그
- 과거 당시 주도주
- 사건별 실제 T+1·T+5·T+20
- 가격 누락·관찰 중·표본 부족
- 엔진·데이터·온톨로지 버전
- 미래 결과가 검색에 사용되지 않았다는 설명

### 금지

- 수익률 높은 순 기본 정렬
- 수치 유사도 확률
- 성공률·적중률
- 예상 수익률
- 매수·매도 추천
- 집계값과 개별 사건값 혼합

### 완료 체크

- [ ] feature flag는 단계 7 승인 후에만 열린다.
- [ ] 엄격·부분 사례가 별도 분모로 표시된다.
- [ ] T+1·T+5·T+20 분모가 각각 정확하다.
- [ ] 대표값이 중앙값으로 표시된다.
- [ ] 사례 목록이 관련성 순서를 유지한다.
- [ ] 수익률로 검색 결과를 재정렬하지 않는다.
- [ ] 개별 사건 상세에 전체 표본 집계가 섞이지 않는다.
- [ ] 당시 주도주와 현재 관련주가 구분된다.
- [ ] 표본·결측·관찰 중 상태가 0으로 표시되지 않는다.
- [ ] 예측·추천 문구가 없다.
- [ ] 계산 기준과 버전을 확인할 수 있다.

### 단계 8 완료 게이트

- [ ] API contract test 통과
- [ ] 검색 결과와 화면 목록 일치
- [ ] 제품·연구·디자인 공동 검수 통과

---

## 14. 단계 9 — 최종 검증과 출시 준비

### 자동 검증

- REST OpenAPI contract test
- WebSocket AsyncAPI contract test
- 계산 단위 테스트
- 미래 정보 누수 테스트
- 저장 체결 replay
- WebSocket 재연결·sequence 테스트
- 프론트 컴포넌트 테스트
- E2E 핵심 여정
- 관심 저장·해제 idempotency, 사용자 소유권 격리, 계정 삭제 E2E
- 시각 회귀 테스트
- 접근성 자동 검사
- 의존성·보안 검사
- OCI Compose 설정 검증과 container health check
- Git·frontend bundle·container image의 비밀정보 검사

### 수동 검증

- iOS·Android 실제 기기
- 좁은 모바일·태블릿·데스크톱
- 장 시작 전·장중·지연·장 마감
- 활성 테마 없음
- Coverage 부족
- 뉴스 근거 없음
- 장중·장후 분류 변경
- 유사사례 0건·소수·충분
- 가격 누락·관찰 중
- 키보드·스크린리더
- 관심 목록 빈 상태·사용 불가 항목·저장 실패 복구·계정 삭제 확인

### 성능 검증

- 180종목 구독 fixture 30분 replay
- 0B 수신부터 서버 반영 P95 500ms 이내
- 영향 테마 재계산 P95 1초 이내
- 홈 반영 P95 3초 이내
- 트리맵 숫자 반영 1초 이내
- 장시간 메모리·DOM 증가 없음
- OCI A1 4 OCPU·24GB에서 API·worker·PostgreSQL·Redis 동시 실행 시 CPU·메모리 여유 확인
- ARM64 browser worker 정기 실행이 장중 처리 지연 목표를 침해하지 않음

### 완료 체크

- [ ] 모든 계약 테스트 통과
- [ ] 핵심 계산 테스트 통과
- [ ] 미래 정보 누수 테스트 통과
- [ ] 핵심 E2E 통과
- [ ] 모바일·데스크톱 시각 검수 통과
- [ ] WCAG AA 목표 검수 통과
- [ ] 성능 목표 통과
- [ ] Vite·런타임 의존성 취약점 검토 완료
- [ ] 비밀정보와 개인정보 노출 검사 완료
- [ ] 데이터 사용 권리 확인 완료
- [ ] 기본 운영 상태 조회와 서버 로그 준비
- [ ] 외부 암호화 backup을 새 volume에 복원하는 시험 완료
- [ ] VM 재부팅 후 자동 기동과 session state 복원 확인
- [ ] 장애 대응 runbook 준비
- [ ] rollback 절차 준비

### 단계 9 완료 게이트

- [ ] 스테이징 출시 승인
- [ ] 알려진 차단 결함 0건
- [ ] 남은 비차단 결함과 위험 문서화

---

## 15. 단계 10 — shadow 운영과 출시

### 순서

1. 개발 환경
2. OCI 스테이징
3. 실제 장중 shadow 운영
4. 장후 인포스탁 결과 대조
5. 팀 내부 제한 공개
6. 사용자 제한 공개
7. 기본 작업 상태와 서버 로그 확인
8. 정식 공개

### shadow 운영에서 확인할 값

- 장중 탐지 재현율
- 홈 Top 5와 장후 주요 테마 일치율
- 대형주 단독 상승 오탐률
- 주도주 일치율
- Coverage 미달 비율
- 뉴스 수집·매칭 지연
- 잘못된 상승 이유 연결률
- WebSocket 재연결 복구 시간
- 장중·장후 분류 변경률
- 유사사례 무관 사례 비율

### 완료 체크

- [ ] 최소 합의된 거래일 수만큼 shadow 운영했다.
- [ ] 장후 대조 보고서가 작성됐다.
- [ ] 차단 품질 기준을 모두 통과했다.
- [ ] 제한 공개 사용자 피드백을 반영했다.
- [ ] 오류·지연·빈 상태 문구를 검증했다.
- [ ] 운영 담당자와 연락 체계가 정해졌다.
- [ ] rollback 실행을 시험했다.
- [ ] 정식 출시 승인 기록이 있다.

---

## 16. 진행 상황 기록 규칙

체크박스는 작업 시작이 아니라 **검증 증거가 존재할 때** 체크한다.

각 단계 완료 시 다음을 남긴다.

```text
완료일:
담당자:
관련 PR 또는 커밋:
테스트 결과:
데모 또는 스테이징 URL:
남은 위험:
승인자:
```

상태 정의:

| 상태 | 의미 |
|---|---|
| 미착수 | 작업 시작 전 |
| 진행 중 | 구현 또는 검증 진행 중 |
| 차단 | 외부 권한·데이터·결정 없이는 진행 불가 |
| 검수 대기 | 구현 완료, 승인 전 |
| 완료 | 체크리스트·증거·게이트 모두 통과 |

완료로 변경하면 이 문서 상단 단계 표도 함께 갱신한다.

---

## 17. 주요 위험과 방지책

| 위험 | 방지책 |
|---|---|
| 아키텍처 없이 UI·API를 각각 고정 | 시스템 경계·상태 소유권 초안 승인 후 계약 작성 |
| 프론트·백엔드 필드 불일치 | OpenAPI·AsyncAPI·fixture를 단일 기준으로 사용 |
| 근거 없는 과도한 마이크로서비스 분할 | 처리량·장애·소유권·SLO 근거로 `ADR-001` 비교 |
| 편의를 위한 과소 분할과 장애 결합 | Market·실시간·뉴스·연구 경계별 독립 배포·데이터 소유권 대안 검증 |
| 실시간 memory 상태 손실 | Redis 공유 상태·PostgreSQL checkpoint·재연결 전체 재계산 |
| DB 변경과 내부 event 발행 불일치 | transactional outbox와 idempotent consumer 사용 |
| 디자인 프로토타입 구조를 그대로 확장 | 시각 자산만 추출하고 화면·데이터 구조 재구축 |
| 하드코딩 통계가 제품에 잔존 | fixture와 운영 데이터 명확히 분리, production build 검사 |
| 집계값과 개별 사건값 혼합 | 화면별 의미 계약과 API 응답 분리 |
| Coverage·결측을 0으로 표시 | schema와 화면 테스트에 `null` 상태 포함 |
| 뉴스 근거 없는 인과 생성 | 근거 없을 때 LLM 호출 금지 |
| v1 엔진을 최종 엔진으로 오해 | 기준선 표시, feature flag, 단계 7 게이트 강제 |
| 기존 봉인 구간 반복 사용 | 새 미사용 구간을 사전에 분리·잠금 |
| 예측 연구와 사례 검색 혼합 | 모델·API·화면·평가 지표 분리 |
| 데이터 권리 문제 | 구현·운영 전 공급원별 이용 범위 승인 |
| 프로토타입 의존성 취약점 | 제품 승격 시 Vite와 관련 의존성 업데이트·audit |
| 모바일 데모 프레임이 제품 레이아웃 지배 | demo wrapper와 실제 responsive app 분리 |
| OCI 단일 VM 장애가 전체 기능 중단으로 전파 | 외부 backup·restore drill·재부팅 자동 복구, 실제 지표가 요구하면 역할별 host 분리 |
| A1 ARM64에서 browser·image 비호환 | 단계 2 초기에 build·로그인·state 복원 PoC, 실패 시 browser worker만 호환 환경으로 분리 |
| staging과 production의 단일 호스트 오염 | Compose project·network·volume·credential 분리와 production 데이터 복제 금지 |
| 사용자 저장 데이터의 계정 간 노출 | session 기반 owner 결정, DB unique·foreign key, IDOR·권한 자동 테스트 |

---

## 18. 바로 다음 작업

순서:

1. [system_architecture.md](./system_architecture.md)에서 `ADR-001` 외 남은 경계 팀 검수
2. [ADR-009](./adr/009-oci-initial-deployment.md)의 이전 프로젝트 잔존 항목 점검과 OCI host 보안 baseline 적용
3. OCI A1에서 핵심 container와 Playwright·Chromium ARM64 PoC
4. [ui_prototype_adaptation_plan.md](./ui_prototype_adaptation_plan.md)와 [api_contract.md](./api_contract.md) 초안 공동 검수
5. 나머지 필수 ADR 초안 작성과 미결정 항목 소유자·기한 지정
6. `contracts/openapi.yaml`, `contracts/asyncapi.yaml` 작성
7. `contracts/schemas/`와 `contracts/fixtures/` 작성
8. OCI staging Compose 기반, 영구 volume, secret 주입 골격 구축
9. 아키텍처·UI 계획·기계 계약을 프론트·백엔드·디자인·데이터 담당이 최종 검토
10. 단계 1과 단계 2를 병렬 시작

착수 체크:

- [x] 프로토타입과 분리된 production 코드 구조 사용 결정
- [x] 새 팀 소유 비공개 production GitHub 저장소 사용을 `ADR-010`으로 확정
- [ ] production 원격 저장소 생성과 프론트·백엔드 배치 확정
- [ ] 담당자와 리뷰어 지정
- [x] 시스템 아키텍처 초안 작성
- [x] 모듈형 모놀리스와 역할별 독립 프로세스·컨테이너를 `ADR-001`로 확정
- [x] 초기 운영 플랫폼을 OCI A1 Flex로 확정하고 ADR 기록
- [x] OCI 운영자 SSH public key 접속 검증
- [ ] 이전 프로젝트 잔존 항목 점검·정리
- [ ] 핵심 container·browser worker ARM64 PoC
- [ ] 시스템 아키텍처 팀 검수
- [ ] 필수 ADR 초안 작성
- [x] UI 적용 계획 초안 작성
- [x] API 의미 계약 초안 작성
- [ ] UI 적용 계획·API 의미 계약 공동 검수
- [ ] 첫 수직 통합 대상이 `오늘 + 테마 상세`로 합의됨

이 문서가 앞으로 전체 구현의 진행 체크리스트다.
