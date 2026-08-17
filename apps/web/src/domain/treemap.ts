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
/** 장중 재생 시연 간격. 1초로 흘리면 눈이 못 따라가서 3초에 한 분씩 넘긴다. */
export const REPLAY_INTERVAL_MS = 3000;

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
  // 마지막 줄에 하나만 남으면 가로로 길게 깔려 혼자 띠처럼 보인다. 앞 줄에서 하나를
  // 내려 두 칸으로 만든다. 앞 줄이 둘뿐이면 그냥 앞 줄에 붙인다.
  const last = rows[rows.length - 1];
  const prev = rows[rows.length - 2];
  if (rows.length >= 2 && last.length === 1) {
    if (prev.length > 2) {
      last.unshift(prev.pop() as TreemapItem);
    } else {
      prev.push(...last);
      rows.pop();
    }
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
 * 무게 합을 절반으로 가르며 긴 변부터 잘라 나가는 재귀 이분할.
 *
 * 고정 3단 배치(2·3·3…)는 줄마다 칸 수가 정해져 있어, 값이 장중에 바뀌면 한 칸이
 * 가로로 길게 늘어지거나 마지막 줄에 하나만 남는다. 이 방식은 남은 자리의 긴 변을
 * 기준으로 갈라서 어떤 개수에서도 칸 모양이 고르다.
 *
 * 나누는 기준은 수익률 합이라 면적은 그대로 수익률에 비례한다 (screen_spec 6.3).
 * 참고한 코드는 얇은 띠를 막으려 비율을 12%~88%로 묶었는데, 그러면 면적이 원값과
 * 어긋나 6.3을 어긴다. 묶지 않는다. 절반에 가장 가까운 자리에서 가르기 때문에
 * 고정 3단 배치보다 칸 모양은 이미 훨씬 고르다.
 */
function partition(
  items: TreemapItem[],
  x: number,
  y: number,
  width: number,
  height: number,
  out: Array<{ item: TreemapItem; x: number; y: number; width: number; height: number }>,
): void {
  if (!items.length) return;
  if (items.length === 1) {
    out.push({ item: items[0], x, y, width, height });
    return;
  }

  const total = items.reduce((sum, item) => sum + item.weightedReturn, 0);
  let running = 0;
  let bestIndex = 1;
  let bestDistance = Infinity;
  for (let index = 1; index < items.length; index += 1) {
    running += items[index - 1].weightedReturn;
    const distance = Math.abs(total / 2 - running);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIndex = index;
    }
  }

  const first = items.slice(0, bestIndex);
  const second = items.slice(bestIndex);
  const firstWeight = first.reduce((sum, item) => sum + item.weightedReturn, 0);
  const ratio = total > 0 ? firstWeight / total : 0.5;

  if (width >= height) {
    const firstWidth = width * ratio;
    partition(first, x, y, firstWidth, height, out);
    partition(second, x + firstWidth, y, width - firstWidth, height, out);
  } else {
    const firstHeight = height * ratio;
    partition(first, x, y, width, firstHeight, out);
    partition(second, x, y + firstHeight, width, height - firstHeight, out);
  }
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
  if (!items.length) return [];
  const total = items.reduce((sum, item) => sum + item.weightedReturn, 0);
  if (total <= 0) return [];

  // 색은 그날 화면에 오른 테마들끼리 비교해서 정한다.
  const intensityOf = treemapIntensityScale(items);
  const measured = width > 0 && height > 0;
  const boxes: Array<{ item: TreemapItem; x: number; y: number; width: number; height: number }> = [];
  partition(items, 0, 0, Math.max(0, width), Math.max(0, height), boxes);

  // 칸 사이는 각 칸을 gap의 절반씩 깎아 만든다. 줄 단위로 빼면 이 배치에서는 어긋난다.
  const half = gap / 2;
  return boxes.map((box) => ({
    item: box.item,
    x: box.x + half,
    y: box.y + half,
    width: Math.max(0, box.width - gap),
    height: Math.max(0, box.height - gap),
    tone: treemapTone(box.item.weightedReturn),
    intensity: intensityOf(box.item.weightedReturn),
    // 아직 컨테이너를 재보기 전이면 라벨을 숨기지 않는다.
    tier: measured ? treemapLabelTier(box.width - gap, box.height - gap) : 'full',
  }));
}
