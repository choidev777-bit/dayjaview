# Market Gateway fixture worker

이 worker는 키움 원천 payload를 `market-event.v1` canonical event로 바꾸는 오프라인 경계만 실행한다.

- 주문·계좌 API surface가 없는 `ReadOnlyKiwoomPort`만 사용한다.
- 입력은 synthetic fixture JSON으로 제한한다.
- credential, 실제 로그인, 장중 WebSocket 접속, 저장 capture·replay를 수행하지 않는다.
- 라이브 검증 상태는 `PENDING_EXTERNAL`이며 `B-MARKET-FIXTURE`를 해제하지 않는다.

fixture 실행 예시:

```text
uv run python apps/worker-market/fixture_worker.py --fixture tests/market-gateway/fixtures/kiwoom-market-v1.json
```
