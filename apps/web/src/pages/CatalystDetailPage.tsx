import { IconArrowLeftLine, IconChevronRightSmallLine } from '@karrotmarket/react-monochrome-icon';
import { useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { useRepository } from '../app/RepositoryContext';
import { formatDate, formatReturn, horizonLabel, returnTone } from '../domain/formatting';
import { asRepositoryError } from '../domain/repositoryErrors';
import { EmptyState, ErrorPage, LoadingState, PermissionState } from '../shared/StatePanel';
import { useGoBack } from '../shared/useGoBack';
import { useRepositoryResource } from '../shared/useRepositoryResource';

const VISIBLE_EVENTS = 3;

export function CatalystDetailPage() {
  const repository = useRepository();
  const location = useLocation();
  const [expanded, setExpanded] = useState(false);
  const from = (location.state as { themeId?: string; eventId?: string } | null) ?? null;
  // 소재 상세는 테마 상세에서 들어온다. 공유 링크로 바로 열면 그 테마로, 모르면 홈으로.
  const goBack = useGoBack(
    from?.themeId && from?.eventId
      ? `/themes/${encodeURIComponent(from.themeId)}/events/${encodeURIComponent(from.eventId)}`
      : '/today',
  );
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
          <CatalystHeader onBack={goBack} />
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
        <CatalystHeader onBack={goBack} />
        <PermissionState />
      </div>
    );
  }

  return (
    <div className="page page--catalyst">
      <CatalystHeader onBack={goBack} />

      {/* 과거 사례 화면과 같은 형식: 제목이 주인공, 테마 이름은 바로 아래 한 줄. */}
      <div className="page-intro">
        <h1>{detail.catalystName}</h1>
        <p className="page-intro__origin">{detail.themeDisplayName}</p>
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
            {/* 여기만 등락이 걸린 값이다. 앞의 두 칸은 건수라 배경을 바꾸지 않는다. */}
            <article data-tone={returnTone(detail.sameDay.medianReturn)}>
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
              {(expanded ? detail.events : detail.events.slice(0, VISIBLE_EVENTS)).map((event) => (
                <li key={event.matchedEventId}>
                  {/* 어느 사건에서 들어왔는지 넘겨야 사건 상세가 `오늘과 비슷한 이유`를
                      같은 기준으로 계산한다. 뒤로 가기 목적지도 여기서 정해진다. */}
                  <Link
                    to={`/events/${encodeURIComponent(event.matchedEventId)}`}
                    state={{
                      contextEventId: from?.eventId ?? null,
                      themeId: detail.themeId,
                      catalystId,
                    }}
                  >
                    <span className="case-list__copy">
                      {/* 주도 종목은 사례를 여는 화면에 이미 있다. 목록 카드에는 두지 않는다. */}
                      <small>{formatDate(`${event.marketDate}T00:00:00+09:00`)}</small>
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
          {detail.events.length > VISIBLE_EVENTS ? (
            <button
              type="button"
              className="expand-button expand-button--inline case-list__more"
              aria-expanded={expanded}
              onClick={() => setExpanded((current) => !current)}
            >
              <span className="visually-hidden">{expanded ? '사건 접기' : '사건 더 보기'}</span>
              <i aria-hidden="true" data-open={expanded ? 'true' : 'false'} />
            </button>
          ) : null}
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
