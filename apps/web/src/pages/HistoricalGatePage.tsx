import { IconArrowLeftLine } from '@karrotmarket/react-monochrome-icon';
import { useNavigate, useParams } from 'react-router-dom';
import { useRepository } from '../app/RepositoryContext';
import { ErrorPage, LoadingState, PermissionState } from '../shared/StatePanel';
import { useRepositoryResource } from '../shared/useRepositoryResource';

export function HistoricalGatePage() {
  const repository = useRepository();
  const navigate = useNavigate();
  const params = useParams();
  const eventId = params.eventId ?? params.matchedEventId ?? '';
  const resource = useRepositoryResource(
    repository,
    'historical',
    () => repository.getHistoricalAccess(eventId),
    [repository, eventId],
  );

  if (resource.status === 'loading') return <LoadingState label="접근 가능 여부를 확인하는 중입니다" />;
  if (resource.status === 'error') return <ErrorPage error={resource.error} retry={resource.retry} />;

  return (
    <div className="page page--gate">
      <header className="app-bar">
        <button type="button" onClick={() => navigate(-1)} aria-label="이전 화면으로 돌아가기">
          <IconArrowLeftLine size={24} aria-hidden="true" />
        </button>
        <strong>과거 사례</strong>
        <span className="app-bar__spacer" aria-hidden="true" />
      </header>
      <PermissionState />
    </div>
  );
}
