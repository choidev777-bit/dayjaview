import { useRef, useState, type KeyboardEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useRepository } from '../app/RepositoryContext';
import type { SavedItem, SavedType } from '../domain/contracts';
import {
  dataStatusLabel,
  eventStatusLabel,
  formatDate,
  formatReturn,
  formatTime,
  returnTone,
} from '../domain/formatting';
import { EmptyState, ErrorState, LoadingState } from '../shared/StatePanel';
import { useRepositoryResource } from '../shared/useRepositoryResource';

const filters = [
  ['ALL', '전체'],
  ['THEME', '테마'],
  ['STOCK', '종목'],
  ['EVENT', '이벤트'],
] as const;

type Filter = (typeof filters)[number][0];

function isFilter(value: string | null): value is Filter {
  return filters.some(([candidate]) => candidate === value);
}

function savedTypeLabel(type: SavedType): string {
  return { THEME: '테마', STOCK: '종목', EVENT: '이벤트' }[type];
}

function SavedRow({ item, onRemove }: { item: SavedItem; onRemove: (item: SavedItem) => Promise<void> }) {
  const [removing, setRemoving] = useState(false);
  const [failed, setFailed] = useState(false);
  const link =
    item.savedType === 'THEME' && item.currentState
      ? `/themes/${encodeURIComponent(item.targetId)}/events/${encodeURIComponent(item.currentState.eventId)}`
      : null;

  async function remove() {
    setRemoving(true);
    setFailed(false);
    try {
      await onRemove(item);
    } catch {
      setFailed(true);
      setRemoving(false);
    }
  }

  return (
    <article className="saved-row">
      <div className="saved-row__topline">
        <span className="badge">{savedTypeLabel(item.savedType)}</span>
        <span>저장 {formatDate(item.savedAt)}</span>
      </div>
      <h3>{item.displayName}</h3>
      {item.availability === 'UNAVAILABLE' ? (
        <div className="permission-inline">
          <strong>현재 확인할 수 없음</strong>
          <span>접근 권한이 없거나 제공 게이트가 닫혀 있습니다.</span>
        </div>
      ) : item.currentState ? (
        <div className="saved-row__state">
          <strong>{eventStatusLabel(item.currentState.eventState, 'PENDING')}</strong>
          <span>{dataStatusLabel(item.currentState.dataStatus)}</span>
          <span className={returnTone(item.currentState.weightedReturn)}>
            {formatReturn(item.currentState.weightedReturn)}
          </span>
          <span>기준 {formatTime(item.currentState.asOf)}</span>
        </div>
      ) : null}
      <div className="saved-row__actions">
        {link ? <Link to={link}>상세 보기</Link> : null}
        <button type="button" className="text-button" onClick={remove} disabled={removing}>
          {removing ? '저장 해제 중' : '저장 해제'}
        </button>
      </div>
      {failed ? <p role="alert">저장을 해제하지 못했습니다. 다시 시도해 주세요.</p> : null}
    </article>
  );
}

export function SavedPage({ onLogout }: { onLogout: () => Promise<void> }) {
  const repository = useRepository();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedFilter = searchParams.get('type');
  const filter = isFilter(requestedFilter) ? requestedFilter : 'ALL';
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const resource = useRepositoryResource(
    repository,
    'saved',
    () => repository.getSaved(filter),
    [repository, filter],
  );

  function selectFilter(nextFilter: Filter, focus = false) {
    setSearchParams(nextFilter === 'ALL' ? {} : { type: nextFilter });
    if (focus) {
      const index = filters.findIndex(([candidate]) => candidate === nextFilter);
      window.setTimeout(() => tabRefs.current[index]?.focus(), 0);
    }
  }

  function handleTabKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    let next = index;
    if (event.key === 'ArrowRight') next = (index + 1) % filters.length;
    if (event.key === 'ArrowLeft') next = (index - 1 + filters.length) % filters.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = filters.length - 1;
    selectFilter(filters[next][0], true);
  }

  async function removeSaved(item: SavedItem) {
    await repository.removeSaved(item);
    resource.retry();
  }

  return (
    <div className="page page--saved">
      {/* 로그아웃은 계정 동작이라 목록 맨 아래 본문 흐름에 두면 저장 항목처럼 읽힌다.
          화면 제목 옆으로 올려 계정 영역이라는 걸 분명히 한다. */}
      <header className="page-intro page-intro--with-action">
        <h1>저장</h1>
        <button className="text-button" type="button" onClick={onLogout}>
          로그아웃
        </button>
      </header>

      <div className="library-heading">
        <h2>저장한 분석</h2>
        <span>
          {resource.status === 'success' ? `${resource.data.data.items.length.toLocaleString('ko-KR')}개` : '—'}
        </span>
      </div>

      <div className="filter-row" role="tablist" aria-label="관심 유형 필터">
        {filters.map(([value, label], index) => (
          <button
            key={value}
            ref={(element) => {
              tabRefs.current[index] = element;
            }}
            type="button"
            role="tab"
            aria-selected={filter === value}
            tabIndex={filter === value ? 0 : -1}
            onClick={() => selectFilter(value)}
            onKeyDown={(event) => handleTabKey(event, index)}
          >
            {label}
          </button>
        ))}
      </div>

      <div role="tabpanel" aria-label={`${filters.find(([value]) => value === filter)?.[1]} 저장 목록`}>
        {resource.status === 'loading' ? <LoadingState label="저장 목록을 불러오는 중입니다" /> : null}
        {resource.status === 'error' ? <ErrorState error={resource.error} retry={resource.retry} /> : null}
        {resource.status === 'success' && !resource.data.data.items.length ? (
          <EmptyState
            title="저장한 항목이 없습니다"
            description="홈과 실시간에서 관심 있는 테마를 찾아 저장해 보세요."
            action={<Link className="button button--secondary" to="/today">홈으로 이동</Link>}
          />
        ) : null}
        {resource.status === 'success' && resource.data.data.items.length ? (
          <div className="saved-list">
            {resource.data.data.items.map((item) => (
              <SavedRow key={`${item.savedType}:${item.targetId}`} item={item} onRemove={removeSaved} />
            ))}
          </div>
        ) : null}
      </div>

      <div className="library-divider">
        <span>최근 본 테마</span>
      </div>

      <div className="library-heading">
        <h2>내가 눌러본 테마</h2>
      </div>
      {/* 시안은 localStorage에 남기지만 제품은 계정 저장이 원칙이다 (adaptation plan §8.6).
          서버 저장소가 생기기 전까지 기기에 남기지 않고 준비 중 상태로 둔다. */}
      <div className="library-empty">
        <strong>최근 본 테마를 아직 계정에 저장하지 않습니다</strong>
        <p>기기에만 남기지 않고 계정에 저장하도록 준비 중이에요.</p>
      </div>

      <p className="section-note notice">저장 여부는 시장 순위·계산·공용 결과에 영향을 주지 않습니다.</p>
    </div>
  );
}
