import type { MarketContext } from '../domain/contracts';
import { dataStatusLabel, formatDate, formatTime } from '../domain/formatting';

export function DataStatusBar({ context }: { context: MarketContext }) {
  const delayed = context.dataStatus === 'DELAYED' || context.dataStatus === 'DEGRADED';
  return (
    <section
      className={`data-status data-status--${context.dataStatus.toLowerCase()}`}
      aria-label="시장 데이터 상태"
    >
      <span className="live-dot" aria-hidden="true" />
      <strong>{dataStatusLabel(context.dataStatus)}</strong>
      <p>
        {formatDate(`${context.marketDate}T00:00:00+09:00`)} · 기준 {formatTime(context.asOf)}
        {delayed && context.lastHealthyAt ? ` · 마지막 정상 ${formatTime(context.lastHealthyAt)}` : ''}
      </p>
      {context.dataStatus === 'DEGRADED' ? <p>영향 범위와 Coverage를 함께 확인해 주세요.</p> : null}
    </section>
  );
}
