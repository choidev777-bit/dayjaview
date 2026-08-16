/**
 * 화면을 떠났다가 돌아왔을 때 보고 있던 자리를 되살리기 위한 값 보관소
 * (ui_prototype_adaptation_plan §5.1).
 *
 * 서버에 보내지 않고 새로고침하면 사라지는, 이번 세션 안에서만 쓰는 값이다.
 * 순위·계산 결과에는 영향을 주지 않는다.
 */
const store = new Map<string, unknown>();

export function readViewState<T>(key: string): T | undefined {
  return store.get(key) as T | undefined;
}

export function writeViewState(key: string, value: unknown): void {
  store.set(key, value);
}

export function clearViewState(): void {
  store.clear();
}
