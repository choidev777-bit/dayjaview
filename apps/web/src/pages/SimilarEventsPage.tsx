import { useCallback } from 'react';
import { IconChevronRightSmallLine } from '@karrotmarket/react-monochrome-icon';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useRepository } from '../app/RepositoryContext';
import type { HistoricalHorizon, HistoricalSummary } from '../domain/contracts';
import { formatDate, formatReturn, horizonLabel, outcomeText, returnTone } from '../domain/formatting';
import { asRepositoryError } from '../domain/repositoryErrors';
import { InfoTip } from '../shared/InfoTip';
import { EmptyState, ErrorPage, LoadingState, PermissionState } from '../shared/StatePanel';
import { useGoBack } from '../shared/useGoBack';
import { useRepositoryResource } from '../shared/useRepositoryResource';

const HORIZONS: readonly HistoricalHorizon[] = [1, 5, 20];
const VISIBLE_CASES = 3;

/** `표본이 적어 참고용이에요` 기준은 E-21에서 확정된다. 그 전까지 보수적으로 5건 미만을 경고한다. */
function isSmallSample(summary: HistoricalSummary | undefined): boolean {
  return !summary || summary.observedCount < 5;
}

export function SimilarEventsPage() {
  const repository = useRepository();
  const params = useParams();
  const themeId = params.themeId ?? '';
  const eventId = params.eventId ?? '';
  const goBack = useGoBack(
    `/themes/${encodeURIComponent(themeId)}/events/${encodeURIComponent(eventId)}`,
  );
  // 기간과 펼침 상태를 주소에 남긴다. 새로고침·공유·뒤로 가기에서 보던 그대로 열린다.
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = Number(searchParams.get('horizon'));
  const horizon: HistoricalHorizon = HORIZONS.includes(requested as HistoricalHorizon)
    ? (requested as HistoricalHorizon)
    : 5;
  const expanded = searchParams.get('cases') === 'all';
  const patchParams = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(searchParams);
      Object.entries(patch).forEach(([key, value]) => {
        if (value === null) next.delete(key);
        else next.set(key, value);
      });
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );
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
          <GateHeader onBack={goBack} />
          <PermissionState />
        </div>
      );
    }
    return <ErrorPage error={resource.error} retry={resource.retry} />;
  }

  const data = resource.data.data;

  if (data.availability !== 'AVAILABLE') {
    return (
      <div className="page page--gate">
        <GateHeader onBack={goBack} />
        <PermissionState />
      </div>
    );
  }

  const summary = data.summary.find((row) => row.horizonTradingDays === horizon);
  const smallSample = isSmallSample(summary);
  // 사례들은 같은 테마에서 나왔다. 당시 테마명이면 어느 테마의 과거인지 알리는 데 충분하다.
  const themeName = data.items[0]?.displayNameAtEvent ?? null;

  return (
    <div className="page page--cases">
      <GateHeader onBack={goBack} />

      <div className="page-intro">
        <h1>과거에는 이런 일이 있었어요</h1>
        {/* 어느 테마의 과거인지가 없으면 어디서 왔는지 알 수 없다. 상세 화면과 형식을 맞춘다. */}
        {themeName ? <p className="page-intro__origin">{themeName}</p> : null}
      </div>

      <section className="historical-summary" aria-labelledby="similar-summary-title">
        <h2 id="similar-summary-title">
          비슷했던 과거{' '}
          <b>{(summary?.eligibleCount ?? data.items.length).toLocaleString('ko-KR')}건</b>
          <InfoTip label="비슷한 사례를 고르는 기준">
            수익률이 아니라 <b>왜 올랐는지</b>가 닮은 사건을 같은 테마 안에서 찾습니다. 결과는 사례를
            고른 뒤에 붙였고, 고를 때는 쓰지 않았습니다.
          </InfoTip>
        </h2>
        {/* 건수 바로 아래에 둔다. 카드 맨 아래면 기간 토글에 붙어 어느 값에 걸린 경고인지
            흐려진다 (screen_spec 8.8 `표본 부족 경고를 요약 가까이에`). */}
        {smallSample ? (
          <p className="historical-summary__warning">표본이 적어 참고용이에요.</p>
        ) : null}
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
      </section>

      {/* MVP에서 허용되는 필터는 기간 전환뿐이다. 표본 수를 숨기는 소재 필터는 두지 않는다 (screen_spec 9.4). */}
      <div className="horizon-toggle" role="tablist" aria-label="사례 결과 기간 선택">
        {HORIZONS.map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={horizon === value}
            onClick={() =>
              // 기간을 바꾸면 목록이 통째로 달라진다. 펼쳐 둔 채로 두면 어디를 보고 있었는지 잃는다.
              patchParams({ horizon: String(value), cases: null })
            }
          >
            {horizonLabel(value)}
          </button>
        ))}
      </div>

      {data.items.length ? (
        <ul className="case-list">
          {/* 사례가 많으면 처음엔 3건만 보여준다. 목록이 길면 아래 고지까지 스크롤이 멀어진다. */}
          {(expanded ? data.items : data.items.slice(0, VISIBLE_CASES)).map((item) => {
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
                    {/* 당시 테마명은 날짜가 아니라 태그다. 같은 줄에 붙이면 날짜의 일부처럼 읽힌다. */}
                    <small>{formatDate(`${item.marketDate}T00:00:00+09:00`)}</small>
                    <strong>{item.normalizedCatalystSummary}</strong>
                    {item.similarityReasons.length ? (
                      <span className="case-list__tags">
                        {item.similarityReasons.map((reason) => (
                          <em key={reason}>{reason}</em>
                        ))}
                      </span>
                    ) : null}
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

      {data.items.length > VISIBLE_CASES ? (
        <button
          type="button"
          className="expand-button expand-button--inline case-list__more"
          aria-expanded={expanded}
          onClick={() => patchParams({ cases: expanded ? null : 'all' })}
        >
          {/* 아코디언 모양은 테마 상세와 맞춘다. 글자 없이 화살표만 둔다. */}
          <span>{expanded ? '접기' : '더 보기'}</span>
          <i aria-hidden="true" data-open={expanded ? 'true' : 'false'} />
        </button>
      ) : null}

      <p className="notice">
        기본 정렬은 관련성 순서입니다. 미래 수익률로 다시 정렬하지 않습니다. 과거 관측 요약이며 미래
        수익률 예측이 아닙니다.
      </p>
    </div>
  );
}

function GateHeader({ onBack }: { onBack: () => void }) {
  return (
    <header className="app-bar">
      <button type="button" className="app-bar__back" onClick={onBack} aria-label="이전 화면으로 돌아가기">
        <IconChevronRightSmallLine size={24} aria-hidden="true" />
      </button>
      {/* 화면 이름은 바로 아래 머리글 카드가 진다. 상단바에 또 두면 두 번 나온다. */}
      <span aria-hidden="true" />
      <span className="app-bar__spacer" aria-hidden="true" />
    </header>
  );
}
