import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type { TreemapItem } from '../domain/contracts';
import { formatReturn, returnTone } from '../domain/formatting';
import {
  TREEMAP_GAP,
  TREEMAP_LAYOUT_INTERVAL_MS,
  TREEMAP_VALUE_INTERVAL_MS,
  layoutTreemap,
} from '../domain/treemap';

function signature(items: TreemapItem[]): string {
  return items
    .map((item) => `${item.themeId}:${item.weightedReturn}:${item.advancingCount}/${item.validCount}`)
    .join('|');
}

/**
 * 값이 바뀐 경우에만, 그것도 intervalMs에 한 번만 화면에 반영한다.
 * 변화가 없으면 재배치도 애니메이션도 실행하지 않는다 (treemap plan §5.4).
 */
function useThrottledItems(items: TreemapItem[], intervalMs: number): TreemapItem[] {
  const key = signature(items);
  const [committed, setCommitted] = useState({ key, items });
  const appliedAt = useRef(0);

  useEffect(() => {
    if (committed.key === key) return undefined;
    const wait = Math.max(0, intervalMs - (Date.now() - appliedAt.current));
    const timer = window.setTimeout(() => {
      appliedAt.current = Date.now();
      setCommitted({ key, items });
    }, wait);
    return () => window.clearTimeout(timer);
  }, [key, items, committed.key, intervalMs]);

  return committed.items;
}

export function ThemeTreemap({ items, motion }: { items: TreemapItem[]; motion: boolean }) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  const layoutItems = useThrottledItems(items, TREEMAP_LAYOUT_INTERVAL_MS);
  const valueItems = useThrottledItems(items, TREEMAP_VALUE_INTERVAL_MS);

  const rects = useMemo(
    () => layoutTreemap(layoutItems, size.width, size.height),
    [layoutItems, size],
  );
  const values = useMemo(
    () => new Map(valueItems.map((item) => [item.themeId, item])),
    [valueItems],
  );

  // 붙는 즉시 한 번 재고, 이후 크기 변화는 ResizeObserver로 따라간다.
  // ResizeObserver 콜백이 오지 않는 환경(비표시 탭 등)에서도 첫 배치가 나온다.
  const measureContainer = useCallback((node: HTMLUListElement | null) => {
    const applySize = (width: number, height: number) => {
      setSize((current) =>
        current.width === width && current.height === height ? current : { width, height },
      );
    };
    if (!node) return undefined;
    applySize(
      Math.max(0, node.clientWidth - TREEMAP_GAP * 2),
      Math.max(0, node.clientHeight - TREEMAP_GAP * 2),
    );
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (box) applySize(box.width, box.height);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <ul
      ref={measureContainer}
      className="treemap"
      role="list"
      aria-label={`실시간 테마 강도 지도, 총 ${rects.length.toLocaleString('ko-KR')}개`}
      data-motion={motion ? 'on' : 'off'}
    >
      {rects.map((rect) => {
        // 면적은 1초 주기 레이아웃, 숫자와 색상은 500ms 주기 최신값을 쓴다.
        const item = values.get(rect.item.themeId) ?? rect.item;
        const value = formatReturn(item.weightedReturn);
        return (
          <li
            key={item.themeId}
            className="treemap__cell"
            style={{
              left: `${rect.x + TREEMAP_GAP}px`,
              top: `${rect.y + TREEMAP_GAP}px`,
              width: `${rect.width}px`,
              height: `${rect.height}px`,
            }}
          >
            <Link
              className="treemap__tile"
              to={`/themes/${encodeURIComponent(item.themeId)}/events/${encodeURIComponent(item.eventId)}`}
              state={{ from: '/insights' }}
              data-tier={rect.tier}
              data-tone={rect.tone}
              style={{ '--tile-intensity': rect.intensity } as React.CSSProperties}
              aria-label={`${item.displayName}, 테마 수익률 ${value}, 관련주 ${item.advancingCount} / ${item.validCount}종목 상승`}
            >
              <strong>{item.displayName}</strong>
              {rect.tier === 'minimal' ? null : (
                <b className={returnTone(item.weightedReturn)}>{value}</b>
              )}
              {rect.tier === 'full' ? (
                <small>
                  {item.advancingCount} / {item.validCount}종목 상승
                </small>
              ) : null}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
