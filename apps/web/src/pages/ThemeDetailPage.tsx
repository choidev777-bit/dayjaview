import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { useRepository } from '../app/RepositoryContext';
import {
  evidenceStatusLabel,
  eventStatusLabel,
  formatDate,
  formatDateTime,
  formatReturn,
  formatTime,
} from '../domain/formatting';
import { CoverageIndicator } from '../shared/CoverageIndicator';
import { EvidenceSection } from '../shared/EvidenceSection';
import { EmptyState, ErrorState, LoadingState } from '../shared/StatePanel';
import { useRepositoryResource } from '../shared/useRepositoryResource';

function SavedThemeControl({ themeId, displayName }: { themeId: string; displayName: string }) {
  const repository = useRepository();
  const [mutating, setMutating] = useState(false);
  const [mutationFailed, setMutationFailed] = useState(false);
  const resource = useRepositoryResource(
    repository,
    'saved',
    () => repository.getSaved('THEME'),
    [repository],
  );

  if (resource.status === 'loading') {
    return <p className="save-control" role="status">저장 상태를 확인하는 중입니다</p>;
  }
  if (resource.status === 'error') {
    return <p className="save-control" role="alert">저장 상태를 확인하지 못했습니다.</p>;
  }

  const saved = resource.data.data.items.some(
    (item) => item.savedType === 'THEME' && item.targetId === themeId,
  );

  async function toggleSaved() {
    setMutating(true);
    setMutationFailed(false);
    try {
      if (saved) {
        await repository.removeSaved({ savedType: 'THEME', targetId: themeId });
      } else {
        await repository.saveSaved({ savedType: 'THEME', targetId: themeId, displayName });
      }
      resource.retry();
    } catch {
      setMutationFailed(true);
    } finally {
      setMutating(false);
    }
  }

  return (
    <div className="save-control">
      <button
        className="button button--secondary"
        type="button"
        onClick={toggleSaved}
        disabled={mutating}
        aria-pressed={saved}
      >
        {mutating ? '관심 동기화 중' : saved ? '관심에서 저장 해제' : '관심에 저장'}
      </button>
      {mutationFailed ? <p role="alert">저장 상태를 동기화하지 못했습니다. 다시 시도해 주세요.</p> : null}
    </div>
  );
}

export function ThemeDetailPage() {
  const repository = useRepository();
  const navigate = useNavigate();
  const location = useLocation();
  const { themeId = '', eventId = '' } = useParams();
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
  const evidenceSummary =
    detail.evidenceSummary.sourceCount > 0 && detail.evidenceSummary.summary
      ? detail.evidenceSummary.summary
      : evidenceStatusLabel(detail.evidenceSummary.evidenceStatus);

  return (
    <div className="page page--detail">
      <button
        className="back-button"
        type="button"
        onClick={() => (from ? navigate(from) : navigate(-1))}
        aria-label="이전 화면으로 돌아가기"
      >
        <span aria-hidden="true">←</span> 이전
      </button>
      <header className="detail-hero">
        <div>
          <p className="eyebrow">{formatDate(`${detail.marketDate}T00:00:00+09:00`)} 현재 Event</p>
          <h1>{detail.classification.displayName}</h1>
          <span className="status-chip">
            {eventStatusLabel(detail.lifecycleStatus, detail.reconciliationStatus)}
          </span>
          <p className="classification-meta">
            분류 revision {detail.classification.classificationVersion.toLocaleString('ko-KR')} · 변경{' '}
            {formatDateTime(detail.classification.changedAt)}
          </p>
        </div>
        <div className="detail-hero__metric">
          <span>테마 수익률</span>
          <strong className={reaction.weightedReturn === null ? '' : 'market-up'}>
            {formatReturn(reaction.weightedReturn)}
          </strong>
          <span>
            관련주 {reaction.advancingCount ?? '—'} / {reaction.validCount ?? '—'}종목 상승
          </span>
        </div>
      </header>

      <SavedThemeControl
        themeId={detail.classification.themeId}
        displayName={detail.classification.displayName}
      />

      <CoverageIndicator coverage={detail.coverage} />

      <section className="detail-section" aria-labelledby="reason-title">
        <p className="eyebrow">현재 → 근거</p>
        <h2 id="reason-title">오늘 부각된 이유</h2>
        <p className="reason-summary">{evidenceSummary}</p>
        <p className="section-note">
          {evidenceStatusLabel(detail.evidenceSummary.evidenceStatus)}
          {detail.evidenceSummary.latestPublishedAt
            ? ` · 최근 확인 ${formatTime(detail.evidenceSummary.latestPublishedAt)}`
            : ''}
        </p>
      </section>

      <section className="detail-grid" aria-label="관심과 현재 움직임">
        <article className="metric-card">
          <span>관심 공백</span>
          <strong>
            {reaction.attentionGapTradingDays === null
              ? '데이터 부족'
              : `${reaction.attentionGapTradingDays.toLocaleString('ko-KR')}거래일`}
          </strong>
          <p>가격 저점이나 저평가를 뜻하지 않습니다.</p>
        </article>
        <article className="metric-card">
          <span>오늘 거래 관심</span>
          <strong>
            {reaction.turnoverMultiple === null
              ? '—'
              : `평소의 ${reaction.turnoverMultiple.toLocaleString('ko-KR', { maximumFractionDigits: 1 })}배`}
          </strong>
          <p>같은 시각의 과거 기준과 비교한 값입니다.</p>
        </article>
      </section>

      <section className="detail-section" aria-labelledby="leaders-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">현재 움직임</p>
            <h2 id="leaders-title">주도 종목</h2>
          </div>
        </div>
        {detail.leaders.length ? (
          <ol className="leader-list">
            {detail.leaders.map((leader, index) => (
              <li key={leader.stockId}>
                <div>
                  <strong>{leader.name}</strong>
                  {index === 0 ? <span className="badge">주도</span> : null}
                </div>
                <strong className="market-up">{formatReturn(leader.return)}</strong>
              </li>
            ))}
          </ol>
        ) : (
          <EmptyState title="확인된 주도 종목이 없습니다" description="데이터가 확보되면 최대 3개 종목을 표시합니다." />
        )}
      </section>

      <EvidenceSection eventId={detail.eventId} />

      {detail.historicalAccess.status !== 'AVAILABLE' ? (
        <aside className="gate-notice" aria-label="과거 유사사례 제공 상태">
          <span className="eyebrow">검증 대기</span>
          <strong>과거 관측은 검증 완료 후 제공합니다</strong>
          <p>유사사례 링크와 과거 결과 데이터는 현재 화면에 노출하지 않습니다.</p>
        </aside>
      ) : null}

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
                ×
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
