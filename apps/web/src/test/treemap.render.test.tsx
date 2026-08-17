import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TreemapItem } from '../domain/contracts';
import { ThemeTreemap } from '../shared/ThemeTreemap';

const CONTAINER = { width: 396, height: 420 };

class StubResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}

  observe(target: Element) {
    this.callback(
      [{ target, contentRect: { ...CONTAINER } } as unknown as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    );
  }

  unobserve() {}

  disconnect() {}
}

function item(name: string, weightedReturn: number): TreemapItem {
  return {
    eventId: `evt_${name}`,
    themeId: `thm_${name}`,
    displayName: name,
    lifecycleStatus: 'ACTIVE',
    weightedReturn,
    advancingCount: 3,
    validCount: 4,
    coverageStatus: 'SUFFICIENT',
    qualityFlags: [],
  };
}

const items = [
  item('원전수출', 0.027),
  item('전력설비', 0.023),
  item('조선기자재', 0.019),
  item('방산', 0.016),
  item('반도체 장비', 0.014),
  item('로봇', 0.012),
  item('바이오', 0.009),
  item('2차전지', 0.007),
];

function renderTreemap(motion = true, tiles = items) {
  return render(
    <MemoryRouter>
      <ThemeTreemap items={tiles} motion={motion} />
    </MemoryRouter>,
  );
}

function tileBoxes() {
  return [...document.querySelectorAll<HTMLElement>('.treemap__cell')].map((cell) => ({
    left: Number.parseFloat(cell.style.left),
    top: Number.parseFloat(cell.style.top),
    width: Number.parseFloat(cell.style.width),
    height: Number.parseFloat(cell.style.height),
  }));
}

describe('실시간 트리맵 렌더링', () => {
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', StubResizeObserver);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('면적이 큰 테마가 먼저 오고 칸 모양이 지나치게 길쭉하지 않다', () => {
    renderTreemap();

    const boxes = tileBoxes();
    expect(boxes).toHaveLength(8);

    const areas = boxes.map((box) => box.width * box.height);
    expect(areas[0]).toBeGreaterThan(areas[1]);
    expect(areas[1]).toBeGreaterThan(areas.at(-1) ?? 0);

    // 재귀 이분할은 줄 수를 고정하지 않는다. 대신 어느 칸도 띠처럼 눕지 않아야 한다.
    boxes.forEach((box) => {
      const ratio = Math.max(box.width / box.height, box.height / box.width);
      expect(ratio).toBeLessThan(6);
    });
  });

  it('블록 크기에 따라 라벨을 줄이되 접근성 이름은 유지한다', () => {
    renderTreemap();

    const map = screen.getByRole('list', { name: '실시간 테마 강도 지도, 총 8개' });
    const largest = within(map).getByRole('link', { name: /원전수출, 테마 수익률 \+2\.7%/ });
    expect(largest).toHaveAttribute(
      'href',
      '/themes/thm_%EC%9B%90%EC%A0%84%EC%88%98%EC%B6%9C/events/evt_%EC%9B%90%EC%A0%84%EC%88%98%EC%B6%9C',
    );
    expect(largest.dataset.tier).toBe('full');
    // 타일 본문은 테마명 + 수익률만 둔다. 상승 종목 수는 접근성 이름에만 남긴다.
    expect(largest).toHaveTextContent('+2.7%');
    expect(largest).not.toHaveTextContent('3 / 4종목 상승');

    const smallest = within(map).getByRole('link', { name: /2차전지, 테마 수익률 \+0\.7%/ });
    expect(smallest.dataset.tier).toBe('compact');
    expect(smallest).toHaveTextContent('+0.7%');
    expect(smallest).not.toHaveTextContent('3 / 4종목 상승');
  });

  it('12개까지 늘어나 타일이 작아져도 이름만 남기고 수익률은 접근성 이름에 유지한다', () => {
    const twelve = [
      ...items,
      item('전선', 0.006),
      item('LED 장비', 0.005),
      item('건설 중소형', 0.004),
      item('AI 인프라', 0.003),
    ];
    renderTreemap(true, twelve);

    const map = screen.getByRole('list', { name: '실시간 테마 강도 지도, 총 12개' });
    const smallest = within(map).getByRole('link', { name: /AI 인프라, 테마 수익률 \+0\.3%/ });
    expect(smallest.dataset.tier).toBe('minimal');
    expect(smallest).not.toHaveTextContent('+0.3%');
    expect(smallest).toHaveTextContent('AI 인프라');
  });

  it('장 마감·수신 지연에서는 블록 애니메이션을 멈춘다', () => {
    renderTreemap(false);

    expect(screen.getByRole('list').dataset.motion).toBe('off');
  });
});
