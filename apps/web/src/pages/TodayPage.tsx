import { useRepository } from '../app/RepositoryContext';
import { formatLongDate } from '../domain/formatting';
import { EmptyState, ErrorPage, LoadingState } from '../shared/StatePanel';
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
  if (resource.status === 'error') return <ErrorPage error={resource.error} retry={resource.retry} />;

  const { data, meta } = resource.data;
  const context = meta.marketContext;

  return (
    <div className="page page--today">
      <header className="home-header">
        <span className="home-mark" role="img" aria-label="DAYJAVIEW" />
      </header>
      <div className="home-title">
        <strong>{formatLongDate(`${context.marketDate}T00:00:00+09:00`)}</strong>
        <h1>오늘 많이 오른 테마예요</h1>
      </div>
      {/* 홈은 순위만 보여준다. 데이터 상태·기준 시각은 실시간 화면과 테마 상세에 남는다. */}
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
      {/* 특징테마(/movers)는 라우트만 살려 두고 진입 링크는 붙이지 않는다.
          어느 화면에 놓을지 정해지면 그때 연결한다. */}
    </div>
  );
}
