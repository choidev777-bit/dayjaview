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

function RowValues({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values).filter(
    ([, value]) => value !== null && value !== undefined && value !== '',
  );
  if (!entries.length) return null;
  return (
    <dl className="research-row__values">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd>
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
      <header className="page-intro">
        <small>데이터 리서치</small>
        <h1>무엇이 궁금하세요?</h1>
        <p>질문하면 보유한 과거 데이터 안에서 답을 찾아요. 근거 없는 답은 만들지 않습니다.</p>
      </header>

      <form
        className="research-form"
        onSubmit={(event) => {
          event.preventDefault();
          void ask(question);
        }}
      >
        <label htmlFor="research-question">질문</label>
        <textarea
          id="research-question"
          value={question}
          maxLength={MAX_LENGTH}
          rows={2}
          placeholder="예: 어제 뭐가 올랐어?"
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button type="submit" className="button" disabled={pending || !question.trim()}>
          {pending ? '찾는 중' : '질문하기'}
        </button>
      </form>

      <ul className="research-examples">
        {EXAMPLES.map((example) => (
          <li key={example}>
            <button
              type="button"
              className="button button--secondary"
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

      {error ? <p className="research-failure">{error}</p> : null}
      {result?.data.status === 'ANSWERED' ? <AnswerBlock answer={result.data.answer} /> : null}
      {result?.data.status === 'FAILED' ? <FailureBlock failure={result.data.failure} /> : null}
    </div>
  );
}
