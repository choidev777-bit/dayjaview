import { useLayoutEffect, useRef, type KeyboardEvent, type PointerEvent } from 'react';
import { Link } from 'react-router-dom';
import type { RankingItem } from '../domain/contracts';
import { formatReturn, returnTone } from '../domain/formatting';
import { CoverageIndicator } from './CoverageIndicator';
import { readViewState, writeViewState } from './viewState';

const CARD_GAP = 8;
const INTRO_STEP_MS = 1000;
/** 끌어 옮긴 거리를 그대로 스크롤에 주면 카드가 손보다 빨리 지나간다 (팀 요청). */
const DRAG_DAMPING = 0.55;
/** 휠 한 번에 카드 여러 장이 넘어가지 않게 줄인다. */
const WHEEL_DAMPING = 0.45;
/** 손을 뗀 뒤 가장 가까운 카드로 맞추기까지 기다리는 시간. */
const SNAP_DELAY_MS = 90;
const WHEEL_INDEX_KEY = 'today.wheelIndex';

function badgeLabel(item: RankingItem): string | null {
  if (item.badges.includes('RISING_FAST')) return '급부상';
  if (item.classification.kind === 'TEMPORARY_THEME') return '신규·임시';
  if (item.lifecycleStatus === 'WEAKENING') return '약화';
  return null;
}

/**
 * 시안 홈의 순위 휠. 시각과 조작감은 시안 그대로 두고,
 * DOM 복제 3벌은 1벌로 줄이고 방향키 이동을 더했다 (adaptation plan §12.1).
 */
export function ThemeRankingWheel({ items }: { items: RankingItem[] }) {
  const wheelRef = useRef<HTMLUListElement>(null);
  const dragRef = useRef({ pointerId: -1, startY: 0, startTop: 0, moved: false });
  const frameRef = useRef<number | undefined>(undefined);
  const introTimersRef = useRef<number[]>([]);
  const introPlayedRef = useRef(false);
  // 가장 가까운 카드로 맞추는 동작. effect 안에서 만들어 두고 포인터 처리에서 부른다.
  const snapRef = useRef<(() => void) | null>(null);
  const snapTimerRef = useRef<number | undefined>(undefined);

  useLayoutEffect(() => {
    const wheel = wheelRef.current;
    if (!wheel) return undefined;

    const cards = Array.from(wheel.querySelectorAll<HTMLElement>('[data-theme-card]'));
    const cardHeight = cards[0]?.offsetHeight ?? 0;
    // jsdom처럼 레이아웃이 없는 환경에서는 곡률 계산과 자동 스크롤을 건너뛴다.
    if (!cardHeight || !wheel.clientHeight) return undefined;

    const step = cardHeight + CARD_GAP;
    const timers = introTimersRef.current;

    const applyPadding = () => {
      const pad = Math.max(0, (wheel.clientHeight - cardHeight) / 2);
      wheel.style.paddingTop = `${pad}px`;
      wheel.style.paddingBottom = `${pad}px`;
    };

    const paint = () => {
      frameRef.current = undefined;
      const bounds = wheel.getBoundingClientRect();
      const focusY = bounds.top + bounds.height / 2;

      cards.forEach((card) => {
        const cardBounds = card.getBoundingClientRect();
        const signedDistance = (cardBounds.top + cardBounds.height / 2 - focusY) / step;
        const distance = Math.abs(signedDistance);
        const boundedDistance = Math.max(-2.2, Math.min(2.2, signedDistance));
        const emphasis = Math.max(0, 1 - distance);
        // 세로 원통의 앞면처럼 초점에서 멀어질수록 회전하며 뒤로 물러난다.
        const visualDistance = Math.min(distance, 2.2);
        card.style.setProperty('--wheel-scale', String(0.985 + emphasis * 0.015));
        card.style.setProperty('--wheel-opacity', String(Math.max(0.34, 1 - Math.max(0, distance - 1) * 0.16)));
        card.style.setProperty('--wheel-rotate', `${Math.max(-17, Math.min(17, -boundedDistance * 8))}deg`);
        card.style.setProperty('--wheel-depth', `${Math.max(-26, 12 - visualDistance * 17)}px`);
        card.style.setProperty('--wheel-curve', `${Math.sign(boundedDistance) * Math.min(2, visualDistance * 0.8)}px`);
        // 0.72로 두면 카드 사이에 멈췄을 때 위아래 둘 다 강조된다. 가장 가까운 하나만 켠다.
        card.dataset.focused = distance < 0.5 ? 'true' : 'false';
      });
    };

    const schedulePaint = () => {
      if (frameRef.current) return;
      frameRef.current = window.requestAnimationFrame(paint);
    };

    applyPadding();

    // 시안과 같은 무한 휠. 목록을 3벌 그려 놓고 가운데 벌만 보이게 유지한다.
    // 한 벌만 그리면 1위 위쪽과 꼴찌 아래쪽이 빈 채로 남아 목록이 끊겨 보인다.
    const tripled = cards.length === items.length * 3;
    const cycleHeight = step * items.length;
    const primaryFirst = cards[tripled ? items.length : 0];
    let baseTop = 0;
    if (primaryFirst) {
      baseTop = Math.max(0, primaryFirst.offsetTop - (wheel.clientHeight - cardHeight) / 2);
      wheel.scrollTop = baseTop;
    }

    // 끝을 넘어가면 같은 화면을 유지한 채 한 바퀴만큼 되돌린다. 복제본이 같은 자리에 있어
    // 사용자에게는 계속 이어지는 것처럼 보인다.
    const keepPrimaryCopy = () => {
      if (!tripled) return;
      const wrapThreshold = step * 0.65;
      if (wheel.scrollTop > baseTop + cycleHeight + wrapThreshold) wheel.scrollTop -= cycleHeight;
      else if (wheel.scrollTop < baseTop - wrapThreshold) wheel.scrollTop += cycleHeight;
    };

    const handleScroll = () => {
      keepPrimaryCopy();
      schedulePaint();
    };

    // 지금 화면 가운데에 가장 가까운 카드의 순서. 위치를 기억하고 되살릴 때 쓴다.
    const currentIndex = () =>
      ((Math.round((wheel.scrollTop - baseTop) / step) % items.length) + items.length) % items.length;

    const snapToNearest = () => {
      const top = baseTop + Math.round((wheel.scrollTop - baseTop) / step) * step;
      if (Math.abs(top - wheel.scrollTop) < 1) return;
      wheel.scrollTo({ top, behavior: 'smooth' });
    };
    snapRef.current = snapToNearest;

    // 휠은 브라우저 기본 속도가 빨라 한 번에 여러 장이 넘어간다. 직접 굴리고 멈추면 맞춘다.
    const handleWheelEvent = (event: globalThis.WheelEvent) => {
      cancelIntro();
      event.preventDefault();
      wheel.scrollTop += event.deltaY * WHEEL_DAMPING;
      window.clearTimeout(snapTimerRef.current);
      snapTimerRef.current = window.setTimeout(snapToNearest, SNAP_DELAY_MS * 2);
    };

    paint();
    wheel.addEventListener('scroll', handleScroll, { passive: true });
    wheel.addEventListener('wheel', handleWheelEvent, { passive: false });
    window.addEventListener('resize', applyPadding);

    // 뒤로 가기로 돌아왔으면 보던 카드로 되돌리고 첫 진입 연출은 건너뛴다 (팀 요청).
    const remembered = readViewState<number>(WHEEL_INDEX_KEY);
    if (remembered !== undefined && remembered > 0 && remembered < items.length) {
      wheel.scrollTop = baseTop + step * remembered;
      introPlayedRef.current = true;
      paint();
    }

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!introPlayedRef.current && !reducedMotion && items.length > 1) {
      // 한 바퀴를 그대로 이어서 굴린다. 복제본이 세 벌이라 마지막 칸을 지나도 화면은
      // 이어지고, 끝난 자리는 다음 벌의 1위라 보이는 그림이 처음과 같다.
      //
      // 예전에는 마지막 칸에서 `behavior: 'auto'`로 baseTop에 되돌렸는데, 앞 칸의
      // smooth 이동이 끝나기 전에 한 바퀴를 거슬러 점프해서 목록이 끊겨 보였다.
      for (let stepIndex = 1; stepIndex <= items.length; stepIndex += 1) {
        timers.push(
          window.setTimeout(() => {
            // 예약 시점에 `재생함`으로 찍으면 StrictMode가 effect를 두 번 부를 때
            // 첫 번째 pass가 flag를 태우고 그 cleanup이 타이머를 지워, 두 번째 pass는
            // 건너뛰어 결국 한 번도 안 돈다. 실제로 시작할 때 찍는다.
            introPlayedRef.current = true;
            wheel.scrollTo({ top: baseTop + step * stepIndex, behavior: 'smooth' });
          }, INTRO_STEP_MS * stepIndex),
        );
      }
    }

    return () => {
      writeViewState(WHEEL_INDEX_KEY, currentIndex());
      wheel.removeEventListener('scroll', handleScroll);
      wheel.removeEventListener('wheel', handleWheelEvent);
      window.removeEventListener('resize', applyPadding);
      window.clearTimeout(snapTimerRef.current);
      snapRef.current = null;
      if (frameRef.current) window.cancelAnimationFrame(frameRef.current);
      timers.forEach((timer) => window.clearTimeout(timer));
      timers.length = 0;
    };
  }, [items]);

  function cancelIntro() {
    introTimersRef.current.forEach((timer) => window.clearTimeout(timer));
    introTimersRef.current.length = 0;
  }

  function focusCard(index: number) {
    const cards = wheelRef.current?.querySelectorAll<HTMLElement>('[data-theme-card][data-copy="main"]');
    const card = cards?.[Math.max(0, Math.min(cards.length - 1, index))];
    if (!card) return;
    cancelIntro();
    card.focus();
    card.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
  }

  function handleKeyDown(event: KeyboardEvent<HTMLUListElement>) {
    const cards = Array.from(
      wheelRef.current?.querySelectorAll<HTMLElement>('[data-theme-card][data-copy="main"]') ?? [],
    );
    const current = cards.findIndex((card) => card === document.activeElement);
    if (event.key === ' ' && current >= 0) {
      event.preventDefault();
      cards[current].click();
      return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'ArrowDown') focusCard(current + 1);
    if (event.key === 'ArrowUp') focusCard(current <= 0 ? 0 : current - 1);
    if (event.key === 'Home') focusCard(0);
    if (event.key === 'End') focusCard(cards.length - 1);
  }

  function handlePointerDown(event: PointerEvent<HTMLUListElement>) {
    cancelIntro();
    const wheel = wheelRef.current;
    if (!wheel || event.pointerType !== 'mouse' || event.button !== 0) return;
    dragRef.current = { pointerId: event.pointerId, startY: event.clientY, startTop: wheel.scrollTop, moved: false };
  }

  function handlePointerMove(event: PointerEvent<HTMLUListElement>) {
    const wheel = wheelRef.current;
    const drag = dragRef.current;
    if (!wheel || event.pointerId !== drag.pointerId) return;
    const delta = event.clientY - drag.startY;
    if (Math.abs(delta) > 12 && !drag.moved) {
      drag.moved = true;
      wheel.setPointerCapture(event.pointerId);
      wheel.dataset.dragging = 'true';
    }
    if (drag.moved) wheel.scrollTop = drag.startTop - delta * DRAG_DAMPING;
  }

  function handlePointerUp(event: PointerEvent<HTMLUListElement>) {
    const wheel = wheelRef.current;
    if (!wheel || event.pointerId !== dragRef.current.pointerId) return;
    if (wheel.hasPointerCapture(event.pointerId)) wheel.releasePointerCapture(event.pointerId);
    delete wheel.dataset.dragging;
    dragRef.current.pointerId = -1;
    // 카드 사이에 걸쳐 멈추지 않게 손을 뗀 뒤 가장 가까운 카드로 맞춘다.
    window.clearTimeout(snapTimerRef.current);
    snapTimerRef.current = window.setTimeout(() => snapRef.current?.(), SNAP_DELAY_MS);
  }

  return (
    <ul
      ref={wheelRef}
      className="wheel"
      role="list"
      aria-label={`오늘 많이 오른 테마 순위, 총 ${items.length.toLocaleString('ko-KR')}개`}
      onKeyDown={handleKeyDown}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
    >
      {/* 3벌 중 가운데 벌만 실제 목록이다. 앞뒤 복제본은 이어져 보이게 하는 장식이라
          보조기술과 키보드에서 감춘다. 그래야 같은 테마가 세 번 읽히지 않는다.
          항목이 적으면 감아 돌 일이 없으므로 복제하지 않는다. */}
      {(items.length >= 4 ? [0, 1, 2] : [1]).flatMap((copy) =>
        items.map((item) => {
        const primary = copy === 1;
        const badge = badgeLabel(item);
        const path = `/themes/${encodeURIComponent(item.classification.themeId)}/events/${encodeURIComponent(item.eventId)}`;
        return (
          <li key={`${copy}-${item.eventId}`} aria-hidden={primary ? undefined : 'true'}>
            <Link
              data-theme-card
              data-copy={primary ? 'main' : 'clone'}
              data-top={item.rank <= 3 ? 'true' : 'false'}
              className="wheel-card"
              to={path}
              tabIndex={primary ? undefined : -1}
              state={{ from: '/today' }}
              onClick={(event) => {
                if (!dragRef.current.moved) return;
                event.preventDefault();
                dragRef.current.moved = false;
              }}
              aria-label={`${item.rank}위 ${item.classification.displayName}, 테마 수익률 ${formatReturn(item.weightedReturn)}`}
            >
              <span className="wheel-card__rank" aria-hidden="true">{item.rank}</span>
              <span className="wheel-card__copy">
                {/* 뱃지는 테마를 꾸미는 말이라 이름 옆에 둔다. 아래로 내리면 줄이 하나 늘고
                    어느 값에 걸린 뱃지인지도 흐려진다. */}
                <span className="wheel-card__name">
                  <strong>{item.classification.displayName}</strong>
                  {badge ? <span className="badge wheel-card__badge">{badge}</span> : null}
                </span>
                {/* 상승 종목 수는 테마 상세의 `상승 종목` 칸에 있다. 목록에서는 이름과 수익률만
                    남겨 줄을 하나로 줄인다. Coverage 미달만 상태로 알린다 (screen_spec 4.5). */}
                {item.coverage.status === 'SUFFICIENT' ? null : (
                  <small className="wheel-card__meta">
                    <CoverageIndicator coverage={item.coverage} />
                  </small>
                )}
              </span>
              <b className={`wheel-card__value ${returnTone(item.weightedReturn)}`}>
                {formatReturn(item.weightedReturn)}
              </b>
            </Link>
          </li>
        );
        }),
      )}
    </ul>
  );
}
