import type { CSSProperties, ReactNode } from 'react';
import { asRepositoryError } from '../domain/repositoryErrors';

/**
 * 시안의 첫 진입 로딩. 검은 배경 위에서 로고에 주황빛이 쓸려 지나간다.
 * 앱이 처음 열릴 때 한 번만 쓰고, 화면 안의 부분 로딩은 `LoadingState`가 맡는다.
 */
/**
 * 첫 진입 스플래시. 로고 sweep·후광·진행 바가 `durationMs` 동안 딱 한 바퀴 돌게 맞춘다.
 * 화면이 사라지는 시각과 애니메이션 길이가 다르면 로고가 지나가는 도중에 잘린다.
 */
export function SplashScreen({ durationMs = 3000 }: { durationMs?: number } = {}) {
  return (
    <div
      className="splash"
      role="status"
      aria-label="DAY JA VIEW 불러오는 중"
      style={{ '--splash-duration': `${durationMs}ms` } as CSSProperties}
    >
      <div className="splash__halo" aria-hidden="true" />
      <div className="splash__logo" aria-hidden="true">
        <i className="splash__logo-base" />
        <span className="splash__glow">
          <i />
        </span>
        <span className="splash__sharp">
          <i />
        </span>
      </div>
      <div className="splash__footer">
        <span>오늘의 테마를, 과거의 기록으로</span>
        <b aria-hidden="true">
          <i />
        </b>
      </div>
    </div>
  );
}

export function LoadingState({ label = '데이터를 불러오는 중입니다' }: { label?: string }) {
  return (
    <div className="state-panel state-panel--loading" role="status" aria-live="polite">
      <span className="skeleton-dot" aria-hidden="true" />
      <p>{label}</p>
    </div>
  );
}

function errorCopy(error?: Error): { title: string; description: string } {
  const repositoryError = asRepositoryError(error);
  if (repositoryError?.kind === 'permission') {
    return {
      title: '접근 권한이 없습니다',
      description: '현재 계정으로는 이 데이터에 접근할 수 없습니다.',
    };
  }
  if (repositoryError?.kind === 'network') {
    return {
      title: '연결이 원활하지 않습니다',
      description: '네트워크 연결을 확인한 뒤 다시 시도해 주세요.',
    };
  }
  return {
    title: '데이터를 불러오지 못했습니다',
    description: '신뢰할 수 있는 결과를 현재 제공할 수 없습니다.',
  };
}

/** 화면 일부만 실패했을 때. 나머지 내용은 그대로 두고 그 자리에만 놓는다. */
export function ErrorState({ error, retry }: { error?: Error; retry: () => void }) {
  const { title, description } = errorCopy(error);

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

/**
 * 화면 전체가 실패했을 때. 인라인 패널을 그대로 쓰면 페이지 높이만큼 늘어나 버튼까지 늘어난다.
 * 이 화면은 하단 탭 위에서 단독으로 서므로 로고를 얹어 앱 안이라는 것을 알린다.
 */
export function ErrorPage({ error, retry }: { error?: Error; retry: () => void }) {
  const { title, description } = errorCopy(error);

  return (
    <div className="page page--error">
      <div className="error-page" role="alert">
        <span className="error-page__mark" role="img" aria-label="DAY JA VIEW" />
        <h1>{title}</h1>
        <p>{description}</p>
        <button className="button button--primary" type="button" onClick={retry}>
          다시 시도
        </button>
      </div>
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
