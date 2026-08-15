import { useRepository } from '../app/RepositoryContext';
import { formatDate } from '../domain/formatting';
import { DataStatusBar } from '../shared/DataStatusBar';
import { EmptyState, ErrorState, LoadingState } from '../shared/StatePanel';
import { ThemeRankingWheel } from '../shared/ThemeRankingWheel';
import { useRepositoryResource } from '../shared/useRepositoryResource';

export function TodayPage() {
  const repository = useRepository();
  const resource = useRepositoryResource(
    repository,
    'rankings',
    () => repository.getRankings(),
    [repository],
  );

  if (resource.status === 'loading') return <LoadingState label="오늘의 테마를 불러오는 중입니다" />;
  if (resource.status === 'error') return <ErrorState error={resource.error} retry={resource.retry} />;

  const { data, meta } = resource.data;
  const context = meta.marketContext;

  return (
    <div className="page page--today">
      <header className="home-header">
        <span className="home-mark" role="img" aria-label="DAYJAVIEW" />
      </header>
      <div className="home-title">
        <strong>{formatDate(`${context.marketDate}T00:00:00+09:00`)}</strong>
        <h1>오늘 많이 오른 테마예요</h1>
      </div>
      <div className="home-status">
        <DataStatusBar context={context} />
      </div>
      {data.items.length ? (
        <ThemeRankingWheel items={data.items} />
      ) : (
        <EmptyState
          title={
            context.dataStatus === 'DELAYED'
              ? '마지막 정상 화면을 불러오는 중입니다'
              : '현재 조건을 충족한 테마가 없습니다'
          }
          description={
            context.dataStatus === 'DELAYED'
              ? '수신이 정상화되면 마지막 정상값부터 이어서 보여드립니다.'
              : '조건을 충족한 활성 테마가 생기면 이곳에 표시됩니다.'
          }
        />
      )}
    </div>
  );
}
