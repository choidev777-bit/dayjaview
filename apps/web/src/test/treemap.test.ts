import { describe, expect, it } from 'vitest';
import rankingLiveFixture from '../../../../contracts/fixtures/rankings/live.json';
import treemapLiveFixture from '../../../../contracts/fixtures/treemap/live.json';
import type { RankingResponse, TreemapItem, TreemapResponse } from '../domain/contracts';
import {
  TREEMAP_GAP,
  isSnapshotStale,
  layoutTreemap,
  selectTreemapItems,
  treemapIntensity,
  treemapIntensityScale,
  treemapLabelTier,
  treemapRows,
} from '../domain/treemap';

const rankingLive = rankingLiveFixture as unknown as RankingResponse;
const treemapLive = treemapLiveFixture as unknown as TreemapResponse;

function item(themeId: string, weightedReturn: number, overrides: Partial<TreemapItem> = {}): TreemapItem {
  return {
    eventId: `evt_${themeId}`,
    themeId,
    displayName: themeId,
    lifecycleStatus: 'ACTIVE',
    weightedReturn,
    advancingCount: 3,
    validCount: 4,
    coverageStatus: 'SUFFICIENT',
    qualityFlags: [],
    ...overrides,
  };
}

describe('트리맵 후보 선택', () => {
  it('상승률 내림차순 상위 12개만 남긴다', () => {
    const items = Array.from({ length: 20 }, (_, index) => item(`t${index}`, (index + 1) / 1000));

    const selected = selectTreemapItems(items);

    expect(selected).toHaveLength(12);
    expect(selected[0].weightedReturn).toBe(0.02);
    expect(selected.map((entry) => entry.weightedReturn)).toEqual(
      [...selected.map((entry) => entry.weightedReturn)].sort((left, right) => right - left),
    );
  });

  it('0% 이하와 Coverage 미달 테마를 0% 타일로 만들지 않고 제외한다', () => {
    const selected = selectTreemapItems([
      item('up', 0.02),
      item('flat', 0),
      item('down', -0.01),
      item('uncovered', 0.03, { coverageStatus: 'INSUFFICIENT' }),
    ]);

    expect(selected.map((entry) => entry.themeId)).toEqual(['up']);
  });

  it('시안 3단 배치대로 상단 2 · 중단 3 · 하단 나머지로 나눈다', () => {
    const eight = Array.from({ length: 8 }, (_, index) => item(`t${index}`, (8 - index) / 100));
    const twelve = Array.from({ length: 12 }, (_, index) => item(`t${index}`, (12 - index) / 100));

    expect(treemapRows(eight).map((row) => row.length)).toEqual([2, 3, 3]);
    expect(treemapRows(twelve).map((row) => row.length)).toEqual([2, 3, 3, 3, 1]);
    expect(treemapRows(eight.slice(0, 2)).map((row) => row.length)).toEqual([2]);
  });
});

describe('트리맵 색상과 라벨', () => {
  it('색상 강도는 |수익률| ÷ 5%이며 1에서 멈춘다', () => {
    expect(treemapIntensity(0)).toBe(0);
    expect(treemapIntensity(0.025)).toBeCloseTo(0.5, 6);
    expect(treemapIntensity(0.05)).toBe(1);
    expect(treemapIntensity(0.12)).toBe(1);
    expect(treemapIntensity(-0.025)).toBeCloseTo(0.5, 6);
  });

  it('강한 날에도 색이 뭉치지 않게 그날 범위로 다시 편다', () => {
    // 전부 5%를 넘겨서 고정 기준으로는 12개가 모두 1이 되던 상황이다.
    const strongDay = [item('a', 0.104), item('b', 0.088), item('c', 0.074)];
    strongDay.forEach((entry) => expect(treemapIntensity(entry.weightedReturn)).toBe(1));

    const scale = treemapIntensityScale(strongDay);
    expect(scale(0.104)).toBeCloseTo(1, 6);
    expect(scale(0.074)).toBeCloseTo(0.3, 6);
    expect(scale(0.088)).toBeGreaterThan(scale(0.074));
    expect(scale(0.088)).toBeLessThan(scale(0.104));
  });

  it('값이 하나뿐이거나 모두 같으면 고정 기준으로 돌아간다', () => {
    expect(treemapIntensityScale([item('a', 0.02)])(0.02)).toBeCloseTo(treemapIntensity(0.02), 6);
    expect(treemapIntensityScale([])(0.03)).toBeCloseTo(treemapIntensity(0.03), 6);
  });

  it('블록 크기로 라벨 단계를 정한다', () => {
    expect(treemapLabelTier(110, 70)).toBe('full');
    expect(treemapLabelTier(109, 70)).toBe('compact');
    expect(treemapLabelTier(70, 48)).toBe('compact');
    expect(treemapLabelTier(69, 48)).toBe('minimal');
    expect(treemapLabelTier(70, 47)).toBe('minimal');
  });
});

describe('트리맵 배치', () => {
  const items = [
    item('a', 0.027),
    item('b', 0.023),
    item('c', 0.019),
    item('d', 0.016),
    item('e', 0.014),
    item('f', 0.012),
    item('g', 0.009),
    item('h', 0.007),
  ];

  it('면적이 수익률 원값에 비례한다', () => {
    const rects = layoutTreemap(items, 396, 420);
    const total = items.reduce((sum, entry) => sum + entry.weightedReturn, 0);
    const areaTotal = rects.reduce((sum, rect) => sum + rect.width * rect.height, 0);

    rects.forEach((rect) => {
      expect(rect.width * rect.height / areaTotal).toBeCloseTo(rect.item.weightedReturn / total, 3);
    });
  });

  it('모든 사각형이 컨테이너 안에 있고 겹치지 않는다', () => {
    const rects = layoutTreemap(items, 396, 420);

    rects.forEach((rect) => {
      expect(rect.x).toBeGreaterThanOrEqual(0);
      expect(rect.y).toBeGreaterThanOrEqual(0);
      expect(rect.x + rect.width).toBeLessThanOrEqual(396 + 0.001);
      expect(rect.y + rect.height).toBeLessThanOrEqual(420 + 0.001);
    });

    rects.forEach((left, leftIndex) => {
      rects.slice(leftIndex + 1).forEach((right) => {
        const overlapX = left.x < right.x + right.width && right.x < left.x + left.width;
        const overlapY = left.y < right.y + right.height && right.y < left.y + left.height;
        expect(overlapX && overlapY).toBe(false);
      });
    });
  });

  it('같은 입력은 같은 좌표를 만든다', () => {
    expect(layoutTreemap(items, 396, 420)).toEqual(layoutTreemap(items, 396, 420));
  });

  it('행과 열 사이에 시안 간격을 유지한다', () => {
    const rects = layoutTreemap(items, 396, 420);

    expect(rects[1].x - (rects[0].x + rects[0].width)).toBeCloseTo(TREEMAP_GAP, 6);
    expect(rects[2].y - (rects[0].y + rects[0].height)).toBeCloseTo(TREEMAP_GAP, 6);
  });

  it('컨테이너를 아직 재지 못했으면 라벨을 숨기지 않는다', () => {
    expect(layoutTreemap(items, 0, 0).every((rect) => rect.tier === 'full')).toBe(true);
  });
});

describe('수신 지연 판정', () => {
  const asOf = '2026-08-14T01:18:22.410Z';
  const asOfMs = Date.parse(asOf);

  it('장중 스냅샷이 3초 이상 밀리면 지연으로 본다', () => {
    expect(isSnapshotStale(asOf, asOfMs + 2_999, 'LIVE')).toBe(false);
    expect(isSnapshotStale(asOf, asOfMs + 3_000, 'LIVE')).toBe(true);
  });

  it('장 마감·지연 상태를 다시 지연으로 판정하지 않는다', () => {
    expect(isSnapshotStale(asOf, asOfMs + 60_000, 'CLOSED')).toBe(false);
    expect(isSnapshotStale(asOf, asOfMs + 60_000, 'DELAYED')).toBe(false);
  });
});

describe('오늘 화면과 값 일치', () => {
  it('같은 eventId의 테마 수익률과 기준 시각이 rankings와 같다', () => {
    const rankingByEvent = new Map(
      rankingLive.data.items.map((entry) => [entry.eventId, entry]),
    );

    expect(treemapLive.data.items.length).toBeGreaterThan(0);
    treemapLive.data.items.forEach((tile) => {
      const ranking = rankingByEvent.get(tile.eventId);
      expect(ranking).toBeDefined();
      expect(tile.weightedReturn).toBe(ranking?.weightedReturn);
      expect(tile.themeId).toBe(ranking?.classification.themeId);
      expect(tile.advancingCount).toBe(ranking?.advancingCount);
      expect(tile.validCount).toBe(ranking?.validCount);
    });
    expect(treemapLive.meta.marketContext.asOf).toBe(rankingLive.meta.marketContext.asOf);
  });
});
