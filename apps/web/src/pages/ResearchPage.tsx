import { useCallback, useState } from 'react';
import { useRepository } from '../app/RepositoryContext';
import type {
  ResearchAnswer,
  ResearchAnswerResponse,
  ResearchEvidence,
  ResearchFailure,
  ResearchRow,
} from '../domain/contracts';
import { asRepositoryError } from '../domain/repositoryErrors';

/** 계약서 4.0절 17종 중 지금 물어볼 수 있는 형태의 예시. */
const EXAMPLES = [
  '어제 뭐가 올랐어?',
  '이번 주 시장 어땠어?',
  '한화에어로스페이스가 직접 한 일만 알려줘',
  '2차전지 테마에 어떤 종목이 있어?',
] as const;

const MAX_LENGTH = 300;

function EvidenceList({ evidence }: { evidence: ResearchEvidence[] }) {
  return (
    <ul className="research-evidence">
      {evidence.map((item, index) => (
        <li key={`${item.labelKo}:${index}`}>
          <small>{item.labelKo}</small>
          <p>{item.excerpt}</p>
        </li>
      ))}
    </ul>
  );
}

/** 서버가 주는 키 이름을 그대로 띄우면 `sectionName`·`direction`이 화면에 나온다. */
const VALUE_LABELS: Record<string, string> = {
  sectionName: '섹션',
  direction: '방향',
  themes: '테마',
  themeName: '테마',
  changeRate: '등락률',
  stocks: '종목',
  stockName: '종목',
  stockCode: '종목코드',
  closePrice: '종가',
  companyName: '회사',
  eventCount: '사건 수',
  catalystType: '소재 유형',
  marketDate: '날짜',
  tradingDate: '날짜',
};

const DIRECTION_LABELS: Record<string, string> = { UP: '상승', DOWN: '하락', FLAT: '보합' };

function labelOf(key: string): string {
  return VALUE_LABELS[key] ?? key;
}

/** 테마 하나를 `테마명 +10.86% · 종목 2개`처럼 한 줄로 읽히게 만든다. */
function themeLine(theme: Record<string, unknown>): string {
  const name = String(theme.themeName ?? theme.themeId ?? '');
  const rate = theme.changeRate ? ` ${theme.changeRate}` : '';
  const stocks = Array.isArray(theme.stocks) ? theme.stocks : [];
  const names = stocks
    .map((stock) => {
      const row = stock as Record<string, unknown>;
      return `${row.stockName ?? ''}${row.changeRate ? ` ${row.changeRate}` : ''}`;
    })
    .filter(Boolean);
  return names.length ? `${name}${rate} — ${names.join(', ')}` : `${name}${rate}`;
}

function renderValue(key: string, value: unknown): string {
  if (key === 'direction') return DIRECTION_LABELS[String(value)] ?? String(value);
  if (key === 'themes' && Array.isArray(value)) {
    return value.map((theme) => themeLine(theme as Record<string, unknown>)).join(' / ');
  }
  if (key === 'closePrice' && typeof value === 'number') return `${value.toLocaleString('ko-KR')}원`;
  if (Array.isArray(value)) return value.map((entry) => String(entry)).join(', ');
  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([innerKey, innerValue]) => `${labelOf(innerKey)} ${String(innerValue)}`)
      .join(' · ');
  }
  return String(value);
}

function RowValues({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values).filter(
    ([, value]) =>
      value !== null && value !== undefined && value !== '' && !(Array.isArray(value) && !value.length),
  );
  if (!entries.length) return null;
  return (
    <dl className="research-row__values">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{labelOf(key)}</dt>
          <dd>{renderValue(key, value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function RowBlock({ row }: { row: ResearchRow }) {
  return (
    <li className="research-row">
      <strong>{row.label}</strong>
      <RowValues values={row.values} />
      <EvidenceList evidence={row.evidence} />
    </li>
  );
}

function AnswerBlock({ answer }: { answer: ResearchAnswer }) {
  return (
    <section className="research-answer" aria-label="답변">
      <p className="research-answer__summary">{answer.summaryKo}</p>

      <ul className="research-metrics">
        {answer.metrics.map((metric) => (
          <li key={metric.labelKo}>
            <small>{metric.labelKo}</small>
            <strong>{metric.value}</strong>
            {metric.countUnitLabelKo ? <span>{metric.countUnitLabelKo} 기준</span> : null}
            {metric.sampleSize !== null ? <span>표본 {metric.sampleSize}</span> : null}
            {metric.noteKo ? <em>{metric.noteKo}</em> : null}
          </li>
        ))}
      </ul>

      {answer.humanVerified ? null : (
        <p className="research-answer__notice">
          이 질문 유형은 아직 사람 검수를 마치지 않았습니다. 수치를 확정된 값으로 쓰지 마세요.
        </p>
      )}
      {answer.notesKo.map((note) => (
        <p className="research-answer__note" key={note}>
          {note}
        </p>
      ))}

      <ul className="research-rows">
        {answer.rows.map((row) => (
          <RowBlock key={row.label} row={row} />
        ))}
      </ul>

      {answer.exclusions.length ? (
        <section className="research-exclusions" aria-label="답에서 뺀 것">
          <h3>답에서 뺀 것</h3>
          <ul>
            {answer.exclusions.map((exclusion) => (
              <li key={exclusion.code}>
                {exclusion.labelKo} {exclusion.count}건
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <footer className="research-versions">
        <small>집계 단위 {answer.countUnitLabelKo} · 표본 {answer.sampleSize}</small>
        <small>
          {Object.entries(answer.versions)
            .map(([key, value]) => `${key} ${value}`)
            .join(' · ')}
        </small>
      </footer>
    </section>
  );
}

function FailureBlock({ failure }: { failure: ResearchFailure }) {
  return (
    <section className="research-failure" aria-label="답하지 못한 이유">
      <strong>{failure.publicLabelKo}</strong>
      <p>{failure.messageKo}</p>
      {failure.candidates.length ? (
        <>
          <p>후보를 골라 다시 물어봐 주세요.</p>
          <ul>
            {failure.candidates.map((candidate) => (
              <li key={`${candidate.seedStockCode}:${candidate.matchedText}`}>
                {candidate.canonicalName} ({candidate.seedStockCode})
                {candidate.validFrom || candidate.validTo
                  ? ` · ${candidate.validFrom ?? ''}~${candidate.validTo ?? ''}`
                  : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}

export function ResearchPage() {
  const repository = useRepository();
  const [question, setQuestion] = useState('');
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<ResearchAnswerResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const ask = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      setPending(true);
      setError(null);
      try {
        setResult(await repository.answerResearchQuestion(trimmed));
      } catch (caught) {
        setResult(null);
        setError(asRepositoryError(caught)?.message ?? '답변을 가져오지 못했습니다.');
      } finally {
        setPending(false);
      }
    },
    [repository],
  );

  return (
    <div className="page page--research">
      {/* 다른 탭과 같은 머리말 형식: 화면 이름이 제목이고 안내는 그 아래 한 단계 작게. */}
      <header className="page-intro">
        <h1>데이터 리서치</h1>
        <p className="page-intro__lead">무엇이 궁금하세요?</p>
      </header>

      <div className="detail-card">
        <form
          className="research-form"
          onSubmit={(event) => {
            event.preventDefault();
            void ask(question);
          }}
        >
          <label className="visually-hidden" htmlFor="research-question">
            질문
          </label>
          {/* 손잡이로 높이를 늘리게 두면 화면이 들쭉날쭉해진다. 글이 길어지면 스스로 커진다. */}
          <textarea
            id="research-question"
            className="research-form__input"
            value={question}
            maxLength={MAX_LENGTH}
            rows={1}
            placeholder="예: 어제 뭐가 올랐어?"
            onChange={(event) => setQuestion(event.target.value)}
            onInput={(event) => {
              const field = event.currentTarget;
              field.style.height = 'auto';
              field.style.height = `${field.scrollHeight}px`;
            }}
          />
          <button
            type="submit"
            className="research-form__submit"
            disabled={pending || !question.trim()}
          >
            {pending ? '찾는 중' : '질문하기'}
          </button>
        </form>

        <p className="research-examples__title">이런 걸 물어볼 수 있어요</p>
        <ul className="research-examples">
          {EXAMPLES.map((example) => (
            <li key={example}>
              <button
                type="button"
                onClick={() => {
                  setQuestion(example);
                  void ask(example);
                }}
              >
                {example}
              </button>
            </li>
          ))}
        </ul>

        <p className="section-note">
          보유한 과거 데이터 안에서만 답합니다. 근거 없는 답은 만들지 않습니다.
        </p>
      </div>

      {error ? <p className="research-failure">{error}</p> : null}
      {result?.data.status === 'ANSWERED' ? <AnswerBlock answer={result.data.answer} /> : null}
      {result?.data.status === 'FAILED' ? <FailureBlock failure={result.data.failure} /> : null}
    </div>
  );
}
