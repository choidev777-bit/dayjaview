import { IconArrowLeftLine } from '@karrotmarket/react-monochrome-icon';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { useRepository } from '../app/RepositoryContext';
import type { HistoricalHorizon } from '../domain/contracts';
import {
  formatDate,
  formatReturn,
  horizonLabel,
  outcomeText,
  returnTone,
} from '../domain/formatting';
import { asRepositoryError } from '../domain/repositoryErrors';
import { EmptyState, ErrorState, LoadingState, PermissionState } from '../shared/StatePanel';
import { useRepositoryResource } from '../shared/useRepositoryResource';

const HORIZONS: readonly HistoricalHorizon[] = [1, 5, 20];

export function HistoricalEventPage() {
  const repository = useRepository();
  const navigate = useNavigate();
  const location = useLocation();
  const params = useParams();
  const matchedEventId = params.matchedEventId ?? '';
  const contextEventId = (location.state as { contextEventId?: string } | null)?.contextEventId;
  const resource = useRepositoryResource(
    repository,
    'historical',
    () => repository.getHistoricalEvent(matchedEventId, contextEventId),
    [repository, matchedEventId, contextEventId],
  );

  if (resource.status === 'loading') {
    return <LoadingState label="과거 사례를 불러오는 중입니다" />;
  }
  if (resource.status === 'error') {
    if (asRepositoryError(resource.error)?.kind === 'permission') {
      return (
        <div className="page page--gate">
          <EventHeader onBack={() => navigate(-1)} />
          <PermissionState />
        </div>
      );
    }
    return <ErrorState error={resource.error} retry={resource.retry} />;
  }

  const detail = resource.data.data;
  const reasons = detail.similarityReasons ?? [];

  return (
    <div className="page page--case-detail">
      <EventHeader onBack={() => navigate(-1)} />

      <div className="page-intro">
        <small>
          {detail.displayNameAtEvent} · {formatDate(`${detail.marketDate}T00:00:00+09:00`)}
        </small>
        <h1>{detail.catalystSummary}</h1>
      </div>

      <div className="detail-card">
        <section aria-labelledby="similarity-title">
          <h2 id="similarity-title">오늘과 비슷한 이유</h2>
          {/* 보정되지 않은 `93% 유사` 같은 수치 유사도는 쓰지 않는다 (screen_spec 10.3). */}
          {reasons.length ? (
            <div className="reason-tags">
              {reasons.map((reason) => (
                <span key={reason} className="badge">
                  {reason}
                </span>
              ))}
            </div>
          ) : (
            <p className="section-note">공유 태그가 기록되지 않았습니다.</p>
          )}
        </section>

        <section aria-labelledby="outcome-title">
          <h2 id="outcome-title">당시 결과</h2>
          <p className="section-note">사건 당시 기록된 주도 종목을 동일가중한 결과입니다.</p>
          <div className="metric-grid">
            {HORIZONS.map((horizon) => {
              const outcome = outcomeText(
                detail.outcomes.find((row) => row.horizonTradingDays === horizon),
              );
              return (
                <article key={horizon}>
                  <span>{horizonLabel(horizon)}</span>
                  <strong className={outcome.tone}>{outcome.text}</strong>
                </article>
              );
            })}
          </div>
        </section>

        <section aria-labelledby="historical-leaders-title">
          <h2 id="historical-leaders-title">당시 주도 종목</h2>
          {/* 현재 관련주를 과거에 소급하지 않는다. 서버가 준 당시 명단만 그대로 쓴다 (screen_spec 10.5). */}
          {detail.leaders.length ? (
            <ol className="leader-list">
              {detail.leaders.map((leader) => (
                <li key={leader.stockId}>
                  <div>
                    <strong>{leader.name}</strong>
                  </div>
                  <strong className={returnTone(leader.return)}>{formatReturn(leader.return)}</strong>
                </li>
              ))}
            </ol>
          ) : (
            <EmptyState
              title="당시 기록된 주도 종목이 없습니다"
              description="가격이 확보되지 않아 바스켓에서 제외된 경우도 여기에 포함됩니다."
            />
          )}
        </section>
      </div>

      {/* 이 고지가 제품의 마지막 방어선이다. 지우지 않는다 (screen_spec 10.7). */}
      <p className="notice">
        미래 결과는 유사사례를 고를 때 사용하지 않았습니다. 사례를 먼저 선택한 뒤 당시 실제 가격
        결과를 연결했습니다.
      </p>
    </div>
  );
}

function EventHeader({ onBack }: { onBack: () => void }) {
  return (
    <header className="app-bar">
      <button type="button" onClick={onBack} aria-label="이전 화면으로 돌아가기">
        <IconArrowLeftLine size={24} aria-hidden="true" />
      </button>
      <strong>과거 사례</strong>
      <span className="app-bar__spacer" aria-hidden="true" />
    </header>
  );
}
