BEGIN;

CREATE SCHEMA IF NOT EXISTS event;
CREATE SCHEMA IF NOT EXISTS realtime;
CREATE SCHEMA IF NOT EXISTS serving;

CREATE TABLE IF NOT EXISTS event.events (
    event_id text PRIMARY KEY CHECK (btrim(event_id) <> ''),
    identity_key text NOT NULL UNIQUE CHECK (btrim(identity_key) <> ''),
    market_date date NOT NULL,
    canonical_theme_id text NOT NULL CHECK (btrim(canonical_theme_id) <> ''),
    catalyst_key text NOT NULL CHECK (btrim(catalyst_key) <> ''),
    lifecycle_status text NOT NULL
        CHECK (lifecycle_status IN (
            'CANDIDATE', 'ACTIVE', 'WEAKENING', 'CLOSED', 'DISCARDED'
        )),
    reconciliation_status text NOT NULL
        CHECK (reconciliation_status IN ('PENDING', 'MATCHED', 'UNMATCHED')),
    state_version bigint NOT NULL CHECK (state_version > 0),
    classification_version bigint NOT NULL CHECK (classification_version > 0),
    first_detected_at timestamptz NOT NULL,
    changed_at timestamptz NOT NULL,
    last_received_at timestamptz NOT NULL,
    event_json jsonb NOT NULL CHECK (jsonb_typeof(event_json) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_event_natural_identity
        UNIQUE (market_date, canonical_theme_id, catalyst_key),
    CONSTRAINT ck_event_times CHECK (
        changed_at >= first_detected_at AND last_received_at >= changed_at
    )
);

CREATE INDEX IF NOT EXISTS ix_event_events_market_lifecycle
    ON event.events (market_date, lifecycle_status, changed_at DESC);
CREATE INDEX IF NOT EXISTS ix_event_events_theme_date
    ON event.events (canonical_theme_id, market_date DESC);

CREATE TABLE IF NOT EXISTS event.state_logs (
    state_log_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id text NOT NULL
        REFERENCES event.events(event_id) ON DELETE RESTRICT,
    state_version bigint NOT NULL CHECK (state_version > 0),
    from_status text,
    to_status text NOT NULL
        CHECK (to_status IN (
            'CANDIDATE', 'ACTIVE', 'WEAKENING', 'CLOSED', 'DISCARDED'
        )),
    policy_version text NOT NULL CHECK (btrim(policy_version) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    source text NOT NULL CHECK (btrim(source) <> ''),
    source_sequence bigint NOT NULL CHECK (source_sequence >= 0),
    command_message_id text NOT NULL UNIQUE
        CHECK (btrim(command_message_id) <> ''),
    lineage jsonb NOT NULL CHECK (jsonb_typeof(lineage) = 'array'),
    CONSTRAINT uq_event_state_log_version UNIQUE (event_id, state_version),
    CONSTRAINT ck_event_state_log_times CHECK (received_at >= occurred_at),
    CONSTRAINT ck_event_state_log_from_status CHECK (
        from_status IS NULL OR from_status IN (
            'CANDIDATE', 'ACTIVE', 'WEAKENING', 'CLOSED', 'DISCARDED'
        )
    )
);

CREATE INDEX IF NOT EXISTS ix_event_state_logs_event_time
    ON event.state_logs (event_id, occurred_at, state_version);

CREATE TABLE IF NOT EXISTS event.command_receipts (
    message_id text PRIMARY KEY CHECK (btrim(message_id) <> ''),
    command_fingerprint character(64) NOT NULL
        CHECK (command_fingerprint ~ '^[0-9a-f]{64}$'),
    event_id text NOT NULL
        REFERENCES event.events(event_id) ON DELETE RESTRICT,
    source text NOT NULL CHECK (btrim(source) <> ''),
    source_sequence bigint NOT NULL CHECK (source_sequence >= 0),
    received_at timestamptz NOT NULL,
    result_json jsonb NOT NULL CHECK (jsonb_typeof(result_json) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_event_command_source_sequence
    ON event.command_receipts (event_id, source, source_sequence DESC);

CREATE TABLE IF NOT EXISTS event.outbox (
    message_id text PRIMARY KEY CHECK (btrim(message_id) <> ''),
    aggregate_id text NOT NULL
        REFERENCES event.events(event_id) ON DELETE RESTRICT,
    event_type text NOT NULL CHECK (btrim(event_type) <> ''),
    event_version text NOT NULL CHECK (btrim(event_version) <> ''),
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    producer text NOT NULL CHECK (btrim(producer) <> ''),
    correlation_id text NOT NULL CHECK (btrim(correlation_id) <> ''),
    causation_id text,
    message_json jsonb NOT NULL CHECK (jsonb_typeof(message_json) = 'object'),
    status text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'CLAIMED', 'PUBLISHED')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL,
    claimed_until timestamptz,
    published_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_event_outbox_times CHECK (received_at >= occurred_at),
    CONSTRAINT ck_event_outbox_published CHECK (
        (status = 'PUBLISHED' AND published_at IS NOT NULL)
        OR (status <> 'PUBLISHED' AND published_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_event_outbox_pending
    ON event.outbox (available_at, message_id)
    WHERE status <> 'PUBLISHED';

CREATE TABLE IF NOT EXISTS realtime.state_checkpoints (
    checkpoint_id text PRIMARY KEY CHECK (btrim(checkpoint_id) <> ''),
    checkpoint_type text NOT NULL
        CHECK (checkpoint_type IN ('HOT_STOCK_STATE', 'DIRTY_THEME_STATE')),
    stream_id text NOT NULL CHECK (btrim(stream_id) <> ''),
    checkpoint_version text NOT NULL CHECK (btrim(checkpoint_version) <> ''),
    created_at timestamptz NOT NULL,
    as_of timestamptz NOT NULL,
    content_hash character(64) NOT NULL
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT uq_realtime_checkpoint_content
        UNIQUE (checkpoint_type, stream_id, content_hash),
    CONSTRAINT ck_realtime_checkpoint_times CHECK (created_at >= as_of)
);

CREATE INDEX IF NOT EXISTS ix_realtime_checkpoint_latest
    ON realtime.state_checkpoints (checkpoint_type, stream_id, as_of DESC);

CREATE TABLE IF NOT EXISTS serving.realtime_stream_sequences (
    stream_id text NOT NULL CHECK (btrim(stream_id) <> ''),
    topic text NOT NULL
        CHECK (topic IN (
            'theme_rank_snapshot',
            'theme_treemap_snapshot',
            'event_state_changed'
        )),
    params_key text NOT NULL CHECK (btrim(params_key) <> ''),
    last_sequence bigint NOT NULL CHECK (last_sequence >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (stream_id, topic, params_key)
);

CREATE TABLE IF NOT EXISTS serving.realtime_snapshots (
    snapshot_id text PRIMARY KEY CHECK (btrim(snapshot_id) <> ''),
    stream_id text NOT NULL CHECK (btrim(stream_id) <> ''),
    topic text NOT NULL
        CHECK (topic IN (
            'theme_rank_snapshot',
            'theme_treemap_snapshot',
            'event_state_changed'
        )),
    params_key text NOT NULL CHECK (btrim(params_key) <> ''),
    sequence bigint NOT NULL CHECK (sequence > 0),
    schema_version text NOT NULL CHECK (btrim(schema_version) <> ''),
    market_date date NOT NULL,
    generated_at timestamptz NOT NULL,
    as_of timestamptz NOT NULL,
    data_status text NOT NULL
        CHECK (data_status IN ('PREOPEN', 'LIVE', 'DELAYED', 'DEGRADED', 'CLOSED')),
    quality_flags text[] NOT NULL DEFAULT '{}',
    versions jsonb NOT NULL CHECK (jsonb_typeof(versions) = 'object'),
    payload jsonb NOT NULL,
    snapshot_json jsonb NOT NULL CHECK (jsonb_typeof(snapshot_json) = 'object'),
    content_hash character(64) NOT NULL
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_realtime_snapshot_sequence
        UNIQUE (stream_id, topic, params_key, sequence),
    CONSTRAINT ck_realtime_snapshot_times CHECK (generated_at >= as_of)
);

CREATE INDEX IF NOT EXISTS ix_realtime_snapshot_latest
    ON serving.realtime_snapshots (
        stream_id, topic, params_key, sequence DESC
    );

CREATE TABLE IF NOT EXISTS serving.realtime_snapshot_requests (
    publication_id text PRIMARY KEY CHECK (btrim(publication_id) <> ''),
    request_fingerprint character(64) NOT NULL
        CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    snapshot_id text NOT NULL UNIQUE
        REFERENCES serving.realtime_snapshots(snapshot_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now()
);

REVOKE ALL ON ALL TABLES IN SCHEMA event FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA realtime FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA serving FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA event FROM PUBLIC;

DO $event_writer_boundary$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_event_writer') THEN
        GRANT USAGE ON SCHEMA event TO dayjaview_event_writer;
        GRANT SELECT, INSERT, UPDATE ON event.events
            TO dayjaview_event_writer;
        GRANT SELECT, INSERT
            ON event.command_receipts, event.state_logs, event.outbox
            TO dayjaview_event_writer;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA event
            TO dayjaview_event_writer;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_outbox_publisher') THEN
        GRANT USAGE ON SCHEMA event TO dayjaview_outbox_publisher;
        GRANT SELECT, UPDATE ON event.outbox TO dayjaview_outbox_publisher;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_realtime_writer') THEN
        GRANT USAGE ON SCHEMA realtime, serving TO dayjaview_realtime_writer;
        GRANT SELECT, INSERT ON realtime.state_checkpoints
            TO dayjaview_realtime_writer;
        GRANT SELECT, INSERT, UPDATE
            ON serving.realtime_stream_sequences
            TO dayjaview_realtime_writer;
        GRANT SELECT, INSERT
            ON serving.realtime_snapshots,
               serving.realtime_snapshot_requests
            TO dayjaview_realtime_writer;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_api_reader') THEN
        GRANT USAGE ON SCHEMA event, serving TO dayjaview_api_reader;
        GRANT SELECT ON event.events, event.state_logs,
            serving.realtime_snapshots TO dayjaview_api_reader;
    END IF;
END
$event_writer_boundary$;

COMMENT ON TABLE event.events IS
    'Event 모듈만 변경하는 canonical Event 현재 상태와 optimistic stateVersion.';
COMMENT ON TABLE event.command_receipts IS
    'messageId 재시도 결과를 보존하는 durable idempotency receipt.';
COMMENT ON TABLE event.outbox IS
    'Event 변경과 같은 transaction에서 기록되는 idempotent delivery outbox.';
COMMENT ON TABLE serving.realtime_snapshots IS
    'streamId·topic·normalized params 범위의 단조 sequence full snapshot.';

COMMIT;
