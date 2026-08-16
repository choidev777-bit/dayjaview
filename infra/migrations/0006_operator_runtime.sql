BEGIN;

CREATE SCHEMA IF NOT EXISTS operations;

CREATE TABLE IF NOT EXISTS operations.jobs (
    run_id text PRIMARY KEY CHECK (btrim(run_id) <> ''),
    job_type text NOT NULL CHECK (btrim(job_type) <> ''),
    status text NOT NULL CHECK (status IN (
        'RUNNING', 'SUCCEEDED', 'PARTIAL', 'RATE_LIMITED',
        'AUTH_REQUIRED', 'FAILED'
    )),
    version integer NOT NULL CHECK (version > 0),
    last_changed_at timestamptz NOT NULL,
    error_code text,
    internal_context jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(internal_context) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_operator_jobs_changed
    ON operations.jobs (last_changed_at DESC, run_id);

CREATE TABLE IF NOT EXISTS operations.reviews (
    review_id text PRIMARY KEY CHECK (btrim(review_id) <> ''),
    review_type text NOT NULL CHECK (btrim(review_type) <> ''),
    review_status text NOT NULL CHECK (review_status IN ('PENDING', 'RESOLVED')),
    target_id text NOT NULL CHECK (btrim(target_id) <> ''),
    reason_code text NOT NULL CHECK (btrim(reason_code) <> ''),
    version integer NOT NULL CHECK (version > 0),
    created_at timestamptz NOT NULL,
    resolved_at timestamptz,
    internal_context jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(internal_context) = 'object'),
    resolution jsonb,
    CONSTRAINT ck_operator_review_resolution CHECK (
        (review_status = 'PENDING' AND resolved_at IS NULL AND resolution IS NULL)
        OR (review_status = 'RESOLVED' AND resolved_at IS NOT NULL
            AND jsonb_typeof(resolution) = 'object')
    )
);

CREATE INDEX IF NOT EXISTS ix_operator_reviews_created
    ON operations.reviews (created_at, review_id);

CREATE TABLE IF NOT EXISTS operations.audit_entries (
    audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id text NOT NULL CHECK (btrim(actor_id) <> ''),
    occurred_at timestamptz NOT NULL,
    action text NOT NULL CHECK (btrim(action) <> ''),
    target_id text NOT NULL CHECK (btrim(target_id) <> ''),
    reason_code text NOT NULL CHECK (btrim(reason_code) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    before_revision integer,
    after_revision integer
);

CREATE INDEX IF NOT EXISTS ix_operator_audit_time
    ON operations.audit_entries (occurred_at DESC, audit_id DESC);

CREATE TABLE IF NOT EXISTS operations.command_receipts (
    actor_id text NOT NULL CHECK (btrim(actor_id) <> ''),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    fingerprint character(64) NOT NULL
        CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    audit_id bigint NOT NULL
        REFERENCES operations.audit_entries(audit_id) ON DELETE RESTRICT,
    PRIMARY KEY (actor_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS operations.infostock_auth_status (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    status text NOT NULL CHECK (status IN ('READY', 'AUTH_REQUIRED', 'UNKNOWN')),
    last_authenticated_at timestamptz,
    runbook_key text
);

INSERT INTO operations.infostock_auth_status (
    singleton, status, last_authenticated_at, runbook_key
) VALUES (true, 'UNKNOWN', NULL, NULL)
ON CONFLICT (singleton) DO NOTHING;

REVOKE ALL ON ALL TABLES IN SCHEMA operations FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA operations FROM PUBLIC;

COMMIT;
