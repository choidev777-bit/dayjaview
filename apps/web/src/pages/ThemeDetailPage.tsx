import { useCallback, useEffect, useRef, useState } from 'react';
import {
  IconArrowLeftLine,
  IconChevronRightSmallLine,
  IconStarFill,
  IconStarLine,
  IconXmarkLine,
} from '@karrotmarket/react-monochrome-icon';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useRepository } from '../app/RepositoryContext';
import type { EvidenceResponse, ResponseMeta, ThemeDetailResponse } from '../domain/contracts';
import {
  evidenceStatusLabel,
  evidenceStatusNote,
  eventStatusLabel,
  formatReturn,
  formatTime,
  matchBasisLabel,
  returnTone,
} from '../domain/formatting';
import { CoverageIndicator } from '../shared/CoverageIndicator';
import { EmptyState, ErrorState, LoadingState } from '../shared/StatePanel';
import { useRepositoryResource } from '../shared/useRepositoryResource';

type ThemeDetail = ThemeDetailResponse['data'];
type EvidenceSummary = ThemeDetail['evidenceSummary'];

const VISIBLE_EVIDENCE = 3;
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
  onFailureChange,
}: {
  themeId: string;
  displayName: string;
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
        await repository.saveSaved({ savedType: 'THEME', targetId: themeId, displayName });
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

function EvidenceList({ items }: { items: EvidenceResponse['data']['items'] }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? items : items.slice(0, VISIBLE_EVIDENCE);

  return (
    <>
      <ul className="evidence-list">
        {visible.map((item) => (
          <li key={item.newsId}>
            <span>{formatTime(item.publishedAt)}</span>
            <a href={item.originalUrl} target="_blank" rel="noreferrer">
              <strong>{item.title}</strong>
              <span>
                {item.sourceName} · {formatTime(item.publishedAt)} · 새 창에서 원문 보기
              </span>
            </a>
            <p>{item.summary}</p>
            <p className="evidence-list__basis">
              <span className="badge">자체 요약</span>
              {item.matchBasis.map(matchBasisLabel).join(' · ')}
            </p>
          </li>
        ))}
      </ul>
      {items.length > VISIBLE_EVIDENCE ? (
        <button
          type="button"
          className="expand-button"
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
        >
          <span>{expanded ? '근거 접기' : `근거 ${items.length - VISIBLE_EVIDENCE}건 더 보기`}</span>
          <i aria-hidden="true" data-open={expanded ? 'true' : 'false'} />
        </button>
      ) : null}
    </>
  );
}

function ReasonSection({ eventId, summary }: { eventId: string; summary: EvidenceSummary }) {
  const repository = useRepository();
  const [requestedTab, setRequestedTab] = useState<'LIVE' | 'AFTER_CLOSE' | null>(null);
  const resource = useRepositoryResource<EvidenceResponse>(
    repository,
    'evidence',
    () => repository.getEvidence(eventId),
    [repository, eventId],
  );

  const evidenceStatus =
    resource.status === 'success' ? resource.data.data.evidenceStatus : summary.evidenceStatus;
  const confirmed =
    summary.evidenceStatus === 'AFTER_CLOSE_CONFIRMED' || evidenceStatus === 'AFTER_CLOSE_CONFIRMED';
  const tab = requestedTab ?? (confirmed ? 'AFTER_CLOSE' : 'LIVE');

  return (
    <section aria-labelledby="reason-title">
      <div className="section-heading">
        <h2 id="reason-title">오늘 왜 올랐을까요?</h2>
      </div>

      <div className="reason-tabs" role="tablist" aria-label="상승 이유 분석 시점">
        <button type="button" role="tab" aria-selected={tab === 'LIVE'} onClick={() => setRequestedTab('LIVE')}>
          실시간 분석
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'AFTER_CLOSE'}
          onClick={() => setRequestedTab('AFTER_CLOSE')}
        >
          장 마감 후 분석
        </button>
      </div>

      <p className="section-note">
        {evidenceStatusLabel(summary.evidenceStatus)}
        {summary.sourceCount > 0 ? ` · 출처 ${summary.sourceCount.toLocaleString('ko-KR')}곳` : ''}
        {summary.latestPublishedAt ? ` · 최근 확인 ${formatTime(summary.latestPublishedAt)}` : ''}
      </p>

      {resource.status === 'loading' ? <LoadingState label="기사 근거를 확인하는 중입니다" /> : null}
      {resource.status === 'error' ? <ErrorState error={resource.error} retry={resource.retry} /> : null}

      {resource.status === 'success'
        ? (() => {
            const { items } = resource.data.data;
            const delayed = newsCollectionDelayed(resource.data.meta);
            const lastHealthyAt = resource.data.meta.marketContext?.lastHealthyAt ?? null;

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

            return (
              <>
                <div className="source-status">
                  <span className="live-dot" aria-hidden="true" />
                  {evidenceStatusLabel(evidenceStatus)}
                </div>

                {delayed ? (
                  <p className="confirmation-note" role="status">
                    뉴스 수집이 지연되고 있습니다. 확인된 신규 소재 없음과 다른 상태입니다.
                    {lastHealthyAt ? ` 마지막 정상 수집 ${formatTime(lastHealthyAt)}` : ''}
                  </p>
                ) : null}

                {tab === 'AFTER_CLOSE' ? (
                  <p className="confirmation-note">
                    장중에 표시했던 근거는 이력으로 남기고 확정 사유를 기본으로 표시합니다.
                  </p>
                ) : null}

                {summary.summary ? <p className="reason-summary">{summary.summary}</p> : null}

                {items.length ? (
                  <>
                    <p className="section-note">{evidenceStatusNote(evidenceStatus)}</p>
                    <EvidenceList items={items} />
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
    </section>
  );
}

export function ThemeDetailPage() {
  const repository = useRepository();
  const navigate = useNavigate();
  const location = useLocation();
  const { themeId = '', eventId = '' } = useParams();
  const [saveFailed, setSaveFailed] = useState(false);
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

  const closeCalculation = useCallback(() => {
    setCalculationOpen(false);
  }, []);

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
  if (resource.status === 'error') return <ErrorState error={resource.error} retry={resource.retry} />;

  const detail = resource.data.data;
  const reaction = detail.currentReaction;
  const from = (location.state as { from?: string } | null)?.from;
  const advancingRatio =
    reaction.advancingCount !== null && reaction.validCount
      ? Math.round((reaction.advancingCount / reaction.validCount) * 100)
      : null;
  const historicalAvailable = detail.historicalAccess.status === 'AVAILABLE';

  return (
    <div className="page page--detail">
      <header className="detail-app-bar">
        <button
          type="button"
          onClick={() => (from ? navigate(from) : navigate(-1))}
          aria-label="이전 화면으로 돌아가기"
        >
          <IconArrowLeftLine size={24} aria-hidden="true" />
        </button>
        <span>{detail.classification.displayName}</span>
        <SaveThemeButton
          themeId={detail.classification.themeId}
          displayName={detail.classification.displayName}
          onFailureChange={setSaveFailed}
        />
      </header>

      <section className="theme-summary">
        <div className="theme-summary__title">
          <span className="status-chip">
            {eventStatusLabel(detail.lifecycleStatus, detail.reconciliationStatus)}
          </span>
          <h1>{detail.classification.displayName}</h1>
        </div>
        <div className="theme-summary__return">
          <strong>{formatReturn(reaction.weightedReturn)}</strong>
          <p className="theme-summary__pill">대형주 반영 테마 수익률</p>
        </div>
        {saveFailed ? (
          <p className="section-note" role="alert">
            저장 상태를 동기화하지 못했습니다. 다시 시도해 주세요.
          </p>
        ) : null}
      </section>

      <div className="detail-card">
        <section aria-labelledby="reaction-title">
          <h2 id="reaction-title">현재 테마 상태</h2>
          <div className="metric-grid">
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
            <article>
              <span>반영 종목</span>
              <strong>
                {detail.coverage.core.observedCount.toLocaleString('ko-KR')}/
                {detail.coverage.core.totalCount.toLocaleString('ko-KR')}
              </strong>
              <small>Coverage</small>
            </article>
          </div>

          {reaction.attentionGapTradingDays !== null ? (
            <div className="interest-gap">
              <strong>
                {reaction.attentionGapTradingDays.toLocaleString('ko-KR')}거래일 만에 다시 주목받고 있어요
              </strong>
              <span>가격 저점이나 저평가를 뜻하지 않습니다.</span>
            </div>
          ) : null}

          {detail.coverage.status === 'SUFFICIENT' ? null : (
            <div className="coverage-band">
              <CoverageIndicator coverage={detail.coverage} />
            </div>
          )}
        </section>

        <ReasonSection eventId={detail.eventId} summary={detail.evidenceSummary} />

        <section aria-labelledby="leaders-title">
          <div className="section-heading">
            <h2 id="leaders-title">오늘의 주도 종목</h2>
          </div>
          {detail.leaders.length ? (
            <ol className="leader-list">
              {detail.leaders.map((leader, index) => (
                <li key={leader.stockId}>
                  <div>
                    <strong>{leader.name}</strong>
                    {index === 0 ? <span className="badge">주도</span> : null}
                  </div>
                  <strong className={returnTone(leader.return)}>{formatReturn(leader.return)}</strong>
                </li>
              ))}
            </ol>
          ) : (
            <EmptyState
              title="확인된 주도 종목이 없습니다"
              description="데이터가 확보되면 최대 3개 종목을 표시합니다."
            />
          )}
        </section>

        {/* 과거 상승 소재 Top 3·이벤트 스터디는 온톨로지 재검증(E-17~E-19) 전까지 계약이 없어 만들지 않는다.
            게이트가 열린 뒤에만 유사사례 진입점을 노출한다 (adaptation plan §4.3). */}
        {historicalAvailable ? (
          <section aria-labelledby="cases-title">
            <div className="section-heading">
              <h2 id="cases-title">DAY-JA-VIEW 케이스</h2>
              <Link
                className="text-button"
                to={`/themes/${encodeURIComponent(themeId)}/events/${encodeURIComponent(detail.eventId)}/similar`}
              >
                전체 보기
                <IconChevronRightSmallLine size={18} aria-hidden="true" />
              </Link>
            </div>
            <p className="section-note">오늘과 비슷했던 과거 사건을 검증된 범위에서만 보여드립니다.</p>
          </section>
        ) : null}
      </div>

      <p className="notice">
        장중 정보는 이후 정정될 수 있습니다. 과거에 관측된 데이터와 확인된 뉴스 근거를 함께 보여줍니다.
      </p>

      <button
        ref={calculationTriggerRef}
        className="text-button"
        type="button"
        onClick={() => setCalculationOpen(true)}
      >
        계산 기준 보기
      </button>

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
            <p>테마 수익률은 전일 기준 상한형 유동시가총액 가중 결과입니다.</p>
            <p>결측값은 0으로 바꾸지 않으며 Coverage 상태를 함께 표시합니다.</p>
          </section>
        </div>
      ) : null}
    </div>
  );
}
