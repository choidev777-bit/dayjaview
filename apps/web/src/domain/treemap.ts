import type { DataStatus, TreemapItem } from './contracts';

/** 한 화면에 올리는 최대 블록 수 (treemap plan §2, screen_spec §6.3). */
export const TREEMAP_LIMIT = 12;
/** 시안 실시간 화면의 3단 고정 배치: 상단 2 · 중단 3. 그 아래는 한 줄 3개씩 이어 붙인다. */
export const TREEMAP_ROW_PLAN = [2, 3] as const;
/** 남은 블록을 한 줄에 몰면 타일이 세로로 길쭉해진다. 아래 줄도 3개까지만 넣는다. */
export const TREEMAP_TAIL_ROW = 3;
export const TREEMAP_GAP = 5;
/** 숫자·색상은 500ms, 레이아웃은 1초 (treemap plan §5.4, screen_spec §6.4). */
export const TREEMAP_VALUE_INTERVAL_MS = 500;
export const TREEMAP_LAYOUT_INTERVAL_MS = 1000;

const STALE_THRESHOLD_MS = 3000;
const NEUTRAL_BAND = 0.001;
const FULL_INTENSITY_RETURN = 0.05;

export type TreemapTone = 'up' | 'down' | 'neutral';
export type TreemapLabelTier = 'full' | 'compact' | 'minimal';

export interface TreemapRect {
  item: TreemapItem;
  x: number;
  y: number;
  width: number;
  height: number;
  tone: TreemapTone;
  intensity: number;
  tier: TreemapLabelTier;
}

/** Coverage를 통과하고 수익률이 0을 넘는 테마만 상위 12개까지 고른다. */
export function selectTreemapItems(items: TreemapItem[], limit = TREEMAP_LIMIT): TreemapItem[] {
  return items
    .filter((item) => item.coverageStatus !== 'INSUFFICIENT' && item.weightedReturn > 0)
    .sort(
      (left, right) =>
        right.weightedReturn - left.weightedReturn || left.themeId.localeCompare(right.themeId),
    )
    .slice(0, limit);
}

export function treemapRows(items: TreemapItem[]): TreemapItem[][] {
  const rows: TreemapItem[][] = [];
  let rest = items;
  for (const size of TREEMAP_ROW_PLAN) {
    if (!rest.length) break;
    rows.push(rest.slice(0, size));
    rest = rest.slice(size);
  }
  while (rest.length) {
    rows.push(rest.slice(0, TREEMAP_TAIL_ROW));
    rest = rest.slice(TREEMAP_TAIL_ROW);
  }
  return rows;
}

export function treemapTone(weightedReturn: number): TreemapTone {
  if (weightedReturn > NEUTRAL_BAND) return 'up';
  if (weightedReturn < -NEUTRAL_BAND) return 'down';
  return 'neutral';
}

/** 색상 강도 = min(|수익률| ÷ 5%, 1) (treemap plan §5.2). */
export function treemapIntensity(weightedReturn: number): number {
  return Math.min(Math.abs(weightedReturn) / FULL_INTENSITY_RETURN, 1);
}

/**
 * 그날 화면에 올라온 테마들의 범위로 색을 다시 편다.
 *
 * 고정 5% 기준만 쓰면 강한 날에는 12개가 전부 상한(1)에 붙어 같은 색이 된다.
 * 실제로 2026-08-10은 +7.4%~+10.4%라 모든 타일이 구분되지 않았다. 그래서
 * 그날의 최소~최대를 INTENSITY_FLOOR~1로 다시 매핑해 순서가 색으로 읽히게 한다.
 *
 * 면적은 여전히 수익률 원값에 비례한다 (screen_spec §6.3). 색만 상대 기준이며,
 * 값 자체는 타일에 부호와 함께 표시하므로 색만으로 크기를 전달하지 않는다 (§13.3).
 */
const INTENSITY_FLOOR = 0.18;

export function treemapIntensityScale(
  items: readonly { weightedReturn: number }[],
): (weightedReturn: number) => number {
  const magnitudes = items.map((item) => Math.abs(item.weightedReturn));
  const min = magnitudes.length ? Math.min(...magnitudes) : 0;
  const max = magnitudes.length ? Math.max(...magnitudes) : 0;
  const span = max - min;

  // 한 종류만 있거나 전부 같은 값이면 나눌 게 없다. 고정 기준으로 돌아간다.
  if (span <= 0) return treemapIntensity;

  return (weightedReturn) => {
    const ratio = (Math.abs(weightedReturn) - min) / span;
    return INTENSITY_FLOOR + Math.min(Math.max(ratio, 0), 1) * (1 - INTENSITY_FLOOR);
  };
}

/** 블록 크기별 라벨 단계 (treemap plan §5.3). 어느 단계든 접근성 이름은 테마명 + 수익률이다. */
export function treemapLabelTier(width: number, height: number): TreemapLabelTier {
  if (width >= 110 && height >= 70) return 'full';
  if (width >= 70 && height >= 48) return 'compact';
  return 'minimal';
}

export function isSnapshotStale(asOf: string, now: number, dataStatus: DataStatus): boolean {
  if (dataStatus !== 'LIVE') return false;
  const asOfMs = Date.parse(asOf);
  if (Number.isNaN(asOfMs)) return false;
  return now - asOfMs >= STALE_THRESHOLD_MS;
}

/**
 * 시안의 3단 배치를 유지하면서 면적을 수익률 원값에 비례시킨다.
 * 행 높이는 그 행의 수익률 합, 행 안의 너비는 각 테마의 수익률에 비례하므로
 * 타일 면적은 곧 수익률에 비례한다. 임의 가중치나 순위 점수를 쓰지 않는다.
 */
export function layoutTreemap(
  items: TreemapItem[],
  width: number,
  height: number,
  gap = TREEMAP_GAP,
): TreemapRect[] {
  const rows = treemapRows(items);
  if (!rows.length) return [];
  const rowSums = rows.map((row) => row.reduce((sum, item) => sum + item.weightedReturn, 0));
  const total = rowSums.reduce((sum, value) => sum + value, 0);
  if (total <= 0) return [];

  // 색은 그날 화면에 오른 테마들끼리 비교해서 정한다.
  const intensityOf = treemapIntensityScale(items);
  const measured = width > 0 && height > 0;
  const usableHeight = Math.max(0, height - gap * (rows.length - 1));
  const rowWidths = rows.map((row) => Math.max(0, width - gap * (row.length - 1)));
  // 행마다 간격으로 잃는 너비가 다르므로 행 높이로 보정한다. 그래야 타일 면적이
  // 정확히 수익률에 비례한다.
  const rowWeights = rowWidths.every((rowWidth) => rowWidth > 0)
    ? rows.map((_, index) => rowSums[index] / rowWidths[index])
    : rowSums;
  const weightTotal = rowWeights.reduce((sum, value) => sum + value, 0);
  const rects: TreemapRect[] = [];
  let y = 0;

  rows.forEach((row, rowIndex) => {
    const rowHeight = usableHeight * (rowWeights[rowIndex] / weightTotal);
    const usableWidth = rowWidths[rowIndex];
    let x = 0;
    row.forEach((item) => {
      const tileWidth = usableWidth * (item.weightedReturn / rowSums[rowIndex]);
      rects.push({
        item,
        x,
        y,
        width: tileWidth,
        height: rowHeight,
        tone: treemapTone(item.weightedReturn),
        intensity: intensityOf(item.weightedReturn),
        // 아직 컨테이너를 재보기 전이면 라벨을 숨기지 않는다.
        tier: measured ? treemapLabelTier(tileWidth, rowHeight) : 'full',
      });
      x += tileWidth + gap;
    });
    y += rowHeight + gap;
  });

  return rects;
}
