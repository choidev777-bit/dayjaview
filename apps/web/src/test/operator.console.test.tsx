import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { OperatorConsole } from '../operator/OperatorConsole';
import { createOperatorRepository } from '../operator/operatorRepository';
import type {
  OperatorCommandInput,
  OperatorJob,
  OperatorRepository,
  OperatorReview,
} from '../operator/contracts';
import { RepositoryError } from '../domain/repositoryErrors';

const job: OperatorJob = {
  runId: 'run_infostock_daily',
  jobType: 'INFOSTOCK_DAILY_INCREMENT',
  status: 'AUTH_REQUIRED',
  version: 1,
  lastChangedAt: '2026-08-14T07:00:00.000Z',
  errorCode: 'AUTH_REQUIRED',
};

const succeededJob: OperatorJob = {
  runId: 'run_reference_data',
  jobType: 'REFERENCE_DATA_DAILY',
  status: 'SUCCEEDED',
  version: 3,
  lastChangedAt: '2026-08-14T06:50:00.000Z',
  errorCode: null,
};

const review: OperatorReview = {
  reviewId: 'review_1',
  reviewType: 'EVENT_RECONCILIATION',
  reviewStatus: 'PENDING',
  targetId: 'evt_unmatched',
  reasonCode: 'RECONCILIATION_UNMATCHED',
  version: 1,
  createdAt: '2026-08-14T07:15:00.000Z',
  resolvedAt: null,
};

function createStubRepository(
  overrides: Partial<OperatorRepository> = {},
): OperatorRepository {
  return {
    getSession: async () => ({ authenticated: true, operator: true }),
    startGoogleLogin: () => undefined,
    getStatus: async () => ({
      deploymentVersion: '2026.08.14.1',
      commit: '6c2637e',
      startedAt: '2026-08-14T00:00:00.000Z',
      services: [
        {
          name: 'infostock',
          status: 'AUTH_REQUIRED',
          lastSucceededAt: null,
          errorCode: 'AUTH_REQUIRED',
        },
      ],
    }),
    getJobs: async () => [job, succeededJob],
    getReviews: async () => [review],
    getAudit: async () => [],
    getInfostockAuthStatus: async () => ({
      status: 'AUTH_REQUIRED',
      lastAuthenticatedAt: '2026-08-13T07:00:00.000Z',
      runbookKey: 'infostock-session-refresh',
    }),
    retryJob: async () => undefined,
    resumeJob: async () => undefined,
    resolveReview: async () => undefined,
    ...overrides,
  };
}

describe('운영자 콘솔 접근 통제', () => {
  it('비로그인 상태에서는 운영 데이터 없이 로그인만 안내한다', async () => {
    render(
      <OperatorConsole
        repository={createStubRepository({
          getSession: async () => ({ authenticated: false, operator: false }),
        })}
      />,
    );

    expect(await screen.findByRole('button', { name: 'Google로 로그인' })).toBeInTheDocument();
    expect(screen.queryByText('run_infostock_daily')).not.toBeInTheDocument();
  });

  it('OPERATOR가 아닌 계정에는 운영 데이터를 보여주지 않는다', async () => {
    render(
      <OperatorConsole
        repository={createStubRepository({
          getSession: async () => ({ authenticated: true, operator: false }),
        })}
      />,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('운영자 권한이 없습니다.');
    expect(screen.queryByRole('heading', { name: '작업' })).not.toBeInTheDocument();
  });
});

describe('운영자 콘솔 화면', () => {
  it('배포·인증·작업·검수 상태를 실제 값으로 보여준다', async () => {
    render(<OperatorConsole repository={createStubRepository()} />);

    expect(await screen.findByRole('heading', { level: 1, name: '운영자 콘솔' })).toBeInTheDocument();
    expect(await screen.findByText(/2026\.08\.14\.1/)).toBeInTheDocument();
    expect(await screen.findByText(/infostock-session-refresh/)).toBeInTheDocument();

    const jobsTable = await screen.findByRole('table', { name: '수집·배치 작업 목록' });
    expect(within(jobsTable).getByRole('rowheader', { name: 'run_infostock_daily' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'run_infostock_daily 재시도' })).toBeInTheDocument();
    // 성공한 run에는 재실행 명령을 노출하지 않는다(서버 전이 규칙과 같다).
    expect(screen.queryByRole('button', { name: 'run_reference_data 재시도' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'review_1 검수 종료' })).toBeInTheDocument();
  });

  it('재시도는 사유와 기대 revision을 담아 보내고 성공하면 목록을 다시 읽는다', async () => {
    const user = userEvent.setup();
    const retryJob = vi.fn<(input: OperatorCommandInput) => Promise<void>>(async () => undefined);
    const getJobs = vi.fn(async () => [job, succeededJob]);
    render(
      <OperatorConsole
        repository={createStubRepository({ retryJob, getJobs })}
        createIdempotencyKey={() => 'fixed-key'}
      />,
    );

    await user.click(await screen.findByRole('button', { name: 'run_infostock_daily 재시도' }));
    await user.clear(screen.getByLabelText('사유 코드'));
    await user.type(screen.getByLabelText('사유 코드'), 'INFOSTOCK_SESSION_REFRESHED');
    await user.type(screen.getByLabelText('사유'), '운영자 수동 재인증 후 재시도');
    await user.click(screen.getByRole('button', { name: '실행' }));

    await waitFor(() => expect(retryJob).toHaveBeenCalledTimes(1));
    expect(retryJob.mock.calls[0][0]).toEqual({
      targetId: 'run_infostock_daily',
      reasonCode: 'INFOSTOCK_SESSION_REFRESHED',
      reason: '운영자 수동 재인증 후 재시도',
      expectedVersion: 1,
      idempotencyKey: 'fixed-key',
    });
    await waitFor(() => expect(getJobs).toHaveBeenCalledTimes(2));
  });

  it('검수 종료는 결정 값을 resolution으로 함께 보낸다', async () => {
    const user = userEvent.setup();
    const resolveReview = vi.fn<(input: OperatorCommandInput) => Promise<void>>(
      async () => undefined,
    );
    render(
      <OperatorConsole
        repository={createStubRepository({ resolveReview })}
        createIdempotencyKey={() => 'resolve-key'}
      />,
    );

    await user.click(await screen.findByRole('button', { name: 'review_1 검수 종료' }));
    await user.type(screen.getByLabelText('사유'), '장후 기사 없이 UNMATCHED 유지');
    await user.selectOptions(screen.getByLabelText('검수 결과'), 'NEEDS_FOLLOW_UP');
    await user.click(screen.getByRole('button', { name: '실행' }));

    await waitFor(() => expect(resolveReview).toHaveBeenCalledTimes(1));
    expect(resolveReview.mock.calls[0][0]).toEqual({
      targetId: 'review_1',
      reasonCode: 'REVIEW_RESOLVED',
      reason: '장후 기사 없이 UNMATCHED 유지',
      expectedVersion: 1,
      idempotencyKey: 'resolve-key',
      resolution: { decision: 'NEEDS_FOLLOW_UP' },
    });
  });

  it('stale revision 충돌은 새로고침 안내로 보여주고 같은 key를 유지한다', async () => {
    const user = userEvent.setup();
    const retryJob = vi
      .fn<(input: OperatorCommandInput) => Promise<void>>()
      .mockRejectedValueOnce(
        new RepositoryError({
          kind: 'contract',
          status: 409,
          code: 'STALE_VERSION',
          message: '화면의 상태가 최신이 아닙니다. 새로고침한 뒤 다시 시도해 주세요.',
        }),
      )
      .mockResolvedValueOnce(undefined);
    render(
      <OperatorConsole
        repository={createStubRepository({ retryJob })}
        createIdempotencyKey={() => 'conflict-key'}
      />,
    );

    await user.click(await screen.findByRole('button', { name: 'run_infostock_daily 재시도' }));
    await user.type(screen.getByLabelText('사유'), '충돌 확인');
    await user.click(screen.getByRole('button', { name: '실행' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('새로고침한 뒤 다시 시도해 주세요.');

    await user.click(screen.getByRole('button', { name: '실행' }));
    await waitFor(() => expect(retryJob).toHaveBeenCalledTimes(2));
    expect(retryJob.mock.calls[1][0].idempotencyKey).toBe('conflict-key');
  });
});

describe('운영자 client 요청 경계', () => {
  it('command는 CSRF token과 Idempotency-Key를 함께 보낸다', async () => {
    const fetcher = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
      async () =>
        new Response(JSON.stringify({ data: { auditId: 'aud_1' } }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    );
    const repository = createOperatorRepository({
      fetcher,
      readCsrfToken: () => 'csrf-token',
    });

    await repository.retryJob({
      targetId: 'run_infostock_daily',
      reasonCode: 'OPERATOR_RETRY',
      reason: '재시도',
      expectedVersion: 1,
      idempotencyKey: 'key-1',
    });

    const [path, init] = fetcher.mock.calls[0];
    expect(path).toBe('/api/v1/operator/jobs/run_infostock_daily/retry');
    expect(init?.method).toBe('POST');
    expect(init?.credentials).toBe('include');
    const headers = new Headers(init?.headers);
    expect(headers.get('X-CSRF-Token')).toBe('csrf-token');
    expect(headers.get('Idempotency-Key')).toBe('key-1');
    expect(JSON.parse(String(init?.body))).toEqual({
      reasonCode: 'OPERATOR_RETRY',
      reason: '재시도',
      expectedVersion: 1,
    });
  });

  it('CSRF token이 없으면 요청을 보내지 않는다', async () => {
    const fetcher = vi.fn(async () => new Response('{}', { status: 200 }));
    const repository = createOperatorRepository({ fetcher, readCsrfToken: () => null });

    await expect(
      repository.resumeJob({
        targetId: 'run_reconcile',
        reasonCode: 'OPERATOR_RESUME',
        reason: '재개',
        expectedVersion: 1,
        idempotencyKey: 'key-2',
      }),
    ).rejects.toBeInstanceOf(RepositoryError);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('403 응답은 권한 오류로 옮긴다', async () => {
    const fetcher = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: { code: 'FEATURE_NOT_ENTITLED' } }), {
          status: 403,
          headers: { 'Content-Type': 'application/json' },
        }),
    );
    const repository = createOperatorRepository({ fetcher, readCsrfToken: () => 'csrf' });

    await expect(repository.getJobs()).rejects.toMatchObject({
      kind: 'permission',
      status: 403,
    });
  });
});
