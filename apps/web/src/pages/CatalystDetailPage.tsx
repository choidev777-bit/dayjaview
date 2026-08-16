import { IconArrowLeftLine, IconChevronRightSmallLine } from '@karrotmarket/react-monochrome-icon';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useRepository } from '../app/RepositoryContext';
import { formatDate, formatReturn, horizonLabel, returnTone } from '../domain/formatting';
import { asRepositoryError } from '../domain/repositoryErrors';
import { EmptyState, ErrorPage, LoadingState, PermissionState } from '../shared/StatePanel';
import { useRepositoryResource } from '../shared/useRepositoryResource';

export function CatalystDetailPage() {
  const repository = useRepository();
  const navigate = useNavigate();
  const params = useParams();
  const catalystId = params.catalystId ?? '';
  const resource = useRepositoryResource(
    repository,
    'historical',
    () => repository.getCatalystDetail(catalystId),
    [repository, catalystId],
  );

  if (resource.status === 'loading') {
    return <LoadingState label="과거 소재를 불러오는 중입니다" />;
  }
  if (resource.status === 'error') {
    // 서버 계약이 아직 없다. 오류가 아니라 미제공으로 닫는다 (배선 매핑표 §5.1).
    if (asRepositoryError(resource.error)?.kind === 'permission') {
      return (
        <div className="page page--gate">
          <CatalystHeader onBack={() => navigate(-1)} />
          <PermissionState />
        </div>
      );
    }
    return <ErrorPage error={resource.error} retry={resource.retry} />;
  }

  const detail = resource.data.data;

  if (detail.availability !== 'AVAILABLE') {
    return (
      <div className="page page--gate">
        <CatalystHeader onBack={() => navigate(-1)} />
        <PermissionState />
      </div>
    );
  }

  return (
    <div className="page page--catalyst">
      <CatalystHeader onBack={() => navigate(-1)} />

      <div className="page-intro">
        <small>{detail.themeDisplayName} · 과거 상승 소재</small>
        <h1>{detail.catalystName}</h1>
        <p>이 소재가 과거 같은 테마와 함께 기록됐던 사례를 모았습니다.</p>
      </div>

      <div className="detail-card">
        <section aria-labelledby="catalyst-sameday-title">
          <h2 id="catalyst-sameday-title">과거 동반 기록</h2>
          {/* 상승 빈도를 확률·성공률·적중률로 바꾸지 않는다. 건수와 중앙 반응으로 적는다
              (screen_spec 8.7·13.2). 프로토타입의 `상승 동반 78%`가 여기에 해당한다. */}
          <div className="metric-grid">
            <article>
              <span>기록된 사건</span>
              <strong>{detail.sameDay.eligibleCount.toLocaleString('ko-KR')}건</strong>
            </article>
            <article>
              <span>당일 상승</span>
              <strong>
                {detail.sameDay.positiveCount.toLocaleString('ko-KR')} /{' '}
                {detail.sameDay.observedCount.toLocaleString('ko-KR')}건
              </strong>
            </article>
            <article>
              <span>당일 중앙 반응</span>
              <strong className={returnTone(detail.sameDay.medianReturn)}>
                {detail.sameDay.medianReturn === null
                  ? '기록 없음'
                  : formatReturn(detail.sameDay.medianReturn)}
              </strong>
            </article>
          </div>
          <p className="section-note">
            사건 당시 기록된 주도 종목을 동일가중해 계산한 중앙값입니다. 평균이 아닙니다.
          </p>
        </section>

        <section aria-labelledby="catalyst-horizons-title">
          <h2 id="catalyst-horizons-title">기간별 중앙 반응</h2>
          <ul className="historical-summary__rows">
            {detail.horizons.map((row) => (
              <li key={row.horizonTradingDays}>
                <span>{horizonLabel(row.horizonTradingDays)}</span>
                <strong>
                  {row.positiveCount.toLocaleString('ko-KR')} /{' '}
                  {row.observedCount.toLocaleString('ko-KR')}건 상승
                </strong>
                <b className={returnTone(row.medianReturn)}>
                  {row.medianReturn === null ? '기록 없음' : `중앙 ${formatReturn(row.medianReturn)}`}
                </b>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="catalyst-events-title">
          <h2 id="catalyst-events-title">집계에 포함된 사건</h2>
          {/* 수익률이 높은 사건만 골라 보여주지 않는다 (screen_spec 11.1). */}
          {detail.events.length ? (
            <ul className="case-list">
              {detail.events.map((event) => (
                <li key={event.matchedEventId}>
                  <Link to={`/events/${encodeURIComponent(event.matchedEventId)}`}>
                    <span className="case-list__copy">
                      <small>
                        {formatDate(`${event.marketDate}T00:00:00+09:00`)}
                        {event.leaderName ? ` · ${event.leaderName}` : ''}
                      </small>
                      <strong>{event.normalizedCatalystSummary}</strong>
                    <b className="case-list__outcome">
                      <small>당일</small>
                      <span className={returnTone(event.sameDayReturn)}>
                        {event.sameDayReturn === null ? '기록 없음' : formatReturn(event.sameDayReturn)}
                      </span>
                    </b>
                    </span>
                    <IconChevronRightSmallLine size={18} aria-hidden="true" />
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="집계에 포함된 사건이 없습니다" />
          )}
        </section>
      </div>

      <p className="notice">
        {detail.qualityNote ? `${detail.qualityNote} ` : ''}
        과거 관측 요약이며 미래 수익률 예측이 아닙니다.
      </p>
    </div>
  );
}

function CatalystHeader({ onBack }: { onBack: () => void }) {
  return (
    <header className="app-bar">
      <button type="button" onClick={onBack} aria-label="이전 화면으로 돌아가기">
        <IconArrowLeftLine size={24} aria-hidden="true" />
      </button>
      <strong>상승 소재</strong>
      <span className="app-bar__spacer" aria-hidden="true" />
    </header>
  );
}
