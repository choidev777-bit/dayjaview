# ADR-010. 팀 소유 비공개 production 저장소

- 상태: `accepted`
- 결정일: 2026-08-14
- 적용 범위: DAYJAVIEW 제품 코드·인프라·계약·문서
- 관련 문서: [system_architecture.md](../system_architecture.md), [ui_prototype_adaptation_plan.md](../ui_prototype_adaptation_plan.md), [implementation_roadmap.md](../implementation_roadmap.md)

## 배경

`nangom/dayjaview-prototype`은 UI/UX 디자이너가 만든 시각·상호작용 참고 원본이다. 현재 화면 구조와 하드코딩 데이터는 DAYJAVIEW 제품 명세와 다르며, 제품용 백엔드·계약·인프라·연구 코드까지 그 저장소에 결합하면 디자이너의 원본 작업 흐름과 production 변경 이력이 섞인다.

현재 제품 작업물은 `C:\dayjaview`에 있으며 아직 production 원격 저장소가 연결되지 않았다.

## 결정

1. 디자이너의 `nangom/dayjaview-prototype` 저장소와 기준 commit은 변경하지 않는 참고 원본으로 유지한다.
2. 현재 `C:\dayjaview` 작업물을 팀 소유의 새로운 비공개 production GitHub 저장소로 승격한다.
3. 프론트엔드·백엔드·계약·infra·운영 문서·연구 코드는 ADR-001의 단일 monorepo에 둔다.
4. 디자이너 원본의 시각 자산·token·layout·motion·화면 구성을 그대로 이식한다. 하드코딩 데이터와 화면 전환 방식은 제품 계약·router로 교체한다. 이식 범위와 기준 커밋은 [ui_prototype_adaptation_plan.md](../ui_prototype_adaptation_plan.md)를 따른다.
5. 제품 기능은 production 저장소에서만 구현하며 두 저장소에 같은 기능을 병렬 구현하지 않는다.
6. 저장소 생성 시 실제 GitHub owner·URL, 관리자, reviewer, branch protection, CI와 OCI 배포 권한을 설정한다.
7. production secret, `.env.local`, SSH private key, browser storage state, 운영 데이터는 commit하지 않는다.

## 검토한 대안

### 디자이너 저장소를 production으로 승격

초기 화면 코드를 바로 사용할 수 있지만 제품 구조의 대규모 교체와 디자이너 작업 이력이 충돌하고 소유권·배포 권한이 혼재한다. 채택하지 않았다.

### 프론트·백엔드를 별도 저장소로 즉시 분리

팀과 계약이 아직 빠르게 변하는 단계에서 schema·fixture·배포 변경의 원자성이 약해진다. 독립 팀과 배포 주기가 생기기 전에는 채택하지 않는다.

### 새 production monorepo

디자인 원본을 보존하면서 API 계약·fixture·애플리케이션·infra를 한 변경 단위로 검증할 수 있어 채택했다.

## 생성·승격 체크

- [ ] 팀이 관리할 실제 GitHub owner 또는 organization 확정
- [ ] 비공개 원격 저장소 생성과 `C:\dayjaview` 초기 commit
- [ ] 기본 branch와 branch protection 설정
- [ ] 관리자·reviewer·배포 주체에 최소 권한 부여
- [ ] secret scanning과 의존성 검사 활성화
- [ ] `.gitignore`와 추적 파일에 비밀정보·운영 데이터가 없는지 검사
- [ ] CI와 OCI 배포 credential을 저장소 secret에 등록
- [ ] 디자이너 원본 저장소·commit을 참고 출처로 기록
