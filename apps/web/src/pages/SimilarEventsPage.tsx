import { useState } from 'react';
import { IconArrowLeftLine, IconChevronRightSmallLine } from '@karrotmarket/react-monochrome-icon';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useRepository } from '../app/RepositoryContext';
import type { HistoricalHorizon, HistoricalSummary } from '../domain/contracts';
import { formatDate, formatReturn, horizonLabel, outcomeText, returnTone } from '../domain/formatting';
import { asRepositoryError } from '../domain/repositoryErrors';
import { EmptyState, ErrorState, LoadingState, PermissionState } from '../shared/StatePanel';
import { useRepositoryResource } from '../shared/useRepositoryResource';

const HORIZONS: readonly HistoricalHorizon[] = [1, 5, 20];

/** `표본이 적어 참고용이에요` 기준은 E-21에서 확정된다. 그 전까지 보수적으로 5건 미만을 경고한다. */
function isSmallSample(summary: HistoricalSummary | undefined): boolean {
  return !summary || summary.observedCount < 5;
}

export function SimilarEventsPage() {
  const repository = useRepository();
  const navigate = useNavigate();
  const params = useParams();
  const themeId = params.themeId ?? '';
  const eventId = params.eventId ?? '';
  const [horizon, setHorizon] = useState<HistoricalHorizon>(5);
  const resource = useRepositoryResource(
    repository,
    'historical',
    () => repository.getSimilarEvents(eventId, horizon),
    [repository, eventId, horizon],
  );

  if (resource.status === 'loading') {
    return <LoadingState label="과거 사례를 불러오는 중입니다" />;
  }
  if (resource.status === 'error') {
    // 게이트는 오류가 아니다. 권한으로 막힌 경우 오류 화면 대신 제한 안내를 보여준다 (screen_spec 9.1).
    if (asRepositoryError(resource.error)?.kind === 'permission') {
      return (
        <div className="page page--gate">
          <GateHeader onBack={() => navigate(-1)} />
          <PermissionState />
        </div>
      );
    }
    return <ErrorState error={resource.error} retry={resource.retry} />;
  }

  const data = resource.data.data;

  if (data.availability !== 'AVAILABLE') {
    return (
      <div className="page page--gate">
        <GateHeader onBack={() => navigate(-1)} />
        <PermissionState />
      </div>
    );
  }

  const summary = data.summary.find((row) => row.horizonTradingDays === horizon);
  const smallSample = isSmallSample(summary);

  return (
    <div className="page page--cases">
      <GateHeader onBack={() => navigate(-1)} title="과거 사례 전체보기" />

      <div className="page-intro">
        <small>과거 관측 요약</small>
        <h1>과거에는 이런 일이 있었어요</h1>
        <p>오늘과 사건 원인문이 관련된 사례입니다.</p>
      </div>

      <section className="historical-summary" aria-labelledby="similar-summary-title">
        <h2 id="similar-summary-title">
          비슷했던 과거 {(summary?.eligibleCount ?? data.items.length).toLocaleString('ko-KR')}건
        </h2>
        {/* 기간마다 유효 분모가 다르다. 한 분모로 합치지 않고 줄마다 따로 적는다 (screen_spec 8.8). */}
        <ul className="historical-summary__rows">
          {data.summary.map((row) => (
            <li key={row.horizonTradingDays}>
              <span>{horizonLabel(row.horizonTradingDays)}</span>
              <strong>
                {row.positiveCount.toLocaleString('ko-KR')} / {row.observedCount.toLocaleString('ko-KR')}건 상승
              </strong>
              <b className={returnTone(row.medianReturn)}>
                {row.medianReturn === null ? '기록 없음' : `중앙 ${formatReturn(row.medianReturn)}`}
              </b>
            </li>
          ))}
        </ul>
        {smallSample ? <p className="section-note">표본이 적어 참고용이에요.</p> : null}
      </section>

      {/* MVP에서 허용되는 필터는 기간 전환뿐이다. 표본 수를 숨기는 소재 필터는 두지 않는다 (screen_spec 9.4). */}
      <div className="horizon-toggle" role="tablist" aria-label="사례 결과 기간 선택">
        {HORIZONS.map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={horizon === value}
            onClick={() => setHorizon(value)}
          >
            {horizonLabel(value)}
          </button>
        ))}
      </div>

      {data.items.length ? (
        <ul className="case-list">
          {data.items.map((item) => {
            const outcome = outcomeText(
              item.outcomes.find((row) => row.horizonTradingDays === horizon),
            );
            return (
              <li key={item.matchedEventId}>
                <Link
                  to={`/events/${encodeURIComponent(item.matchedEventId)}`}
                  state={{ contextEventId: eventId, themeId }}
                >
                  <span className="case-list__copy">
                    <small>
                      {formatDate(`${item.marketDate}T00:00:00+09:00`)} · {item.displayNameAtEvent}
                    </small>
                    <strong>{item.normalizedCatalystSummary}</strong>
                    <span className="case-list__tags">
                      {item.similarityReasons.map((reason) => (
                        <em key={reason}>{reason}</em>
                      ))}
                    </span>
                  <b className="case-list__outcome">
                    <small>{horizonLabel(horizon)}</small>
                    <span className={outcome.tone}>{outcome.text}</span>
                  </b>
                  </span>
                  <IconChevronRightSmallLine size={18} aria-hidden="true" />
                </Link>
              </li>
            );
          })}
        </ul>
      ) : (
        <EmptyState
          title="동일 유형 과거사례 없음"
          description="관련성 기준을 통과한 과거 기록이 아직 없습니다."
        />
      )}

      <p className="notice">
        기본 정렬은 관련성 순서입니다. 미래 수익률로 다시 정렬하지 않습니다. 과거 관측 요약이며 미래
        수익률 예측이 아닙니다.
      </p>
    </div>
  );
}

function GateHeader({ onBack, title = '과거 사례' }: { onBack: () => void; title?: string }) {
  return (
    <header className="app-bar">
      <button type="button" onClick={onBack} aria-label="이전 화면으로 돌아가기">
        <IconArrowLeftLine size={24} aria-hidden="true" />
      </button>
      <strong>{title}</strong>
      <span className="app-bar__spacer" aria-hidden="true" />
    </header>
  );
}
