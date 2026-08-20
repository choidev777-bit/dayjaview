# DAYJAVIEW UI 프로토타입 적용 계획

- 문서 버전: `1.0`
- 문서 상태: 적용 기준
- 최종 수정일: 2026-08-15
- 디자인 원본: [nangom/dayjaview-prototype](https://github.com/nangom/dayjaview-prototype)
- 기준 커밋: [`da00c8f`](https://github.com/nangom/dayjaview-prototype/tree/da00c8f) (2026-08-15 `main`)
- 배포 시안: [dayjaview-prototype.vercel.app](https://dayjaview-prototype.vercel.app)
- 제품 기준: [PRD.md](./PRD.md)
- 화면 기준: [screen_spec.md](./screen_spec.md)
- 시스템 기준: [system_architecture.md](./system_architecture.md)
- 데이터 계약: [api_contract.md](./api_contract.md)
- 실행 순서: [implementation_roadmap.md](./implementation_roadmap.md)

---

## 0. 문서 목적과 권한

이 문서는 디자이너 시안의 시각 언어와 화면 구성을 DAYJAVIEW 제품 화면에 적용하는 방법을 정의한다.

2026-08-15 제품 결정으로 **시안의 디자인을 그대로 사용한다**. 시각 계층에서 시안을 제품 사정에 맞춰 재해석하지 않는다.

### 0.1 문서 우선순위

이 문서의 이전 버전은 "충돌 시 기준 문서가 이기고 시안을 기준 문서에 맞춘다"였다. **2026-08-15 결정으로 이 규칙을 계층별로 나눈다.**

**시각 계층 — 시안이 최상위다.**

색상, 타이포, 간격, 곡률, 그림자, 모션, 화면 배치, 컴포넌트 외형, 화면 간 이동 구조, 문구 톤.
기준 문서가 이와 다르게 적혀 있으면 **기준 문서를 고친다.**

**데이터 계층 — 기준 문서가 최상위다.**

수치의 의미, 상태 enum, 식별자, wire 계약, 게이트 조건, 결측 표현.
순서는 [PRD.md](./PRD.md) → [system_architecture.md](./system_architecture.md) → [screen_spec.md](./screen_spec.md) → [api_contract.md](./api_contract.md).

**충돌 처리.**

시안의 배치와 외형은 유지하되, 그 자리에 들어가는 값·상태·게이트는 데이터 계층을 따른다.
시안의 하드코딩 숫자(`+2.7%`, `17/21`, `34건`)는 배치 예시이지 제품 값이 아니다.
시안에 있는 값을 서버가 줄 수 없으면 그 자리를 지어내지 않고 [§10](#10-상태-표현-matrix)의 결측 규칙으로 표시한다.

### 0.2 이 문서가 결정하지 않는 것

- 최종 로고 사용권과 Pretendard 공급 방식
- API 경로와 payload의 기계 판독 형식
- 트리맵 rendering library

---

## 1. 기준 시안 감사

### 1.0 이전 감사 기록 폐기

이 문서 `0.2-draft`(2026-08-14)의 §1 감사와 §7 토큰 값은 **전부 폐기한다.**

- 기준 커밋으로 적었던 `65324878db4ef92bb29c7fce21e63a1031c3be17`은 시안 저장소의 어느 브랜치에도 존재하지 않는다.
- 감사 결과로 적었던 `src/App.jsx`, `src/IOSDevice.jsx`, `src/css.js`, `src/index.css`, React 18.3 + Vite 5.4 구조는 이 저장소에 존재한 적이 없다. 저장소 전체 커밋 58개는 모두 2026-08-14~15이며 최초 커밋부터 Next.js 모노레포다.
- 토큰 후보로 적었던 민트 `#50FFEB`, 틸 `#12B5A2`, canvas `#EFEEEB`, text `#16160F`은 시안에 존재하지 않는 색이다.

이 오류가 [implementation_roadmap.md](./implementation_roadmap.md)와 `apps/web/src/styles/global.css`의 청록 팔레트로 전파됐다. 아래 감사는 실제 저장소를 받아 확인한 결과다.

### 1.1 저장소 구조 (`da00c8f` 기준)

```text
package.json                              npm workspaces 모노레포
packages/design-tokens/src/tokens.css     :root 토큰 39개 + 다크 override 11개
packages/ui/src/                          AppBar, BottomNavigation, Button, StatusLabel, ThemeRow
apps/web/                                 Next.js 15.3 · React 19.1
  src/app/layout.tsx                      seed base.css → tokens.css → globals.css 순서로 로드
  src/app/globals.css                     reset 4줄
  src/app/page.tsx                        전체 화면이 모인 단일 client component (596줄)
  src/app/page.module.css                 화면 스타일 (915줄)
  src/components/ThemeRankingWheel.tsx    홈 순위 휠 (201줄)
  src/data/prototype.ts                   합성 데이터
docs/design-system-boundary.md            토큰·UI·화면 소유 경계
```

의존성: `next@15.3.4`, `react@19.1.0`, `@seed-design/css@^2.5.0`, `@seed-design/react@^2.3.0`, `@karrotmarket/react-monochrome-icon@^1.25.0`.

**시안은 당근 Seed 디자인 시스템 위에 올라가 있다.** `layout.tsx`가 `@seed-design/css/base.css`를 토큰 파일보다 먼저 로드하므로, `tokens.css`에 적힌 fallback 값은 실제 렌더링 값이 아니다. [§7](#7-디자인-토큰)의 실측값을 기준으로 삼는다.

`packages/ui`의 5개 컴포넌트는 각 5~11줄의 껍데기이며 실제 화면은 전부 `page.tsx` + `page.module.css`가 그린다. 시안의 UI 패키지 경계는 아직 선언 수준이다.

### 1.2 화면 목록

`page.tsx`의 `currentScreen` 상태로 전환하는 8개 화면이다. 실제 route는 없고 URL은 `/`에 머문다.

| 화면 ID | 내용 |
|---|---|
| (loading) | 4초 로고 스플래시 — 주황 sweep 애니메이션 |
| (login) | 저장·즐겨찾기 진입 시에만 뜨는 모달형 로그인 |
| `screen-home` | 날짜 + "오늘 많이 오른 테마예요" + 순위 휠 |
| `screen-detail` | 테마 상세 — 상태·이유·주도주·과거 소재 TOP3·이벤트 스터디·케이스 |
| `screen-realtime` | 실시간 테마 트리맵 (3단 고정 배치) |
| `screen-cases` | 과거 사례 전체보기 — 기간 토글·필터·목록 |
| `screen-case-detail` | 개별 과거 사례 |
| `screen-catalyst` | 상승 소재 상세 |
| `screen-leader` | 주도 종목 상세 |
| `screen-natural` | 리서치 — 자연어 질문·답변 |
| (saved) | 홈 탭 안의 저장 라이브러리 |

하단 탭은 **홈 · 실시간 · 즐겨찾기 · 리서치** 4개다.

### 1.3 시각 언어

- 주황 브랜드 `#ff6600`(당근 carrot-600), 강조 배경 `#fff2ec`
- 웜 그레이 배경 `#eae8e3` 위 흰 카드
- Pretendard Variable, 12~32px 7단계
- 큰 곡률 (11 / 16 / 22 / 28 / 999px)
- 국내 관행 상승 red `#e5484d` · 하락 blue `#356ae6`
- 당근 monochrome 아이콘
- 짧은 전환 (160ms / 220ms, `cubic-bezier(0.2, 0, 0, 1)`)
- 로딩 화면의 검정 배경 + 주황 sweep 로고

### 1.4 레이아웃 전제

**시안은 모바일 전용이다.** 데스크톱 레이아웃이 없다.

- `page.module.css`의 `.preview`는 회색 배경에 `.phone`(393×852 고정, 곡률 32px)을 중앙 배치한 **디자이너 목업 wrapper**다. 제품 화면이 아니다.
- 토큰의 `--djv-app-max-width: 420px`가 제품 기준 최대 폭이다.
- 미디어쿼리는 `440px` 경계 하나와 휠의 `prefers-reduced-motion` 하나뿐이다.

### 1.5 구조적 한계

| 항목 | 시안 상태 | 이식 시 처리 |
|---|---|---|
| 화면 분리 | `page.tsx` 단일 파일 596줄 | 제품에서는 page 단위 분리 |
| navigation | `currentScreen` 상태 | 실제 route로 교체 ([§5](#5-route)) |
| 데이터 | 하드코딩 배열 | 서버 계약으로 교체 |
| 순위 휠 | 카드 3벌 복제 + 진입 자동 스크롤 | 시각·조작감 유지, 접근성 보완 ([§12](#12-접근성)) |
| 상태 | 성공 화면만 존재 | 상태 matrix 추가 ([§10](#10-상태-표현-matrix)) |
| 통계 | 검증되지 않은 예시 숫자 | 서버 값 또는 결측 표시 |
| 로그인 | 이메일·비밀번호 데모 폼 | Google OAuth로 교체 |

---

## 2. 적용 원칙

1. **시각 언어와 화면 구성을 그대로 가져온다.** 제품 사정으로 재해석하지 않는다.
2. **시안의 하드코딩 수치는 배치 예시다.** 제품 요구사항으로 승격하지 않는다.
3. **route와 식별자는 표시명 대신 `themeId`, `eventId`, `matchedEventId`를 쓴다.**
4. **프론트는 서버 수치를 재계산하거나 결측을 0으로 바꾸지 않는다.**
5. **시안에 없는 상태(로딩·지연·결측·오류·게이트)는 시안의 시각 언어로 새로 그린다.**
6. **유사사례는 온톨로지 재검증 게이트를 통과한 경우에만 사용자 route를 연다.**
7. **접근성은 시각을 바꾸지 않는 범위에서 보완한다.** 충돌하면 [§12](#12-접근성)의 개별 판단을 따른다.
8. **393×852 목업 프레임은 제품에 넣지 않는다.** 제품 최대 폭은 420px다.

---

## 3. 유지·수정·폐기 결정

### 3.1 그대로 유지

| 자산 | 조건 |
|---|---|
| 토큰 39개 전체 (색·타이포·간격·곡률·모션·그림자) | [§7](#7-디자인-토큰) 실측값 |
| 로고 mark·wordmark, 로딩 스플래시 | 사용권 확인 |
| 홈 순위 휠 | 시각·조작감 유지, [§12](#12-접근성) 보완 |
| 하단 탭 4개 구성과 아이콘 | 당근 아이콘 패키지 사용 |
| 테마 상세 섹션 순서와 카드 구조 | 값은 계약을 따름 |
| 트리맵 3단 배치 | 타일 수·크기 규칙은 [screen_spec.md](./screen_spec.md) §6.3 |
| 케이스 목록·상세, 소재 상세, 주도주 상세 레이아웃 | 게이트 통과 시에만 노출 |
| 리서치 화면 레이아웃 | 자리만 유지, 내용은 비움 ([§4.2](#42-리서치-탭)) |
| 접기·펼치기 패턴, 기간 토글, 필터 행 | 그대로 |
| 웜 그레이 배경 + 흰 카드 대비 | 그대로 |
| 상승 red · 하락 blue | 색만으로 의미 전달 금지 ([§12](#12-접근성)) |

### 3.2 수정

| 시안 요소 | 제품 처리 |
|---|---|
| `currentScreen` 상태 전환 | 실제 route + browser history |
| 이메일·비밀번호 데모 로그인 폼 | Google OAuth 버튼, 시안의 모달 시각 유지 |
| 하드코딩 배열 | 서버 응답 / fixture |
| 순위 휠 카드 3벌 복제 | DOM은 1벌 + 시각적 무한 스크롤, 스크린리더 중복 제거 |
| 4초 고정 스플래시 | 실제 초기 로딩과 연동, 최소 노출 시간만 유지 |
| `page.tsx` 단일 파일 | page 단위 분리 |
| 검증 안 된 통계 숫자 | 서버 값 또는 결측 표시 |

### 3.3 폐기

- 393×852 목업 프레임과 회색 `.preview` 배경 (제품 layout으로 사용하지 않음)
- 하드코딩된 테마·사례·종목·통계의 production bundle 포함
- 화면 표시명으로 route를 구성하는 방식
- 서버 mutation 없이 저장된 것처럼 보이는 저장 토글
- 실제 데이터 장애를 빈 배열이나 0으로 치환하는 fallback
- 데스크톱 사이드바 layout (현재 `global.css`에 구현된 것 — 시안에 없음)

---

## 4. 화면 매핑

| 시안 화면 | 제품 화면 | 출시 상태 |
|---|---|---|
| (loading) | 초기 brand/loading | 선택 |
| (login) | 로그인 | 핵심 |
| `screen-home` | 오늘 | 핵심 |
| `screen-realtime` | 인사이트 | 핵심 |
| (saved) | 관심 | 핵심 |
| `screen-detail` | 테마 상세 > 요약 | 핵심 |
| `screen-cases` | 테마 상세 > 유사사례 | 조건부 — 온톨로지 재검증 후 |
| `screen-case-detail` | 과거 이벤트 상세 | 조건부 |
| `screen-catalyst` | 과거 소재 유형 상세 | 조건부 |
| `screen-leader` | 주도 종목 상세 | 후속 |
| `screen-natural` | 리서치 | 자리만 — [§4.2](#42-리서치-탭) |
| 없음 | 내부 운영자 콘솔 | 내부 필수, 사용자 shell 재사용 금지 |

### 4.1 탭 이름

시안 표기를 따른다. [screen_spec.md](./screen_spec.md) §2.1의 탭 표는 이에 맞춰 갱신한다.

| 시안 | 이전 기준 문서 | 채택 |
|---|---|---|
| 홈 | 오늘 | **홈** |
| 실시간 | 인사이트 | **실시간** |
| 즐겨찾기 | 관심 | **즐겨찾기** |
| 리서치 | (없음) | **리서치** |
| — | 테마 (후속) | 하단 탭에서 제외 |

route path는 영문 식별자를 유지한다 ([§5](#5-route)).

### 4.2 리서치 탭

시안에만 있고 [PRD.md](./PRD.md)·[screen_spec.md](./screen_spec.md)에 없는 기능이다.

- 탭과 진입 화면의 **시각 구성은 시안대로 구현한다.**
- 자연어 질의·응답 기능은 구현하지 않는다. 요구사항 문서가 없다.
- 화면 본문은 준비 중 상태로 둔다. 가짜 답변을 렌더링하지 않는다.
- 기능 착수 전에 PRD와 screen_spec에 요구사항을 먼저 쓴다.

### 4.3 조건부 화면 처리

`screen-cases`, `screen-case-detail`, `screen-catalyst`는 시안에서 테마 상세로부터 바로 열린다. 제품에서는 [§5.2](#52-조건부-route)의 게이트를 통과할 때만 진입점을 노출한다. 게이트 미통과 시 해당 섹션 자체를 숨긴다.

---

## 5. route

`currentScreen` 문자열을 실제 route로 바꾼다.

| route | 화면 | 필수 식별자 | 상태 |
|---|---|---|---|
| `/today` | 홈 | 없음 | 핵심 |
| `/insights` | 실시간 | 없음 | 핵심 |
| `/saved` | 즐겨찾기 | 현재 Google session | 핵심 |
| `/research` | 리서치 | 없음 | 자리만 |
| `/themes/:themeId/events/:eventId` | 테마 상세 | `themeId`, `eventId` | 핵심 |
| `/themes/:themeId/events/:eventId/similar` | 유사사례 | 위 + 게이트 | 조건부 |
| `/events/:matchedEventId` | 과거 이벤트 상세 | `matchedEventId` | 조건부 |
| `/catalysts/:catalystId` | 과거 소재 유형 상세 | `catalystId` | 조건부 |
| `/operator` | 운영자 콘솔 | Google session + `OPERATOR` | 내부 필수, 미노출 |

`/`는 `/today`로 redirect한다. 표시명을 URL 식별자로 쓰지 않는다.

### 5.1 이동 상태 보존

- 홈 → 상세 → 뒤로: 순위 휠의 스크롤 위치 복원
- 실시간 → 상세 → 뒤로: 트리맵 선택 복원
- 요약 ↔ 유사사례: `themeId`, `eventId` 유지
- 유사사례 → 과거 이벤트: `matchedEventId`와 원래 `eventId` 구분
- 장후 분류 변경: 같은 `eventId`로 canonical route 유지
- 공유 URL: server fetch만으로 화면 복원 가능

local state에만 식별자를 보관하지 않는다. 시안의 `localStorage` 기반 최근 본 테마는 서버 저장으로 옮긴다.

### 5.2 조건부 route

다음을 모두 만족할 때만 navigation에 노출한다.

```text
featureFlag enabled
approved retrieval artifact active
ontology validation gate passed
event reconciliation permits historical linking
API availability = AVAILABLE
```

게이트 미통과는 오류가 아니다. 잠긴 가짜 화면을 보여주지 않고 진입점을 숨긴다.

### 5.3 운영자 route

`/operator`는 하단 탭·검색·sitemap에서 노출하지 않는다. client 숨김으로 권한을 대신하지 않고 모든 `/v1/operator` 요청을 서버에서 검사한다. `OPERATOR`가 아닌 session은 `403`이다.

운영자 shell은 시안 시각 언어를 재사용하되 사용자 shell 컴포넌트를 공유하지 않는다. 서버 terminal, 파일 브라우저, 임의 SQL, secret 원문 UI는 만들지 않는다.

---

## 6. 프론트엔드 구조

```text
apps/web/src/
├─ app/            router, providers, AppShell, featureFlags
├─ pages/          route별 화면
├─ features/       도메인 UI와 상호작용
├─ shared/         api, realtime, fixtures, ui, tokens, formatting
└─ styles/         tokens.css + 전역 reset
```

강제하는 경계:

- page는 route orchestration만 담당한다.
- API client가 wire response를 검증하고 view model로 변환한다.
- 표시 component가 fetch·WebSocket 연결을 직접 만들지 않는다.
- formatting은 수익률·날짜·분모 표현을 한 위치에서 처리한다.
- historical matching code는 feature flag 경계 밖으로 새지 않는다.

### 6.1 상태 소유권

| 상태 | 소유 위치 |
|---|---|
| URL 상태 | router |
| 서버 조회 상태 | query cache |
| 실시간 상태 | realtime store |
| 사용자 입력 | local UI |
| 제품 gate | server entitlement + feature flag |
| 계산·분류 | server |

프론트는 WebSocket snapshot의 `sequence`를 비교할 수 있지만 rank·Coverage·evidence 상태를 새로 계산하지 않는다.

---

## 7. 디자인 토큰

`packages/design-tokens/src/tokens.css`를 그대로 이식한다. 변수 이름 `--djv-*`를 유지한다.

**Seed 의존 처리.** 시안은 `@seed-design/css/base.css`를 로드하므로 토큰 파일의 fallback 값이 실제 렌더링 값과 다르다. 제품에는 당근 디자인 시스템을 설치하지 않고 **해석된 실측값을 직접 기입한다.** 아래 표의 "실측값"이 기준이다.

### 7.1 색상

**2026-08-21 갱신.** 임원 재발표를 앞두고 키움증권 앱 계열로 옮겼다. §0.1의 시각 계층
규칙(시안이 최상위, 다르면 기준 문서를 고친다)에 따라 이 표를 현재 값으로 다시 적는다.
전환 배경과 화면별 파급은 [2026-08-21 키움 팔레트 전환 기록](./release/2026-08-21_kiwoom_palette.md).

색은 **역할별로 넷**이다. 하나가 브랜드·활성 상태·수치를 겸하지 않는다.

| 토큰 | 이전 (당근 계열) | **현재 (기준)** | 용도 |
|---|---|---|---|
| `--djv-color-bg` | `#eae8e3` | **`#f4f4f6`** | 배경. 카드가 뜨도록 흰색에서 내렸다 |
| `--djv-color-surface` | `#ffffff` | `#ffffff` | 카드 |
| `--djv-color-surface-muted` | `#f3f4f5` | `#f3f4f5` | 보조 면 |
| `--djv-color-track` | — | **`#e6e7ee`** | 세그먼트 토글 트랙 (신규) |
| `--djv-color-border` | `#00000010` | `#00000010` | 테두리 (검정 6% 알파) |
| `--djv-color-text` | `#1a1c20` | `#1a1c20` | 본문 |
| `--djv-color-text-muted` | `#555d6d` | `#555d6d` | 보조 문구 |
| `--djv-color-brand` | `#ff6600` | **`#7b2ff7`** | 화면 주색. 활성 탭·초점 카드·강조 헤더 |
| `--djv-color-brand-soft` | `#fff2ec` | **`#f2eafe`** | 주색 배경 |
| `--djv-color-logo` | — | **`#1c1c5e`** | 로고 마크 전용 (신규) |
| `--djv-color-favorite` | — | **`#ffc02e`** | 즐겨찾기 별 (신규) |
| `--djv-color-on-brand` | `#ffffff` | `#ffffff` | 주색 위 글자 |
| `--djv-color-market-up` | `#e5484d` | **`#ff2d8e`** | 상승 수치 |
| `--djv-color-market-down` | `#356ae6` | **`#2b5be8`** | 하락 수치 |
| `--djv-color-warning` | `#a76700` | `#a76700` | 경고 |

**로고를 주색에서 분리한 이유.** 마크가 주색을 따라가면 팔레트를 바꿀 때마다 로고 색이
바뀐다. 로고는 제품 아이덴티티라 화면 주색과 수명이 다르다.

**즐겨찾기를 따로 둔 이유.** 별을 주색으로 칠하면 활성 탭·초점 카드와 같은 색이라
`저장했다`는 신호가 화면의 다른 보라들에 묻힌다.

트리맵 타일 채움은 별도 토큰 네 개로 범위를 잡는다. 옅은 색에서 시작하면 흰 글씨가
묻혀 타일마다 글자색이 달라지므로, 약한 칸도 흰 글씨가 읽히는 농도에서 출발한다.

| 방향 | weak | strong |
|---|---|---|
| 상승 | `#ff5aa8` | `#8f0049` |
| 하락 | `#3f6fd8` | `#1b3a86` |

market color를 오류·성공 색으로 재사용하지 않는다.

### 7.2 타이포

`--djv-font-sans`: `"Pretendard Variable", Pretendard, "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif`

**2026-08-21 갱신.** 디자이너 요청으로 한 단계씩 올렸다. 토스 TDS 기준(15/13/12)보다
한 단계 크다. 제목 계열도 위계가 무너지지 않게 같은 폭으로 올렸다.

| 토큰 | 이전 | **현재 (기준)** |
|---|---|---|
| `--djv-font-size-caption` | 12px | **13px** |
| `--djv-font-size-label` | 13px | **15px** |
| `--djv-font-size-body` | 15px | **17px** |
| `--djv-font-size-heading-3` | 17px | **19px** |
| `--djv-font-size-heading-2` | 20px | **22px** |
| `--djv-font-size-heading-1` | 24px | **25px** |
| `--djv-font-size-display` | 32px | **31px** |

### 7.3 간격·곡률·모션·레이아웃

| 분류 | 토큰 | 값 |
|---|---|---|
| 간격 | `--djv-space-1`~`-8` | 4 · 8 · 12 · 16 · 20 · 24 · 32px (`-7` 없음) |
| 곡률 | `--djv-radius-sm`~`-pill` | 11 · 16 · 22 · 28 · 999px |
| 모션 | `--djv-motion-fast` / `-screen` | 160ms / 220ms |
| 모션 | `--djv-easing-standard` | `cubic-bezier(0.2, 0, 0, 1)` |
| 레이아웃 | `--djv-app-max-width` | 420px |
| 레이아웃 | `--djv-touch-size` | 48px |
| 그림자 | `--djv-shadow-card` | `0 1px 2px rgba(0,0,0,.025)` |
| 그림자 | `--djv-shadow-nav` | `0 2px 10px rgba(0,0,0,.06)` |

**카드 간격 (2026-08-21 신규).** 바탕이 회색이 되면서 화면이 카드의 나열이 됐다.
카드마다 여백이 제각각이면 내려 읽을 때 리듬이 끊기므로 세 값으로 묶었다.
새 카드를 만들 때 이 셋 밖의 값을 쓰지 않는다.

| 토큰 | 값 | 뜻 |
|---|---|---|
| `--djv-card-gutter` | 16px | 카드 좌우 |
| `--djv-card-gap` | 12px | 카드 사이 |
| `--djv-card-pad` | 20px 16px | 카드 안쪽 |

### 7.4 다크 테마

`:root[data-theme="dark"]`에서 11개 토큰을 재정의한다. **시안에서 opt-in이며 토글 UI가 없다.** 제품에서도 설정 화면이 생길 때까지 opt-in으로 유지한다.

| 토큰 | 다크 값 |
|---|---|
| `bg` / `surface` / `surface-muted` | `#171719` / `#222225` / `#2b2b2f` |
| `border` | `#343438` |
| `text` / `text-muted` | `#f2f2f3` / `#a4a4aa` |
| `brand` / `brand-soft` | `#f28752` / `#3d2b23` |
| `shadow-card` / `shadow-nav` | `0 1px 2px rgba(0,0,0,.3)` / `0 4px 18px rgba(0,0,0,.28)` |

**시안 미완성 구간.** `market-up`, `market-down`, `warning`은 다크에서 재정의되지 않아 라이트 값을 그대로 쓴다. `#356ae6` 하락색은 `#171719` 배경에서 대비가 부족하다. 다크 토글을 실제로 여는 시점에 디자이너에게 3색 다크값을 요청한다.

### 7.5 이식 규칙

- 토큰은 `apps/web/src/styles/tokens.css` 한 곳에서만 정의한다.
- JSX와 component CSS에 raw 색상값 복사를 금지한다.
- 현재 `global.css`의 `--color-*`, `--radius-*` 계열 22개 변수는 전부 제거한다.

---

## 8. 화면별 적용 규칙

### 8.1 홈

시안 구성: 로고 헤더 → 날짜 + "오늘 많이 오른 테마예요" → 순위 휠.

순위 휠 카드에 배치할 값:

- rank
- 테마 표시명
- 대형주 반영 수익률
- 보조 문구 (시안의 `metadata` 자리 — 상승 종목 분자/분모 또는 Coverage 상태)

내부 Theme Score, 검증되지 않은 예상 수익률, 긴 과거 통계는 넣지 않는다.

`dataStatus`·기준 시각·Coverage 표시는 시안에 없다. 시안의 시각 언어로 헤더 아래에 새로 배치한다.

### 8.2 실시간

- 시안의 3단 트리맵 배치 유지 (상단 2 · 중단 3 · 하단 3)
- 타일 수·크기·색 규칙은 [screen_spec.md](./screen_spec.md) §6.3
- Core Coverage 통과한 `ACTIVE`·`WEAKENING` 테마만 표시
- Coverage 미달 테마를 0% 타일로 만들지 않음
- 접근 가능한 대체 목록 제공
- `DELAYED`·`DEGRADED`에서는 마지막 정상 snapshot 유지 + 상태 표시
- 타일 선택 시 동일 `themeId`·`eventId`로 테마 상세 이동

### 8.3 테마 상세

시안 섹션 순서를 유지한다.

```text
테마 요약 (순위 pill · 이름 · 평균 등락률)
현재 테마 상태 (상승 종목 · 거래 관심 · 테마 거래대금 · 관심 공백)
오늘 왜 올랐을까요? (실시간 / 장 마감 후 탭)
오늘의 주도 종목
과거 상승 소재 Top 3          ← 조건부
과거엔 어땠을까요?             ← 조건부
DAY-JA-VIEW 케이스            ← 조건부
```

- 각 섹션은 독립적으로 loading·unavailable 상태를 가진다. 한 섹션 실패로 전체를 비우지 않는다.
- 조건부 섹션은 게이트 미통과 시 섹션 자체를 숨긴다.
- "실시간 / 장 마감 후" 탭은 `evidenceStatus`와 연결한다. 근거가 없으면 이유를 생성하지 않는다.
- 시안의 관심 공백 문구("8개월 만에 다시 주목받고 있어요")는 `attention` 계산 결과가 있을 때만 노출한다.

### 8.4 유사사례 (조건부)

- 서버가 `AVAILABLE`로 응답하고 feature gate를 통과한 경우에만 노출
- 시안의 기간 토글(1·5·20일)과 필터 행 유지
- 집계마다 유효 표본 분모 표시
- 거래일을 달력일로 표현하지 않음
- 정렬·filter를 URL에 보존
- `matchedEventId`를 현재 `eventId`와 혼동하지 않음

### 8.5 과거 이벤트 상세 (조건부)

시안 섹션 순서 유지: 비슷했던 점 → 사건 기록 → 당시 바스켓 흐름 → 당시 기록된 종목 → 고지.

현재 membership을 과거 사건에 소급 표시하지 않는다.

### 8.6 즐겨찾기

시안 구성: 저장한 분석 목록 → 구분선 → 내가 눌러본 테마.

- 저장·해제는 서버 mutation 결과를 반영한다. 낙관적 UI 후 실패 시 되돌린다.
- 최근 본 테마는 계정에 저장한다. `localStorage`에 남기지 않는다.
- 빈 상태 문구와 아이콘은 시안 그대로.

### 8.7 로그인

- 시안의 모달형 진입과 문구 톤 유지 ("이 분석을 저장할까요?" / "저장한 분석을 확인하세요")
- 이메일·비밀번호 입력은 제거하고 Google 로그인 버튼으로 교체
- 제품 데이터 preview를 포함하지 않음
- 성공 후 검증된 내부 `returnTo`로 복귀

---

## 9. API·fixture 연결

### 9.1 화면별 데이터 경계

| 화면 | 최초 REST | 실시간 topic | 조건부 |
|---|---|---|---|
| 홈 | market session, theme rankings | `theme_rank_snapshot` | 없음 |
| 실시간 | market session, treemap snapshot | `theme_treemap_snapshot` | 없음 |
| 테마 상세 | event detail, evidence | `event_state_changed` | 과거 요약 |
| 유사사례 | similar event aggregate/list | 없음 | gate 필요 |
| 과거 이벤트 | matched event detail/outcome | 없음 | gate 필요 |
| 즐겨찾기 | saved list | 없음 | 없음 |

정확한 endpoint와 payload는 [api_contract.md](./api_contract.md)와 OpenAPI·AsyncAPI를 따른다.

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

fixture는 같은 JSON Schema를 통과해야 한다. production build에서 fixture import를 차단한다.

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

| 축 | UI 규칙 |
|---|---|
| `lifecycleStatus` | `CANDIDATE` 숨김, `ACTIVE` 정상, `WEAKENING` 보조 badge, `CLOSED` 최종값 |
| `evidenceStatus` | 확정 수준에 맞는 문구·출처 제공, 원인 생성 금지 |
| `reconciliationStatus` | `PENDING` 확정 대기, `MATCHED` 확정값, `UNMATCHED` 과거 연결 금지 |
| `reviewStatus` | 내부 운영 상태, 일반 화면에는 마지막 승인값 유지 |

`dataStatus=CLOSED`와 `lifecycleStatus=CLOSED`는 다른 필드다.

### 10.3 Coverage

| 상태 | UI |
|---|---|
| 충분 | 정상 수치·rank·treemap 표시 |
| 부분 | `현재 n/m종목 반영`, 해석 주의 |
| 미달 | 수치 대신 데이터 갱신 상태, rank·treemap 제외 |

### 10.4 공통 비정상 상태

| 상황 | 처리 |
|---|---|
| 비로그인·session 만료 | 제품 데이터 제거, Google 로그인, 안전한 `returnTo` 복귀 |
| 최초 loading | layout을 유지하는 skeleton |
| 화면 전체 empty | 명확한 원인 문구와 다음 행동 |
| section empty | 해당 section만 empty, 다른 데이터 유지 |
| retry 가능 error | 마지막 값 또는 구조 유지 + retry |
| 인증·권한 error | 노출 가능한 정보 없이 재인증 흐름 |
| unknown enum | 안전한 generic 상태 + telemetry |
| stale WebSocket | 실시간 badge 제거, REST 복구 또는 재연결 |
| `null` metric | `—`와 계산 불가 이유, `0` 표시 금지 |

시안에는 위 상태가 하나도 없다. 시안의 카드·문구·색 토큰을 재사용해 새로 그린다.

---

## 11. 레이아웃

### 11.1 제품 기준

**모바일 단일 열 고정.** 시안에 데스크톱 레이아웃이 없으므로 만들지 않는다.

- 콘텐츠 최대 폭 `--djv-app-max-width: 420px`, 넓은 화면에서는 중앙 정렬
- 하단 고정 탭 + `env(safe-area-inset-*)`
- 시안의 `440px` 경계 하나만 유지
- 현재 `global.css`의 `72rem` content width, `64rem` 사이드바 전환, `40rem` 2열 grid는 제거한다

### 11.2 검수 viewport

```text
360×800
393×852
430×932
768×1024
1280×800    ← 420px 중앙 정렬이 깨지지 않는지만 확인
```

### 11.3 목업 프레임

시안의 `.preview` / `.phone`(393×852, 회색 배경)은 디자이너 목업 wrapper다.

- production route에서 사용하지 않는다.
- 필요하면 `/__demo` 전용 route 또는 별도 demo entry로 격리한다.
- business layout 계산에 393·852를 쓰지 않는다.

---

## 12. 접근성

시각을 바꾸지 않는 범위에서 보완한다.

최소 기준:

- WCAG 2.2 AA 목표
- 모든 action은 keyboard로 실행 가능
- focus indicator를 제거하지 않음 (시안 `globals.css`의 `2px solid var(--djv-color-brand)` 유지)
- 아이콘 버튼에 한국어 accessible name — 시안의 `aria-label`은 이미 한국어다
- 상승·하락·상태를 색만으로 전달하지 않음
- 터치 타깃 `--djv-touch-size: 48px`
- dynamic update는 필요한 범위만 `aria-live`
- 트리맵에 표 또는 목록 대체 표현
- reduced motion 지원
- zoom 200%와 text reflow 검수

### 12.1 순위 휠 보완

시안 휠은 카드를 3벌 복제하고 진입 시 800ms 간격으로 자동 스크롤한다. 시각과 조작감은 유지하되 다음을 보완한다.

- DOM에는 1벌만 두고 시각적 무한 스크롤을 구현한다. 스크린리더가 같은 테마를 3번 읽지 않게 한다.
- 순위 목록에 `role`과 항목 수를 제공한다.
- 위·아래 방향키로 항목 이동이 가능해야 한다.
- 진입 자동 스크롤은 `prefers-reduced-motion`에서 건너뛴다 (시안에 이미 구현됨).
- 자동 스크롤은 사용자 입력 시 즉시 취소된다 (시안에 이미 구현됨).
- 자동 스크롤은 진입 1회로 끝나며 반복하지 않는다 (시안에 이미 구현됨).

---

## 13. 저장소 전략

2026-08-14 제품 결정으로 **전략 B — 제품 저장소로 이식**을 채택했다 ([ADR-010](./adr/010-production-repository.md)). 시안 저장소는 참고 원본으로 유지하고 제품 기능은 production 저장소에서만 구현한다.

### 13.1 기준 커밋 추종 정책

2026-08-15 결정: **기준 커밋을 고정한다.**

- 현재 기준: `da00c8f` (2026-08-15 `main`)
- 디자이너가 시안을 갱신해도 자동으로 따라가지 않는다.
- 기준을 옮길 때는 이전 기준과의 diff를 확인하고 이 문서의 §1·§7·§8을 갱신한 뒤 옮긴다.
- 옮긴 기록은 이 절에 남긴다.

시안 저장소는 2026-08-14~15 이틀간 58커밋이 올라갔다. 고정 없이는 작업 중 기준이 흔들린다.

### 13.2 이식 대상

- 토큰 파일 전체
- 로고·아이콘 asset
- 화면별 layout과 component 시각 treatment
- motion pattern

`page.tsx` 전체를 복사하지 않는다. 화면 단위로 옮기며 데이터 계층은 제품 계약으로 교체한다.

---

## 14. 구현 단계

### UI-0. 기준 동결

- [x] 기준 commit `da00c8f` 기록
- [x] 시안 실제 구조·토큰 실측값 감사
- [x] 문서 우선순위 규칙 확정 ([§0.1](#01-문서-우선순위))
- [ ] 화면별 screenshot 보존
- [ ] logo·font asset 권리 확인

### UI-1. 토큰 교체

- 시안 토큰 39개를 `apps/web/src/styles/tokens.css`로 이식 ([§7](#7-디자인-토큰) 실측값)
- 현재 `global.css`의 청록 팔레트 22개 변수 제거
- 420px 단일 열 shell로 교체, 데스크톱 사이드바 제거
- 하단 탭 4개로 교체

완료 조건: 기존 화면이 시안 색·타이포·간격으로 렌더링되고 web lint·typecheck·test·build 통과.

### UI-2. 화면 정합

- 홈: 순위 휠 이식 + 접근성 보완
- 실시간: 3단 트리맵
- 테마 상세: 시안 섹션 순서와 카드 구조
- 즐겨찾기: 저장 목록 + 최근 본 테마
- 로그인: 시안 모달 시각 + Google OAuth
- 리서치: 자리만
- 모든 `dataStatus`·Coverage·empty·error 상태

완료 조건: fixture로 핵심 여정의 visual·interaction test 통과.

### UI-3. 조건부 화면

- gate component
- 유사사례 목록·상세, 소재 상세
- 표본·누락·관찰 중 상태

완료 조건: gate off에서 route·link 미노출.

### UI-4. 실제 API·WebSocket

- REST client와 runtime validation
- WebSocket subscribe·sequence·reconnect
- 지연·장애 복구

완료 조건: fixture와 staging 응답이 같은 contract test 통과.

### UI-5. 출시 검수

- viewport·browser·keyboard·screen reader
- reduced motion·contrast·zoom
- performance budget
- fake data production bundle 검사
- 디자이너 시각 QA

---

## 15. 검증

### 자동

- route·deep link·뒤로가기 test
- schema fixture validation
- component interaction test
- state matrix test
- visual regression by viewport
- accessibility rule scan
- WebSocket reconnect·old sequence test
- production bundle fixture import 검사

### 수동

- 디자이너: 시안 대비 시각 일치
- 제품: 정보 순서·문구·게이트
- 프론트: 접근성·상태 ownership
- 백엔드: payload 의미·stale·null
- 데이터: 수치·분모·Coverage·시각

---

## 16. 미결정 사항

| 항목 | 결정 주체 | 시점 |
|---|---|---|
| 다크 테마 market-up/down/warning 값 | 디자인 | 다크 토글 착수 시 |
| 다크 테마 토글 UI 위치 | 제품·디자인 | 설정 화면 착수 시 |
| Pretendard 공급 방식 (CDN vs self-host) | 프론트 | UI-1 |
| logo·아이콘 license | 제품·디자인 | UI-0 |
| 리서치 기능 요구사항 | 제품 | 착수 전 |
| 주도 종목 상세 화면 착수 시점 | 제품 | 후속 |
| 유사사례 pilot entitlement | 제품·백엔드 | 단계 7 |

미결정 항목을 시안의 현재 구현으로 자동 확정하지 않는다.

---

## 17. 변경 관리

- 시안 기준 commit을 옮기면 [§13.1](#131-기준-커밋-추종-정책)에 기록하고 §1·§7·§8을 갱신한다.
- 시각·화면 구성 변경은 시안이 먼저 바뀌고 이 문서가 따라간다.
- wire 의미 변경은 [api_contract.md](./api_contract.md)와 기계 schema를 먼저 갱신한다.
- 제품 범위·게이트 변경은 [PRD.md](./PRD.md)를 먼저 갱신한다.
- 구현 편의를 이유로 조건부 기능을 핵심 MVP로 승격하지 않는다.
