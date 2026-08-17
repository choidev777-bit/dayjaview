import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import {
  IconArrowLeftLine,
  IconChevronRightSmallLine,
  IconStarFill,
  IconStarLine,
  IconXmarkLine,
} from '@karrotmarket/react-monochrome-icon';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useRepository } from '../app/RepositoryContext';
import type {
  EvidenceItem,
  EvidenceResponse,
  EvidenceStatus,
  ResponseMeta,
  SavedTarget,
  ThemeDetailResponse,
} from '../domain/contracts';
import {
  coverageStatusLabel,
  evidenceFlagLabel,
  evidenceStatusLabel,
  evidenceStatusNote,
  eventStatusLabel,
  formatDate,
  formatReturn,
  formatTime,
  hasConfirmedEvidence,
  horizonLabel,
  outcomeText,
  returnTone,
} from '../domain/formatting';
import { CoverageIndicator } from '../shared/CoverageIndicator';
import { InfoTip } from '../shared/InfoTip';
import { EmptyState, ErrorPage, ErrorState, LoadingState } from '../shared/StatePanel';
import { useGoBack } from '../shared/useGoBack';
import { useRepositoryResource } from '../shared/useRepositoryResource';
import { readViewState, writeViewState } from '../shared/viewState';

type ThemeDetail = ThemeDetailResponse['data'];
type EvidenceSummary = ThemeDetail['evidenceSummary'];
type EvidencePage = EvidenceResponse['data']['page'];
type EvidencePhase = 'LIVE' | 'AFTER_CLOSE';

/** 관심 공백 배지 기준 (screen_spec 8.4). */
const ATTENTION_GAP_MIN = 60;
const VISIBLE_EVIDENCE = 3;
/** 상세 기본 노출은 3개 (screen_spec 8.6). 나머지는 펼쳐서 본다. */
const VISIBLE_LEADERS = 3;
const NEWS_DELAY_FLAGS = ['SOURCE_DEGRADED', 'STALE_NEWS_DATA'];

function newsCollectionDelayed(meta: ResponseMeta): boolean {
  const context = meta.marketContext;
  if (!context) return false;
  return (
    context.dataStatus === 'DELAYED' ||
    context.dataStatus === 'DEGRADED' ||
    context.qualityFlags.some((flag) => NEWS_DELAY_FLAGS.includes(flag))
  );
}

function SaveThemeButton({
  themeId,
  displayName,
  currentState,
  onFailureChange,
}: {
  themeId: string;
  displayName: string;
  /** 저장 목록이 요구하는 현재 상태(screen_spec 12.1). 없으면 목록에 이름만 남는다. */
  currentState: SavedTarget['currentState'];
  onFailureChange: (failed: boolean) => void;
}) {
  const repository = useRepository();
  const [mutating, setMutating] = useState(false);
  const resource = useRepositoryResource(
    repository,
    'saved',
    () => repository.getSaved('THEME'),
    [repository],
  );

  if (resource.status !== 'success') {
    return (
      <button type="button" disabled aria-label="저장 상태 확인 중">
        <IconStarLine size={24} aria-hidden="true" />
      </button>
    );
  }

  const saved = resource.data.data.items.some(
    (item) => item.savedType === 'THEME' && item.targetId === themeId,
  );

  async function toggleSaved() {
    setMutating(true);
    onFailureChange(false);
    try {
      if (saved) {
        await repository.removeSaved({ savedType: 'THEME', targetId: themeId });
      } else {
        await repository.saveSaved({
          savedType: 'THEME',
          targetId: themeId,
          displayName,
          currentState,
        });
      }
      resource.retry();
    } catch {
      onFailureChange(true);
    } finally {
      setMutating(false);
    }
  }

  return (
    <button
      type="button"
      onClick={toggleSaved}
      disabled={mutating}
      aria-pressed={saved}
      aria-label={saved ? '관심에서 저장 해제' : '관심에 저장'}
    >
      {saved ? <IconStarFill size={24} aria-hidden="true" /> : <IconStarLine size={24} aria-hidden="true" />}
    </button>
  );
}

/**
 * 요약이 제목과 사실상 같은 문장인지. 인포스탁 기록은 제목 뒤에 `(주도주 : …)`만 덧붙는
 * 경우가 많아, 괄호 뒤를 떼고 비교한다.
 */
function sameSentence(title: string, summary: string): boolean {
  const strip = (text: string) =>
    text
      .replace(/\(주도주[^)]*\)/g, '')
      .replace(/\s+/g, '')
      .trim();
  const a = strip(title);
  const b = strip(summary);
  return a === b || b.startsWith(a) || a.startsWith(b);
}

function EvidenceList({
  items,
  showTime = true,
  showSource = true,
  hasMore = false,
  loadingMore = false,
  loadMoreFailed = false,
  onLoadMore,
}: {
  items: EvidenceItem[];
  /** 장중 근거는 몇 시에 들어왔는지가 중요하다. 마감 후 확정 사유는 시각이 하나뿐이라 뺀다. */
  showTime?: boolean;
  /** 장중 이력은 그때 본 화면을 그대로 되살리는 자리라 출처 물음표까지 달지 않는다. */
  showSource?: boolean;
  hasMore?: boolean;
  loadingMore?: boolean;
  loadMoreFailed?: boolean;
  onLoadMore?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? items : items.slice(0, VISIBLE_EVIDENCE);
  const hidden = items.length - visible.length;

  return (
    <>
      <ul className="evidence-list">
        {visible.map((item) => (
          <li key={item.newsId} data-time={showTime ? 'true' : 'false'}>
            {showTime ? <span>{formatTime(item.publishedAt)}</span> : null}
            {/* 매체·발행 시각·원문 링크와 근거 상태를 물음표 하나에 모은다. 지우는 게 아니라
                접는 것이라 `영역 선택 또는 별도 상세`를 허용하는 screen_spec §8.3을 지킨다. */}
            <strong className="evidence-list__title">
              <span>{item.title}</span>
            </strong>
            {/* 물음표를 화면마다 달면 눈이 아파서 옅은 한 줄로 되돌린다. 매체·시각·원문은
                지울 수 없다 (screen_spec §4.2 SINGLE_SOURCE `매체·시각·원문 제공`). */}
            {showSource ? (
              <a className="evidence-list__source" href={item.originalUrl} target="_blank" rel="noreferrer">
                {item.sourceName} ·{' '}
                {item.publishedAt
                  ? formatTime(item.publishedAt)
                  : `발행 시각 미확인 · 수집 ${formatTime(item.receivedAt)}`}{' '}
                · 원문
              </a>
            ) : null}
            {/* 인포스탁 기록은 제목과 요약이 같은 문장인 경우가 많다. 그러면 같은 글이 두 번
                나오고, 요약 안 `주도주 : …`는 아래 `오늘의 주도 종목`과도 겹친다. */}
            {item.summary && !sameSentence(item.title, item.summary) ? <p>{item.summary}</p> : null}
            {item.qualityFlags.some((flag) => evidenceFlagLabel(flag)) ? (
              <p className="evidence-list__basis">
                {item.qualityFlags.map((flag) => {
                  const label = evidenceFlagLabel(flag);
                  return label ? (
                    <span key={flag} className="badge">
                      {label}
                    </span>
                  ) : null;
                })}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
      {hidden > 0 || expanded ? (
        <button
          type="button"
          className="expand-button"
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
        >
          <span>{expanded ? '근거 접기' : `근거 ${hidden.toLocaleString('ko-KR')}건 더 보기`}</span>
          <i aria-hidden="true" data-open={expanded ? 'true' : 'false'} />
        </button>
      ) : null}
      {hasMore && onLoadMore ? (
        <button type="button" className="expand-button" onClick={onLoadMore} disabled={loadingMore}>
          <span>{loadingMore ? '근거를 더 불러오는 중입니다' : '이전 근거 더 불러오기'}</span>
          <i aria-hidden="true" data-open="false" />
        </button>
      ) : null}
      {loadMoreFailed ? (
        <p className="confirmation-note" role="alert">
          이전 근거를 더 불러오지 못했습니다. 지금까지 확인된 근거만 표시합니다.
        </p>
      ) : null}
    </>
  );
}

/**
 * 주도 종목. 상세는 상위 3~5개를 표시하고(realtime_theme_feature_spec 14),
 * 기본은 3개만 펼쳐 둔다 (screen_spec 8.6).
 *
 * `주도` 라벨은 첫 번째 종목에만 붙인다. 나머지도 주도 종목이지만, 라벨을 다 붙이면
 * Leader Score 1위가 누구인지 사라진다 (screen_spec 8.6).
 *
 * 종목 상세가 후속 범위라 행을 링크로 만들지 않는다 (screen_spec 8.6). 저장해도
 * 돌아갈 화면이 없어 종목 저장도 두지 않는다. 종목 상세가 생기면 그때 함께 만든다.
 */
function LeaderList({ leaders }: { leaders: ThemeDetail['leaders'] }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? leaders : leaders.slice(0, VISIBLE_LEADERS);
  const hidden = leaders.length - visible.length;

  return (
    <>
      <ol className="leader-list">
        {visible.map((leader, index) => (
          <li key={leader.stockId}>
            <div>
              <strong>{leader.name}</strong>
              {index === 0 ? <span className="badge">주도</span> : null}
            </div>
            <strong className={returnTone(leader.return)}>{formatReturn(leader.return)}</strong>
          </li>
        ))}
      </ol>
      {hidden > 0 || expanded ? (
        <button
          type="button"
          className="expand-button expand-button--inline"
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
        >
          <span className="visually-hidden">{expanded ? '주도 종목 접기' : '주도 종목 더 보기'}</span>
          <i aria-hidden="true" data-open={expanded ? 'true' : 'false'} />
        </button>
      ) : null}
    </>
  );
}

/** 장중에 표시했던 근거. 확정으로 바뀌어도 같은 화면에서 이력으로 남긴다. */
interface LiveEvidenceHistory {
  evidenceStatus: EvidenceStatus;
  summary: string | null;
  items: EvidenceItem[];
  observedAt: string;
  /** 이 이력이 어느 장의 것인지. 날짜가 바뀌면 버린다. */
  marketDate: string | null;
}

/** 첫 page 이후 사용자가 더 불러온 근거. 근거 응답이 갱신되면 처음부터 다시 센다. */
interface EvidencePagination {
  source: EvidenceResponse | null;
  items: EvidenceItem[];
  page: EvidencePage | null;
  loading: boolean;
  failed: boolean;
}

const EMPTY_PAGINATION: EvidencePagination = {
  source: null,
  items: [],
  page: null,
  loading: false,
  failed: false,
};

/** 시안의 `과거엔 어땠을까요` + `DAY-JA-VIEW 케이스`. 둘 다 유사사례 응답 하나에서 나온다. */
function DejavuSummarySection({ themeId, eventId }: { themeId: string; eventId: string }) {
  const repository = useRepository();
  const resource = useRepositoryResource(
    repository,
    'historical',
    () => repository.getSimilarEvents(eventId, 5),
    [repository, eventId],
  );

  if (resource.status === 'loading') return <LoadingState label="과거 기록을 불러오는 중입니다" />;
  if (resource.status !== 'success') return null;

  const data = resource.data.data;
  if (data.availability !== 'AVAILABLE') return null;

  const total = data.summary[0]?.eligibleCount ?? data.items.length;

  return (
    <>
      <section aria-labelledby="dejavu-summary-title">
        <div className="section-heading">
          <h2 id="dejavu-summary-title">과거엔 어땠을까요?</h2>
        </div>
        {/* `이벤트 스터디`는 버튼처럼 보여서 제목 옆에서 내려 설명 문장에 녹인다.
            분모가 되는 건수는 표본 크기라 굵게 둔다 (screen_spec 8.8 표본 부족 경고). */}
        <p className="section-note">
          비슷했던 <strong>과거 {total.toLocaleString('ko-KR')}건</strong>에서 당시 주도 종목이 어떻게
          움직였는지 모아 중앙값으로 봤어요.
        </p>
        {/* 칸 안은 값만 둔다. `중앙`과 기간별 분모는 아래 계산 기준에서 밝힌다
            (screen_spec 8.8 `대표를 사용한다면 계산 기준에서 중앙값임을 명시한다`). */}
        <div className="metric-grid">
          {data.summary.map((row) => (
            <article key={row.horizonTradingDays} data-tone={returnTone(row.medianReturn)}>
              <span>{horizonLabel(row.horizonTradingDays)}</span>
              <strong className={returnTone(row.medianReturn)}>
                {row.medianReturn === null ? '기록 없음' : formatReturn(row.medianReturn)}
              </strong>
            </article>
          ))}
        </div>
      </section>

      <section aria-labelledby="cases-title">
        <div className="section-heading">
          <div>
            {/* 제품 이름 부분만 포인트 색으로 둔다. `케이스`까지 물들이면 제목이 아니라
                배지처럼 읽힌다. */}
            <h2 id="cases-title">
              <span className="brand-icon" aria-hidden="true" />
              <em className="brand-mark">DAY-JA-VIEW</em> 케이스
              <InfoTip label="비슷한 사례를 고르는 기준">
                오늘과 비슷했던 과거입니다. 수익률이 아니라 <b>왜 올랐는지</b>가 닮은 사건을 같은 테마
                안에서 찾습니다.
              </InfoTip>
            </h2>
          </div>
          <Link
            className="text-button"
            to={`/themes/${encodeURIComponent(themeId)}/events/${encodeURIComponent(eventId)}/similar`}
          >
            전체 보기
            <IconChevronRightSmallLine size={18} aria-hidden="true" />
          </Link>
        </div>
        {data.items.length ? (
          <ul className="case-list">
            {data.items.slice(0, 3).map((item) => {
              const result = outcomeText(item.outcomes.find((row) => row.horizonTradingDays === 5));
              return (
                <li key={item.matchedEventId}>
                  <Link
                    to={`/events/${encodeURIComponent(item.matchedEventId)}`}
                    state={{ contextEventId: eventId, themeId }}
                  >
                    <span className="case-list__copy">
                      <small>{formatDate(`${item.marketDate}T00:00:00+09:00`)}</small>
                      <strong>{item.normalizedCatalystSummary}</strong>
                      {item.similarityReasons.length ? (
                        <span className="case-list__tags">
                          {item.similarityReasons.map((reason) => (
                            <em key={reason}>{reason}</em>
                          ))}
                        </span>
                      ) : null}
                      {/* 기간과 결과는 오른쪽 끝으로 보낸다. 카드마다 자리가 같아야 눈으로 훑힌다. */}
                      <b className="case-list__outcome">
                        <small>+5 거래일</small>
                        <span className={result.tone}>{result.text}</span>
                      </b>
                    </span>
                    <IconChevronRightSmallLine size={18} aria-hidden="true" />
                  </Link>
                </li>
              );
            })}
          </ul>
        ) : (
          <EmptyState title="동일 유형 과거사례 없음" />
        )}
      </section>
    </>
  );
}

function CatalystTop3Section({
  themeId,
  eventId,
  themeName,
}: {
  themeId: string;
  eventId: string;
  themeName: string;
}) {
  const repository = useRepository();
  const resource = useRepositoryResource(
    repository,
    'historical',
    () => repository.getCatalystTop3(themeId, eventId),
    [repository, themeId, eventId],
  );

  // 게이트·미제공은 오류가 아니다. 섹션을 그리지 않는다.
  if (resource.status !== 'success') return null;

  const { items, qualityNote } = resource.data.data;
  if (!items.length) return null;
  // 유효 유형이 1~2개면 `TOP3`라고 부르지 않는다 (screen_spec 8.7).
  const tail = items.length >= 3 ? '반응 TOP3' : '반응 기록';

  return (
    <section aria-labelledby="catalyst-top-title">
      <div className="section-heading">
        {/* 테마 이름이 길면 `반응 TOP3`만 남고 잘려 보인다. 이름과 꼬리말을 아예 다른 줄로 둔다. */}
        <h2 id="catalyst-top-title">
          <span className="catalyst-heading__theme">과거 {themeName}</span>
          <span className="catalyst-heading__tail">{tail}</span>
        </h2>
        {/* 품질 주의는 화면에서 빼고 발표 때 말로 설명한다
            (바탕화면 `발표자-구두설명-체크리스트.md`). 물음표 안에는 남긴다. */}
        {qualityNote ? (
          <InfoTip label="소재 유형 기준">
            <strong>소재 유형 기준</strong>
            {qualityNote}
          </InfoTip>
        ) : null}
      </div>
      <ol className="catalyst-list">
        {items.slice(0, 3).map((item, index) => (
          <li key={item.catalystId}>
            <Link
              to={`/catalysts/${encodeURIComponent(item.catalystId)}`}
              state={{ themeId, eventId }}
            >
              <b>{index + 1}</b>
              <span className="catalyst-list__copy">
                {/* `오늘과 같은 유형`은 이름보다 먼저 눈에 들어와야 왜 이 소재를 보는지가 읽힌다. */}
                {item.matchesToday ? <em>오늘과 같은 유형</em> : null}
                <strong>{item.catalystName}</strong>
              </span>
              {/* 표본 크기는 오른쪽으로 빼서 항목끼리 바로 비교되게 한다.
                  중앙 반응은 소재 상세에서 기간별로 보여준다. 목록에서는 건수만 센다. */}
              <span className="catalyst-list__count">
                {item.eligibleCount.toLocaleString('ko-KR')}건
              </span>
              <IconChevronRightSmallLine className="row-chevron" size={18} aria-hidden="true" />
            </Link>
          </li>
        ))}
      </ol>
    </section>
  );
}

function ReasonSection({ eventId, summary }: { eventId: string; summary: EvidenceSummary }) {
  const repository = useRepository();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab: EvidencePhase | null =
    searchParams.get('reason') === 'live'
      ? 'LIVE'
      : searchParams.get('reason') === 'after'
        ? 'AFTER_CLOSE'
        : null;
  const setRequestedTab = useCallback(
    (next: EvidencePhase) => {
      const params = new URLSearchParams(searchParams);
      params.set('reason', next === 'LIVE' ? 'live' : 'after');
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams],
  );
  const [paginationState, setPaginationState] = useState<EvidencePagination>(EMPTY_PAGINATION);
  // 장중 이력은 화면을 떠나도 남긴다. 상세를 나갔다 오면 사라져 `이력이 없습니다`가 되던 문제.
  // 장 마감 뒤에도 그날 안에는 계속 볼 수 있어야 한다.
  const historyKey = `evidence.live:${eventId}`;
  const liveTabRef = useRef<HTMLButtonElement>(null);
  const afterCloseTabRef = useRef<HTMLButtonElement>(null);
  const resource = useRepositoryResource<{
    response: EvidenceResponse;
    history: LiveEvidenceHistory | null;
  }>(
    repository,
    'evidence',
    async () => {
      const response = await repository.getEvidence(eventId);
      // 장중 근거는 확정 뒤에도 이력으로 보여준다 (screen_spec 4.2 AFTER_CLOSE_CONFIRMED).
      const marketDate = response.meta.marketContext?.marketDate ?? null;
      // 시연 어댑터는 그날 장중에 봤을 법한 이력을 meta에 실어 보낸다. 실제 서버 응답에는
      // 없는 값이라 있을 때만 쓴다.
      const seeded = (response.meta as { liveEvidenceHistory?: LiveEvidenceHistory })
        .liveEvidenceHistory;
      const stored = readViewState<LiveEvidenceHistory>(historyKey) ?? seeded;
      // 날짜가 바뀌면 어제 장중 이력을 오늘 것으로 보여주지 않는다.
      const kept = stored && stored.marketDate === marketDate ? stored : null;
      if (response.data.evidenceStatus === 'AFTER_CLOSE_CONFIRMED') {
        return { response, history: kept };
      }
      const observed: LiveEvidenceHistory = {
        evidenceStatus: response.data.evidenceStatus,
        summary: hasConfirmedEvidence(response.data.evidenceStatus) ? summary.summary : null,
        items: response.data.items,
        observedAt: response.meta.generatedAt,
        marketDate,
      };
      writeViewState(historyKey, observed);
      return { response, history: null };
    },
    [repository, eventId],
  );

  const loaded = resource.status === 'success' ? resource.data.response : null;
  const history = resource.status === 'success' ? resource.data.history : null;
  const pagination = loaded && paginationState.source === loaded ? paginationState : EMPTY_PAGINATION;

  const items = loaded ? [...loaded.data.items, ...pagination.items] : [];
  // 근거 응답이 이 화면의 기준이다. 상세 문서의 요약 상태는 근거를 불러오기 전까지만 쓴다.
  const evidenceStatus = loaded ? loaded.data.evidenceStatus : summary.evidenceStatus;
  const confirmed = evidenceStatus === 'AFTER_CLOSE_CONFIRMED';

  const page = pagination.page ?? loaded?.data.page ?? null;
  const hasMore = page !== null && page.hasMore && page.nextCursor !== null;

  async function loadMore() {
    const cursor = page?.nextCursor;
    if (!loaded || !cursor) return;
    setPaginationState({ ...pagination, source: loaded, loading: true, failed: false });
    try {
      const next = await repository.getEvidence(eventId, cursor);
      const known = new Set(items.map((item) => item.newsId));
      setPaginationState((current) => ({
        source: loaded,
        items: [
          ...(current.source === loaded ? current.items : []),
          ...next.data.items.filter((item) => !known.has(item.newsId)),
        ],
        page: next.data.page,
        loading: false,
        failed: false,
      }));
    } catch {
      setPaginationState((current) => ({
        ...current,
        source: loaded,
        loading: false,
        failed: true,
      }));
    }
  }

  const tab = requestedTab ?? (confirmed ? 'AFTER_CLOSE' : 'LIVE');
  const changedFromLive =
    confirmed &&
    history !== null &&
    (history.summary !== summary.summary ||
      history.items.map((item) => item.newsId).join(',') !==
        items.map((item) => item.newsId).join(','));

  function selectTab(next: EvidencePhase) {
    setRequestedTab(next);
    (next === 'LIVE' ? liveTabRef : afterCloseTabRef).current?.focus();
  }

  function handleTabKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'ArrowRight' || event.key === 'End') {
      event.preventDefault();
      selectTab('AFTER_CLOSE');
    }
    if (event.key === 'ArrowLeft' || event.key === 'Home') {
      event.preventDefault();
      selectTab('LIVE');
    }
  }

  return (
    <section aria-labelledby="reason-title">
      <div className="section-heading">
        {/* 근거 상태 설명은 기사 옆 물음표 하나로 합쳤다. 제목에도 붙이면 같은 말이 두 번 나온다. */}
        <h2 id="reason-title">오늘 왜 올랐을까요?</h2>
      </div>

      <div
        className="reason-tabs"
        role="tablist"
        aria-label="상승 이유 분석 시점"
        onKeyDown={handleTabKeyDown}
      >
        <button
          ref={liveTabRef}
          type="button"
          role="tab"
          id="reason-tab-live"
          aria-controls="reason-panel"
          aria-selected={tab === 'LIVE'}
          tabIndex={tab === 'LIVE' ? 0 : -1}
          onClick={() => setRequestedTab('LIVE')}
        >
          {confirmed ? '장중 분석 이력' : '실시간 분석'}
        </button>
        <button
          ref={afterCloseTabRef}
          type="button"
          role="tab"
          id="reason-tab-after-close"
          aria-controls="reason-panel"
          aria-selected={tab === 'AFTER_CLOSE'}
          tabIndex={tab === 'AFTER_CLOSE' ? 0 : -1}
          onClick={() => setRequestedTab('AFTER_CLOSE')}
        >
          장 마감 후 분석
        </button>
      </div>

      {/* 탭 아래 내용이 배경 위에 그냥 떠 있어서 어느 탭의 내용인지 경계가 없었다. 박스로 묶는다. */}
      <div
        className="reason-panel"
        role="tabpanel"
        id="reason-panel"
        aria-labelledby={tab === 'LIVE' ? 'reason-tab-live' : 'reason-tab-after-close'}
        tabIndex={0}
      >
        {resource.status === 'loading' ? <LoadingState label="기사 근거를 확인하는 중입니다" /> : null}
        {resource.status === 'error' ? <ErrorState error={resource.error} retry={resource.retry} /> : null}

        {loaded
          ? (() => {
              const delayed = newsCollectionDelayed(loaded.meta);
              const lastHealthyAt = loaded.meta.marketContext?.lastHealthyAt ?? null;

              if (tab === 'AFTER_CLOSE' && !confirmed) {
                return (
                  <div className="after-close-pending">
                    <strong>장 마감 후 오늘 하루를 다시 분석해요</strong>
                    <p>
                      장중 가격 움직임과 마감 후 확정된 시장 기록을 함께 살펴 상승 배경을 정리해 보여드려요.
                    </p>
                    <small>장중 분석과 달라진 내용이 있다면 마감 후 분석을 기준으로 안내합니다.</small>
                  </div>
                );
              }

              if (tab === 'LIVE' && confirmed) {
                if (!history) {
                  return (
                    <EmptyState
                      title="장중 근거 이력이 남아 있지 않습니다"
                      description="이 화면에서 장중 근거를 확인하기 전에 확정돼 확정 사유만 제공합니다."
                    />
                  );
                }
                return (
                  <>
                    <p className="section-note">
                      {evidenceStatusLabel(history.evidenceStatus)} · 장중 마지막 확인{' '}
                      {formatTime(history.observedAt)}
                    </p>
                    {history.summary ? <p className="reason-summary">{history.summary}</p> : null}
                    {history.items.length ? (
                      <EvidenceList items={history.items} showSource={false} />
                    ) : (
                      <EmptyState
                        title={evidenceStatusLabel(history.evidenceStatus)}
                        description={evidenceStatusNote(history.evidenceStatus)}
                      />
                    )}
                    <p className="confirmation-note">
                      장중에 표시했던 내용이며 현재 기준은 장 마감 후 분석입니다.
                    </p>
                  </>
                );
              }

              return (
                <>
                  {delayed ? (
                    <p className="confirmation-note" role="status">
                      뉴스 수집이 지연되고 있습니다. 확인된 신규 소재 없음과 다른 상태입니다.
                      {lastHealthyAt ? ` 마지막 정상 수집 ${formatTime(lastHealthyAt)}` : ''}
                    </p>
                  ) : null}

                  {changedFromLive ? (
                    <p className="confirmation-note" role="status">
                      장중에 표시했던 내용과 달라졌습니다. 확정 사유를 기준으로 안내합니다.
                    </p>
                  ) : null}

                  {/* 근거가 한 건이고 그 제목이 확정 사유와 같은 문장이면 두 번 적지 않는다.
                      인포스탁 기록처럼 사유 자체가 출처인 경우가 그렇다. */}
                  {hasConfirmedEvidence(evidenceStatus) &&
                  summary.summary &&
                  !(items.length === 1 && items[0].title.trim() === summary.summary.trim()) ? (
                    <p className="reason-summary">{summary.summary}</p>
                  ) : null}

                  {items.length ? (
                    <>
                      {/* 상태 설명은 제목 옆 물음표로 옮겼다. */}
                      <EvidenceList
                        items={items}
                        showTime={tab === 'LIVE'}
                        
                        hasMore={hasMore}
                        loadingMore={pagination.loading}
                        loadMoreFailed={pagination.failed}
                        onLoadMore={loadMore}
                      />
                    </>
                  ) : (
                    <EmptyState
                      title={evidenceStatusLabel(evidenceStatus)}
                      description={
                        delayed
                          ? '수집이 지연되는 동안에는 확인된 신규 소재가 없다고 단정하지 않습니다.'
                          : evidenceStatusNote(evidenceStatus)
                      }
                    />
                  )}

                  {tab === 'LIVE' ? (
                    <p className="confirmation-note">
                      뉴스 근거는 장중 계속 갱신되며 이후 정정될 수 있습니다.
                    </p>
                  ) : null}
                </>
              );
            })()
          : null}
      </div>
    </section>
  );
}

export function ThemeDetailPage() {
  const repository = useRepository();
  const { themeId = '', eventId = '' } = useParams();
  const [saveFailed, setSaveFailed] = useState(false);
  // 보던 탭은 URL에 남긴다. 새로고침·공유·뒤로 가기에서 그대로 열린다
  // (ui_prototype_adaptation_plan §5.1). 화면 안에서만 쓰는 상태를 주소에 두는 것이라
  // 히스토리를 늘리지 않도록 replace로 바꾼다.
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get('tab') === 'today'
    ? ('today' as const)
    : searchParams.get('tab') === 'dejavu'
      ? ('dejavu' as const)
      : null;
  const [calculationOpen, setCalculationOpen] = useState(false);
  const calculationTriggerRef = useRef<HTMLButtonElement>(null);
  const calculationCloseRef = useRef<HTMLButtonElement>(null);
  const calculationWasOpenRef = useRef(false);
  const resource = useRepositoryResource(
    repository,
    'detail',
    () => repository.getThemeDetail(themeId, eventId),
    [repository, themeId, eventId],
  );

  const goBack = useGoBack('/today');

  const closeCalculation = useCallback(() => {
    setCalculationOpen(false);
  }, []);

  const selectDetailTab = useCallback(
    (tab: 'dejavu' | 'today') => {
      const next = new URLSearchParams(searchParams);
      next.set('tab', tab);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  useEffect(() => {
    if (!calculationOpen) {
      if (calculationWasOpenRef.current) calculationTriggerRef.current?.focus();
      calculationWasOpenRef.current = false;
      return undefined;
    }
    calculationWasOpenRef.current = true;
    calculationCloseRef.current?.focus();
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') closeCalculation();
      if (event.key === 'Tab') {
        event.preventDefault();
        calculationCloseRef.current?.focus();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [calculationOpen, closeCalculation]);

  if (resource.status === 'loading') return <LoadingState label="테마 상세를 불러오는 중입니다" />;
  if (resource.status === 'error') return <ErrorPage error={resource.error} retry={resource.retry} />;

  const detail = resource.data.data;
  const reaction = detail.currentReaction;
  const advancingRatio =
    reaction.advancingCount !== null && reaction.validCount
      ? Math.round((reaction.advancingCount / reaction.validCount) * 100)
      : null;
  const historicalAvailable = detail.historicalAccess.status === 'AVAILABLE';
  // 상세 응답에 rank가 없다. 목록에서 넘어왔다면 그때 받은 순위를 그대로 쓴다.
  const rank = repository.getCachedRank(detail.eventId);
  // 시안 기본 탭은 DAY-JA-VIEW다. 다만 게이트가 닫혀 있으면 그 탭에 보여줄 게 없으므로
  // 사용자가 직접 고르기 전까지는 오늘 현황을 먼저 편다.
  const detailTab = requestedTab ?? (historicalAvailable ? 'dejavu' : 'today');
  const calculationContext = resource.data.meta.marketContext ?? null;

  return (
    <div className="page page--detail">
      <header className="detail-app-bar">
        <button
          type="button"
          onClick={goBack}
          aria-label="이전 화면으로 돌아가기"
        >
          <IconArrowLeftLine size={24} aria-hidden="true" />
        </button>
        <span>{detail.classification.displayName}</span>
        <SaveThemeButton
          themeId={detail.classification.themeId}
          displayName={detail.classification.displayName}
          currentState={
            calculationContext
              ? {
                  eventId: detail.eventId,
                  eventState: detail.lifecycleStatus,
                  weightedReturn: reaction.weightedReturn ?? 0,
                  dataStatus: calculationContext.dataStatus,
                  asOf: calculationContext.asOf,
                }
              : null
          }
          onFailureChange={setSaveFailed}
        />
      </header>

      <section className="theme-summary">
        {/* 테마명과 수익률을 위아래로 쌓으면 주황 카드만 화면의 절반을 먹는다.
            이름은 왼쪽, 수익률은 오른쪽에 두고 이름만 줄바꿈을 허용한다. */}
        <div className="theme-summary__head">
        <div className="theme-summary__title">
          {/* 시안 §4.2: 뱃지 자리는 순위와 관심 공백 둘뿐이고 값이 있을 때만 놓는다.
              둘 다 없으면 `:empty`로 영역이 사라진다. 사건 상태(활성·약화)는 이 자리에 두지 않는다.
              순위는 좌상단, 등락률은 우하단으로 고정한다. */}
          <div className="theme-badges">
            {rank !== null ? <span className="theme-rank-pill">오늘 상승 {rank}위</span> : null}
          </div>
          <h1>{detail.classification.displayName}</h1>
        </div>
        <div className="theme-summary__return">
          <div className="theme-badges">
            {/* 관심 공백은 장기 미관심에만 의미가 있다. `6 거래일 만의 관심`은 알려주는 게 없다.
                screen_spec 8.4의 기준(60거래일 이상)을 그대로 쓴다. */}
            {reaction.attentionGapTradingDays !== null &&
            reaction.attentionGapTradingDays >= ATTENTION_GAP_MIN ? (
              <span className="theme-gap-pill">
                {reaction.attentionGapTradingDays.toLocaleString('ko-KR')} 거래일 만의 관심
              </span>
            ) : null}
          </div>
          {/* 계산 기준은 카드 밖 지표 줄 옆으로 옮겼다. 큰 숫자에 붙이면 자리가 애매하다. */}
          <strong>{formatReturn(reaction.weightedReturn)}</strong>
          {/* 평상시 `활성`은 뱃지 자리를 차지할 만한 정보가 아니다. 다만 확정 대기·약화·종료는
              screen_spec 4.4가 표시를 요구하므로 그때만 상태를 붙인다. */}
          {detail.lifecycleStatus === 'ACTIVE' && detail.reconciliationStatus !== 'UNMATCHED' ? null : (
            <span className="status-chip">
              {eventStatusLabel(detail.lifecycleStatus, detail.reconciliationStatus)}
            </span>
          )}
        </div>
        </div>
        {/* 시안의 가로 3열 칩. 세 번째 칸은 시안이 `거래대금`인데 계약에 그 값이 없어 Coverage를 쓴다. */}
        <button
          ref={calculationTriggerRef}
          className="theme-summary__basis"
          type="button"
          onClick={() => setCalculationOpen(true)}
        >
          계산 기준
          <span className="info-tip__button" aria-hidden="true">
            ?
          </span>
        </button>
        <div className="theme-stats">
          <article>
            <span>상승 종목</span>
            <strong>
              {reaction.advancingCount === null || reaction.validCount === null
                ? '—'
                : `${reaction.advancingCount.toLocaleString('ko-KR')}/${reaction.validCount.toLocaleString('ko-KR')}`}
            </strong>
            <small>{advancingRatio === null ? '계산 불가' : `${advancingRatio}%`}</small>
          </article>
          <article>
            <span>거래 관심</span>
            <strong>
              {reaction.turnoverMultiple === null
                ? '—'
                : `${reaction.turnoverMultiple.toLocaleString('ko-KR', { maximumFractionDigits: 1 })}배`}
            </strong>
            <small>{reaction.turnoverMultiple === null ? '기준선 부족' : '같은 시각 과거 기준'}</small>
          </article>
          {/* `17/21`로 적으면 바로 왼쪽 상승 종목과 분자·분모가 같아 보여 구분이 안 된다.
              여기서는 믿을 만한 값인지만 말하고, 분모·분자는 CoverageIndicator가 따로 보여준다. */}
          <article>
            <span>데이터 반영</span>
            <strong>{coverageStatusLabel(detail.coverage.status)}</strong>
            {/* 세 칸이 같은 너비라 이 설명이 길면 혼자 세 줄로 접혀 칸이 어그러진다.
                핵심 종목 관측 비율만 한 줄로 적는다. */}
            <small>
              핵심 {detail.coverage.core.observedCount.toLocaleString('ko-KR')}/
              {detail.coverage.core.totalCount.toLocaleString('ko-KR')}종목
            </small>
          </article>
        </div>
        {saveFailed ? (
          <p className="section-note" role="alert">
            저장 상태를 동기화하지 못했습니다. 다시 시도해 주세요.
          </p>
        ) : null}
      </section>

      {detail.coverage.status === 'SUFFICIENT' ? null : (
        <div className="coverage-band">
          <CoverageIndicator coverage={detail.coverage} />
        </div>
      )}

      <div className="detail-card">
        {/* 시안의 폴더형 2탭. 바깥 축은 과거(데자뷰)와 오늘이고,
            근거의 실시간·장 마감 후 전환은 오늘 현황 안쪽 탭으로 그대로 남는다. */}
        <div className="detail-tabs" role="tablist" aria-label="테마 상세 보기 전환">
          <button
            type="button"
            role="tab"
            id="detail-tab-dejavu"
            aria-selected={detailTab === 'dejavu'}
            aria-controls="detail-panel-dejavu"
            onClick={() => selectDetailTab('dejavu')}
          >
            DAY-JA-VIEW
          </button>
          <button
            type="button"
            role="tab"
            id="detail-tab-today"
            aria-selected={detailTab === 'today'}
            aria-controls="detail-panel-today"
            onClick={() => selectDetailTab('today')}
          >
            오늘 현황
          </button>
        </div>

        {detailTab === 'dejavu' ? (
          <div
            className="detail-panel"
            id="detail-panel-dejavu"
            role="tabpanel"
            aria-labelledby="detail-tab-dejavu"
          >
            {/* 과거 영역 전체가 온톨로지 재검증 게이트에 종속된다.
                게이트가 닫혀 있으면 잠긴 가짜 화면 대신 안내만 남긴다 (adaptation plan §4.3·§5.2). */}
            {historicalAvailable ? (
              <>
                <DejavuSummarySection themeId={themeId} eventId={detail.eventId} />
                <CatalystTop3Section
                  themeId={themeId}
                  eventId={detail.eventId}
                  themeName={detail.classification.displayName}
                />
              </>
            ) : (
              <EmptyState
                title="과거 사례는 아직 준비 중이에요"
                description="검증이 끝난 뒤에 오늘과 비슷했던 과거 기록을 붙여 보여드립니다."
              />
            )}
          </div>
        ) : (
          <div
            className="detail-panel"
            id="detail-panel-today"
            role="tabpanel"
            aria-labelledby="detail-tab-today"
          >
            <ReasonSection eventId={detail.eventId} summary={detail.evidenceSummary} />

            <section aria-labelledby="leaders-title">
              <div className="section-heading">
                <h2 id="leaders-title">오늘의 주도 종목</h2>
              </div>
              {detail.leaders.length ? (
                <LeaderList leaders={detail.leaders} />
              ) : (
                <EmptyState
                  title="확인된 주도 종목이 없습니다"
                  description="데이터가 확보되면 상위 3개 종목을 표시합니다."
                />
              )}
            </section>
          </div>
        )}
      </div>


      {calculationOpen ? (
        <div className="sheet-backdrop" role="presentation" onMouseDown={closeCalculation}>
          <section
            className="bottom-sheet"
            role="dialog"
            aria-modal="true"
            aria-labelledby="calculation-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="section-heading">
              <h2 id="calculation-title">계산 기준</h2>
              <button
                ref={calculationCloseRef}
                className="icon-button"
                type="button"
                onClick={closeCalculation}
                aria-label="계산 기준 닫기"
              >
                <IconXmarkLine size={20} aria-hidden="true" />
              </button>
            </div>
            {/* screen_spec 3.4가 요구하는 여섯 가지를 모두 적는다.
                값이 답하는 질문 · 계산 대상과 기간 · 가중 방식 · 기준 시각 · 제외·결측 규칙 · 예측 아님. */}
            <dl className="calculation-basis">
              <dt>무엇을 답하는 값인가</dt>
              <dd>이 테마에 속한 종목들이 오늘 전체적으로 얼마나 움직였는지를 봅니다.</dd>
              <dt>계산 대상과 기간</dt>
              <dd>
                핵심 {detail.coverage.core.totalCount.toLocaleString('ko-KR')}종목의 전일 종가 대비
                오늘 가격 변화입니다.
              </dd>
              <dt>가중 방식</dt>
              <dd>
                종목별 비중은 전일 기준 상한형 유동시가총액 가중입니다. 검증된 유동주식비율이 없으면
                다른 방식으로 대신 계산하지 않고 계산할 수 없음으로 표시합니다.
              </dd>
              <dt>데이터 기준 시각</dt>
              <dd>
                {calculationContext
                  ? `${formatDate(calculationContext.asOf)} ${formatTime(calculationContext.asOf)} 기준`
                  : '기준 시각을 확인할 수 없습니다.'}
              </dd>
              <dt>제외·결측 규칙</dt>
              <dd>
                값이 없는 종목은 0으로 바꾸지 않고 계산에서 빼며, 몇 종목이 반영됐는지를 Coverage
                상태로 함께 표시합니다.
              </dd>
              {/* 칸 안에서 뺀 `중앙`과 기간별 분모를 여기서 밝힌다 (screen_spec 8.8). */}
              <dt>과거 사례 수치</dt>
              <dd>
                `과거엔 어땠을까요`의 기간별 값은 평균이 아니라 <b>중앙값</b>입니다. 기간마다 결과가
                확인된 사례 수가 다를 수 있어 분모를 따로 셉니다. 상승 빈도이며 확률·성공률이 아닙니다.
              </dd>
              <dt>고지</dt>
              <dd>관측된 값이며 미래 수익률 예측이 아닙니다.</dd>
            </dl>
          </section>
        </div>
      ) : null}
    </div>
  );
}
