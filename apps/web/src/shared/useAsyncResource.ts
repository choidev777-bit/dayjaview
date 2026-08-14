import { useCallback, useEffect, useState } from 'react';

export type AsyncResource<T> =
  | { status: 'loading'; data: null; error: null; retry: () => void }
  | { status: 'success'; data: T; error: null; retry: () => void }
  | { status: 'error'; data: null; error: Error; retry: () => void };

export function useAsyncResource<T>(loader: () => Promise<T>, dependencies: readonly unknown[]): AsyncResource<T> {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<Omit<AsyncResource<T>, 'retry'>>({
    status: 'loading',
    data: null,
    error: null,
  });

  const retry = useCallback(() => {
    setState({ status: 'loading', data: null, error: null });
    setAttempt((value) => value + 1);
  }, []);

  useEffect(() => {
    let active = true;
    Promise.resolve()
      .then(() => {
        if (active) setState({ status: 'loading', data: null, error: null });
        return loader();
      })
      .then(
      (data) => {
        if (active) setState({ status: 'success', data, error: null });
      },
      (error: unknown) => {
        if (active) {
          setState({
            status: 'error',
            data: null,
            error: error instanceof Error ? error : new Error('알 수 없는 오류가 발생했습니다.'),
          });
        }
      },
    );
    return () => {
      active = false;
    };
    // Callers provide stable repository methods and explicit resource keys.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, attempt]);

  return { ...state, retry } as AsyncResource<T>;
}
