export type JobStatus =
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'PARTIAL'
  | 'RATE_LIMITED'
  | 'AUTH_REQUIRED'
  | 'FAILED';

export type ReviewStatus = 'PENDING' | 'RESOLVED';

export interface OperatorServiceStatus {
  name: string;
  status: JobStatus;
  lastSucceededAt: string | null;
  errorCode: string | null;
}

export interface OperatorStatus {
  deploymentVersion: string;
  commit: string;
  startedAt: string;
  services: OperatorServiceStatus[];
}

export interface OperatorJob {
  runId: string;
  jobType: string;
  status: JobStatus;
  version: number;
  lastChangedAt: string;
  errorCode: string | null;
}

export interface OperatorReview {
  reviewId: string;
  reviewType: string;
  reviewStatus: ReviewStatus;
  targetId: string;
  reasonCode: string;
  version: number;
  createdAt: string;
  resolvedAt: string | null;
}

export interface OperatorAuditEntry {
  auditId: string;
  actorId: string;
  occurredAt: string;
  action: string;
  targetId: string;
  reasonCode: string;
  beforeRevision: number | null;
  afterRevision: number | null;
}

export interface InfostockAuthStatus {
  status: 'READY' | 'AUTH_REQUIRED' | 'UNKNOWN';
  lastAuthenticatedAt: string | null;
  runbookKey: string | null;
}

export interface OperatorSession {
  authenticated: boolean;
  operator: boolean;
}

export interface OperatorCommandInput {
  targetId: string;
  reasonCode: string;
  reason: string;
  expectedVersion: number;
  /** 같은 의도의 재전송이 같은 key를 써야 서버가 중복 실행하지 않는다. */
  idempotencyKey: string;
  resolution?: Record<string, string>;
}

export interface OperatorRepository {
  getSession(): Promise<OperatorSession>;
  startGoogleLogin(returnTo: string): void;
  getStatus(): Promise<OperatorStatus>;
  getJobs(): Promise<OperatorJob[]>;
  getReviews(): Promise<OperatorReview[]>;
  getAudit(): Promise<OperatorAuditEntry[]>;
  getInfostockAuthStatus(): Promise<InfostockAuthStatus>;
  retryJob(input: OperatorCommandInput): Promise<void>;
  resumeJob(input: OperatorCommandInput): Promise<void>;
  resolveReview(input: OperatorCommandInput): Promise<void>;
}

/** 서버가 허용하는 전이와 같은 규칙(packages/operator/models.py). */
export const RETRYABLE_JOB_STATUSES: readonly JobStatus[] = [
  'FAILED',
  'RATE_LIMITED',
  'AUTH_REQUIRED',
];
export const RESUMABLE_JOB_STATUSES: readonly JobStatus[] = ['PARTIAL'];

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  RUNNING: '실행 중',
  SUCCEEDED: '성공',
  PARTIAL: '부분 성공',
  RATE_LIMITED: '호출 제한',
  AUTH_REQUIRED: '재인증 필요',
  FAILED: '실패',
};

export const INFOSTOCK_AUTH_LABELS: Record<InfostockAuthStatus['status'], string> = {
  READY: '정상',
  AUTH_REQUIRED: '재인증 필요',
  UNKNOWN: '확인 안 됨',
};
