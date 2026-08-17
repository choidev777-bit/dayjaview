import { useState } from 'react';
import { IconArrowLeftLine, IconStarFill, IconStarLine } from '@karrotmarket/react-monochrome-icon';
import { useLocation, useParams } from 'react-router-dom';
import { useRepository } from '../app/RepositoryContext';
import type { HistoricalEventResponse, HistoricalHorizon } from '../domain/contracts';
import {
  formatDate,
  formatReturn,
  horizonLabel,
  outcomeText,
  returnTone,
} from '../domain/formatting';
import { asRepositoryError } from '../domain/repositoryErrors';
import { InfoTip } from '../shared/InfoTip';
import { EmptyState, ErrorPage, LoadingState, PermissionState } from '../shared/StatePanel';
import { useGoBack } from '../shared/useGoBack';
import { useRepositoryResource } from '../shared/useRepositoryResource';

const HORIZONS: readonly HistoricalHorizon[] = [1, 5, 20];

export function HistoricalEventPage() {
  const repository = useRepository();
  const location = useLocation();
  const params = useParams();
  const matchedEventId = params.matchedEventId ?? '';
  const state = location.state as {
    contextEventId?: string;
    themeId?: string;
    catalystId?: string;
  } | null;
  const contextEventId = state?.contextEventId;
  // 공유 링크로 바로 들어오면 되돌아갈 기록이 없다. 들어온 맥락이 있으면 그 화면으로,
  // 없으면 홈으로 보낸다.
  const fallback = state?.catalystId
    ? `/catalysts/${encodeURIComponent(state.catalystId)}`
    : state?.themeId && contextEventId
      ? `/themes/${encodeURIComponent(state.themeId)}/events/${encodeURIComponent(contextEventId)}`
      : '/today';
  const goBack = useGoBack(fallback);
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
          <EventHeader onBack={goBack} />
          <PermissionState />
        </div>
      );
    }
    return <ErrorPage error={resource.error} retry={resource.retry} />;
  }

  const detail = resource.data.data;
  const reasons = detail.similarityReasons ?? [];
  // 바스켓에 실제로 반영된 종목 수. 가격이 없어 빠진 종목을 조용히 감추지 않는다 (screen_spec 10.5·10.6).
  const priced = detail.leaders.filter((leader) => leader.return !== null).length;

  return (
    <div className="page page--case-detail">
      <EventHeader
        onBack={goBack}
        // screen_spec 10.2 1번: 사건일·테마·사건 요약과 함께 이벤트 저장·해제를 둔다.
        save={{ eventId: detail.eventId, displayName: detail.catalystSummary }}
      />

      <div className="page-intro">
        <small>
          {detail.displayNameAtEvent} · {formatDate(`${detail.marketDate}T00:00:00+09:00`)}
        </small>
        <h1>{detail.catalystSummary}</h1>
      </div>

      <div className="detail-card">
        {/* 어디서 열었는지와 겹치는 종목이 먼저다. 태그만 있는 `비슷한 이유`보다 이쪽이 증거다. */}
        {state?.themeId && contextEventId ? (
          <TodayOverlap
            themeId={state.themeId}
            contextEventId={contextEventId}
            pastLeaders={detail.leaders}
            marketDate={detail.marketDate}
          />
        ) : null}

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
                <article key={horizon} data-tone={outcome.tone}>
                  <span>{horizonLabel(horizon)}</span>
                  <strong className={outcome.tone}>{outcome.text}</strong>
                </article>
              );
            })}
          </div>
        </section>

        <section aria-labelledby="historical-leaders-title">
          {/* 기준 설명 두 줄은 물음표로 접는다. 목록보다 먼저 읽힐 내용이 아니다. */}
          <div className="section-heading">
            <h2 id="historical-leaders-title">당시 주도 종목</h2>
            {detail.leaders.length ? (
              <InfoTip label="당시 주도 종목 계산 기준">
                <b>사건 당일 등락률</b>입니다. 전일 종가 대비 그날 종가로 계산했습니다. 위의 `당시 결과`는
                T+1·T+5·T+20이라 기준이 다릅니다.
                <br />
                당시 기록된 {detail.leaders.length.toLocaleString('ko-KR')}종목 중 가격이 확인된{' '}
                {priced.toLocaleString('ko-KR')}종목이 바스켓에 반영됐습니다.
              </InfoTip>
            ) : null}
          </div>
          {/* 현재 관련주를 과거에 소급하지 않는다. 서버가 준 당시 명단만 그대로 쓴다 (screen_spec 10.5). */}
          {detail.leaders.length ? (
            <>
              <ol className="leader-list">
                {detail.leaders.map((leader) => (
                  <li key={leader.stockId}>
                    <div>
                      <strong>{leader.name}</strong>
                    </div>
                    {leader.return === null ? (
                      <span className="leader-list__missing">가격 없음 · 바스켓 제외</span>
                    ) : (
                      <strong className={returnTone(leader.return)}>
                        {formatReturn(leader.return)}
                      </strong>
                    )}
                  </li>
                ))}
              </ol>
            </>
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

/**
 * 오늘 사건과 이 과거 사건에서 이름이 겹치는 종목. 두 응답이 이미 주는 값만 맞춰 보는 것이고
 * 유사도 점수를 새로 만들지 않는다 (screen_spec 10.3 `보정되지 않은 93% 유사 같은 숫자는 쓰지 않는다`).
 */
function TodayOverlap({
  themeId,
  contextEventId,
  pastLeaders,
  marketDate,
}: {
  themeId: string;
  contextEventId: string;
  pastLeaders: HistoricalEventResponse['data']['leaders'];
  marketDate: string;
}) {
  const repository = useRepository();
  const resource = useRepositoryResource(
    repository,
    'detail',
    () => repository.getThemeDetail(themeId, contextEventId),
    [repository, themeId, contextEventId],
  );

  if (resource.status !== 'success') return null;

  const today = resource.data.data;
  const pastIds = new Set(pastLeaders.map((leader) => leader.stockId));
  const shared = today.leaders.filter((leader) => pastIds.has(leader.stockId));

  const pastReturn = new Map(pastLeaders.map((leader) => [leader.stockId, leader.return]));

  return (
    <>
      {/* 어디서 타고 들어왔는지는 맥락일 뿐이라 제목 위에 한 줄로만 둔다. */}
      <p className="today-origin">
        오늘 <strong>{today.classification.displayName}</strong>에서 열어봄
      </p>

      <section aria-labelledby="overlap-title">
        <h2 id="overlap-title">오늘과 겹치는 종목</h2>
        {shared.length ? (
          <>
            {/* 오늘 값만 두면 비교가 안 된다. 같은 종목이 그때는 어땠는지 나란히 놓는다. */}
            <ul className="overlap-list">
              <li className="overlap-list__head" aria-hidden="true">
                <span />
                <span>{formatDate(`${marketDate}T00:00:00+09:00`)}</span>
                <span>오늘</span>
              </li>
              {shared.map((leader) => {
                const then = pastReturn.get(leader.stockId) ?? null;
                return (
                  <li key={leader.stockId}>
                    <strong>{leader.name}</strong>
                    <span className={returnTone(then)}>{formatReturn(then)}</span>
                    <span className={returnTone(leader.return)}>{formatReturn(leader.return)}</span>
                  </li>
                );
              })}
            </ul>
            <p className="section-note">
              왼쪽은 이 사례 당일, 오른쪽은 오늘 등락률입니다. 같은 종목이 두 번 다 주도 종목이었다는
              뜻이고, 오늘 결과를 예측하지 않습니다.
            </p>
          </>
        ) : (
          <p className="section-note">오늘 주도 종목과 겹치는 종목은 없습니다.</p>
        )}
      </section>
    </>
  );
}

function EventHeader({
  onBack,
  save,
}: {
  onBack: () => void;
  save?: { eventId: string; displayName: string };
}) {
  return (
    <header className="app-bar">
      <button type="button" onClick={onBack} aria-label="이전 화면으로 돌아가기">
        <IconArrowLeftLine size={24} aria-hidden="true" />
      </button>
      <strong>과거 사례</strong>
      {save ? <SaveEventButton {...save} /> : <span className="app-bar__spacer" aria-hidden="true" />}
    </header>
  );
}

/** 과거 사건 저장·해제 (screen_spec 10.2). 저장 목록의 `이벤트` 필터가 이 항목을 받는다. */
function SaveEventButton({ eventId, displayName }: { eventId: string; displayName: string }) {
  const repository = useRepository();
  const [mutating, setMutating] = useState(false);
  const resource = useRepositoryResource(
    repository,
    'saved',
    () => repository.getSaved('EVENT'),
    [repository],
  );

  if (resource.status !== 'success') {
    return (
      <button type="button" disabled aria-label="저장 상태 확인 중">
        <IconStarLine size={24} aria-hidden="true" />
      </button>
    );
  }

  const saved = resource.data.data.items.some((item) => item.targetId === eventId);

  async function toggle() {
    setMutating(true);
    try {
      if (saved) {
        await repository.removeSaved({ savedType: 'EVENT', targetId: eventId });
      } else {
        await repository.saveSaved({ savedType: 'EVENT', targetId: eventId, displayName });
      }
      resource.retry();
    } finally {
      setMutating(false);
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={mutating}
      aria-pressed={saved}
      aria-label={saved ? '저장한 사례에서 해제' : '이 사례를 저장'}
    >
      {saved ? <IconStarFill size={24} aria-hidden="true" /> : <IconStarLine size={24} aria-hidden="true" />}
    </button>
  );
}
