import { useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

/**
 * 뒤로 가기. 앱 밖에서 바로 들어온 화면(공유 링크·새 탭)에서는 되돌아갈 기록이 없어
 * `navigate(-1)`이 브라우저를 앱 밖으로 내보낸다. 그럴 때는 지정한 화면으로 보낸다.
 *
 * react-router는 이 세션에서 처음 들어온 위치의 `key`를 `default`로 준다.
 * 그 값이면 우리 화면을 거쳐 온 것이 아니다.
 */
export function useGoBack(fallback: string): () => void {
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from;

  return useCallback(() => {
    if (from) {
      navigate(from);
      return;
    }
    if (location.key === 'default') {
      navigate(fallback, { replace: true });
      return;
    }
    navigate(-1);
  }, [navigate, from, location.key, fallback]);
}
