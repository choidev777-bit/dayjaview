export type RepositoryErrorKind =
  | 'authentication'
  | 'permission'
  | 'network'
  | 'unavailable'
  | 'contract';

export class RepositoryError extends Error {
  readonly kind: RepositoryErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly retryable: boolean;

  constructor({
    kind,
    message,
    status = null,
    code = null,
    retryable = false,
  }: {
    kind: RepositoryErrorKind;
    message: string;
    status?: number | null;
    code?: string | null;
    retryable?: boolean;
  }) {
    super(message);
    this.name = 'RepositoryError';
    this.kind = kind;
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

export function asRepositoryError(error: unknown): RepositoryError | null {
  return error instanceof RepositoryError ? error : null;
}
