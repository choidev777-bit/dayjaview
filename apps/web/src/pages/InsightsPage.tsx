import { Link } from 'react-router-dom';
import { useRepository } from '../app/RepositoryContext';
import { formatReturn } from '../domain/formatting';
import { DataStatusBar } from '../shared/DataStatusBar';
import { EmptyState, ErrorState, LoadingState } from '../shared/StatePanel';
import { useAsyncResource } from '../shared/useAsyncResource';

export function InsightsPage() {
  const repository = useRepository();
  const resource = useAsyncResource(() => repository.getTreemap(), [repository]);

  if (resource.status === 'loading') return <LoadingState label="실시간 테마 맵을 준비하는 중입니다" />;
  if (resource.status === 'error') return <ErrorState retry={resource.retry} />;

  const { data, meta } = resource.data;

  return (
    <div className="page page--insights">
      <header className="page-header">
        <p className="eyebrow">상승률 분포</p>
        <h1>인사이트</h1>
        <p>정밀 추적 중인 테마의 현재 시장 반응을 살펴보세요.</p>
      </header>
      <DataStatusBar context={meta.marketContext} />
      <section className="treemap-section" aria-labelledby="treemap-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">테마 수익률 기준</p>
            <h2 id="treemap-title">실시간 테마 맵</h2>
          </div>
          <div className="legend" aria-label="상승률 색상 범례">
            <span aria-hidden="true" /> 상승
          </div>
        </div>
        {data.items.length ? (
          <div className="treemap" aria-label="테마 수익률 지도">
            {data.items.map((item) => (
              <Link
                key={item.eventId}
                className="treemap__tile"
                to={`/themes/${encodeURIComponent(item.themeId)}/events/${encodeURIComponent(item.eventId)}`}
                state={{ from: '/insights' }}
                aria-label={`${item.displayName}, 테마 수익률 ${formatReturn(item.weightedReturn)}, 관련주 ${item.advancingCount} / ${item.validCount}종목 상승`}
              >
                <span>{item.displayName}</span>
                <strong>{formatReturn(item.weightedReturn)}</strong>
                <small>
                  {item.advancingCount} / {item.validCount}종목 상승
                </small>
              </Link>
            ))}
          </div>
        ) : (
          <EmptyState
            title="현재 조건을 충족한 테마가 없습니다"
            description="Coverage 기준에 미달한 테마는 0% 타일로 표시하지 않습니다."
          />
        )}
        <p className="section-note">면적과 색상은 같은 테마 수익률 원값을 사용합니다. 내부 순위 점수는 사용하지 않습니다.</p>
      </section>
    </div>
  );
}
