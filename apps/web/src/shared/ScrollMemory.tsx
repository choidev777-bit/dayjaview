import { useEffect } from 'react';
import { useLocation, useNavigationType } from 'react-router-dom';
import { readViewState, writeViewState } from './viewState';

/**
 * 뒤로 가기로 돌아온 화면은 떠날 때 보던 위치로 되돌리고, 새로 들어간 화면은 맨 위에서 시작한다
 * (ui_prototype_adaptation_plan §5.1).
 *
 * 되돌릴 위치는 화면이 그려진 뒤에야 정할 수 있다. 데이터를 불러오는 동안에는 문서가 짧아
 * 곧바로 scrollTo를 부르면 0으로 잘린다. 두 프레임 동안 다시 시도한다.
 */
export function ScrollMemory() {
  const location = useLocation();
  const navigationType = useNavigationType();
  const key = `scroll:${location.pathname}`;

  useEffect(() => {
    let frame = 0;
    let attempts = 0;
    const target = navigationType === 'POP' ? (readViewState<number>(key) ?? 0) : 0;

    const restore = () => {
      // 이미 그 자리면 부르지 않는다. 레이아웃이 없는 시험 환경에서 불필요한 경고가 쌓인다.
      if (Math.abs(window.scrollY - target) > 1) {
        window.scrollTo({ top: target, behavior: 'auto' });
      }
      attempts += 1;
      // 문서가 아직 짧아 목표까지 못 갔으면 다음 프레임에 다시 시도한다.
      if (attempts < 12 && target > 0 && Math.abs(window.scrollY - target) > 1) {
        frame = window.requestAnimationFrame(restore);
      }
    };
    frame = window.requestAnimationFrame(restore);

    const remember = () => writeViewState(key, window.scrollY);
    window.addEventListener('scroll', remember, { passive: true });
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('scroll', remember);
      remember();
    };
  }, [key, navigationType]);

  return null;
}
