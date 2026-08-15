import { safeReturnTo } from '../domain/formatting';
import { RepositoryError } from '../domain/repositoryErrors';
import type {
  InfostockAuthStatus,
  OperatorAuditEntry,
  OperatorCommandInput,
  OperatorJob,
  OperatorRepository,
  OperatorReview,
  OperatorSession,
  OperatorStatus,
} from './contracts';

const CSRF_COOKIE = '__Host-dayjaview_csrf';

type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

interface Envelope<T> {
  data: T;
}

interface PagedEnvelope<T> {
  data: { items: T[] };
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie
    .split(';')
    .map((value) => value.trim())
    .find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

class LiveOperatorRepository implements OperatorRepository {
  private readonly fetcher: Fetcher;
  private readonly readCsrfToken: () => string | null;

  constructor(options: { fetcher?: Fetcher; readCsrfToken?: () => string | null } = {}) {
    this.fetcher = options.fetcher ?? fetch.bind(globalThis);
    this.readCsrfToken = options.readCsrfToken ?? (() => readCookie(CSRF_COOKIE));
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set('Accept', 'application/json');
    let response: Response;
    try {
      response = await this.fetcher(path, {
        ...init,
        headers,
        credentials: 'include',
        cache: 'no-store',
      });
    } catch (error) {
      if (error instanceof RepositoryError) throw error;
      throw new RepositoryError({
        kind: 'network',
        message: '네트워크 연결이 원활하지 않습니다.',
        retryable: true,
      });
    }

    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }

    if (!response.ok) {
      const code =
        isRecord(payload) && isRecord(payload.error) && typeof payload.error.code === 'string'
          ? payload.error.code
          : null;
      throw new RepositoryError({
        kind:
          response.status === 401
            ? 'authentication'
            : response.status === 403
              ? 'permission'
              : response.status >= 500
                ? 'unavailable'
                : 'contract',
        status: response.status,
        code,
        message: commandMessage(response.status, code),
        retryable: response.status >= 500,
      });
    }

    if (!isRecord(payload) || !isRecord(payload.data)) {
      throw new RepositoryError({
        kind: 'contract',
        status: response.status,
        message: '서버 응답 형식을 확인할 수 없습니다.',
      });
    }
    return payload as T;
  }

  private commandHeaders(idempotencyKey: string): HeadersInit {
    const token = this.readCsrfToken();
    if (!token) {
      throw new RepositoryError({
        kind: 'authentication',
        status: 401,
        code: 'AUTHENTICATION_REQUIRED',
        message: '로그인 보안 정보를 확인할 수 없습니다. 다시 로그인해 주세요.',
      });
    }
    return {
      'X-CSRF-Token': token,
      'Idempotency-Key': idempotencyKey,
      'Content-Type': 'application/json',
    };
  }

  private async command(path: string, input: OperatorCommandInput): Promise<void> {
    const body: Record<string, unknown> = {
      reasonCode: input.reasonCode,
      reason: input.reason,
      expectedVersion: input.expectedVersion,
    };
    if (input.resolution) body.resolution = input.resolution;
    await this.request<Envelope<unknown>>(path, {
      method: 'POST',
      headers: this.commandHeaders(input.idempotencyKey),
      body: JSON.stringify(body),
    });
  }

  async getSession(): Promise<OperatorSession> {
    const response = await this.request<
      Envelope<{ authenticated: boolean; roles: string[] }>
    >('/api/auth/session');
    return {
      authenticated: response.data.authenticated === true,
      operator: (response.data.roles ?? []).includes('OPERATOR'),
    };
  }

  startGoogleLogin(returnTo: string): void {
    const target = safeReturnTo(returnTo);
    window.location.assign(`/api/auth/google?returnTo=${encodeURIComponent(target)}`);
  }

  async getStatus(): Promise<OperatorStatus> {
    const response = await this.request<Envelope<OperatorStatus>>('/api/v1/operator/status');
    return response.data;
  }

  async getJobs(): Promise<OperatorJob[]> {
    const response = await this.request<PagedEnvelope<OperatorJob>>(
      '/api/v1/operator/jobs?limit=50',
    );
    return response.data.items;
  }

  async getReviews(): Promise<OperatorReview[]> {
    const response = await this.request<PagedEnvelope<OperatorReview>>(
      '/api/v1/operator/reviews?status=PENDING&limit=50',
    );
    return response.data.items;
  }

  async getAudit(): Promise<OperatorAuditEntry[]> {
    const response = await this.request<PagedEnvelope<OperatorAuditEntry>>(
      '/api/v1/operator/audit?limit=50',
    );
    return response.data.items;
  }

  async getInfostockAuthStatus(): Promise<InfostockAuthStatus> {
    const response = await this.request<Envelope<InfostockAuthStatus>>(
      '/api/v1/operator/infostock/auth-status',
    );
    return response.data;
  }

  async retryJob(input: OperatorCommandInput): Promise<void> {
    await this.command(
      `/api/v1/operator/jobs/${encodeURIComponent(input.targetId)}/retry`,
      input,
    );
  }

  async resumeJob(input: OperatorCommandInput): Promise<void> {
    await this.command(
      `/api/v1/operator/jobs/${encodeURIComponent(input.targetId)}/resume`,
      input,
    );
  }

  async resolveReview(input: OperatorCommandInput): Promise<void> {
    await this.command(
      `/api/v1/operator/reviews/${encodeURIComponent(input.targetId)}/resolve`,
      input,
    );
  }
}

export function commandMessage(status: number, code: string | null): string {
  if (status === 401) return '로그인이 만료되었습니다. 다시 로그인해 주세요.';
  if (status === 403) return '운영자 권한이 없습니다.';
  if (status === 404) return '대상을 찾을 수 없습니다. 목록을 새로고침해 주세요.';
  if (code === 'STALE_VERSION') {
    return '화면의 상태가 최신이 아닙니다. 새로고침한 뒤 다시 시도해 주세요.';
  }
  if (code === 'COMMAND_NOT_ALLOWED') {
    return '현재 상태에서는 이 작업을 실행할 수 없습니다.';
  }
  if (status >= 500) return '서버가 요청을 처리하지 못했습니다.';
  return '요청 내용을 확인해 주세요.';
}

export function createOperatorRepository(
  options: { fetcher?: Fetcher; readCsrfToken?: () => string | null } = {},
): OperatorRepository {
  return new LiveOperatorRepository(options);
}
