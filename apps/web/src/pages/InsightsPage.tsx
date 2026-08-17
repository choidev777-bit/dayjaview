import { useEffect, useMemo, useState } from 'react';
import { useRepository } from '../app/RepositoryContext';
import type { DataStatus, TreemapResponse } from '../domain/contracts';
import { dataStatusLabel } from '../domain/formatting';
import { REPLAY_INTERVAL_MS, isSnapshotStale, selectTreemapItems } from '../domain/treemap';
import { DataStatusBar } from '../shared/DataStatusBar';
import { InfoTip } from '../shared/InfoTip';
import { EmptyState, ErrorPage } from '../shared/StatePanel';
import { ThemeTreemap } from '../shared/ThemeTreemap';
import { useRepositoryResource } from '../shared/useRepositoryResource';

const STALE_TICK_MS = 1000;

function useSnapshotStale(
  asOf: string,
  dataStatus: DataStatus,
  qualityFlags: readonly string[],
): boolean {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (dataStatus !== 'LIVE') return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), STALE_TICK_MS);
    return () => window.clearInterval(timer);
  }, [dataStatus]);

  return isSnapshotStale(asOf, now, dataStatus, qualityFlags);
}

function TreemapSkeleton() {
  return (
    <ul className="treemap treemap--skeleton" role="status" aria-label="실시간 테마 맵을 준비하는 중입니다">
      <li />
      <li />
      <li />
    </ul>
  );
}

/**
 * 장중에는 화면이 스스로 다음 스냅샷을 받아 온다. 장이 끝난 데이터에서는 값이 변하지
 * 않으니 굳이 돌리지 않는다.
 *
 * `retry`가 아니라 `refresh`를 쓴다. `retry`는 화면을 비우고 로딩으로 되돌려서
 * 타일 DOM이 매번 새로 생기고, 그러면 크기 전환 애니메이션이 아예 걸리지 않는다.
 */
function useLiveRefresh(dataStatus: DataStatus, refresh: () => void) {
  useEffect(() => {
    if (dataStatus !== 'LIVE') return undefined;
    const timer = window.setInterval(() => {
      // 설명 시트를 읽는 중이면 건너뛴다. 갱신하면 화면이 새로 그려지며 시트가 닫힌다.
      if (document.querySelector('.info-tip__panel')) return;
      refresh();
    }, REPLAY_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [dataStatus, refresh]);
}

function InsightsScreen({ response, refresh }: { response: TreemapResponse; refresh: () => void }) {
  const context = response.meta.marketContext;
  const items = useMemo(() => selectTreemapItems(response.data.items), [response.data.items]);
  const stale = useSnapshotStale(context.asOf, context.dataStatus, context.qualityFlags);
  useLiveRefresh(context.dataStatus, refresh);
  // 장 마감과 수신 지연에서는 마지막 화면을 그대로 두고 블록을 움직이지 않는다.
  const motion = !stale && context.dataStatus !== 'CLOSED';
  const displayContext = stale
    ? {
        ...context,
        dataStatus: 'DELAYED' as const,
        lastHealthyAt: context.lastHealthyAt ?? context.asOf,
      }
    : context;

  return (
    <div className="page page--insights">
      <header className="page-intro">
        <h1>실시간 테마 중계</h1>
        {displayContext.dataStatus === 'LIVE' ? (
          <div className="live-status">
            <span className="live-dot" aria-hidden="true" />
            LIVE
          </div>
        ) : null}
      </header>
      <div className="page-status">
        <DataStatusBar context={displayContext} />
      </div>
      {items.length ? (
        <ThemeTreemap items={items} motion={motion} />
      ) : (
        <EmptyState
          title="현재 조건을 충족한 테마가 없습니다"
          description="Coverage 기준에 미달하거나 상승하지 않은 테마는 0% 타일로 표시하지 않습니다."
        />
      )}
      <div className="treemap-legend">
        <span>
          <i aria-hidden="true" />
          면적: 테마 수익률
          {/* 갱신 시점까지 물음표 안으로 넣는다. 범례 줄에 문장을 늘어놓으면 지저분하다.
              장이 끝났는데 `장중 갱신`이라고 적으면 화면 위 상태 줄과 어긋나므로 상태를 따라간다. */}
          <InfoTip label="면적과 색 기준">
            <strong>면적</strong>
            테마 수익률 원값에 비례합니다. 내부 순위 점수는 사용하지 않습니다.
            <strong>색</strong>
            이날 화면에 오른 테마들 사이의 상대 비교입니다. 다른 날과 직접 비교하지 마세요.
            <strong>갱신</strong>
            {displayContext.dataStatus === 'LIVE'
              ? '수치는 장중 갱신됩니다.'
              : `수치는 ${dataStatusLabel(displayContext.dataStatus)} 기준입니다.`}
          </InfoTip>
        </span>
      </div>
    </div>
  );
}

export function InsightsPage() {
  const repository = useRepository();
  const resource = useRepositoryResource(
    repository,
    'treemap',
    () => repository.getTreemap(),
    [repository],
  );

  if (resource.status === 'error') return <ErrorPage error={resource.error} retry={resource.retry} />;

  if (resource.status === 'loading') {
    return (
      <div className="page page--insights">
        <header className="page-intro">
          <h1>실시간 테마 중계</h1>
        </header>
          <TreemapSkeleton />
      </div>
    );
  }

  return <InsightsScreen response={resource.data} refresh={resource.refresh} />;
}
