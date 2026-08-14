# DAYJAVIEW 최종 구현 세션 프롬프트 팩

- 작성일: 2026-08-14 12:00 KST
- 목적: 세션당 작업 1개 → 커밋·푸시 → 새 세션 반복으로 컨텍스트 오염 없이 최종 구현
- 사용법: 아래 순서표대로 새 세션(Codex 또는 Claude Code)을 열고, 해당 프롬프트 블록을 통째로 붙여넣는다. 오케스트레이터 세션은 두지 않는다.

---

## 0. 현재 상태 요약 (2026-08-14 12:00 기준)

- 레포에 제품 코드 0. 문서(PRD·화면·API 계약·아키텍처·ADR·로드맵)는 완비.
- **이 PC에서 2026-08-14 장중 리플레이 수집이 실행 중** (`scripts/run_market_capture.ps1` 등). 15:30 장 마감 후 장후 절차(백필·최종화·감사)가 남아 있음 → 세션 R이 처리.
- DNS(`dayjaview.duckdns.org`, `api.dayjaview.duckdns.org` → 168.107.25.213)와 OCI SSH 접속 검증 완료. 앱 배포는 미시작.
- 로컬 툴체인: Node 26.4 / Python 3.12.10 / Docker 29.7 확인됨.
- 디자인 원본 `nangom/dayjaview-prototype`은 public, 기준 커밋 `6532487`.
- 인포스탁 테마 원본 JSON은 `data/infostock/import/`에 로컬로 존재 (data/는 gitignore, 커밋 금지).

## 1. 실행 순서표

| # | 세션 | 작업 | 도구 | 모델 | 의존 | 비고 |
|---|---|---|---|---|---|---|
| 1 | **R** | 시장 리플레이 장후 최종화 | Claude | fable5 max | 없음 (15:30 이후) | **기상 즉시 최우선. B 레인보다 먼저** |
| 2 | **C1** | 기계 판독 계약(contracts/) 생성 | Codex | sol xhigh | 없음 | R과 동시 시작 가능 |
| 3 | **B1** | 백엔드 골격 + 스택 ADR | Claude | opus4.8 max | 없음 | R 종료 후 시작 |
| 4 | **C2** | 프론트엔드 셸 전체 (fixture 기반) | Codex | **sol max** | C1 | 가장 큰 프론트 작업 |
| 5 | **B2** | 인포스탁 데이터 기반 (DB·import) | Claude | fable5 max | B1 | |
| 6 | **B3** | 실시간 테마 엔진 + 리플레이 연동 | Claude | fable5 max | B2, R | 핵심 엔진 |
| 7 | **B4** | REST·WS API + Google OAuth + 관심 | Claude | opus4.8 max | B2 (계약 C1) | |
| 8 | **C3** | 유사사례 잠금 셸 + 운영자 화면 UI | Codex | sol xhigh | C2 | C2 직후 아무 때나 |
| 9 | **V1** | 첫 수직 통합 (오늘·상세·관심) | Claude | **fable5 max** | C2, B3, B4 | 두 레인 합류점 |
| 10 | **C4** | 인사이트 트리맵 | Codex | sol xhigh | V1 | |
| 11 | **B5** | 장후 확정 + 운영자 파이프라인 | Claude | fable5 max | B3, B4 | |
| 12 | **B6** | 뉴스 수집·근거 파이프라인 | Claude | opus4.8 max | B2, B4 | 키 없으면 fixture 모드 |
| 13 | **V2** | 자동 검증 스위트 + CI | Claude | opus4.8 max | V1 | |
| 14 | **D1** | 배포 준비 (compose·Caddy·vercel.json) | Claude | opus4.8 max | B4 | V1과 병렬 가능 |
| 15 | **D2** | OCI 스테이징 실배포 + Vercel | Claude | fable5 max | D1, V1 | **사용자 참관 권장** |

레인 운영: 터미널 2개면 충분하다.

```text
Codex 레인 : C1 ──▶ C2 ──▶ C3 ──────────────▶ C4
Claude 레인: R ──▶ B1 ─▶ B2 ─▶ B3 ─▶ B4 ─▶ V1 ─▶ B5 ─▶ B6 ─▶ V2 ─▶ D1 ─▶ D2
```

담당 구분 원칙: **Codex = 계약·화면(코드젠 성격의 잘 명세된 작업)**, **Claude = 데이터·엔진·통합·검증·운영(장기 맥락·크로스 시스템·이 PC의 실행 중 수집기와 공존해야 하는 작업)**. 모델은 통합·엔진·운영처럼 판단이 무거운 세션만 최상위(fable5 max / sol max), 나머지는 opus4.8 max / sol xhigh.

## 2. 사용자 준비물 (없으면 해당 부분은 코드+placeholder로 진행됨)

- Google OAuth 클라이언트 ID/secret (redirect: `https://api.dayjaview.duckdns.org/api/v1/auth/google/callback`, 로컬 dev용 `http://localhost:8000/...`)
- NAVER API HUB client id/secret (뉴스, B6)
- Anthropic API 키 (뉴스 LLM 구조화, B6, 선택)
- OpenDART 인증키 (유동주식 기준정보, 선택 — 없으면 qualityFlags 처리)
- Vercel 계정 연결 (D2)
- OCI VM SSH 접속 가능 상태 (D2)
- 키움 credential은 `.env.local`에 이미 존재 (수집기 사용 중)

## 3. 이 팩으로 도달하는 지점 (정직한 범위)

- 도달: 계약→프론트 셸→백엔드(데이터·엔진·API)→리플레이 기반 수직 통합→트리맵→장후 확정→뉴스 골격→검증 스위트→스테이징 배포까지. 즉 **로드맵 단계 0~6 + 9의 코드 부분**.
- 도달 불가(사람·거래일 필요): 실장(다음 거래일 월요일) 라이브 검증, 단계 7 온톨로지 재검증(2인 블라인드 평가), 단계 8 유사사례 해금, 단계 10 shadow 운영·출시. 유사사례 화면은 feature flag 잠금 상태로만 존재.

---

## 세션 R — 시장 리플레이 장후 최종화 [Claude, fable5 max]

```text
# 작업: 2026-08-14 시장 리플레이 fixture 장후 최종화 (이 세션은 이 작업만 수행)

저장소: C:\dayjaview (main 브랜치, 이 PC에서 오늘 장중 수집이 실행됐다)

## 필독 (순서대로)
1. docs/one_time_market_replay_collection_plan.md — 성공 조건과 장후 절차의 기준
2. docs/market_replay_2026-08-14_completion_report.md — 현재 상태, §4 장후 실행 절차, §5 증거 기록란

## 전제 확인 (수정 아님, 확인만)
- 지금이 15:30 KST 이후인지 확인한다.
- 본 수집 run과 보조 run이 스스로 정상 종료됐는지 확인한다 (collection_runs.status=COMPLETED).
  아직 RUNNING이면 절대 프로세스를 종료하지 말고 종료될 때까지 대기 후 진행한다.

## 목표
완료 보고서 §4의 절차를 그대로 실행해 데이터셋을 최종 판정하고, §5 증거 기록란을 실측값으로 채운다.
1. scripts/check_market_capture.ps1 -TradeDate 2026-08-14
2. python -m pytest -q (레포 루트, 기존 리플레이 테스트)
3. ka10084 공백 복구 전수 실행 (run_market_gap_recovery.ps1, 계획 문서의 장후 전수 기준)
4. 전 종목 1분봉 백필 (repair_market_backfill.py 등 계획 문서가 정한 절차)
5. finalize_market_replay.py → audit-supplement → audit-recovery → prove-combined → socket-prove
6. 각 게이트의 PASS/FAIL/알려진 한계를 §1 표와 §5 기록란에 실측값·해시로 기입. 판정을 미화하지 않는다.
   계획 문서가 인정한 알려진 한계(09:00~10:09 보조 공백 등)는 FAIL 유지로 정직하게 기록한다.

## 금지
- scripts/의 수집·검증 로직 변경 금지 (버그로 절차가 막힐 때만 최소 수정하고 사유를 커밋 메시지에 명시)
- data/, logs/, .env* 커밋 금지. credential이 로그·문서에 남지 않게 한다.
- 이 작업 외 다른 구현 착수 금지. 발견한 문제는 docs/BACKLOG.md에 한 줄 기록만.

## 완료 기준
- verify/audit/prove 계열 명령의 exit code와 최종 해시가 보고서에 기록됨
- 완료 보고서 상태가 실측 결과에 따라 갱신됨 (완료 또는 한계 명시된 부분 완료)
- docs/implementation_roadmap.md에는 손대지 않는다 (이 작업은 로드맵 체크 대상 아님)

## 마무리
git pull --rebase origin main → 변경된 문서(와 불가피했던 스크립트 최소 수정)만 커밋 → push
커밋 메시지 예: docs: finalize 2026-08-14 market replay dataset with audit evidence
```

---

## 세션 C1 — 기계 판독 계약 생성 [Codex, sol xhigh]

```text
# 작업: contracts/ 디렉터리 생성 — OpenAPI·AsyncAPI·JSON Schema·fixtures (이 세션은 이 작업만 수행)

저장소: C:\dayjaview (main 브랜치)

## 필독
1. docs/api_contract.md — 의미 계약의 단일 기준. 여기 정의된 것만 기계 계약으로 옮긴다.
2. docs/implementation_roadmap.md §5.4 — 산출물 구조와 필수 fixture 12종 목록
3. docs/screen_spec.md — 화면이 소비하는 필드 확인용 (계약 확장 금지)

## 목표
contracts/ 아래에 다음을 만든다.
- openapi.yaml: api_contract.md에 정의된 REST 전체 (market/session, themes/rankings, insights/treemap,
  theme event 상세, evidence, auth, saved 관심, operator). 문서에 없는 endpoint를 발명하지 않는다.
- asyncapi.yaml: theme_rank_snapshot, theme_treemap_snapshot, event_state_changed + 재연결·sequence·전체 snapshot 규칙
- schemas/: 공용 JSON Schema (ID 체계, 시간 의미 asOf/publishedAt/receivedAt, LIVE/DELAYED/CLOSED,
  Coverage와 qualityFlags, null·0·빈 배열 구분, 오류 형식, 버전 필드)
- fixtures/: 로드맵 §5.4의 필수 12종 (LIVE 정상, DELAYED, CLOSED, Coverage 충분·부분·미달, 상승 이유 확인 중,
  뉴스 한 건 추정, 복수 뉴스 확인, 장후 확정, 미매칭·검수 대기, 활성 테마 없음, 계산 불가 null, 재연결 전체 snapshot)
- 검증 스크립트: npx @redocly/cli lint openapi.yaml, npx @asyncapi/cli validate asyncapi.yaml,
  그리고 모든 fixture가 해당 schema에 valid한지 검사하는 스크립트 1개 (contracts/validate 스크립트로 통합, npm 또는 python 자유)

## 금지
- 이 세션은 위 작업만 수행. scripts/, tests/, data/, logs/, .env* 는 기존 리플레이 수집기 자산이므로 수정·커밋 금지.
- 실행 중인 python/pwsh 수집 프로세스 종료 금지.
- 계약 의미를 임의로 바꾸지 않는다. api_contract.md와 충돌을 발견하면 문서를 따르고 모호한 점만 docs/BACKLOG.md에 한 줄 기록.

## 완료 기준
- 검증 스크립트 전체 exit 0
- fixture 12종이 전부 존재하고 schema valid
- docs/implementation_roadmap.md §5.4 체크박스 중 증거가 생긴 항목만 체크 갱신

## 마무리
git pull --rebase origin main → 커밋 → push
커밋 메시지 예: feat(contracts): add validated OpenAPI/AsyncAPI schemas and core fixtures
```

---

## 세션 B1 — 백엔드 골격 + 스택 ADR [Claude, opus4.8 max]

```text
# 작업: backend/ 골격 생성과 백엔드 스택 확정 ADR-012 (이 세션은 이 작업만 수행)

저장소: C:\dayjaview (main 브랜치)

## 필독
1. docs/system_architecture.md — 모듈 경계·상태 소유권·프로세스 배치
2. docs/adr/001-application-modularity.md — 역할별 entrypoint 원칙
3. docs/implementation_roadmap.md 단계 2 — 백엔드 기반 범위

## 스택 (이대로 확정하고 ADR-012로 기록)
Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2 + uvicorn + redis-py + pytest, 패키지 관리는 uv.
근거: 기존 리플레이 수집기·인포스탁 수집기(scripts/)와 언어 통일로 검증 로직 재사용, Playwright python의 ARM64 실적,
asyncio 기반 WebSocket. 이 근거를 docs/adr/012-backend-stack.md로 남긴다.

## 목표
backend/ 아래 모듈형 모놀리스 골격:
- backend/pyproject.toml (uv), backend/src/dayjaview/ 패키지
- 도메인 모듈 디렉터리: market(실시간), themes, events, news, infostock, identity(auth·saved), operator, shared(계약·시간·설정)
- ADR-001의 역할별 entrypoint: api, realtime-worker, news-worker, infostock-worker, scheduler (python -m 실행 가능한 스텁)
- FastAPI 앱 + GET /api/health (DB·Redis 연결 상태 포함, 제품 데이터 없음)
- 설정: pydantic-settings, .env.example 갱신 (실값 금지)
- infra/compose.dev.yml: postgres:16 + redis:7 (dev 전용, 포트는 기본값과 충돌 없게)
- backend/Dockerfile (linux/arm64 호환 베이스, python:3.12-slim)
- backend/tests/ 에 health·설정 smoke test. 레포 루트 tests/(리플레이 수집기용)와 완전 분리. pytest 설정은 backend/ 안에서만 동작하게.

## 금지
- 이 세션은 위 골격만. 도메인 로직·API 구현·DB 스키마는 다음 세션 몫이다.
- scripts/, tests/(루트), data/, logs/, .env* 수정·커밋 금지. 실행 중인 수집 프로세스 종료 금지.
- 과잉 금지: 메시지 브로커, 마이크로서비스 분리, 불필요한 추상 계층 도입 금지 (ADR-001 준수).

## 완료 기준
- docker compose -f infra/compose.dev.yml up -d 후 backend에서 uv run pytest 통과
- uvicorn 기동 → /api/health 200 (db·redis ok)
- ADR-012 문서 존재

## 마무리
git pull --rebase origin main → 커밋 → push
커밋 메시지 예: feat(backend): scaffold modular monolith skeleton with health check (ADR-012)
```

---

## 세션 C2 — 프론트엔드 셸 전체 [Codex, sol max]

```text
# 작업: frontend/ 생성 — 디자인 이식 + 앱 셸 + fixture 기반 4개 화면 (이 세션은 이 작업만 수행)

저장소: C:\dayjaview (main 브랜치)

## 필독
1. docs/ui_prototype_adaptation_plan.md — 유지/교체/폐기 기준의 단일 문서
2. docs/screen_spec.md — 화면별 요구사항과 상태
3. docs/PRD.md — 제품 의미 (예측·추천 표현 금지 등)
4. contracts/ — C1이 만든 schema와 fixtures (프론트의 데이터 계약. 임의 필드 추가 금지)
5. 디자인 원본: git clone https://github.com/nangom/dayjaview-prototype 를 임시 폴더에 받고
   기준 커밋 6532487 을 checkout 해 참고한다. 이 레포 안으로 복사하지 않고 시각 자산·토큰만 이식한다.
   docs/dayjaview-mockup.html 도 시각 참고용.

## 목표
- frontend/: Vite + React + TypeScript. 라이브러리는 최소·표준 (router, 데이터 fetch, 상태는 과하지 않게)
- 디자인 토큰 추출: 색·타이포·간격·라운드·그림자·모션을 frontend/src/design/ 한곳에
- 공통 컴포넌트: 카드, 배지, 데이터 상태 표시(LIVE/DELAYED/CLOSED), 바텀시트, skeleton, 빈 상태
- 앱 셸: 오늘 / 인사이트 / 테마 / 관심 하단 탭 + URL 라우팅 (themeId·eventId로 화면 복원 가능)
- 화면: TodayScreen(테마 카드 목록 — 프로토타입의 순위 휠은 폐기), InsightScreen(트리맵 자리, 지금은 placeholder),
  ThemeScreen(상세: 이유·주도주·확산·Coverage), SavedScreen(유형 필터·빈 상태·저장 실패 상태)
- OperatorScreen: 별도 route, 일반 navigation·sitemap에 절대 노출 금지
- 유사사례 영역: feature flag로 잠금 (기본 off)
- 아이폰 목업 프레임: demo wrapper로 격리, 제품 레이아웃과 무관하게
- API 계층: client interface + mock adapter 분리. mock adapter는 contracts/fixtures/를 읽어
  12종 상태를 전부 화면에서 재현 (개발용 상태 스위처 포함)
- 모바일 bare / 데스크톱 레이아웃 분리, 키보드 포커스·기본 aria
- lint + build + 최소 컴포넌트 테스트 통과

## 금지
- 이 세션은 위 작업만. 실제 API 연동(수직 통합)은 다른 세션 몫 — mock adapter까지만.
- scripts/, tests/(루트), data/, logs/, .env* 수정·커밋 금지. 실행 중인 수집 프로세스 종료 금지.
- 프로토타입의 App.jsx·하드코딩 데이터·화면 구조를 복사하지 않는다 (ADR-010).
- 과잉 금지: 스토리북·i18n·테마 시스템 등 문서에 없는 인프라 도입 금지.

## 완료 기준
- npm run build, lint, test 모두 통과
- fixture 12종 상태가 화면에서 전부 재현됨 (상태 스위처로 확인)
- 로드맵 단계 1 체크박스 중 증거가 생긴 항목만 갱신

## 마무리
git pull --rebase origin main → 커밋 → push
커밋 메시지 예: feat(frontend): app shell, design tokens, fixture-driven screens
```

---

## 세션 B2 — 인포스탁 데이터 기반 [Claude, fable5 max]

```text
# 작업: 도메인 DB 스키마 + 인포스탁 import 파이프라인 (이 세션은 이 작업만 수행)

저장소: C:\dayjaview (main 브랜치)

## 필독
1. docs/infostock_db_implementation_plan.md — 원천 DB·이력 설계 기준
2. docs/system_architecture.md — 데이터 소유권 (infostock 모듈이 자기 테이블의 유일한 writer)
3. docs/implementation_roadmap.md 단계 2 "데이터 기반" 절
4. data/infostock/import/ 의 실제 JSON 구조 (로컬 존재, 커밋 금지) 와 scripts/collect_infostock.py (읽기 참고만)

## 목표
- Alembic migration: 인포스탁 원본 snapshot(원문·해시·파서 버전·수집 시각), 테마, 종목, 테마-종목 membership과 변경 이력,
  KRX 거래일 달력, 기준정보 구조(발행주식수·자기주식·유동비율 — 값은 나중, 구조와 버전 필드만)
- 현재 관련주와 과거 당시 주도주를 구분할 수 있는 구조 (로드맵 요구)
- import CLI: uv run python -m dayjaview.infostock.import_cli data/infostock/import/
  → 테마 약 590건과 membership이 DB에 적재되고, 같은 입력 재실행 시 중복 없이 idempotent
- 원본 재현성: 저장된 snapshot·해시로 정규화 결과를 재생성할 수 있음을 테스트로 증명
- KRX 거래일: 2025~2026 달력 적재 (공휴일 포함, 출처 주석)
- backend/tests/ 에 import 정합성·idempotency 테스트

## 금지
- 이 세션은 위 작업만. 실시간 계산·API는 다음 세션 몫.
- scripts/, tests/(루트), data/, logs/, .env* 수정·커밋 금지. data/ 내용물 커밋 금지. 수집 프로세스 종료 금지.
- 유동주식비율 실계산(OpenDART 호출)은 범위 밖 — 구조만 만들고 docs/BACKLOG.md에 기록.

## 완료 기준
- migration up/down 동작, import 후 테마·membership 건수가 원본과 일치하는 검증 출력
- 전체 backend 테스트 통과

## 마무리
git pull --rebase origin main → 커밋 → push
커밋 메시지 예: feat(infostock): domain schema, provenance-preserving import pipeline
```

---

## 세션 B3 — 실시간 테마 엔진 + 리플레이 연동 [Claude, fable5 max]

```text
# 작업: 실시간 테마 계산 엔진과 리플레이 Market Gateway 연동 (이 세션은 이 작업만 수행)

저장소: C:\dayjaview (main 브랜치)

## 필독
1. docs/realtime_theme_feature_spec.md — 계산 정의의 단일 기준 (가중 수익률·중앙값·확산·거래 관심·Coverage·Event 상태 전이)
2. docs/system_architecture.md — Event 모듈 단일 writer, Redis snapshot 역할
3. docs/market_replay_2026-08-14_completion_report.md — 리플레이 재생 인터페이스 (WebSocket 재생, 서비스 profile)
4. scripts/replay_market.py — 재생 명령 사용법 (읽기 참고만, 수정 금지)

## 목표
- Market Gateway 입력 어댑터: 운영에서는 키움 WS, 개발·테스트에서는 리플레이 재생을 같은 인터페이스로 소비.
  리플레이 소스: scripts/replay_market.py 의 WebSocket 재생(1배속·20배속·무지연)을 클라이언트로 구독하거나
  sqlite를 직접 읽는 재생기를 backend 쪽에 구현 (scripts는 절대 수정하지 않는다)
- 종목 실시간 상태 저장 + 종목→테마 역색인 + dirty 테마 증분 계산
- 테마 지표: spec 문서의 상한형 유동시가총액 가중 수익률 (유동비율 미승인 상태는 spec이 정한 fallback과 qualityFlags로 표기),
  중앙값, 상승 확산, 거래 관심, Coverage (미달을 0으로 계산 금지)
- Event ID 생성과 상태 전이 (CANDIDATE→ACTIVE 등 spec의 상태 기계), 상태 이력 저장
- 산출 snapshot을 Redis에 publish: theme_rank_snapshot, theme_treemap_snapshot 형태 (contracts/asyncapi.yaml 준수)
- realtime-worker entrypoint에서 이 파이프라인 실행
- 테스트: 2026-08-14 리플레이의 짧은 구간(예: 09:00~09:05)을 무지연 재생해
  (1) 같은 입력·버전에서 같은 결과 해시 (결정성), (2) Coverage 미달 시 null 처리, (3) 최소 1개 테마 활성화 를 증명

## 금지
- 이 세션은 위 작업만. REST/WS API 노출은 B4 몫.
- scripts/, tests/(루트), data/(쓰기), logs/, .env* 수정·커밋 금지. 리플레이 DB는 읽기 전용으로만 연다.
- 키움 라이브 연결 시도 금지 (지금은 리플레이만. 라이브는 다음 거래일에 사용자와 함께).

## 완료 기준
- 리플레이 구간 재생으로 테마 순위 snapshot이 Redis에 갱신되는 데모 커맨드 1개
- 결정성·Coverage·상태 전이 테스트 통과

## 마무리
git pull --rebase origin main → 커밋 → push
커밋 메시지 예: feat(realtime): incremental theme engine driven by market replay
```

---

## 세션 B4 — REST·WS API + 인증 + 관심 [Claude, opus4.8 max]

```text
# 작업: 계약 기반 REST·WebSocket API + Google OAuth + 관심 저장 + 운영자 권한 (이 세션은 이 작업만 수행)

저장소: C:\dayjaview (main 브랜치)

## 필독
1. contracts/openapi.yaml, asyncapi.yaml, fixtures/ — 구현의 단일 기준 (계약을 바꾸지 말고 계약에 맞춘다)
2. docs/api_contract.md — 의미 (인증 범위, null·0 구분, sequence·재연결 규칙, 오류 형식)
3. docs/system_architecture.md — WebSocket ticket 인증(30초·1회용), 세션 규칙
4. docs/implementation_roadmap.md 단계 2 "API 기반" 절

## 목표
- Alembic migration: 사용자(Google subject 기반)·server session·saved(테마/종목/이벤트, 사용자별 unique)
- Google OAuth: 로그인 시작→callback→server session cookie(host-only)→로그아웃. client id/secret은 env로,
  없으면 dev 전용 fake 로그인 provider로 전체 흐름 테스트 가능하게 (fake는 설정으로만 켜짐, 기본 off)
- 안전한 returnTo (내부 경로만 허용)
- 인증 강제: health·auth 외 모든 REST·WS는 세션 필수. 비로그인 401
- REST: openapi.yaml 전체 구현 — market/session, themes/rankings, insights/treemap, 테마 상세, evidence(지금은 상태만),
  saved CRUD(idempotent, 소유권 검사, IDOR 차단), 계정 삭제(세션·프로필·저장 항목 제거)
- WebSocket /v1/realtime: ticket 발급 API + 30초·1회용 원자적 소비, sequence 증가, 재연결 시 전체 snapshot,
  Redis snapshot(B3 산출)을 fan-out
- OPERATOR role bootstrap(env의 Google subject 목록) + /v1/operator/* 골격 + 일반 사용자 403
- contract test: 실제 응답이 openapi/asyncapi schema에 valid함을 자동 검사
- 응답에 API·계산·membership 버전 필드 포함 (계약대로)

## 금지
- 이 세션은 위 작업만. 프론트 연동은 V1 몫.
- scripts/, tests/(루트), data/, logs/, .env* 수정·커밋 금지. 수집 프로세스 종료 금지.
- 응답·로그에 secret/cookie/token 원문 출력 금지.

## 완료 기준
- contract test 전체 통과, saved idempotency·소유권·계정삭제 테스트 통과
- ticket 재사용·만료·타 세션 사용이 거부되는 테스트 통과

## 마무리
git pull --rebase origin main → 커밋 → push
커밋 메시지 예: feat(api): contract-complete REST/WS with Google OAuth and saved items
```

---

## 세션 C3 — 유사사례 잠금 셸 + 운영자 화면 UI [Codex, sol xhigh]

```text
# 작업: 유사사례 잠금 셸과 OperatorScreen fixture UI 마감 (이 세션은 이 작업만 수행)

저장소: C:\dayjaview (main 브랜치)

## 필독
1. docs/implementation_roadmap.md 단계 8 (잠금 전 허용 범위·금지 표현), 단계 6 (운영자 화면 요구)
2. docs/screen_spec.md — 해당 화면 명세
3. docs/PRD.md — 금지 표현 (성공률·적중률·예상 수익률·매수 추천 등)
4. frontend/ 기존 구조와 디자인 토큰 (C2 산출)

## 목표
- 유사사례 화면: feature flag 잠금 유지. 잠긴 셸 + 빈 상태 + 표본 부족 상태 + fixture 기반 내부 검토 모드만.
  관련성 순서, 기간별 분모, 중앙값, 당시 주도주 vs 현재 관련주 구분 등 단계 8 요구 레이아웃을 fixture로 구현.
  금지 표현(수익률순 기본 정렬, 확률, 성공률, 예상 수익률, 추천)은 UI에 존재하지 않게.
- OperatorScreen: 수집 상태, 작업 목록, retry/resume 버튼, UNMATCHED 검수 대기열, audit 이력 — 전부 fixture 기반 UI.
  일반 사용자 navigation·검색·sitemap에 운영자 route가 노출되지 않음을 테스트로 확인.
- 접근성·반응형 마감: 키보드 탐색, 포커스 순서, 좁은 모바일 레이아웃 점검.

## 금지
- 이 세션은 위 작업만. 실제 API 연동 금지 (fixture만).
- feature flag 기본값을 켜지 않는다 (온톨로지 게이트 전 노출 금지 — 로드맵 원칙 8).
- scripts/, tests/(루트), data/, logs/, .env* 수정·커밋 금지.

## 완료 기준
- build·lint·test 통과, flag off 시 유사사례 접근 불가 테스트

## 마무리
git pull --rebase origin main → 커밋 → push
커밋 메시지 예: feat(frontend): locked similar-events shell and operator screen fixtures
```

---

## 세션 V1 — 첫 수직 통합 [Claude, fable5 max]

```text
# 작업: 오늘·테마 상세·관심 첫 수직 통합 — fixture adapter를 실제 API로 교체 (이 세션은 이 작업만 수행)

저장소: C:\dayjaview (main 브랜치)

## 필독
1. docs/implementation_roadmap.md 단계 3 — 통합 범위·완료 체크
2. contracts/ — 계약 (프론트·백 어느 쪽이 계약과 다르면 계약 쪽이 맞다)
3. frontend/src/api/ 의 client interface (C2), backend API (B4), realtime 엔진 (B3)

## 목표
- frontend real API adapter 구현: 환경변수로 mock/real 전환, 같은 컴포넌트가 두 adapter를 그대로 사용
- 오늘 화면: 초기 REST snapshot → WebSocket 증분 snapshot 반영, 오래된 sequence 무시, 재연결 시 전체 복구
- 테마 상세: themeId/eventId 라우팅, 현재 반응·Coverage·주도주 표시, 홈과 상세의 같은 필드 값 일치
- 관심: 로그인 상태에서 저장·해제·목록, 세션 만료·로그아웃 시 client cache와 snapshot 폐기
- LIVE·DELAYED·CLOSED 상태 연결, Coverage 부족은 "데이터 갱신 중" 표기 (0 표시 금지)
- 로컬 풀스택 기동 절차 1개로 정리: compose.dev(pg·redis) + realtime-worker(리플레이 20배속) + api + frontend dev
  → 리플레이 재생으로 오늘 화면 숫자가 실제로 변하는 것을 확인
- Playwright E2E 1본: 로그인(dev fake)→오늘 화면 숫자 갱신→상세 이동→관심 저장→새로고침 후 유지
- mock 화면과 real 화면의 시각 결과 비교 (동일 컴포넌트 사용 확인)

## 금지
- 이 세션은 위 작업만. 트리맵·뉴스·장후는 다른 세션 몫.
- 계약 변경 금지 (불일치 발견 시 구현을 계약에 맞추고, 계약 자체 결함만 BACKLOG 기록)
- scripts/, tests/(루트), data/(쓰기), logs/, .env* 수정·커밋 금지.

## 완료 기준
- E2E 통과 + 리플레이 재생 중 화면 갱신을 확인한 기록 (스크린샷 또는 로그)
- 로드맵 단계 3 체크박스 중 리플레이로 증명 가능한 항목 갱신 (실장 P95 수치 등 라이브 전용 항목은 남겨둔다)

## 마무리
git pull --rebase origin main → 커밋 → push
커밋 메시지 예: feat: first vertical integration of today/detail/saved over replay
```

---

## 세션 C4 — 인사이트 트리맵 [Codex, sol xhigh]

```text
# 작업: 인사이트 실시간 트리맵 구현 (이 세션은 이 작업만 수행)

저장소: C:\dayjaview (main 브랜치)

## 필독
1. docs/realtime_theme_treemap_implementation_plan.md — 단일 기준 문서
2. docs/implementation_roadmap.md 단계 5 — 완료 체크
3. contracts/asyncapi.yaml 의 theme_treemap_snapshot, frontend WS 연결부 (V1 산출)

## 목표
- InsightScreen placeholder를 실제 트리맵으로 교체: Core Coverage 통과 상승 테마 상위 12개,
  weightedReturn 단일 지표로 면적·색상 (지표 토글 없음)
- 500ms 값 반영·최대 1초 레이아웃 갱신, keyed DOM (테마별 안정 key), reduced motion 지원
- stale sequence 무시, 재연결, CLOSED 시 애니메이션 정지·최종 snapshot 고정
- 타일 클릭·Enter·Space → 동일 Event의 테마 상세 이동
- Coverage 미달 테마가 0% 타일로 나오지 않음
- 백엔드 treemap topic이 B3 산출과 다르면 프론트를 계약에 맞추고 차이만 BACKLOG 기록
- 테스트: 오늘 화면과 동일 테마의 수익률 일치 검사, 리플레이 재생으로 타일 갱신 확인

## 금지
- 이 세션은 위 작업만. scripts/, tests/(루트), data/, logs/, .env* 수정·커밋 금지.
- d3 전체 등 무거운 의존성 지양 — treemap 레이아웃 계산만 가볍게.

## 완료 기준
- 리플레이 재생에서 트리맵 숫자 1초 이내 반영 확인, build·test 통과

## 마무리
git pull --rebase origin main → 커밋 → push
커밋 메시지 예: feat(insight): realtime treemap driven by theme_treemap_snapshot
```

---

## 세션 B5 — 장후 확정 + 운영자 파이프라인 [Claude, fable5 max]

```text
# 작업: 장 마감 CLOSED 전환·인포스탁 장후 확정·운영자 검수 파이프라인 (이 세션은 이 작업만 수행)

저장소: C:\dayjaview (main 브랜치)

## 필독
1. docs/implementation_roadmap.md 단계 6 — 전체 요구와 완료 체크
2. docs/infostock_db_implementation_plan.md — 확정 데이터 취급
3. scripts/collect_infostock.py — 인포스탁 수집·세션 관리 로직 참고 (수정 금지, backend 모듈로 새로 구현)
4. docs/system_architecture.md — AUTH_REQUIRED 상태와 운영자 refresh 흐름

## 목표
- scheduler: 장 마감 시 활성 Event CLOSED 전환 + 장중 최종 지표 고정 저장
- infostock-worker: 장후 업데이트 수집 잡 (Playwright 로그인 세션은 암호화 storage state 재사용,
  만료 시 자동 재로그인 반복 금지 → AUTH_REQUIRED 전환, 운영자 refresh 후 다음 예약 실행에서 복구)
- 장중 임시 분류 ↔ 장후 확정 분류 매칭: 같은 Event ID 유지, 이름·themeId 변경 이력 저장,
  장중 기록은 절대 삭제·덮어쓰기 금지
- 매칭 실패 → UNMATCHED (자동 통계 제외) → 운영자 검수 대기열
- /v1/operator: 수집 상태, 작업 목록, retry/resume (idempotent — 중복 요청이 같은 작업을 두 번 실행하지 않음),
  분류 수정·병합·제외 (reason·revision·audit 필수)
- C3의 OperatorScreen fixture를 실제 operator API로 연결
- 테스트: Event ID 연속성, 이력 보존, UNMATCHED 처리, retry idempotency.
  실제 인포스탁 호출 없이 저장된 원본(fixture)으로 매칭 로직 검증

## 금지
- 이 세션은 위 작업만. 실제 인포스탁 라이브 로그인은 수행하지 않는다 (운영자 bootstrap은 사용자가 D2 이후 직접).
- scripts/, tests/(루트), data/(쓰기), logs/, .env* 수정·커밋 금지.

## 완료 기준
- 장후 매칭 시나리오 테스트(확정·변경·UNMATCHED) 통과, 운영자 화면에서 대기열·retry 동작 확인

## 마무리
git pull --rebase origin main → 커밋 → push
커밋 메시지 예: feat(post-market): infostock reconciliation with operator review queue
```

---

## 세션 B6 — 뉴스 수집·근거 파이프라인 [Claude, opus4.8 max]

```text
# 작업: 뉴스 수집·중복 제거·테마 매칭·근거 상태·LLM 구조화 게이트 (이 세션은 이 작업만 수행)

저장소: C:\dayjaview (main 브랜치)

## 필독
1. docs/implementation_roadmap.md 단계 4 — 전체 요구·화면 상태·완료 체크
2. docs/PRD.md — "근거 없으면 원인 생성 금지" 원칙
3. contracts/ evidence 관련 schema·fixtures

## 목표
- news-worker: 허용 RSS + NAVER API HUB polling (키는 env, 없으면 저장된 fixture 기사로 동작하는 dev 모드)
- 중복 제거(URL·제목·매체·발행 시각), 뉴스 저장소와 cursor, 원문 링크·매체·발행 시각 보존
- Entity 추출(종목·기업·정책·기술 — 규칙·사전 기반 최소 구현), 테마↔기사 양방향 매칭
  (테마 상태 변화 시 로컬 조회 + 새 기사 저장 시 활성 테마 역매칭)
- 근거 상태 기계: 상승 이유 확인 중 / 뉴스 기반 추정 / 복수 뉴스 확인 / 확인된 신규 소재 없음 / 기존 소재 재부각 / 인포스탁 기준 확정
- LLM 구조화: 관련성 기준 통과 기사만, 근거 0건이면 LLM 호출 자체 금지, LLM 외부 검색 금지,
  모델·프롬프트·입력 기사 버전 저장. Anthropic API(claude) 사용, 키 없으면 구조화 스킵하고 상태만 정확히
- 수집 장애와 "실제 기사 없음"의 내부 구분, 미래 기사 소급 금지 (publishedAt > asOf 차단)
- evidence API를 실데이터로 채우고 프론트 상세 화면의 근거 섹션 연결
- 테스트: 근거 없는 원인 생성 0건, 중복 저장 0건, 소급 차단

## 금지
- 이 세션은 위 작업만. scripts/, tests/(루트), data/(쓰기), logs/, .env* 수정·커밋 금지.
- 크롤링 금지 — 허용된 RSS·API만 (이용약관 원칙).

## 완료 기준
- fixture 기사 기반 파이프라인 e2e 테스트 통과, 화면 근거 상태가 API 상태와 일치

## 마무리
git pull --rebase origin main → 커밋 → push
커밋 메시지 예: feat(news): evidence pipeline with strict no-evidence-no-cause gate
```

---

## 세션 V2 — 자동 검증 스위트 + CI [Claude, opus4.8 max]

```text
# 작업: 로드맵 단계 9의 자동 검증 항목 구축 + GitHub Actions CI (이 세션은 이 작업만 수행)

저장소: C:\dayjaview (main 브랜치)

## 필독
1. docs/implementation_roadmap.md 단계 9 "자동 검증" 절 — 이 목록이 범위 그 자체다
2. 기존 backend/frontend 테스트 구조

## 목표 (이미 있는 것은 묶고, 없는 것만 추가)
- REST OpenAPI·WS AsyncAPI contract test 일괄 실행 태스크
- 핵심 계산 유닛 + 미래 정보 누수 테스트 (publishedAt/asOf, point-in-time 위반 검사)
- 저장 체결 replay 회귀: 2026-08-14 리플레이 짧은 구간의 결과 해시 고정 테스트
- WebSocket 재연결·sequence 테스트
- 프론트 컴포넌트 테스트 + E2E 핵심 여정(V1 것 확장: 관심 idempotency·소유권 격리·계정 삭제)
- 접근성 자동 검사(axe) 핵심 화면
- 의존성 취약점 점검(npm audit, uv 기반 pip-audit) + secret scan(gitleaks) — 결과 기록, 차단 기준은 high 이상
- GitHub Actions: PR·push 시 contracts 검증, backend 테스트, frontend build·test 실행
  (리플레이 데이터가 필요한 테스트는 로컬 전용 마커로 분리해 CI에서 skip)
- 한 번에 전부 도는 로컬 명령 1개 (예: make verify 또는 스크립트)

## 금지
- 이 세션은 위 작업만. 새 기능 구현 금지. 실패를 감추는 skip 금지 (로컬 전용 마커 제외).
- scripts/, tests/(루트), data/, logs/, .env* 수정·커밋 금지.

## 완료 기준
- 로컬 verify 전체 green, CI 워크플로우가 push에서 green

## 마무리
git pull --rebase origin main → 커밋 → push
커밋 메시지 예: test: full verification suite and CI per roadmap stage 9
```

---

## 세션 D1 — 배포 준비 [Claude, opus4.8 max]

```text
# 작업: OCI·Vercel 배포 산출물 준비 (파일·스크립트·runbook, 실배포는 하지 않음) (이 세션은 이 작업만 수행)

저장소: C:\dayjaview (main 브랜치)

## 필독
1. docs/adr/009-oci-initial-deployment.md, docs/adr/011-vercel-oci-split-deployment.md — 배치·보안 결정
2. docs/system_architecture.md §13 — Vercel+OCI 경계, 첫 배포 게이트
3. docs/implementation_roadmap.md 단계 2 "배포 기반" 절

## 목표 (전부 레포 안의 파일·문서로 완결, 원격 접속 없음)
- infra/compose.staging.yml, infra/compose.production.yml: api·realtime-worker·news-worker·infostock-worker·scheduler·
  postgres·redis·caddy. staging/production의 project 이름·network·volume·포트·credential 완전 분리,
  restart policy·health check·resource limit·로그 설정 포함
- infra/Caddyfile: api.dayjaview.duckdns.org TLS(ACME)·reverse proxy·보안 헤더
- frontend vercel.json: /api/* → https://api.dayjaview.duckdns.org external rewrite, SPA fallback
- secret 주입 규약: Git 밖 root 소유 0600 env 파일 경로 규약 + .env.production.example (실값 금지)
- 스크립트: deploy(이미지 pull·up·health 확인), rollback(직전 태그 복귀), backup(pg_dump + 인포스탁 storage state 암호화 백업),
  restore — 전부 멱등하고 실패 시 중단
- linux/arm64 이미지 빌드 검증: docker buildx로 arm64 빌드가 성공하는지 로컬 확인 (qemu 필요 시 설정 문서화)
- docs/runbook.md: 재부팅 복구, 장애 대응, 인포스탁 AUTH_REQUIRED 재인증(SSH tunnel), rollback 절차
- GitHub Actions에 이미지 빌드(arm64) job 추가 (push는 자유, 배포 트리거는 수동)

## 금지
- 이 세션은 위 작업만. OCI SSH 접속·Vercel 계정 연결 등 원격 실행 금지 (그건 D2에서 사용자와 함께).
- scripts/, tests/(루트), data/, logs/, .env* 수정·커밋 금지. 실값 secret 커밋 금지.

## 완료 기준
- docker compose config 검증 통과 (양쪽 파일), arm64 빌드 성공, runbook 존재

## 마무리
git pull --rebase origin main → 커밋 → push
커밋 메시지 예: feat(infra): staging/production compose, caddy, vercel rewrite, runbooks
```

---

## 세션 D2 — OCI 스테이징 실배포 + Vercel [Claude, fable5 max] ⚠️ 사용자 참관 권장

```text
# 작업: OCI VM 정리·보안 baseline·스테이징 배포·Vercel preview 연결 (이 세션은 이 작업만 수행)
# 주의: 원격 VM 상태를 바꾸는 작업이다. 파괴적 정리 단계는 실행 전에 목록을 보여주고 사용자 확인을 받는다.

저장소: C:\dayjaview (main 브랜치). OCI VM: ubuntu@168.107.25.213 (SSH key는 운영자 단말에만 존재)

## 필독
1. docs/adr/009-oci-initial-deployment.md — 잔존 항목 점검·정리 체크리스트와 보안 baseline
2. docs/adr/011-vercel-oci-split-deployment.md — TLS·rewrite·OAuth callback·WSS ticket 검증 조건
3. infra/ (D1 산출)와 docs/runbook.md

## 목표 (순서 엄수)
1. SSH 접속 → 이전 프로젝트 잔존물 조사: systemd service, container, image, volume, cron, 파일.
   → 발견 목록을 먼저 출력하고 사용자 확인 후 정리 (백업 필요한 것은 tar로 보존 후 제거)
2. 보안 baseline: apt 보안 업데이트, SSH key-only 확인, ufw(22 제한, 80/443 오픈), fail2ban
3. Docker Engine·Compose 설치, staging용 network·volume 생성, 0600 env 파일 배치 (값은 사용자에게 요청)
4. 이미지 배포(레지스트리 또는 VM 빌드) → compose.staging up → /api/health 200 확인
5. Caddy 기동 → api.dayjaview.duckdns.org ACME 인증서 발급·자동 갱신 확인
6. Vercel: frontend를 preview로 배포(vercel CLI, 사용자 로그인 필요), /api/* rewrite로 staging API 도달 확인
7. Google OAuth callback 실키 연결 → 로그인 왕복 확인 (키는 사용자가 제공)
8. WSS 직결 + ticket 인증 확인, 재부팅 시험(sudo reboot 후 자동 기동·상태 복원)
9. 결과를 ADR-009·011 체크박스와 로드맵 단계 2 배포 항목에 증거와 함께 기록

## 금지
- production 공개 배포 금지 (첫 배포 게이트: staging·preview까지만 — 로드맵 원칙 10)
- private key를 VM·레포로 복사 금지. secret을 셸 히스토리·로그에 남기지 않기
- 이 PC의 scripts/, data/ 등 리플레이 자산 수정 금지

## 완료 기준
- https://api.dayjaview.duckdns.org/api/health 가 유효한 TLS로 200
- Vercel preview에서 로그인→오늘 화면(리플레이 또는 대기 상태)이 열림
- 재부팅 후 자동 복구 확인 기록

## 마무리
git pull --rebase origin main → 문서 체크·기록 커밋 → push
커밋 메시지 예: ops: OCI staging deployment with TLS, OAuth, WSS ticket verified
```

---

## 4. 남는 것 (이 팩 이후, 사용자와 함께)

1. **다음 거래일(월 08-17) 라이브 검증**: Market Gateway를 키움 라이브로 전환, 실장 P95 목표 측정 — 리플레이와 동일 인터페이스라 전환 코드는 이미 있음.
2. 인포스탁 운영자 bootstrap (최초 수동 로그인 1회, staging에서).
3. 단계 7 온톨로지 연구 트랙 (블라인드 평가 등 사람 필요) → 통과 시 유사사례 flag 해제(단계 8).
4. 단계 10 shadow 운영 → production 공개.
