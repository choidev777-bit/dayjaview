# DAYJAVIEW UI 프로토타입 적용 계획

- 문서 버전: `0.2-draft`
- 문서 상태: 구현 전 초안 — 디자인·프론트·제품 공동 승인 필요
- 최종 수정일: 2026-08-14
- 디자인 원본: [nangom/dayjaview-prototype](https://github.com/nangom/dayjaview-prototype)
- 기준 커밋: [`65324878db4ef92bb29c7fce21e63a1031c3be17`](https://github.com/nangom/dayjaview-prototype/tree/65324878db4ef92bb29c7fce21e63a1031c3be17)
- 배포 시안: [dayjaview-prototype.vercel.app](https://dayjaview-prototype.vercel.app)
- 제품 기준: [PRD.md](./PRD.md)
- 화면 기준: [screen_spec.md](./screen_spec.md)
- 시스템 기준: [system_architecture.md](./system_architecture.md)
- 데이터 계약: [api_contract.md](./api_contract.md)
- 실행 순서: [implementation_roadmap.md](./implementation_roadmap.md)

---

## 0. 문서 목적과 권한

이 문서는 디자이너가 만든 React 시안의 시각 자산과 상호작용을 DAYJAVIEW 제품 화면에 적용하는 방법을 정의한다. 시안의 화면 구조·문구·하드코딩 수치를 제품 요구사항으로 승격하지 않는다.

이 문서가 답하는 질문은 다음과 같다.

1. 기준 시안에서 무엇을 유지·수정·폐기하는가?
2. 기존 6개 화면을 목표 정보 구조에 어떻게 대응시키는가?
3. 프론트엔드의 route·상태·컴포넌트 경계를 어떻게 나누는가?
4. 실시간·지연·장후·결측·게이트 상태를 어떻게 표현하는가?
5. 모바일·태블릿·데스크톱과 데모용 아이폰 프레임을 어떻게 분리하는가?
6. fixture 개발에서 실제 API 연결까지 어떤 순서로 진행하는가?

### 0.1 문서 우선순위

충돌 시 다음 순서를 따른다.

1. [PRD.md](./PRD.md)의 제품 범위·출시 게이트·금지사항
2. [system_architecture.md](./system_architecture.md)의 시스템 경계·상태 소유권
3. [screen_spec.md](./screen_spec.md)의 화면 구조·상태·표시 규칙
4. [api_contract.md](./api_contract.md)의 wire 의미·식별자·상태 계약
5. 이 문서의 적용·분해 순서
6. 디자인 원본의 현재 구현

시안과 기준 문서가 다르면 시안을 기준 문서에 맞춘다. 시안에 있는 기능이 PRD에서 후속 또는 조건부면 완성된 제품 기능처럼 노출하지 않는다.

### 0.2 이 문서가 결정하지 않는 것

- React/Vite 세부 버전과 상태 관리·query library
- 최종 breakpoint 값과 grid column 수
- 최종 로고 사용권·폰트 공급 방식
- API 경로와 payload의 기계 판독 형식
- 트리맵 rendering library

위 항목은 구현 전에 담당자와 ADR 또는 기술 결정 기록으로 확정한다.

---

## 1. 기준 시안 감사

### 1.1 확인한 범위

기준 커밋에서 확인된 구조는 다음과 같다.

```text
index.html              Vite 진입점, Pretendard CDN
src/main.jsx            React root
src/App.jsx             화면·mock data·상태·스타일이 모인 단일 class component
src/IOSDevice.jsx       393×852 아이폰 데모 프레임
src/css.js              CSS 문자열 → style object helper
src/index.css           전역 스타일·animation keyframes
public/                 로고 이미지
package.json            React 18.3, Vite 5.4 계열
```

현재 화면은 다음 6개다.

```text
splash
home
theme
cases
case
stats
```

`App.jsx`의 `state.screen`으로 화면을 전환하므로 실제 route가 없고 URL은 `/`에 머문다. `?screen=` query는 시안 직접 진입용이다. `themes`, `cases`, `memberRows`와 통계는 모두 하드코딩 mock이다.

### 1.2 확인된 시각 언어

- Pretendard 중심의 한국어 typography
- 밝은 중성 배경과 흰 card surface
- 큰 radius와 충분한 내부 여백
- 검정·짙은 ink 본문
- mint accent와 teal 보조색
- 국내 시장 관행에 맞춘 상승 red·하락 blue
- sheet·drawer·card·pill·segmented control 패턴
- 짧은 push·fade·sheet animation
- 로고 mark와 wordmark

### 1.3 확인된 구조적 한계

| 항목 | 현재 상태 | 제품 위험 |
|---|---|---|
| 화면 분리 | `App.jsx` 한 파일 | 병렬 개발·테스트·ownership 어려움 |
| navigation | `state.screen` | deep link·뒤로가기·복원 불가 |
| 데이터 | JSX 내부 hardcode | 실제 상태·결측·오류와 혼합 위험 |
| 스타일 | 대량 inline CSS 문자열 | token 일관성·반응형·접근성 유지 어려움 |
| 홈 | 자동 순환 wheel | 순위 비교·탐색·screen reader 사용성 저하 |
| desktop | 아이폰 frame 확대·축소 | 실제 desktop 정보 구조 부재 |
| 상태 | 성공 mock 중심 | LIVE·DELAYED·Coverage·오류 표현 부족 |
| 통계 | 예시 숫자와 graph | 검증되지 않은 결과가 제품 사실처럼 보일 위험 |
| 관심 기능 | quick action만 있고 기준 저장소·목록 화면 없음 | mutation 실패·계정 동기화·빈 상태를 처리하지 못할 위험 |

---

## 2. 적용 원칙

1. **시각 언어는 재사용하고 정보 구조는 재구축한다.**
2. **제품 route와 식별자는 표시명 대신 `themeId`, `eventId`, `matchedEventId`를 사용한다.**
3. **실시간 계산·분류·표시 문구의 의미는 서버 계약을 따른다.**
4. **프론트는 서버 수치를 재계산하거나 결측을 0으로 바꾸지 않는다.**
5. **성공 화면보다 상태 matrix를 먼저 구현한다.**
6. **유사사례는 온톨로지 재검증 게이트를 통과한 경우에만 사용자 route를 연다.**
7. **아이폰 mockup은 제품 layout이 아니라 demo wrapper다.**
8. **내부 모놀리스·마이크로서비스 선택은 UI 구조와 API client에 노출하지 않는다.**
9. **프로토타입의 가짜 수치와 실제 fixture를 명시적으로 분리한다.**
10. **접근성과 reduced motion을 초기 컴포넌트 조건으로 둔다.**

---

## 3. 유지·수정·폐기 결정

### 3.1 유지

| 자산 | 적용 방식 | 조건 |
|---|---|---|
| 로고 mark·wordmark | asset 후보로 보존 | 최종 파일·사용권·dark/light variant 확인 |
| Pretendard typography 방향 | design token으로 추출 | CDN 의존 여부와 self-host 정책 별도 결정 |
| 밝은 neutral surface | semantic color token으로 정리 | 명암비 검증 |
| 큰 radius·여백 리듬 | card·sheet·button token 후보 | 작은 화면 정보 밀도 검증 |
| mint·teal accent | 선택·강조·상태 보조에 사용 | 상승·하락 색과 의미 충돌 금지 |
| 상승 red·하락 blue | 숫자·보조 indicator에 사용 | 색상만으로 의미 전달 금지 |
| sheet·drawer·pill 패턴 | 공통 primitive로 재구현 | focus trap·keyboard·ARIA 포함 |
| 짧은 transition | motion token으로 추출 | `prefers-reduced-motion` 지원 |
| `100dvh`·safe-area 처리 아이디어 | 모바일 shell에 적용 | demo frame 코드와 분리 |

### 3.2 수정

| 현재 요소 | 변경 후 |
|---|---|
| 홈 자동 순환 wheel | 비교 가능한 세로 theme card 목록 |
| 단일 class state | route·server state·local UI state 분리 |
| inline CSS 문자열 | token + component style 경계 |
| `?screen=` 진입 | 실제 route와 browser history |
| 고정 393×852 layout | mobile-first responsive layout |
| 성공 mock 단일 상태 | loading·empty·delayed·degraded·error·closed 상태 |
| 테마 상세의 예시 통계 | 화면 명세 순서와 API availability에 따른 section |
| 관심 drawer·quick action | Google 계정 기반 저장 mutation과 `/saved` 목록 화면으로 교체 |
| fake toast | 실제 mutation 결과와 오류를 반영하는 feedback |
| 직접 그린 chart | 접근 가능한 chart primitive·table fallback |

### 3.3 폐기

- `App.jsx` 하나에 모든 화면을 유지하는 구조
- `state.screen` 기반 navigation
- 무한 자동 순환과 blur로 항목을 흐리는 ranking wheel
- 하드코딩된 테마·사례·종목·통계의 production bundle 포함
- 화면 표시명으로 route를 구성하는 방식
- desktop에서 제품 전체를 아이폰 frame 안에만 표시하는 방식
- 계산 근거 없는 평균·상승 비율·MDD·초과수익 graph
- 서버 mutation 없이 저장된 것처럼 보이는 관심 quick action과 제외된 알림 action
- 실제 데이터 장애를 빈 배열이나 0으로 치환하는 fallback

폐기는 디자인 취향 평가가 아니다. 제품 정보 구조·데이터 신뢰·접근성 기준과 맞지 않는 구현 형태를 제거한다는 뜻이다.

---

## 4. 기존 화면 → 목표 화면 매핑

| 시안 화면 | 목표 화면·처리 | 재사용 | 교체·추가 | 출시 상태 |
|---|---|---|---|---|
| `splash` | 선택적 초기 brand/loading | 로고·dark treatment | 고정 2.8초 제거, 데이터 loading과 분리 | 선택 |
| `home` | `오늘` | card surface·typography | wheel → theme card 목록, 시장 상태·기준 시각 | 핵심 |
| 없음 | `인사이트` | 색·card primitive | 실시간 상승률 treemap 신규 | 핵심 |
| `theme` | `테마 상세 > 요약` | header·section card·sheet | 이유·관심 공백·현재 움직임·주도주 순서 재구성 | 핵심 |
| `cases` | `테마 상세 > 유사사례` | row·기간 selector 일부 | 집계 분모·중앙값·filter·게이트 상태 | 조건부 |
| `case` | `과거 이벤트 상세` | 상세 hero·result card 일부 | 유사 근거·발생 전 정보·당시 주도주·고지 | 조건부 |
| `stats` | 독립 핵심 route로 승격하지 않음 | chart visual 일부 | 유사사례 상단 집계·계산 기준 sheet로 분산 | 조건부 |
| 없음 | 관심 | 공통 shell 신규 | 테마·종목·이벤트 저장 목록·빈 상태·mutation·계정 삭제 | 핵심 |
| 없음 | 테마/검색/히스토리/관련주 | 공통 shell만 | 별도 명세 후 구현 | 후속 |
| 없음 | 내부 운영자 콘솔 | 사용자 shell 재사용 금지 | 상태·작업·검수·audit 전용 shell 신규 | 내부 필수 |

### 4.1 `stats` 처리 원칙

시안의 `stats` 화면 전체를 그대로 유지하지 않는다.

- 표본 수·중앙값·상승 분자는 `유사사례` 상단 집계에 배치한다.
- 분포·변동성·제외 수는 검증된 데이터가 있고 화면 명세가 승인된 경우에만 추가한다.
- 계산 버전·출처·제외 기준은 `계산 기준` sheet에서 제공한다.
- 예측처럼 보이는 trend line은 검증된 관측 graph가 아니면 제거한다.

### 4.2 신규 `인사이트` 화면

프로토타입에는 대응 화면이 없다. 기존 card를 억지로 늘려 만들지 않는다.

- 상승률 단일 지표 treemap
- Core Coverage 통과한 `ACTIVE`·`WEAKENING` 테마만 표시
- size·color·label 규칙은 [screen_spec.md](./screen_spec.md)를 따른다.
- DOM 또는 접근 가능한 대체 목록을 제공한다.
- 선택 시 동일 `themeId`·`eventId`로 테마 상세에 이동한다.

---

## 5. 목표 route와 이동

경로 이름은 프론트 구현 승인 시 확정한다. 다음은 기준안이다.

| route 후보 | 화면 | 필수 식별자 | 상태 |
|---|---|---|---|
| `/today` | 오늘 | 없음 | 핵심 |
| `/insights` | 인사이트 | 없음 | 핵심 |
| `/saved` | 관심 | 현재 Google session | 핵심 |
| `/operator` | 내부 운영자 콘솔 | Google session + `OPERATOR` 역할 | 내부 필수, 사용자 navigation 비노출 |
| `/themes/:themeId/events/:eventId` | 테마 상세 > 요약 | `themeId`, `eventId` | 핵심 |
| `/themes/:themeId/events/:eventId/similar` | 테마 상세 > 유사사례 | `themeId`, `eventId` | 조건부 |
| `/events/:matchedEventId` | 과거 이벤트 상세 | `matchedEventId`, 현재 사건은 query 또는 navigation state | 조건부 |

`/`는 `/today`로 redirect한다. 표시명은 URL identifier로 사용하지 않는다.

### 5.1 이동 상태 보존

- 오늘 → 상세 → 뒤로: scroll 위치·선택 event 복원
- 인사이트 → 상세 → 뒤로: treemap viewport·선택 복원
- 요약 ↔ 유사사례: `themeId`, `eventId` 유지
- 유사사례 → 과거 이벤트: `matchedEventId`와 원래 `eventId` 구분
- 장후 분류 변경: 같은 `eventId`로 canonical route 유지, 최신 `themeId` 연결
- 공유 URL: server fetch만으로 화면 복원 가능해야 함

local state에만 식별자를 보관하지 않는다.

### 5.2 조건부 route

유사사례 관련 route는 다음 조건을 모두 만족할 때만 navigation에 노출한다.

```text
featureFlag enabled
approved retrieval artifact active
ontology validation gate passed
event reconciliation permits historical linking
API availability = AVAILABLE
```

게이트 미통과는 오류가 아니다. 사용자에게 잠긴 가짜 화면을 보여주지 않고 진입점 자체를 숨기는 것이 기본이다. 제한된 pilot 계정은 별도 entitlement로 통제한다.

### 5.3 운영자 route

`/operator`는 일반 AppShell·하단 navigation·검색·sitemap에서 노출하지 않는다. client에서 route를 숨기는 것만으로 권한을 대신하지 않고 모든 `/v1/operator` 요청을 서버에서 검사한다. `OPERATOR`가 아닌 session은 `403`으로 처리한다.

운영자 shell에는 작업 상태·검수·revision·audit만 구현한다. 서버 terminal, 파일 브라우저, 임의 SQL, secret·cookie·token·credential 원문 UI는 만들지 않는다. 인포스탁 수동 재인증은 public 화면에 embed하지 않고 OCI loopback + SSH tunnel runbook으로 연결한다.

---

## 6. 목표 프론트엔드 구조

제품 저장소와 library 선택 전의 논리 구조다.

```text
src/
├─ app/
│  ├─ router/
│  ├─ providers/
│  ├─ AppShell/
│  └─ featureFlags/
├─ pages/
│  ├─ TodayPage/
│  ├─ InsightsPage/
│  ├─ ThemeSummaryPage/
│  ├─ SimilarEventsPage/
│  └─ HistoricalEventPage/
├─ features/
│  ├─ market-session/
│  ├─ theme-ranking/
│  ├─ theme-treemap/
│  ├─ event-evidence/
│  ├─ event-summary/
│  └─ historical-matching/
├─ entities/
│  ├─ theme/
│  ├─ event/
│  ├─ stock/
│  └─ news/
├─ shared/
│  ├─ api/
│  ├─ realtime/
│  ├─ fixtures/
│  ├─ ui/
│  ├─ tokens/
│  ├─ formatting/
│  └─ testing/
└─ demo/
   └─ DeviceFrame/
```

폴더 이름은 강제가 아니다. 다음 경계는 강제한다.

- page는 route orchestration만 담당한다.
- feature가 domain UI와 interaction을 소유한다.
- API client가 wire response를 검증하고 view model로 변환한다.
- 표시 component가 fetch·WebSocket 연결을 직접 만들지 않는다.
- formatting은 수익률·날짜·분모 표현을 한 위치에서 처리한다.
- demo frame은 production shell에서 import하지 않는다.
- historical matching code는 feature flag 경계 밖으로 새지 않는다.

### 6.1 상태 소유권

| 상태 | 소유 위치 | 예 |
|---|---|---|
| URL 상태 | router | route ID, tab, filter, sort |
| 서버 조회 상태 | query cache | session, detail, evidence, history |
| 실시간 상태 | realtime store | ranking·treemap snapshot, sequence |
| 사용자 입력 | local UI | 열린 sheet, focus, 임시 filter |
| 제품 gate | server entitlement + feature flag | 유사사례 노출 |
| 계산·분류 | server | rank, return, Coverage, status |

프론트는 WebSocket snapshot의 `sequence`를 비교할 수 있지만 rank·Coverage·evidence 상태를 새로 계산하지 않는다.

### 6.2 컴포넌트 계층

#### 공통 primitive

```text
Button
IconButton
Card
Badge
StatusLabel
BottomSheet
Drawer
Tabs
Skeleton
EmptyState
ErrorState
Timestamp
DataStatusBar
MetricValue
RatioValue
SourceLink
```

#### domain component

```text
ThemeRankCard
ThemeTreemap
CoverageIndicator
CatalystSummary
EvidenceList
AttentionGapMetric
LeaderStockList
SimilarEventSummary
SimilarEventRow
OutcomeTable
CalculationBasisSheet
```

DOM 구조와 accessible name을 먼저 고정한 뒤 시안 스타일을 입힌다.

---

## 7. 디자인 토큰 추출

현재 raw color를 그대로 여러 component에 복사하지 않는다. 기준 커밋에서 후보를 추출하고 semantic token으로 정리한다.

### 7.1 색상 후보

| semantic token 후보 | 시안 후보값 | 용도 |
|---|---|---|
| `color.text.primary` | `#16160F` | 본문·제목 |
| `color.surface.canvas` | `#EFEEEB`, `#FFFFFF` | 배경·surface |
| `color.accent.primary` | `#50FFEB` | 선택·핵심 action |
| `color.accent.teal` | `#12B5A2` | 보조 강조 |
| `color.market.up` | `#E5484D`, `#D83A43` | 상승 |
| `color.market.down` | `#2F6BE0`, `#3267D6` | 하락 |
| `color.text.muted` | 회색 계열 다수 | 보조 문구 |

중복 red·blue·gray는 contrast 검증 후 하나의 scale로 통합한다. market color를 오류·성공 색으로 재사용하지 않는다.

### 7.2 나머지 token

- typography: display·title·body·label·caption
- spacing: 4px 또는 합의된 base scale
- radius: card·sheet·pill·control
- elevation: default·floating·modal
- motion: fast·normal, push·fade·sheet
- layout: content max width·bottom navigation height·safe area
- focus: 모든 interactive element의 명시적 outline

token은 CSS variable 또는 선택한 theme system으로 구현한다. JSX에 raw 값 복사를 금지한다.

---

## 8. 화면별 적용 규칙

### 8.1 오늘

구조:

```text
제목·시장 상태
기준 시각·dataStatus
활성 theme card 목록
계산 기준 진입점
하단 navigation
```

Theme card에는 다음만 우선 배치한다.

- rank와 `급부상` 보조 badge
- display name과 임시 분류 badge
- 상승 이유 상태·요약
- 대표 종목 또는 주도 종목
- 대형주 반영 수익률과 상승 종목 분자/분모
- Coverage 또는 데이터 갱신 상태
- 마지막 기준 시각

내부 Theme Score·검증되지 않은 예상 수익률·긴 과거 통계는 제외한다.

### 8.2 인사이트

- 상승률 단일 treemap
- 타일 label·수치·상태는 keyboard와 screen reader에서도 제공
- hover만으로 정보를 숨기지 않음
- `DELAYED`·`DEGRADED`에서는 마지막 정상 snapshot을 유지하고 상태 표시
- Coverage 미달 theme는 0% tile로 만들지 않음
- tile 선택 시 theme summary route로 이동

### 8.3 테마 상세 > 요약

순서는 [screen_spec.md](./screen_spec.md)를 따른다.

```text
현재 theme·event 상태
현재 theme 반응
오늘 부각된 이유와 근거
관심 공백·거래 관심
현재 움직임·확산
주도 종목
조건부 과거 요약·가장 관련된 사례
```

각 section은 독립적으로 loading·unavailable 상태를 가질 수 있다. 한 section 실패로 전체 화면을 비우지 않는다.

### 8.4 테마 상세 > 유사사례

- 서버가 `AVAILABLE`로 응답하고 feature gate를 통과한 경우에만 노출
- 집계마다 유효 표본 분모 표시
- 1·5·20거래일을 달력일로 표현하지 않음
- 목록 정렬 기준과 filter를 URL에 보존
- `matchedEventId`를 현재 `eventId`와 혼동하지 않음
- 표본 부족·관찰 중·가격 누락을 각기 다른 상태로 표현

### 8.5 과거 이벤트 상세

- 왜 비슷한지
- 사건 당시 알 수 있던 정보
- 발생 전 상태
- 사건 당시 주도주
- T+1·T+5·T+20 실제 관측 결과
- 누락·제외 사유
- 미래 결과를 검색에 쓰지 않았다는 고지

현재 membership을 과거 사건에 소급 표시하지 않는다.

---

## 9. API·fixture 연결

### 9.1 화면별 데이터 경계

| 화면 | 최초 REST | 실시간 topic | 조건부 데이터 |
|---|---|---|---|
| 오늘 | market session, theme rankings | `theme_rank_snapshot` | 없음 |
| 인사이트 | market session, treemap snapshot | `theme_treemap_snapshot` | 없음 |
| 테마 요약 | event detail, evidence | `event_state_changed` 또는 detail 재조회 signal | 과거 요약 |
| 유사사례 | similar event aggregate/list | 없음, 필요 시 polling | gate 통과 필요 |
| 과거 이벤트 | matched event detail/outcome | 없음 | gate 통과 필요 |

정확한 endpoint와 payload는 [api_contract.md](./api_contract.md)와 이후 OpenAPI·AsyncAPI를 따른다.

### 9.2 필수 fixture

```text
today.live.json
today.delayed.json
today.degraded.json
today.closed.json
today.empty.json
today.error.json
insights.live.json
insights.partial-coverage.json
theme.searching-evidence.json
theme.single-source.json
theme.multi-source.json
theme.after-close-confirmed.json
theme.unmatched-review-pending.json
similar.available.json
similar.empty.json
similar.small-sample.json
similar.gated.json
historical.partial-outcomes.json
realtime.reconnected-snapshot.json
```

fixture는 문서 예시를 복사한 임의 JSON이 아니라 같은 JSON Schema를 통과해야 한다. production build에서는 fixture import를 차단한다.

### 9.3 연결 순서

1. schema 기반 type 또는 validator 생성
2. fixture mock server 연결
3. 화면 상태·visual regression 완성
4. 실제 REST base URL 연결
5. WebSocket 연결과 sequence 처리
6. 오류·지연·재연결 주입 시험
7. fixture와 실제 응답 diff 검사

---

## 10. 상태 표현 matrix

### 10.1 데이터 상태

| `dataStatus` | UI |
|---|---|
| `PREOPEN` | 장 시작 전, 전일 확정값 또는 준비 상태 |
| `LIVE` | 실시간·기준 시각, 정상 갱신 |
| `DELAYED` | 마지막 정상값 유지, 수신 지연과 정상 시각 |
| `DEGRADED` | 영향 범위·Coverage 표시, 정상처럼 보이지 않음 |
| `CLOSED` | 장 마감, 장후 확정 또는 확정 대기 |
| 알 수 없는 값 | 안전한 `상태 확인 중`, error telemetry |

### 10.2 Event·근거·정합 상태

| 축 | 주요 UI 규칙 |
|---|---|
| `lifecycleStatus` | `CANDIDATE` 숨김, `ACTIVE` 정상, `WEAKENING` 보조 badge, `CLOSED` 최종값 |
| `evidenceStatus` | 확정 수준에 맞는 문구·출처 제공, 원인 생성 금지 |
| `reconciliationStatus` | `PENDING` 확정 대기, `MATCHED` 확정값, `UNMATCHED` 과거 연결 금지 |
| `reviewStatus` | 내부 운영 상태이며 일반 화면에는 마지막 승인값 유지 |

`dataStatus=CLOSED`와 `lifecycleStatus=CLOSED`는 이름이 같아도 다른 필드다.

### 10.3 Coverage

| 상태 | UI |
|---|---|
| 충분 | 정상 수치·rank·treemap 표시 |
| 부분 | `현재 n/m종목 반영`, 해석 주의 |
| 미달 | 수치 대신 데이터 갱신 상태, rank·treemap 제외 |

### 10.4 공통 비정상 상태

| 상황 | 처리 |
|---|---|
| 비로그인·session 만료 | 제품 데이터 제거, Google 로그인 화면, 성공 후 안전한 내부 `returnTo` 복귀 |
| 최초 loading | layout을 유지하는 skeleton |
| 화면 전체 empty | 명확한 원인 문구와 가능한 다음 행동 |
| section empty | 해당 section만 empty, 다른 데이터 유지 |
| retry 가능 error | 마지막 값 또는 구조 유지 + retry |
| 인증·권한 error | 노출 가능한 정보 없이 명확한 재인증 흐름 |
| unknown enum | 안전한 generic 상태 + telemetry |
| stale WebSocket | 실시간 badge 제거, REST 복구 또는 재연결 |
| `null` metric | `—`와 계산 불가 이유, `0` 표시 금지 |

---

## 11. 반응형·데모 frame

### 11.1 제품 반응형 기준안

| 구간 | 기본 구조 |
|---|---|
| compact | 단일 열, 하단 navigation, full-width sheet |
| medium | 넓은 단일 열 또는 보조 2열, 하단/측면 navigation 검토 |
| wide | content max width, 목록+detail 또는 2열 가능, 실제 desktop canvas |

정확한 breakpoint는 콘텐츠가 깨지는 지점에서 결정하고 visual test로 고정한다. 특정 아이폰 폭을 제품 기준으로 삼지 않는다.

### 11.2 필수 viewport 검수 후보

```text
360×800
393×852
430×932
768×1024
1024×768
1280×800
1440×900
```

### 11.3 demo frame 격리

`IOSDevice.jsx`는 다음 중 한 곳으로 이동한다.

```text
Storybook 또는 component preview
/__demo 전용 route
별도 demo entry build
```

조건:

- production route에서 import하지 않음
- business layout 계산에 `FRAME_W`, `FRAME_H`, `BARE_MAX_W`를 사용하지 않음
- mock status bar·home indicator에 접근성 tree 제외
- 실제 모바일은 `env(safe-area-inset-*)` 사용
- 실제 desktop은 device frame 없이 responsive layout 표시

---

## 12. 접근성

최소 기준:

- WCAG 2.2 AA 목표
- 모든 action은 keyboard로 실행 가능
- focus indicator를 제거하지 않음
- drawer·sheet는 focus trap, Escape, trigger focus 복귀
- icon button에 한국어 accessible name
- 상승·하락·상태를 색만으로 전달하지 않음
- 44×44px 수준의 touch target 목표
- dynamic update는 필요한 범위만 `aria-live` 사용
- treemap과 chart에 표 또는 목록 대체 표현
- skeleton과 animation에 reduced motion 지원
- 자동 순환 content 금지
- zoom 200%와 text reflow 검수
- source link는 새 창 여부와 목적 명확화

시안의 영어 `aria-label="Back"`은 사용자 언어에 맞춰 `뒤로` 등으로 교체한다.

---

## 13. 저장소 적용 전략

2026-08-14 제품 결정으로 **전략 B — 제품 저장소로 이식**을 채택했다. 디자이너 프로토타입 저장소와 기준 commit은 변경하지 않는 참고 원본으로 유지한다. 현재 `C:\dayjaview` 작업물을 팀 소유의 새 비공개 production GitHub 저장소로 승격하고 실제 제품 기능은 그 저장소에서만 구현한다. 두 저장소에 동시에 제품 기능을 구현하지 않는다.

### 전략 A — 프로토타입 저장소 승격

적합 조건:

- 팀이 이 저장소를 제품 frontend 원본으로 합의
- history와 배포 권한 이전 가능
- dependency upgrade·test·CI·보안 설정을 추가할 수 있음
- 대규모 구조 변경을 디자이너 작업 흐름과 조율 가능

필수 선행 작업:

1. 보호 branch와 reviewer 설정
2. Node·React·Vite 지원 버전 확정 및 dependency audit
3. `App.jsx`를 route·feature·shared component로 분해
4. token 추출
5. mock data를 fixture package로 이동
6. test·lint·type check·CI 추가
7. Vercel preview와 production 환경 분리

### 전략 B — 제품 저장소로 이식

적합 조건:

- 기존 제품 frontend가 별도로 존재
- 인증·배포·design system이 이미 있음
- 시안 history보다 기존 platform 일관성이 중요

이식 대상:

- 승인된 logo asset
- semantic token
- 공통 component의 시각 treatment
- screen별 승인된 layout pattern
- motion pattern

`App.jsx` 전체를 복사하지 않는다.

### 선택 게이트

| 기준 | 확인 |
|---|---|
| 저장소 전략 | 전략 B 승인 |
| 실제 제품 frontend 저장소가 존재하는가? | 새 팀 소유 비공개 저장소 사용 승인, 원격 생성 필요 |
| repository 소유권과 배포 권한은 누구에게 있는가? | 팀 소유, 실제 owner·관리자·reviewer·배포 권한은 생성 작업에서 설정 |
| 인증 기반이 있는가? | Google OAuth 필수 로그인과 server session 계약 확정 |
| designer preview 흐름을 유지할 수 있는가? | 원본 저장소에서 독립 유지 |
| dependency upgrade 비용과 보안 상태는 어떤가? | audit 필요 |

디자인 원본 commit을 고정하고 두 저장소에 동시에 제품 기능을 구현하지 않는다.

---

## 14. 구현 단계

### UI-0. 기준 동결과 검수

- 기준 commit·배포 URL 기록
- 6개 화면·주요 interaction screenshot 보존
- logo·font·asset 권리 확인
- [x] 저장소 전략 B 결정
- 목표 route와 feature gate 승인

완료 조건: 제품·디자인·프론트가 같은 원본과 목표 화면표를 승인한다.

### UI-1. Token·primitive·shell

- semantic token 생성
- 공통 primitive 구현
- 실제 router와 AppShell
- 오늘·인사이트·조건부 route skeleton
- demo frame 격리

완료 조건: fixture 없이도 route·focus·responsive shell이 동작한다.

### UI-2. Fixture 기반 핵심 화면

- 오늘
- 인사이트
- 테마 상세 요약
- 모든 `dataStatus`·Coverage·empty·error

완료 조건: JSON Schema를 통과한 fixture로 핵심 여정 visual·interaction test 통과.

### UI-3. 조건부 유사사례 화면

- gate component
- 유사사례 집계·목록
- 과거 이벤트 상세
- 표본·누락·관찰 중 상태

완료 조건: gate off에서는 route·link 미노출, gate on pilot에서만 동작.

### UI-4. 실제 API·WebSocket

- REST client와 runtime validation
- WebSocket subscribe·sequence·reconnect
- 지연·장애 복구
- analytics·error telemetry

완료 조건: fixture와 staging 응답이 같은 contract test를 통과한다.

### UI-5. 출시 검수

- viewport·browser·keyboard·screen reader 검수
- reduced motion·contrast·zoom
- performance budget
- fake data production bundle 검사
- design QA와 product wording QA

완료 조건: [implementation_roadmap.md](./implementation_roadmap.md)의 단계 9·10 gate 충족.

---

## 15. 검증 전략

### 자동 검증

- route·deep link·뒤로가기 test
- schema fixture validation
- component interaction test
- state matrix test
- visual regression by viewport
- accessibility rule scan
- WebSocket reconnect·old sequence test
- production bundle fixture import 검사

### 수동 검증

- 디자이너: 시각 계층·token·motion
- 제품: 정보 순서·문구·게이트
- 프론트: responsive·접근성·상태 ownership
- 백엔드: payload 의미·stale·null
- 데이터: 수치·분모·Coverage·시각

### 화면별 완료 증거

```text
승인된 screenshot 또는 preview URL
사용한 fixture ID
검수 viewport
접근성 결과
contract schema version
open issue 목록
reviewer와 검수일
```

---

## 16. 완료 체크리스트

### 문서 초안

- [x] 디자인 원본 저장소와 기준 commit을 기록했다.
- [x] 원본 파일·화면·데이터 구조를 감사했다.
- [x] 유지·수정·폐기 항목을 구분했다.
- [x] 기존 6개 화면과 목표 화면을 매핑했다.
- [x] 신규 인사이트 화면을 구분했다.
- [x] 목표 route·식별자·이동 상태를 정의했다.
- [x] component·상태 ownership 경계를 정의했다.
- [x] 반응형과 demo frame 격리를 정의했다.
- [x] 상태 matrix와 필수 fixture를 정의했다.
- [x] 접근성과 검증 기준을 정의했다.
- [x] 저장소 승격·이식 대안을 구분했다.
- [x] 제품 저장소 이식 전략을 승인했다.
- [x] 팀 소유의 별도 비공개 production 저장소 정책을 승인했다.
- [x] 미결정 항목을 기록했다.
- [x] 관심 기능의 Google 계정 저장 범위를 승인했다.

### 승인 전 남은 일

- [ ] 디자이너가 유지할 시각 자산을 승인했다.
- [ ] 제품 담당이 화면 매핑과 후속 범위를 승인했다.
- [ ] 프론트 담당이 component·route 구조를 검토했다.
- [ ] 백엔드 담당이 API 연결 경계를 검토했다.
- [ ] 실제 production 저장소를 생성하고 소유권·배포 권한을 설정했다.
- [ ] 주요 화면 wire 또는 수정된 디자인을 연결했다.
- [ ] asset·font 사용권을 확인했다.
- [ ] 문서 상태를 `승인`으로 변경했다.

문서 작성 완료와 적용 완료는 다르다. 위 승인 전 항목을 통과하기 전에는 이 문서를 확정 계약으로 취급하지 않는다.

---

## 17. 미결정 사항

| 항목 | 결정 주체 | 결정 시점 |
|---|---|---|
| production 저장소 실제 GitHub owner·URL과 팀 권한 provisioning | 제품·프론트 | UI-0 |
| React/Vite 목표 버전 | 프론트 | UI-0 |
| TypeScript 전환 여부 | 프론트 | UI-0 |
| router·query·state library | 프론트 | UI-0 |
| token 구현 방식 | 디자인·프론트 | UI-1 |
| breakpoint·desktop 2열 규칙 | 디자인·프론트 | UI-1 |
| treemap library와 접근성 대체 UI | 프론트·디자인 | UI-1 |
| splash 유지 여부 | 제품·디자인 | UI-0 |
| 테마 하단 탭 노출 방식 | 제품 | 후속 범위 승인 시 |
| font 공급·logo license | 제품·디자인 | UI-0 |
| 유사사례 pilot entitlement | 제품·백엔드 | 단계 7 |

미결정 항목을 시안의 현재 구현으로 자동 확정하지 않는다.

---

## 18. 변경 관리

- 디자인 원본 기준 commit이 바뀌면 변경점과 재검토 범위를 기록한다.
- 화면 구조 변경은 [screen_spec.md](./screen_spec.md)를 먼저 갱신한다.
- wire 의미 변경은 [api_contract.md](./api_contract.md)와 기계 schema를 먼저 갱신한다.
- 제품 범위·게이트 변경은 [PRD.md](./PRD.md)를 먼저 갱신한다.
- 시각 token만 바뀌는 경우 이 문서의 자산 표와 visual baseline을 갱신한다.
- 구현 편의를 이유로 조건부 기능을 핵심 MVP로 승격하지 않는다.
