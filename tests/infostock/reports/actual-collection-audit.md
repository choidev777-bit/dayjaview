# Infostock 기존 수집본 품질 보고

- 전체 상태: PARTIAL
- dataset hash: `4959314b09c1152f2e9ec3365a7be2f890647eede74a46991e8e2d6a2ff12017`
- parser version: `infostock-existing-collection/2.0.0`
- Theme DB: COMPLETE
  - theme: 280/280
  - history: 39,696건
  - current related stock: 6,629건
  - leader: 65,526건
  - historical membership: 652,241건
  - 원본 history 중복: 4건(보존)
  - leader code 누락: 90건(보존)
  - historical membership code 누락: 7,498건(보존)
  - legacy history의 memberStocks field 누락: 274건
- DailyFeaturedTheme: BLOCKED
  - 확보 목록: 5건, 본문: 1건, 관계: 232건
  - pagination: 1페이지만 확보, next=2, 전체 기간 미완료
  - blocker: B-INFOSTOCK-AUTH, B-DATA-RIGHTS

Daily 실제 전체 backfill이 완료되지 않았으므로 S1 전체 DB 상태는 PARTIAL입니다.
