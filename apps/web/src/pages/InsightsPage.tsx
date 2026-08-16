import { useEffect, useMemo, useState } from 'react';
import { useRepository } from '../app/RepositoryContext';
import type { DataStatus, TreemapResponse } from '../domain/contracts';
import { isSnapshotStale, selectTreemapItems } from '../domain/treemap';
import { DataStatusBar } from '../shared/DataStatusBar';
import { EmptyState, ErrorPage } from '../shared/StatePanel';
import { ThemeTreemap } from '../shared/ThemeTreemap';
import { useRepositoryResource } from '../shared/useRepositoryResource';

const STALE_TICK_MS = 1000;

function useSnapshotStale(asOf: string, dataStatus: DataStatus): boolean {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (dataStatus !== 'LIVE') return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), STALE_TICK_MS);
    return () => window.clearInterval(timer);
  }, [dataStatus]);

  return isSnapshotStale(asOf, now, dataStatus);
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

function InsightsScreen({ response }: { response: TreemapResponse }) {
  const context = response.meta.marketContext;
  const items = useMemo(() => selectTreemapItems(response.data.items), [response.data.items]);
  const stale = useSnapshotStale(context.asOf, context.dataStatus);
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
        </span>
        <span>수치는 장중 갱신</span>
      </div>
      <p className="section-note notice">
        면적과 색상은 같은 테마 수익률 원값을 사용합니다. 내부 순위 점수는 사용하지 않습니다.
      </p>
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

  return <InsightsScreen response={resource.data} />;
}
