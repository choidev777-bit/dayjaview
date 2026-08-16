import { useMemo, useState } from 'react';
import { useRepository } from '../app/RepositoryContext';
import type { DayMoversSection, DayMoversTheme } from '../domain/contracts';
import { formatLongDate } from '../domain/formatting';
import { EmptyState, ErrorPage, LoadingState } from '../shared/StatePanel';
import { useRepositoryResource } from '../shared/useRepositoryResource';

/** 서울 기준 오늘. 특징테마는 KRX 거래일 단위라 브라우저 표준시로 밀리면 안 된다. */
function seoulToday(): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const value = (type: string) => parts.find((part) => part.type === type)?.value ?? '';
  return `${value('year')}-${value('month')}-${value('day')}`;
}

function isDown(theme: DayMoversTheme): boolean {
  return theme.changeRate !== null && theme.changeRate.startsWith('-');
}

function rateLabel(rate: string | null): string {
  if (rate === null) return '—';
  return rate.startsWith('-') ? `${rate}%` : `+${rate}%`;
}

function rateTone(rate: string | null): string {
  if (rate === null) return 'market-flat';
  return rate.startsWith('-') ? 'market-down' : 'market-up';
}

function ThemeBlock({ theme }: { theme: DayMoversTheme }) {
  return (
    <li className="movers-theme">
      <div className="movers-theme__head">
        <strong>{theme.themeName}</strong>
        <span className={rateTone(theme.changeRate)}>{rateLabel(theme.changeRate)}</span>
      </div>
      <ul className="movers-stocks">
        {theme.stocks.map((stock) => (
          <li key={`${theme.themeName}:${stock.stockCode ?? stock.stockName}`}>
            <span className="movers-stocks__name">{stock.stockName}</span>
            <span className={rateTone(stock.changeRate)}>{rateLabel(stock.changeRate)}</span>
            <span className="movers-stocks__price">
              {stock.closePrice === null ? '' : `${stock.closePrice.toLocaleString('ko-KR')}원`}
            </span>
          </li>
        ))}
      </ul>
    </li>
  );
}

function SectionBlock({ section }: { section: DayMoversSection }) {
  return (
    <article className="movers-section">
      <h2>{section.headline || section.sectionName}</h2>
      {section.headline ? <small>{section.sectionName}</small> : null}
      {section.details.map((paragraph) => (
        <p key={paragraph}>{paragraph}</p>
      ))}
      {section.themes.length ? (
        <ul className="movers-themes">
          {section.themes.map((theme) => (
            <ThemeBlock key={theme.themeName} theme={theme} />
          ))}
        </ul>
      ) : null}
    </article>
  );
}

export function DayMoversPage() {
  const repository = useRepository();
  const [requestedDate, setRequestedDate] = useState(seoulToday);
  const resource = useRepositoryResource(
    repository,
    'dayMovers',
    () => repository.getDayMovers(requestedDate),
    [repository, requestedDate],
  );

  const grouped = useMemo(() => {
    if (resource.status !== 'success') return { up: [], down: [] };
    const up: DayMoversSection[] = [];
    const down: DayMoversSection[] = [];
    for (const section of resource.data.data.sections) {
      // 테마 등락률 부호로만 가른다. 문장에서 방향을 추측하지 않는다.
      (section.themes.some(isDown) && section.themes.every(isDown) ? down : up).push(section);
    }
    return { up, down };
  }, [resource]);

  if (resource.status === 'loading') return <LoadingState label="특징테마를 불러오는 중입니다" />;
  if (resource.status === 'error') return <ErrorPage error={resource.error} retry={resource.retry} />;

  const { data } = resource.data;

  return (
    <div className="page page--movers">
      <header className="page-intro">
        <small>특징테마</small>
        <h1>이날 뭐가 움직였나요?</h1>
        <label className="movers-date">
          날짜
          <input
            type="date"
            value={requestedDate}
            max={seoulToday()}
            onChange={(event) => setRequestedDate(event.target.value)}
          />
        </label>
      </header>

      {data.status === 'NOT_PUBLISHED' && data.publishedDate ? (
        <p className="movers-notice">
          아직 {formatLongDate(`${data.requestedDate}T00:00:00+09:00`)} 특징테마가 발행되지 않았어요.
          직전 거래일 {formatLongDate(`${data.publishedDate}T00:00:00+09:00`)} 결과를 보여드립니다.
        </p>
      ) : null}

      {data.sections.length ? (
        <>
          <section className="movers-group">
            <h2 className="movers-group__title">오른 테마</h2>
            {grouped.up.map((section) => (
              <SectionBlock key={section.sectionName} section={section} />
            ))}
          </section>
          {grouped.down.length ? (
            <section className="movers-group">
              <h2 className="movers-group__title">빠진 테마</h2>
              {grouped.down.map((section) => (
                <SectionBlock key={section.sectionName} section={section} />
              ))}
            </section>
          ) : null}
        </>
      ) : (
        <EmptyState
          title="이 날짜의 특징테마가 없습니다"
          description="거래일이 아니거나 아직 수집되지 않은 날짜입니다."
        />
      )}
    </div>
  );
}
