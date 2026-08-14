import type { ReactNode } from 'react';
import { asRepositoryError } from '../domain/repositoryErrors';

export function LoadingState({ label = '데이터를 불러오는 중입니다' }: { label?: string }) {
  return (
    <div className="state-panel state-panel--loading" role="status" aria-live="polite">
      <span className="skeleton-dot" aria-hidden="true" />
      <p>{label}</p>
    </div>
  );
}

export function ErrorState({ error, retry }: { error?: Error; retry: () => void }) {
  const repositoryError = asRepositoryError(error);
  const permissionDenied = repositoryError?.kind === 'permission';
  const connectionFailed = repositoryError?.kind === 'network';
  const title = permissionDenied
    ? '접근 권한이 없습니다'
    : connectionFailed
      ? '연결이 원활하지 않습니다'
      : '데이터를 불러오지 못했습니다';
  const description = permissionDenied
    ? '현재 계정으로는 이 데이터에 접근할 수 없습니다.'
    : connectionFailed
      ? '네트워크 연결을 확인한 뒤 다시 시도해 주세요.'
      : '신뢰할 수 있는 결과를 현재 제공할 수 없습니다.';

  return (
    <div className="state-panel" role="alert">
      <p className="state-panel__title">{title}</p>
      <p>{description}</p>
      <button className="button button--secondary" type="button" onClick={retry}>
        다시 시도
      </button>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="state-panel">
      <p className="state-panel__title">{title}</p>
      {description ? <p>{description}</p> : null}
      {action}
    </div>
  );
}

export function PermissionState({ title = '이 기능은 아직 제공되지 않습니다' }: { title?: string }) {
  return (
    <div className="state-panel" role="status">
      <span className="eyebrow">접근 제한</span>
      <p className="state-panel__title">{title}</p>
      <p>검증과 권한 확인이 끝난 뒤 안전하게 제공할 예정입니다.</p>
    </div>
  );
}
