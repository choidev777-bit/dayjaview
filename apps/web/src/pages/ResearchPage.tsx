import { useCallback, useEffect, useRef, useState } from 'react';
import { useRepository } from '../app/RepositoryContext';
import type {
  ResearchAnswer,
  ResearchAnswerResponse,
  ResearchEvidence,
  ResearchFailure,
  ResearchRow,
  ResearchStep,
} from '../domain/contracts';
import { asRepositoryError } from '../domain/repositoryErrors';

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

/** 화면에 띄울 항목만 등록한다. 등록하지 않은 키는 아예 그리지 않는다.
 *  서버는 44종을 보내는데 그중 sourceThemeId·catalystId·projectId·countUnit 같은
 *  내부 식별자는 사용자가 볼 값이 아니다. 목록에 없으면 자동으로 빠진다. */
const VALUE_LABELS: Record<string, string> = {
  sectionName: '섹션',
  direction: '방향',
  themes: '테마',
  themeName: '테마',
  themeNames: '테마',
  changeRate: '등락률',
  themeChangeRate: '테마 등락률',
  closePrice: '종가',
  stockCode: '종목코드',
  sectionHeadline: '특징테마 문구',
  details: '상세',
  reason: '편입 이유',
  appearanceCount: '나온 횟수',
  recordCount: '기록 수',
  catalystCount: '소재 수',
  reactionCount: '반응 수',
  sharedCatalystCount: '함께 나온 횟수',
  share: '비중',
  observedDays: '관측일',
  sumChangeRate: '등락률 합',
  medianChangeRate: '중앙 등락률',
  bestDate: '가장 오른 날',
  bestChangeRate: '그날 등락률',
  worstDate: '가장 내린 날',
  worstChangeRate: '그날 등락률',
  certainty: '확실성',
  eventStage: '진행 단계',
  roles: '역할',
  geographyCodes: '지역',
  factType: '수치 유형',
  reportedValue: '발표 값',
  unit: '단위',
  currency: '통화',
  valueBasis: '금액 기준',
  baseTradingDate: '기준일',
  baseClose: '기준일 종가',
  returns: '이후 수익률',
  missingReason: '값이 없는 이유',
  leaderCount: '주도주',
  upCount: '상승',
  medianReturn: '기준 거래일 뒤 중앙값',
  horizon: '기준 거래일',
};

/** 값 자리에도 영어 코드가 온다. 항목 이름만 한글로 바꾸면 `ANTICIPATION`이 남는다. */
const CODE_LABELS: Record<string, Record<string, string>> = {
  direction: { UP: '상승', DOWN: '하락', FLAT: '보합' },
  missingReason: {
    HORIZON_NOT_REACHED: '아직 그날이 오지 않았습니다',
    NO_PRICE_ON_OR_BEFORE_EVENT: '그날 이전 주가 기록이 없습니다',
    BASE_CLOSE_MISSING: '기준일 종가가 없습니다',
    BEFORE_CORPUS_RANGE: '주가 자료가 시작되기 전입니다',
  },
  certainty: { CONFIRMED: '확정', ANTICIPATION: '기대·전망', UNSPECIFIED: '표지 없음' },
  eventStage: {
    RUMOR: '소문',
    REVIEW: '검토',
    DISCUSSION: '협의',
    BID: '입찰',
    SHORTLIST: '후보 선정',
    PREFERRED_BIDDER: '우선협상대상',
    SIGNED: '계약 체결',
    EXECUTING: '수행 중',
    COMPLETED: '완료',
    DELAYED: '지연',
    CANCELLED: '취소',
    UNSPECIFIED: '표지 없음',
  },
  roles: {
    ACTOR: '주체',
    ISSUER: '발행',
    CONTRACTOR: '수주',
    COUNTERPARTY: '상대방',
    TARGET: '대상',
    BENEFICIARY: '수혜',
    ADVERSELY_AFFECTED: '피해',
    LEADER: '주도주',
    RELATED: '관련',
  },
  factType: {
    CONTRACT_VALUE: '계약 금액',
    INVESTMENT_VALUE: '투자 금액',
    CAPACITY: '생산능력',
    QUANTITY: '수량',
    STAKE_PERCENT: '지분율',
  },
  valueBasis: {
    EXACT: '확정치',
    ESTIMATE: '추정치',
    UP_TO: '최대',
    LOWER_BOUND: '최소',
    RANGE: '구간',
    TOTAL_PROJECT: '사업 전체',
    COMPANY_SHARE: '자사 몫',
  },
};

/** 등락률로 읽는 항목. 다른 화면이 쓰는 `.market-up`·`.market-down`을 그대로 쓴다. */
const RATE_KEYS = new Set([
  'changeRate',
  'themeChangeRate',
  'bestChangeRate',
  'worstChangeRate',
  'sumChangeRate',
  'medianChangeRate',
]);

const DIRECTION_CLASS: Record<string, string> = { UP: 'market-up', DOWN: 'market-down' };

/** 서버는 `+10.86%`로도 `12.34`로도 준다. 값이 없을 때의 `—`에는 색을 주지 않는다. */
function rateClass(text: string): string | undefined {
  if (text.startsWith('-')) return 'market-down';
  if (text.startsWith('+')) return 'market-up';
  const numeric = Number.parseFloat(text);
  return Number.isFinite(numeric) && numeric > 0 ? 'market-up' : undefined;
}

function labelOf(key: string): string {
  return VALUE_LABELS[key] ?? key;
}

function renderValue(key: string, value: unknown): string {
  const codes = CODE_LABELS[key];
  if (codes) {
    if (Array.isArray(value)) {
      return value.map((entry) => codes[String(entry)] ?? String(entry)).join(', ');
    }
    return codes[String(value)] ?? String(value);
  }
  if (key === 'closePrice' || key === 'baseClose') {
    const numeric = typeof value === 'number' ? value : Number.parseFloat(String(value));
    if (Number.isFinite(numeric)) return `${numeric.toLocaleString('ko-KR')}원`;
  }
  if (Array.isArray(value)) return value.map((entry) => String(entry)).join(', ');
  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .filter(([, inner]) => inner !== null && inner !== undefined && inner !== '')
      .map(([innerKey, innerValue]) => `${labelOf(innerKey)} ${String(innerValue)}`)
      .join(' · ');
  }
  return String(value);
}

/** 섹션 > 테마 > 종목 3단 중첩. 한 칸에 이어붙이면 500자짜리 문장 하나가 된다.
 *  서버는 테마 6개·종목 8개까지만 보낸다. 총 개수를 받아 "외 N개"로 잘림을 알린다. */
function ThemeList({ themes, total }: { themes: Record<string, unknown>[]; total: number | null }) {
  return (
    <ul className="research-themes">
      {themes.map((theme, index) => {
        const name = String(theme.themeName ?? theme.themeId ?? '');
        const rate = theme.changeRate ? String(theme.changeRate) : '';
        const stocks = Array.isArray(theme.stocks)
          ? (theme.stocks as Record<string, unknown>[])
          : [];
        const stockTotal = countOf(theme.stockTotal) ?? stocks.length;
        return (
          <li key={`${name}:${index}`}>
            <p className="research-theme__head">
              <strong>{name}</strong>
              {rate ? <span className={rateClass(rate)}>{rate}</span> : null}
            </p>
            {stocks.length ? (
              <ul className="research-theme__stocks">
                {stocks.map((stock, stockIndex) => {
                  const stockRate = stock.changeRate ? String(stock.changeRate) : '';
                  return (
                    <li key={`${String(stock.stockCode ?? stock.stockName ?? '')}:${stockIndex}`}>
                      <span>{String(stock.stockName ?? '')}</span>
                      {stockRate ? <span className={rateClass(stockRate)}>{stockRate}</span> : null}
                    </li>
                  );
                })}
                {stockTotal > stocks.length ? (
                  <li className="research-more">외 {stockTotal - stocks.length}개 종목</li>
                ) : null}
              </ul>
            ) : null}
          </li>
        );
      })}
      {total !== null && total > themes.length ? (
        <li className="research-more">외 {total - themes.length}개 테마</li>
      ) : null}
    </ul>
  );
}

/** 근거 칸에 그대로 나오는 문장. 값칸에서 한 번 더 보여주지 않는다. */
const EVIDENCE_DEDUP_KEYS = new Set(['reason', 'sectionHeadline', 'details']);

function countOf(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function RowValues({
  values,
  evidenceTexts,
}: {
  values: Record<string, unknown>;
  evidenceTexts: ReadonlySet<string>;
}) {
  const entries = Object.entries(values).filter(
    ([key, value]) =>
      key in VALUE_LABELS &&
      value !== null &&
      value !== undefined &&
      value !== '' &&
      !(Array.isArray(value) && !value.length) &&
      !(EVIDENCE_DEDUP_KEYS.has(key) && typeof value === 'string' && evidenceTexts.has(value)),
  );
  if (!entries.length) return null;
  return (
    <dl className="research-row__values">
      {entries.map(([key, value]) => {
        if (key === 'themes' && Array.isArray(value)) {
          return (
            <div key={key}>
              <dt>{labelOf(key)}</dt>
              <dd>
                <ThemeList
                  themes={value as Record<string, unknown>[]}
                  total={countOf(values.themeTotal)}
                />
              </dd>
            </div>
          );
        }
        if (key === 'details' && Array.isArray(value)) {
          const paragraphs = value.map((entry) => String(entry));
          const fresh = paragraphs.filter((paragraph) => !evidenceTexts.has(paragraph));
          const total = countOf(values.detailTotal) ?? paragraphs.length;
          const unseen = total - paragraphs.length;
          if (!fresh.length && unseen <= 0) return null;
          return (
            <div key={key}>
              <dt>{labelOf(key)}</dt>
              <dd>
                {fresh.map((paragraph, paragraphIndex) => (
                  <p key={paragraphIndex}>{paragraph}</p>
                ))}
                {unseen > 0 ? (
                  <p className="research-more">원문에 문단 {unseen}개 더 있음</p>
                ) : null}
              </dd>
            </div>
          );
        }
        const text = renderValue(key, value);
        const hiddenNames =
          key === 'themeNames' && Array.isArray(value)
            ? Math.max(0, (countOf(values.themeNameTotal) ?? value.length) - value.length)
            : 0;
        const tone =
          key === 'direction'
            ? DIRECTION_CLASS[String(value)]
            : RATE_KEYS.has(key)
              ? rateClass(text)
              : undefined;
        return (
          <div key={key}>
            <dt>{labelOf(key)}</dt>
            <dd className={tone}>
              {text}
              {hiddenNames > 0 ? <span className="research-more"> 외 {hiddenNames}개</span> : null}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/** 사건 하나에 딸린 주도주 성적표. 반응한 테마별로 묶는다 — 섞어 놓으면
 *  '테마 3곳'이 어느 셋인지 세어 보기 전엔 알 수 없다. */
function LeaderTable({ leaders }: { leaders: Record<string, unknown>[] }) {
  const byTheme = new Map<string, Record<string, unknown>[]>();
  for (const leader of leaders) {
    const theme = String(leader.themeName ?? '테마 미상');
    const bucket = byTheme.get(theme);
    if (bucket) bucket.push(leader);
    else byTheme.set(theme, [leader]);
  }
  return (
    <div className="research-leaders">
      {[...byTheme].map(([theme, members]) => (
        <section key={theme}>
          <h4 className="research-leaders__theme">
            {theme} <span className="research-more">{members.length}곳</span>
          </h4>
          <ul>
            {members.map((leader, index) => {
              const returns = (leader.returns ?? {}) as Record<string, string | null>;
              const close = leader.baseClose
                ? renderValue('baseClose', leader.baseClose)
                : '';
              return (
                <li key={`${String(leader.companyName ?? '')}:${index}`}>
                  <strong>{String(leader.companyName ?? '')}</strong>
                  {close ? <span className="research-leaders__close">{close}</span> : null}
                  <span className="research-leaders__returns">
                    {Object.entries(returns)
                      .filter(([, value]) => value)
                      .map(([horizon, value]) => (
                        <em key={horizon} className={rateClass(String(value))}>
                          {horizon.replace('T+', '')}일 {value}
                        </em>
                      ))}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

function RowBlock({ row }: { row: ResearchRow }) {
  const [open, setOpen] = useState(false);
  const evidenceTexts = new Set(row.evidence.map((item) => item.excerpt));
  /* 행 제목과 같은 문장을 근거가 또 들고 있으면 한 번만 보인다.
     그 근거가 유일한 근거면 남긴다 — 근거 없는 답처럼 보이면 안 된다. */
  const deduped = row.evidence.filter((item) => item.excerpt !== row.label);
  const evidence = deduped.length ? deduped : row.evidence;
  const leaders = Array.isArray(row.values.leaders)
    ? (row.values.leaders as Record<string, unknown>[])
    : null;

  /* 사건 하나에 주도주가 여럿인 답은 날짜 줄만 먼저 보이고, 눌러야 그날의
     원문과 종목별 성적이 열린다. 다 펴 두면 사건 두 개가 한 화면을 넘는다. */
  if (leaders) {
    const median = row.values.medianReturn ? String(row.values.medianReturn) : '';
    /* 며칠 뒤를 물었는지는 질문마다 다르다. 서버가 고른 기준일을 그대로 쓴다 —
       화면이 5로 박아 두면 "3거래일 뒤" 질문에 5일 답이 붙는다. */
    const horizon = Number(row.values.horizon) || 5;
    const themeNames = Array.isArray(row.values.themeNames)
      ? (row.values.themeNames as string[])
      : [];
    return (
      <li className="research-row research-row--event">
        <button
          type="button"
          className="research-row__head"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          <span className="research-row__date">
            {open ? '▾' : '▸'} {row.label}
          </span>
          <span className="research-row__brief">
            {median ? (
              <em className={rateClass(median)}>
                {horizon}일 뒤 {median}
              </em>
            ) : null}
            <span className="research-more">
              {themeNames.length ? `테마 ${themeNames.length}곳 · ` : ''}
              주도주 {String(row.values.leaderCount ?? leaders.length)}곳 중{' '}
              {String(row.values.upCount ?? 0)}곳 상승
            </span>
          </span>
        </button>
        {open ? (
          <>
            <EvidenceList evidence={evidence} />
            <LeaderTable leaders={leaders} />
          </>
        ) : null}
      </li>
    );
  }

  return (
    <li className="research-row">
      <strong>{row.label}</strong>
      <RowValues values={row.values} evidenceTexts={evidenceTexts} />
      <EvidenceList evidence={evidence} />
    </li>
  );
}

/** 복합 질문의 답. 마지막으로 답한 단계가 손님이 물은 것에 가장 가깝다 —
 *  그것만 펴 두고, 거기까지 간 과정은 접어 둔다. */
function ComposedAnswer({ asked, steps }: { asked: string | null; steps: ResearchStep[] }) {
  const [openSteps, setOpenSteps] = useState(false);
  /* 어느 단계가 손님이 물은 답인지는 서버가 정한다(conclusion). 화면이
     '마지막 성공 단계'로 고르면 LLM이 끝에 딴 질문을 던졌을 때 그게 결론이
     된다 — 2026-08-20에 실제로 그랬다. */
  const answered = steps.filter((step) => step.status === 'ANSWERED');
  const conclusion =
    answered.find((step) => step.conclusion) ??
    (answered.length ? answered[answered.length - 1] : null);
  const rest = steps.filter((step) => step !== conclusion);
  return (
    <>
      {conclusion?.status === 'ANSWERED' ? (
        <AnswerBlock answer={conclusion.answer} asked={asked} />
      ) : null}
      {rest.length ? (
        <>
          <button
            type="button"
            className="research-rows__toggle research-steps__toggle"
            onClick={() => setOpenSteps(!openSteps)}
          >
            {openSteps ? '찾아간 과정 접기' : `찾아간 과정 ${rest.length}단계 보기`}
          </button>
          {openSteps
            ? rest.map((step, index) =>
                step.status === 'ANSWERED' ? (
                  <AnswerBlock key={index} answer={step.answer} asked={step.question} />
                ) : (
                  <FailureBlock key={index} failure={step.failure} asked={step.question} />
                ),
              )
            : null}
        </>
      ) : null}
    </>
  );
}

/** 처음 3개만 펴 둔다. 20개를 다 펴면 답이 스크롤 밑으로 밀린다. */
const VISIBLE_ROWS = 3;

function RowList({ rows }: { rows: ResearchRow[] }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? rows : rows.slice(0, VISIBLE_ROWS);
  const hidden = rows.length - shown.length;
  return (
    <>
      <ul className="research-rows">
        {shown.map((row, index) => (
          <RowBlock key={`${row.label}:${index}`} row={row} />
        ))}
      </ul>
      {hidden > 0 || expanded ? (
        <button
          type="button"
          className="research-rows__toggle"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? '접기' : `나머지 ${hidden}개 보기`}
        </button>
      ) : null}
    </>
  );
}

function AnswerBlock({ answer, asked }: { answer: ResearchAnswer; asked: string | null }) {
  return (
    <>
      <section className="research-answer" aria-label="답변">
      {/* 물어본 문장을 따로 띄우면 박스가 둘로 늘어난다. 답변 머리에 붙인다. */}
      {asked ? <p className="research-answer__asked">{asked}</p> : null}
      <p className="research-answer__summary">{answer.summaryKo}</p>

      <ul className="research-metrics">
        {answer.metrics.map((metric) => (
          /* 값이 오름인지 내림인지를 화면 다른 곳과 같은 색으로 알린다. 부호는 값 문자열이
             이미 달고 있으므로 그것으로 가른다. */
          <li
            key={metric.labelKo}
            data-tone={
              metric.value.startsWith('+') ? 'up' : metric.value.startsWith('-') ? 'down' : 'flat'
            }
          >
            <small>{metric.labelKo}</small>
            <strong>{metric.value}</strong>
            {metric.countUnitLabelKo ? <span>{metric.countUnitLabelKo} 기준</span> : null}
            {/* 일부를 뽑아 추정한 게 아니라 그날 것을 다 세고 거른 분모다. `표본`은 오해를 준다. */}
            {metric.sampleSize !== null ? <span>{metric.sampleSize}개 중</span> : null}
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

      {/* 집계 단위·표본은 위 지표 칸에 이미 있고, 엔진 버전 목록은 화면에 둘 값이 아니다. */}
      </section>
      {/* 사례는 답변 카드 밖으로 뺀다. 답변은 `무엇을 알아냈나`이고 사례는 그 근거라,
          같은 카드에 넣으면 어디까지가 답인지 흐려진다. */}
      <RowList rows={answer.rows} />
    </>
  );
}

function FailureBlock({ failure, asked }: { failure: ResearchFailure; asked: string | null }) {
  return (
    <section className="research-failure" aria-label="답하지 못한 이유">
      {asked ? <p className="research-answer__asked">{asked}</p> : null}
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
  /** 답변 위에 되짚어 줄 질문. 입력창은 물어본 뒤 비우므로 따로 들고 있는다. */
  const [asked, setAsked] = useState<string | null>(null);
  /** 빈 채로 눌렀을 때 버튼을 한 번 흔든다. */
  const [nudge, setNudge] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  // 값을 코드로 넣으면 onInput이 걸리지 않아 높이가 그대로다. 값이 바뀔 때마다 다시 잰다.
  useEffect(() => {
    const field = inputRef.current;
    if (!field) return;
    field.style.height = 'auto';
    field.style.height = `${field.scrollHeight}px`;
  }, [question]);

  const ask = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) {
        setNudge(true);
        window.setTimeout(() => setNudge(false), 420);
        return;
      }
      setAsked(trimmed);
      setQuestion('');
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
        <h1>테마 서치</h1>
        <p className="page-intro__lead">무엇이 궁금하세요?</p>
      </header>

      <div className="research-panel">
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
            ref={inputRef}
            value={question}
            maxLength={MAX_LENGTH}
            rows={1}
            placeholder="예: 어제 뭐가 올랐어?"
            onChange={(event) => setQuestion(event.target.value)}
          />
          {/* 비활성으로 두면 왜 못 누르는지 모른다. 늘 누를 수 있게 두고, 빈 채로 누르면
              한 번 흔들어 알려 준다. */}
          <button
            type="submit"
            className="research-form__submit"
            data-nudge={nudge ? 'true' : 'false'}
            disabled={pending}
          >
            {pending ? '찾는 중' : '질문하기'}
          </button>
        </form>

        <p className="section-note">
          보유한 과거 데이터 안에서만 답합니다. 근거 없는 답은 만들지 않습니다.
        </p>
      </div>

      {error ? <p className="research-failure">{error}</p> : null}
      {result?.data.status === 'ANSWERED' && result.data.steps?.length ? (
        <ComposedAnswer asked={asked} steps={result.data.steps} />
      ) : result?.data.status === 'ANSWERED' ? (
        <AnswerBlock answer={result.data.answer} asked={asked} />
      ) : null}
      {result?.data.status === 'FAILED' ? (
        <FailureBlock failure={result.data.failure} asked={asked} />
      ) : null}
    </div>
  );
}
