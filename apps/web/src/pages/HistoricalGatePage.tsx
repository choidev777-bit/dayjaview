import { useParams } from 'react-router-dom';
import { useRepository } from '../app/RepositoryContext';
import { ErrorState, LoadingState, PermissionState } from '../shared/StatePanel';
import { useRepositoryResource } from '../shared/useRepositoryResource';

export function HistoricalGatePage() {
  const repository = useRepository();
  const params = useParams();
  const eventId = params.eventId ?? params.matchedEventId ?? '';
  const resource = useRepositoryResource(
    repository,
    'historical',
    () => repository.getHistoricalAccess(eventId),
    [repository, eventId],
  );

  if (resource.status === 'loading') return <LoadingState label="접근 가능 여부를 확인하는 중입니다" />;
  if (resource.status === 'error') return <ErrorState error={resource.error} retry={resource.retry} />;

  return (
    <div className="page page--gate">
      <header className="page-header">
        <p className="eyebrow">과거 관측</p>
        <h1>유사사례</h1>
      </header>
      <PermissionState />
    </div>
  );
}
