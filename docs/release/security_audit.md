# DAYJAVIEW 보안 점검 (F-23)

- **점검일**: 2026-08-16 · 기준 commit `d53793b`
- **범위**: auth·권한·secret·입력·의존성 경계. `apps/api`, `apps/web`, `apps/worker-*`, `packages/identity`, `packages/news`, `packages/infostock`, `packages/adapters`, `packages/reference-data`, `packages/operator`, `infra/**`
- **원칙**: 제품 파일은 고치지 않았다. 재현 가능한 finding과 그것을 고정하는 테스트만 남긴다. **수리는 별도 작업이다.**
- **재현 테스트**: `tests/security/**` (11개, `uv run pytest -q` 510 passed·8 skipped)
- **주의**: `tests/security/**`의 테스트는 이제 **수리된 동작을 고정한다.** 감사 시점에는 취약한 동작을 고정했고, 수리 작업에서 함께 바꿨다.
- **정정(2026-08-16)**: 최초판이 발견 1을 "중간"으로 올린 근거는 "React는 `href`의 scheme을 검사하지 않는다"였는데 **사실이 아니었다.** 이 저장소의 react-dom 19.2.8은 렌더 시점에 `javascript:` href를 차단한다(1번 참조). 심각도를 낮음으로 내렸다.
- **추가(2026-08-16)**: 최초판이 미실시로 남긴 의존성 알려진 취약점 조회(`pnpm audit`·`pip-audit`)를 실행해 13번으로 기록했다. 최종 **13건 — 중간 1 · 낮음 6 · 정보성 6.**

---

## 발견 요약

번호는 발견 순서를 그대로 둔 고정 식별자이고, 표만 심각도 순으로 정렬했다.

| # | 심각도 | 항목 | 재현 |
|---|---|---|---|
| 2 | 중간 | OpenDART API 키가 URL 쿼리로 나가고 예외 체인에 남는다 | `test_collector_credential_exposure.py` |
| 1 | 낮음 | 근거 기사 URL의 scheme이 http(s)로 제한되지 않는다 | `test_evidence_url_scheme.py` |
| 3 | 낮음 | 인증 진입점에 요청 한도가 없고 만료 레코드를 지우지 않는다 | `test_auth_endpoint_abuse.py` |
| 4 | 낮음 | 웹 정적 호스트에 보안 헤더 설정이 없다 | 코드 부재 |
| 5 | 낮음 | OpenDART ZIP을 크기 상한 없이 압축해제한다 | 코드 확인 |
| 6 | 낮음 | 외부 응답 본문에 크기 상한이 없다 | 코드 확인 |
| 7 | 낮음 | 인포스탁 `urlopen`이 리다이렉트를 추종한다 | 코드 확인 |
| 8 | 정보성 | 배포 env 계약이 코드가 읽지 않는 secret을 필수로 선언한다 | `test_production_secret_wiring.py` |
| 9 | 정보성 | 커서 서명 키가 프로세스마다 무작위다 | `test_production_secret_wiring.py` |
| 10 | 정보성 | `_slug()`가 `..`를 무력화하지 않는다 (현재 악용 불가) | 코드 확인 |
| 11 | 정보성 | `/api/health`가 무인증이다 | 코드 확인 |
| 12 | 정보성 | 운영자 repo만 `returnTo`를 내부에서 정화하지 않는다 | 코드 확인 |
| 13 | 정보성 | 고정된 `pytest` 버전에 알려진 취약점이 있다 (개발 전용) | `pip-audit` 재실행 |

---

## 수리 상태 (2026-08-16)

감사 뒤 **별도 수리 작업에서 12건을 고쳤다.** 아래 각 절의 본문은 *수리 전 상태* 설명이고, 무엇을 어떻게 고쳤는지는 이 표가 기준이다.

| # | 상태 | 수리 |
|---|---|---|
| 1 | 수리 | `canonical_url`이 `http`/`https`만 통과시킨다. 오염된 항목은 예외가 아니라 `INVALID_URL` 거부 사유가 되어 배치 전체가 죽지 않는다 |
| 2 | 수리 | OpenDART 경로가 httpx 상태 예외를 물고 가지 않는다. 상태코드만 담은 예외를 새로 만든다(체인 없음) |
| 3 | 수리 | `/auth/*`에 클라이언트 주소 단위 한도(5분 20회 → 429 `RATE_LIMITED`). 만료 state·session·ticket은 로그인 시작마다 `purge_expired`로 지운다 |
| 4 | 수리 | `apps/web/vercel.json`에 CSP·HSTS·`X-Frame-Options: DENY`·nosniff·`Referrer-Policy` |
| 5 | 수리 | ZIP 해제에 상한 64MiB(선언 크기와 실제 읽은 양을 모두 검사) |
| 6 | 수리 | 외부 응답을 스트리밍으로 읽고 32MiB를 넘으면 끊는다(reference-data·news·infostock) |
| 7 | 수리 | 인포스탁 `urlopen`이 리다이렉트를 거부한다 |
| 8 | 수리 | `SESSION_SIGNING_SECRET`을 커서 서명 키로 연결했다. 코드가 안 읽는 나머지는 필수 선언을 내렸다 |
| 9 | 수리 | 커서 서명 키를 env에서 주입한다. 공유 저장소(`DATABASE_URL`)인데 키가 없으면 기동하지 않는다 |
| 10 | 수리 | `_slug()`가 `.`을 남기지 않아 `..`가 생기지 않는다 |
| 11 | 유지 | 표준 health endpoint 범위이고 secret이 없어 바꾸지 않았다 |
| 12 | 수리 | 운영자 repo가 `safeReturnTo`를 내부에서 적용한다 |
| 13 | 수리 | `pytest` 9.0.3으로 상향. `pip-audit` 재실행 결과 **0건** |

- **수리 중 새로 드러난 것**: 계약이 production 필수로 선언한 값 중 `REDIS_URL`·`INFOSTOCK_SESSION_STATE_PATH`도 코드가 전혀 읽지 않았다(Redis는 저장소 어디에도 없다). 8번과 같은 성격이라 함께 필수 선언을 내렸고, "production 필수 = 코드가 읽는다"를 테스트로 고정했다.
- **검증**: `uv run pytest -q` 518 passed·8 skipped · 웹 lint·typecheck·test(83)·build 통과.
- **남은 것**: `/api/*` Vercel rewrite 설정은 넣지 않았다. 배포 조립이라 F-25에서 같은 파일에 붙인다.

---

## 1. 근거 기사 URL의 scheme이 제한되지 않는다 (낮음)

- **위치**: `packages/news/normalize.py:16-19` (수집 검증) → `packages/news/ingestion.py:128` (저장) → `packages/catalyst/projection.py:24` (API 투영) → `apps/web/src/pages/ThemeDetailPage.tsx:134` (렌더링)
- **문제**: `canonical_url()`은 "절대 주소인가"(scheme과 netloc이 있는가)만 본다. scheme 허용목록이 없어 `javascript://host/%0a…`와 `data://host/…`가 통과한다. 실행으로 확인했다.
  ```
  'javascript://dayjaview.test/%0aalert(document.cookie)' -> 그대로 통과
  'javascript:alert(1)'                                   -> 거부(netloc 없음)
  ```
  게다가 `ingestion.py:128`이 저장하는 값은 정규화된 `canonical`이 아니라 공급원이 준 **원문 그대로**(`raw.original_url`)다. 이 값이 `originalUrl`로 API에 실려 웹의 **유일한 동적 `href`**에 들어간다.
- **차단 지점**: 이 저장소의 react-dom 19.2.8이 렌더 시점에 막는다. `sanitizeURL()`이 `/^[\u0000-\u001F ]*j[\r\n\t]*a…t[\r\n\t]*:/i`로 검사해 제어문자·개행을 끼워 넣은 변형까지 잡아내고, 걸리면 `href`를 오류를 던지는 스텁으로 바꿔치기한다. 개발 빌드와 운영 빌드 양쪽에 들어 있다(`react-dom/cjs/react-dom-client.production.js` 확인). **따라서 지금 웹에서는 위 payload를 눌러도 스크립트가 실행되지 않는다.** `rel="noreferrer"`도 붙어 있어 리퍼러 유출·reverse tabnabbing은 별도로 막힌다.
- **그래도 남는 것**: 검증되지 않은 scheme이 그대로 저장되고 API 응답으로 나간다. 방어가 프레임워크 한 겹뿐이라 ⑴ React를 거치지 않는 소비자(메일 발송, `window.open`, 다른 클라이언트, 운영자 도구)가 생기면 바로 노출되고, ⑵ React가 막는 것은 `javascript:` 하나뿐이라 `data:`·`vbscript:`는 그대로 `href`에 들어간다. 저장 데이터 품질 문제이기도 하다.
- **악용 조건**: 등록된 RSS 매체가 침해되거나 오염된 항목을 내보내면 `<link>javascript://x/%0a…</link>` 한 줄이 그대로 저장된다. 현재 화면에서 실행되지는 않는다.
- **계약 쪽**: `contracts/schemas/stage0.schema.json:486`이 `{"type": "string", "format": "uri"}`인데, `format`은 기본적으로 주석이고 위 payload는 문법상 유효한 URI라 어느 쪽으로도 막지 못한다.
- **권고(별도 작업)**: 수집 경계(`canonical_url`)에서 scheme을 `http`/`https`로 제한한다. 이게 본질이고, 웹 렌더링 검사는 React가 이미 `javascript:`를 막으므로 선택이다.

## 2. OpenDART API 키가 URL 쿼리로 나가고 예외 체인에 남는다 (중간)

- **위치**: `packages/reference-data/reference_data/adapters.py:227`, `:280` (키를 `params`로 전달) / `:230`, `:85` (`raise_for_status()` → `raise … from exc`)
- **문제**: `OPENDART_API_KEY`가 `?crtfc_key=…`로 URL에 실린다. httpx가 만드는 `HTTPStatusError` 메시지에는 **키가 포함된 전체 URL**이 들어가고, `SourceTransportError(…) from exc`로 `__cause__`에 보존된다. 상위 메시지(`endpoint`)에는 키가 없지만 traceback을 통째로 로깅하면 그대로 찍힌다.
- **대조**: 같은 파일의 KRX 어댑터는 `headers={"AUTH_KEY": …}`(`:170`)로 올바르게 보낸다. OpenDART만 쿼리 방식이다.
- **악용 조건**: OpenDART가 4xx/5xx를 반환한 상태에서 상위가 예외 체인을 로깅하거나 미처리 예외가 기본 excepthook로 stderr에 찍히면 키가 노출된다. URL 쿼리 secret은 중간 프록시·게이트웨이 access log에도 남는다.
- **현재 완화**: worker 진입점은 `str(exc)`(최상위 메시지)만 출력하므로 지금 경로에서 직접 유출되지는 않는다. 키가 manifest·snapshot 파일에 저장되지도 않는다.
- **권고(별도 작업)**: OpenDART도 헤더 전달로 바꾸거나, 불가능하면 예외를 다시 던질 때 `from None`으로 체인을 끊는다.

## 3. 인증 진입점에 요청 한도가 없고 만료 레코드를 지우지 않는다 (낮음)

- **위치**: `apps/api/app.py:123` (`GET /auth/google`, 무인증·무CSRF) → `packages/identity/service.py:128-150` (`store_oauth_state`)
- **문제**: 저장소 전체에 HTTP 요청 한도(rate limit)가 없다. `/auth/google`은 쿠키 없이 호출할 수 있고 호출마다 `identity.oauth_states` 행을 하나씩 만든다. 축출도 없다 — 40번 호출 뒤에도 첫 state가 그대로 소비 가능함을 테스트로 확인했다.
- **더불어**: 만료·폐기된 레코드를 지우는 경로가 코드 어디에도 없다(`identity.oauth_states`·`identity.sessions`·`identity.realtime_tickets`). 세션은 TTL이 지나면 `revoked_at`만 찍히고 행은 남아, 사용자마다 로그인 횟수만큼 영구히 쌓인다.
- **악용 조건**: 외부인이 요청 수만큼 DB 행 수를 늘릴 수 있다. 인증 우회는 아니고 저장소 고갈(가용성) 문제다.
- **권고(별도 작업)**: 인증 진입점 rate limit + 만료 레코드 정리 작업. 인덱스(`…_expiry_idx`)는 이미 정리 쿼리를 염두에 두고 만들어져 있다.

## 4. 웹 정적 호스트에 보안 헤더 설정이 없다 (낮음)

- **위치**: 저장소에 `vercel.json`·`_headers`·정적 호스트 헤더 설정이 **없다**.
- **문제**: API 응답에는 `X-Content-Type-Options`·`Referrer-Policy`·`Cache-Control: private, no-store`가 붙지만(`apps/api/http.py:107-112`) 그건 API origin 응답이다. SPA 문서(`dayjaview.vercel.app`)에는 CSP·HSTS·X-Frame-Options/`frame-ancestors`가 없다.
- **영향**: 운영자 콘솔(`operator.html`)에 clickjacking 방어가 없고, 발견 1에 대한 CSP 심층방어가 없다.
- **주의**: Vercel 대시보드에서 헤더를 설정했는지는 코드로 확인할 수 없다. "저장소에 설정이 없다"만 사실로 기록한다.

## 5~7. 외부 응답 처리 상한 (낮음)

- **ZIP 폭탄**: `adapters.py:231-232` — `response.content`와 `archive.read()` 모두 크기 상한이 없다. 소량 압축 → 거대 해제면 메모리 고갈. 항목명은 고정(`CORPCODE.xml`)이라 zip-slip은 없다.
- **응답 크기**: `packages/infostock/daily_api.py:122`(`response.read()`), `packages/news/live.py:113`(`ElementTree.fromstring(response.content)`), reference-data의 `response.json()` — 모두 본문 전량을 메모리에 올린다.
- **리다이렉트 추종**: `daily_api.py:121`의 `urllib.request.urlopen`은 3xx를 기본 추종한다. endpoint가 고정 상수라 고전적 SSRF는 아니지만, 인포스탁 서버가 침해되면 리다이렉트로 유도될 수 있다. 나머지 수집기는 httpx 기본값(`follow_redirects=False`)이라 추종하지 않는다.
- 셋 다 TLS + 신뢰 상류 전제라 낮음이다.

## 8~9. 배포 조립 (정보성)

- **계약-코드 드리프트**: `infra/deployment/environment.contract.json`이 `SESSION_SIGNING_SECRET`·`APPLICATION_ENCRYPTION_KEY`를 staging·production **필수**로 선언하는데 코드는 둘 다 한 번도 읽지 않는다. 두 값을 넣고 조립해도 결과가 동일함을 테스트로 확인했다. 운영자가 이 값을 회전해도 보호되는 대상이 없다는 뜻이라, 잘못된 안심을 준다. `.env.example:71`의 `INFOSTOCK_SESSION_STATE_PATH=….enc`도 암호화를 암시하지만 저장소에 암호화 코드가 없다(`Fernet`·`cryptography`·AES 전부 부재).
- **커서 서명 키**: `packages/identity/service.py:126`이 `cursor_secret or secrets.token_bytes(32)`라 env에서 주입할 경로가 없고 프로세스마다 새로 생긴다. 같은 저장소를 보는 인스턴스 2개를 만들면 한쪽이 발급한 커서를 다른 쪽이 `InvalidCursor`로 거부한다. **fail-closed라 정보가 새지는 않지만**, F-25에서 API를 2개 이상 띄우거나 재기동하면 관심 목록 2페이지부터 실패한다.

## 10~12. 나머지 정보성

- `apps/worker-batch/reference-data/collect_daily.py:76` — `_slug()`가 `.`을 남겨 `..`가 살아남는다. 현재 `source_key` 입력은 전부 내부에서 검증된 값(고정 enum·8자리 숫자·choices)이라 **지금은 탈출 불가**다. 상류 검증에만 의존하는 심층방어 공백으로 기록한다.
- `apps/api/serve.py:319-348` — `/api/health`가 무인증으로 의존성 상태·마이그레이션 상태·`liveBlockersPreserved`를 준다. 표준적인 health endpoint 범위이고 secret은 없다.
- `apps/web/src/operator/operatorRepository.ts:146-148` — 사용자 repo(`productionRepository.ts:286`)는 `safeReturnTo()`를 내부에서 적용하는데 운영자 repo는 인자를 그대로 쓴다. 유일한 호출부(`OperatorConsole.tsx:79`)가 이미 정화하므로 현재는 안전하고, 호출부 신뢰에 의존하는 비대칭만 남는다.

## 13. 의존성 알려진 취약점 (정보성)

2026-08-16에 실제로 조회했다(최초판에서 미실시로 남겨 둔 항목).

- **웹**: `pnpm audit`을 `apps/web`에서 실행 — **0건**(info·low·moderate·high·critical 전부 0). dev 포함 267개 패키지를 봤다(`--prod`는 53개라 dev가 빠지지 않았음을 확인).
- **파이썬**: `uv export --all-groups`로 뽑은 잠금 목록 109줄을 `pip-audit`에 넣었다 — **1건**. `pytest 8.4.1` · `PYSEC-2026-1845` · 수정본 `9.0.3`. UNIX에서 `/tmp/pytest-of-{user}` 이름 규칙에 의존해, 같은 호스트의 다른 로컬 사용자가 서비스 거부나 권한 상승을 시도할 수 있다.
- **영향**: 제품에 실리지 않는 개발·테스트 전용 의존성이고(`pyproject.toml`의 `[dependency-groups] dev`), UNIX 한정이며 같은 머신에 다른 로컬 사용자가 있어야 한다. 배포물과 런타임에는 경로가 없어 정보성으로 둔다.
- **권고(별도 작업)**: `pytest`를 9.0.3 이상으로 올린다. 8 → 9는 major 상향이라 테스트가 깨질 수 있어 F-23 범위 밖이다.
- **재현 테스트를 만들지 않았다.** 결과가 시점에 따라 바뀌고 조회에 네트워크가 필요해서, 테스트로 고정하면 불안정 테스트가 된다. 위 두 명령을 그대로 다시 돌리는 것이 재현 절차다.

---

## 방어가 확인된 항목

점검하면서 취약점을 찾지 못했고 근거를 확인한 것들이다.

**인증·세션·CSRF**
- OAuth state가 브라우저 nonce에 묶여 1회만 소비된다(`service.py:167-173`, `repository.py:101-116`). 원문 토큰은 저장하지 않고 SHA-256 해시만 쓴다.
- CSRF는 Origin 정확 일치 + double-submit + 서버 보관 해시 비교 3중이다(`service.py:502-530`). 비교는 전부 `hmac.compare_digest`.
- 쿠키는 `__Host-` 접두사 + `Secure` + `Path=/`, 세션은 `HttpOnly`·`SameSite=Lax`, CSRF는 `SameSite=Strict`(`cookies.py:6-53`).
- `returnTo`는 3중 디코딩 후 `//`·역슬래시·scheme·netloc·제어문자를 모두 거부한다(`security.py:57-99`).
- 계정 삭제는 최근 인증(10분)을 요구하고, 마이그레이션의 `ON DELETE CASCADE`로 세션·티켓·저장목록이 함께 지워진다(`0001_identity_library.sql:24,57,77,93`).

**권한 경계**
- 운영자 command는 role을 먼저 보고 CSRF를 나중에 본다 — 일반 사용자에게 운영자 surface의 존재가 CSRF 오류로 새지 않는다(`service.py:274-296`).
- 운영자 응답은 필드 allowlist 투영이고, 형식에 맞지 않으면 500으로 닫는다. `diagnostic_context`·`internal_context`·command reason 원문은 어떤 경로로도 나가지 않는다(`operator_boundary.py:388-468`).
- 실시간 WS는 Origin 정확 일치 + 1회용 ticket + 매 루프 세션 재확인으로, 세션이 폐기되면 즉시 닫힌다(`realtime.py:345-470`).

**입력 처리**
- 위험 함수가 저장소 전체에 **없다**: `pickle`·`marshal`·`eval`·`exec`·`os.system`·`subprocess`(제품 코드)·`yaml.load`·`lxml`·`verify=False`. XML은 stdlib `ElementTree`만 쓴다(외부 엔티티 미해석).
- 모든 SQL이 `%s` 파라미터화다(`identity`·`infostock`·`events`·`realtime` 저장소). f-string에 들어가는 것은 상수 컬럼 목록과 allowlist 검증된 테이블명뿐이다.
- 요청 본문 1MiB 상한, WS 메시지 64KiB 상한, 중복 header·중복 cookie·중복 query 거부(`http.py:30-81`, `realtime.py:593`).
- 경로 격리가 여러 겹이다: `_relative_file`의 resolve+부모검사+symlink 거부, fixture 로더의 `is_relative_to`, 파일명 `sha256` 치환.
- 키움 자격증명은 메모리 보관 + POST 본문 전송이라 URL·헤더·로그에 남지 않는다.

**웹**
- XSS 싱크가 하나도 없다: `dangerouslySetInnerHTML`·`innerHTML`·`document.write`·`eval`·`new Function` 전부 부재(전수 확인). 서버 문자열은 전부 JSX 텍스트 자식이라 React가 이스케이프한다.
- 세션 쿠키를 읽는 코드가 없다. `document.cookie`는 CSRF 쿠키를 읽는 데만 쓴다(`productionRepository.ts:60-67`).
- 클라이언트 노출 env는 `VITE_REALTIME_URL` 하나뿐이고 하드코딩 secret이 없다. 빌드 시 `scripts/assert-production-boundary.mjs`가 fixture 진입점을 dist에서 차단한다.
- 인라인 `style`에는 계산된 숫자만 들어간다(CSS 인젝션 경로 없음).

**공급망·설정**
- 파이썬 의존성이 전부 정확한 버전으로 고정돼 있다(`pyproject.toml:6-21`). 웹은 `pnpm-lock.yaml` 기준.
- `.env`·`.env.*`가 gitignore이고 추적되는 secret 파일이 없다(`.env.example` 제외).
- `tests/infra/test_secret_scan.py`가 `infra/**`에서 개인키·AWS 키·Google 키·DSN 문자열을 이미 막고 있다.

---

## 하지 않은 것

- **수리하지 않았다.** F-23의 범위는 감사이고, 로드맵이 "제품 파일 직접 수리 금지(수리는 별도 작업)"로 못박았다. 위 12건 중 어느 것도 고치지 않았다.
- 동적 침투 시험(실제 배포 대상에 대한 공격 시도)은 하지 않았다. 배포가 F-25이므로 대상이 아직 없다.
- 의존성 알려진 취약점 조회는 **2026-08-16에 실행했다**(13번). 최초판의 "돌리지 않았다"는 그때 상태였다.
