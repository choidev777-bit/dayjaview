import { useState } from 'react';
import { formatDate, formatTime, safeReturnTo } from '../domain/formatting';
import { asRepositoryError } from '../domain/repositoryErrors';
import { useAsyncResource } from '../shared/useAsyncResource';
import {
  INFOSTOCK_AUTH_LABELS,
  JOB_STATUS_LABELS,
  RESUMABLE_JOB_STATUSES,
  RETRYABLE_JOB_STATUSES,
  type OperatorCommandInput,
  type OperatorJob,
  type OperatorRepository,
  type OperatorReview,
} from './contracts';

type CommandKind = 'retry' | 'resume' | 'resolve';

interface OpenCommand {
  kind: CommandKind;
  targetId: string;
  expectedVersion: number;
  /** 화면을 여는 순간 고정한다. 실패 후 다시 눌러도 서버는 같은 command로 본다. */
  idempotencyKey: string;
}

const COMMAND_LABELS: Record<CommandKind, string> = {
  retry: '재시도',
  resume: '이어서 실행',
  resolve: '검수 종료',
};

const DEFAULT_REASON_CODES: Record<CommandKind, string> = {
  retry: 'OPERATOR_RETRY',
  resume: 'OPERATOR_RESUME',
  resolve: 'REVIEW_RESOLVED',
};

const RESOLUTION_OPTIONS = [
  { value: 'ACCEPTED', label: '확인 완료' },
  { value: 'NEEDS_FOLLOW_UP', label: '추가 확인 필요' },
] as const;

function formatMoment(value: string | null): string {
  if (!value) return '—';
  return `${formatDate(value)} ${formatTime(value)}`;
}

function defaultKeySource(): string {
  return globalThis.crypto.randomUUID();
}

export function OperatorConsole({
  repository,
  createIdempotencyKey = defaultKeySource,
  currentPath = '/operator.html',
}: {
  repository: OperatorRepository;
  createIdempotencyKey?: () => string;
  currentPath?: string;
}) {
  const session = useAsyncResource(() => repository.getSession(), [repository]);

  if (session.status === 'loading') {
    return (
      <main className="operator-page">
        <p role="status">운영자 권한을 확인하는 중입니다</p>
      </main>
    );
  }

  if (session.status === 'error' || !session.data.authenticated) {
    return (
      <main className="operator-page">
        <h1>운영자 콘솔</h1>
        <p>운영자 계정으로 로그인해야 합니다.</p>
        <button
          className="operator-button"
          type="button"
          onClick={() => repository.startGoogleLogin(safeReturnTo(currentPath))}
        >
          Google로 로그인
        </button>
      </main>
    );
  }

  if (!session.data.operator) {
    return (
      <main className="operator-page">
        <h1>운영자 콘솔</h1>
        <p role="alert">운영자 권한이 없습니다.</p>
      </main>
    );
  }

  return (
    <ConsoleBody
      repository={repository}
      createIdempotencyKey={createIdempotencyKey}
    />
  );
}

function ConsoleBody({
  repository,
  createIdempotencyKey,
}: {
  repository: OperatorRepository;
  createIdempotencyKey: () => string;
}) {
  const status = useAsyncResource(() => repository.getStatus(), [repository]);
  const infostock = useAsyncResource(() => repository.getInfostockAuthStatus(), [repository]);
  const jobs = useAsyncResource(() => repository.getJobs(), [repository]);
  const reviews = useAsyncResource(() => repository.getReviews(), [repository]);
  const audit = useAsyncResource(() => repository.getAudit(), [repository]);
  const [open, setOpen] = useState<OpenCommand | null>(null);
  const [commandError, setCommandError] = useState<string | null>(null);

  function refreshAll() {
    status.refresh();
    infostock.refresh();
    jobs.refresh();
    reviews.refresh();
    audit.refresh();
  }

  function cancelCommand() {
    setOpen(null);
    setCommandError(null);
  }

  async function submit(kind: CommandKind, input: OperatorCommandInput) {
    try {
      if (kind === 'retry') await repository.retryJob(input);
      else if (kind === 'resume') await repository.resumeJob(input);
      else await repository.resolveReview(input);
      setOpen(null);
      setCommandError(null);
      refreshAll();
    } catch (error) {
      const repositoryError = asRepositoryError(error);
      setCommandError(repositoryError?.message ?? '요청을 처리하지 못했습니다.');
    }
  }

  function openCommand(kind: CommandKind, targetId: string, expectedVersion: number) {
    setCommandError(null);
    setOpen({ kind, targetId, expectedVersion, idempotencyKey: createIdempotencyKey() });
  }

  return (
    <main className="operator-page">
      <header className="operator-header">
        <h1>운영자 콘솔</h1>
        <button className="operator-button" type="button" onClick={refreshAll}>
          새로고침
        </button>
      </header>

      <section aria-labelledby="operator-status-heading">
        <h2 id="operator-status-heading">배포와 서비스 상태</h2>
        {status.status === 'success' ? (
          <>
            <p className="operator-meta">
              배포 {status.data.deploymentVersion} · commit {status.data.commit} · 시작{' '}
              {formatMoment(status.data.startedAt)}
            </p>
            <ul className="operator-list">
              {status.data.services.map((service) => (
                <li key={service.name}>
                  <span className="operator-name">{service.name}</span>
                  <span className={`operator-badge operator-badge--${service.status}`}>
                    {JOB_STATUS_LABELS[service.status]}
                  </span>
                  <span className="operator-meta">
                    마지막 성공 {formatMoment(service.lastSucceededAt)}
                  </span>
                  {service.errorCode ? (
                    <span className="operator-meta">오류 {service.errorCode}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <ResourceNote resource={status.status} />
        )}
      </section>

      <section aria-labelledby="operator-infostock-heading">
        <h2 id="operator-infostock-heading">인포스탁 인증</h2>
        {infostock.status === 'success' ? (
          <p className="operator-meta">
            <span className={`operator-badge operator-badge--${infostock.data.status}`}>
              {INFOSTOCK_AUTH_LABELS[infostock.data.status]}
            </span>{' '}
            마지막 인증 {formatMoment(infostock.data.lastAuthenticatedAt)}
            {infostock.data.runbookKey ? ` · runbook ${infostock.data.runbookKey}` : ''}
          </p>
        ) : (
          <ResourceNote resource={infostock.status} />
        )}
      </section>

      <section aria-labelledby="operator-jobs-heading">
        <h2 id="operator-jobs-heading">작업</h2>
        {jobs.status === 'success' ? (
          jobs.data.length ? (
            <table className="operator-table">
              <caption className="visually-hidden">수집·배치 작업 목록</caption>
              <thead>
                <tr>
                  <th scope="col">run</th>
                  <th scope="col">유형</th>
                  <th scope="col">상태</th>
                  <th scope="col">revision</th>
                  <th scope="col">마지막 변경</th>
                  <th scope="col">실행</th>
                </tr>
              </thead>
              <tbody>
                {jobs.data.map((job) => (
                  <JobRow
                    key={job.runId}
                    job={job}
                    open={open}
                    commandError={commandError}
                    onOpen={openCommand}
                    onCancel={cancelCommand}
                    onSubmit={submit}
                  />
                ))}
              </tbody>
            </table>
          ) : (
            <p>기록된 작업이 없습니다.</p>
          )
        ) : (
          <ResourceNote resource={jobs.status} />
        )}
      </section>

      <section aria-labelledby="operator-reviews-heading">
        <h2 id="operator-reviews-heading">검수 대기</h2>
        {reviews.status === 'success' ? (
          reviews.data.length ? (
            <ul className="operator-list">
              {reviews.data.map((review) => (
                <ReviewRow
                  key={review.reviewId}
                  review={review}
                  open={open}
                  commandError={commandError}
                  onOpen={openCommand}
                  onCancel={cancelCommand}
                  onSubmit={submit}
                />
              ))}
            </ul>
          ) : (
            <p>검수 대기 중인 항목이 없습니다.</p>
          )
        ) : (
          <ResourceNote resource={reviews.status} />
        )}
      </section>

      <section aria-labelledby="operator-audit-heading">
        <h2 id="operator-audit-heading">감사 기록</h2>
        {audit.status === 'success' ? (
          audit.data.length ? (
            <ul className="operator-list">
              {audit.data.map((entry) => (
                <li key={entry.auditId}>
                  <span className="operator-name">{entry.action}</span>
                  <span className="operator-meta">
                    {entry.targetId} · {entry.reasonCode} · revision{' '}
                    {entry.beforeRevision ?? '—'}→{entry.afterRevision ?? '—'} ·{' '}
                    {formatMoment(entry.occurredAt)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p>기록된 운영자 조치가 없습니다.</p>
          )
        ) : (
          <ResourceNote resource={audit.status} />
        )}
      </section>
    </main>
  );
}

function ResourceNote({ resource }: { resource: 'loading' | 'error' }) {
  return resource === 'loading' ? (
    <p role="status">불러오는 중입니다</p>
  ) : (
    <p role="alert">불러오지 못했습니다</p>
  );
}

function JobRow({
  job,
  open,
  commandError,
  onOpen,
  onCancel,
  onSubmit,
}: {
  job: OperatorJob;
  open: OpenCommand | null;
  commandError: string | null;
  onOpen: (kind: CommandKind, targetId: string, expectedVersion: number) => void;
  onCancel: () => void;
  onSubmit: (kind: CommandKind, input: OperatorCommandInput) => void;
}) {
  const retryable = RETRYABLE_JOB_STATUSES.includes(job.status);
  const resumable = RESUMABLE_JOB_STATUSES.includes(job.status);
  const active = open?.targetId === job.runId ? open : null;

  return (
    <>
      <tr>
        <th scope="row">{job.runId}</th>
        <td>{job.jobType}</td>
        <td>
          <span className={`operator-badge operator-badge--${job.status}`}>
            {JOB_STATUS_LABELS[job.status]}
          </span>
          {job.errorCode ? <span className="operator-meta"> {job.errorCode}</span> : null}
        </td>
        <td>{job.version}</td>
        <td>{formatMoment(job.lastChangedAt)}</td>
        <td>
          {retryable ? (
            <button
              className="operator-button"
              type="button"
              aria-label={`${job.runId} 재시도`}
              onClick={() => onOpen('retry', job.runId, job.version)}
            >
              재시도
            </button>
          ) : null}
          {resumable ? (
            <button
              className="operator-button"
              type="button"
              aria-label={`${job.runId} 이어서 실행`}
              onClick={() => onOpen('resume', job.runId, job.version)}
            >
              이어서 실행
            </button>
          ) : null}
          {!retryable && !resumable ? <span className="operator-meta">—</span> : null}
        </td>
      </tr>
      {active ? (
        <tr>
          <td colSpan={6}>
            <CommandForm
              command={active}
              commandError={commandError}
              onCancel={onCancel}
              onSubmit={onSubmit}
            />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function ReviewRow({
  review,
  open,
  commandError,
  onOpen,
  onCancel,
  onSubmit,
}: {
  review: OperatorReview;
  open: OpenCommand | null;
  commandError: string | null;
  onOpen: (kind: CommandKind, targetId: string, expectedVersion: number) => void;
  onCancel: () => void;
  onSubmit: (kind: CommandKind, input: OperatorCommandInput) => void;
}) {
  const active = open?.targetId === review.reviewId ? open : null;
  return (
    <li>
      <span className="operator-name">{review.reviewType}</span>
      <span className="operator-meta">
        대상 {review.targetId} · {review.reasonCode} · revision {review.version} · 등록{' '}
        {formatMoment(review.createdAt)}
      </span>
      <button
        className="operator-button"
        type="button"
        aria-label={`${review.reviewId} 검수 종료`}
        onClick={() => onOpen('resolve', review.reviewId, review.version)}
      >
        검수 종료
      </button>
      {active ? (
        <CommandForm
          command={active}
          commandError={commandError}
          onCancel={onCancel}
          onSubmit={onSubmit}
        />
      ) : null}
    </li>
  );
}

function CommandForm({
  command,
  commandError,
  onCancel,
  onSubmit,
}: {
  command: OpenCommand;
  commandError: string | null;
  onCancel: () => void;
  onSubmit: (kind: CommandKind, input: OperatorCommandInput) => void;
}) {
  const [reasonCode, setReasonCode] = useState(DEFAULT_REASON_CODES[command.kind]);
  const [reason, setReason] = useState('');
  const [decision, setDecision] = useState<string>(RESOLUTION_OPTIONS[0].value);
  const reasonCodeId = `${command.targetId}-reason-code`;
  const reasonId = `${command.targetId}-reason`;
  const decisionId = `${command.targetId}-decision`;

  return (
    <form
      className="operator-form"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(command.kind, {
          targetId: command.targetId,
          reasonCode,
          reason,
          expectedVersion: command.expectedVersion,
          idempotencyKey: command.idempotencyKey,
          ...(command.kind === 'resolve' ? { resolution: { decision } } : {}),
        });
      }}
    >
      <p className="operator-meta">
        {COMMAND_LABELS[command.kind]} · 기대 revision {command.expectedVersion}
      </p>
      <label htmlFor={reasonCodeId}>사유 코드</label>
      <input
        id={reasonCodeId}
        name="reasonCode"
        required
        pattern="[A-Z][A-Z0-9_]*"
        value={reasonCode}
        onChange={(event) => setReasonCode(event.target.value)}
      />
      <label htmlFor={reasonId}>사유</label>
      <textarea
        id={reasonId}
        name="reason"
        required
        maxLength={1000}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
      />
      {command.kind === 'resolve' ? (
        <>
          <label htmlFor={decisionId}>검수 결과</label>
          <select
            id={decisionId}
            name="decision"
            value={decision}
            onChange={(event) => setDecision(event.target.value)}
          >
            {RESOLUTION_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </>
      ) : null}
      {commandError ? (
        <p className="operator-error" role="alert">
          {commandError}
        </p>
      ) : null}
      <div className="operator-form__actions">
        <button className="operator-button" type="submit">
          실행
        </button>
        <button className="operator-button" type="button" onClick={onCancel}>
          취소
        </button>
      </div>
    </form>
  );
}
