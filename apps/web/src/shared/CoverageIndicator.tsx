import type { Coverage } from '../domain/contracts';

export function CoverageIndicator({ coverage }: { coverage: Coverage }) {
  if (coverage.status === 'SUFFICIENT') {
    return <span className="coverage coverage--sufficient">Coverage 충분</span>;
  }
  const label = coverage.status === 'PARTIAL' ? '일부 데이터 반영' : '데이터 갱신 중';
  return (
    <span className={`coverage coverage--${coverage.status.toLowerCase()}`}>
      <strong>{label}</strong>
      <span>
        현재 {coverage.core.observedCount.toLocaleString('ko-KR')} / {coverage.core.totalCount.toLocaleString('ko-KR')}
        종목 반영
      </span>
    </span>
  );
}
