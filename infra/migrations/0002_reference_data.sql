-- S2-REFDATA: KRX/OpenDART raw lineage and point-in-time reference revisions.
-- Fixture verification is independent from live validation; secrets are never stored here.

BEGIN;

CREATE SCHEMA IF NOT EXISTS ingest;
CREATE SCHEMA IF NOT EXISTS core;
CREATE EXTENSION IF NOT EXISTS btree_gist;

REVOKE CREATE ON SCHEMA ingest FROM PUBLIC;
REVOKE CREATE ON SCHEMA core FROM PUBLIC;

CREATE TABLE IF NOT EXISTS ingest.reference_source_snapshots (
    source_snapshot_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_provider text NOT NULL
        CHECK (source_provider IN ('KRX_OPEN_API', 'OPENDART')),
    dataset text NOT NULL CHECK (btrim(dataset) <> ''),
    source_key text NOT NULL CHECK (btrim(source_key) <> ''),
    endpoint text NOT NULL CHECK (endpoint LIKE 'https://%'),
    as_of timestamptz NOT NULL,
    collected_at timestamptz NOT NULL,
    parser_version text NOT NULL CHECK (btrim(parser_version) <> ''),
    source_revision integer NOT NULL CHECK (source_revision > 0),
    raw_hash character(64) NOT NULL CHECK (raw_hash ~ '^[0-9a-f]{64}$'),
    raw_payload_text text NOT NULL CHECK (btrim(raw_payload_text) <> ''),
    lineage text[] NOT NULL CHECK (cardinality(lineage) > 0),
    source_document_ids text[] NOT NULL DEFAULT '{}',
    live_validation_status text NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (live_validation_status IN ('UNVERIFIED', 'VERIFIED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_reference_snapshot_time CHECK (as_of <= collected_at),
    CONSTRAINT uq_reference_snapshot_revision
        UNIQUE (source_provider, dataset, source_key, as_of, source_revision),
    CONSTRAINT uq_reference_snapshot_content
        UNIQUE (source_provider, dataset, source_key, as_of, raw_hash)
);

CREATE INDEX IF NOT EXISTS ix_reference_snapshot_pit
    ON ingest.reference_source_snapshots
    (source_provider, dataset, source_key, as_of DESC, collected_at DESC);

CREATE TABLE IF NOT EXISTS core.reference_share_observations (
    share_observation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stock_code character(6) NOT NULL CHECK (stock_code ~ '^[0-9A-Z]{6}$'),
    economic_field text NOT NULL CHECK (economic_field = 'LISTED_COMMON_SHARES'),
    share_class text NOT NULL CHECK (share_class = 'COMMON'),
    share_count bigint NOT NULL CHECK (share_count > 0),
    effective_on date NOT NULL,
    known_at timestamptz NOT NULL,
    source_priority integer NOT NULL CHECK (source_priority > 0),
    source_snapshot_id bigint NOT NULL
        REFERENCES ingest.reference_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_reference_share_source
        UNIQUE (source_snapshot_id, stock_code, economic_field, share_class, effective_on)
);

CREATE INDEX IF NOT EXISTS ix_reference_share_pit
    ON core.reference_share_observations
    (stock_code, economic_field, effective_on DESC, known_at DESC);

CREATE TABLE IF NOT EXISTS core.reference_non_float_holdings (
    non_float_holding_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stock_code character(6) NOT NULL CHECK (stock_code ~ '^[0-9A-Z]{6}$'),
    holder_id text NOT NULL CHECK (btrim(holder_id) <> ''),
    holder_name text NOT NULL CHECK (btrim(holder_name) <> ''),
    category text NOT NULL CHECK (
        category IN ('TREASURY', 'CONTROLLING_HOLDER', 'STRATEGIC_LOCKUP')
    ),
    share_class text NOT NULL CHECK (share_class = 'COMMON'),
    share_count bigint NOT NULL CHECK (share_count >= 0),
    effective_on date NOT NULL,
    known_at timestamptz NOT NULL,
    source_priority integer NOT NULL CHECK (source_priority > 0),
    source_snapshot_id bigint NOT NULL
        REFERENCES ingest.reference_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    source_receipt_no text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_reference_holding_source
        UNIQUE (source_snapshot_id, stock_code, holder_id, share_class, effective_on)
);

CREATE INDEX IF NOT EXISTS ix_reference_holding_pit
    ON core.reference_non_float_holdings
    (stock_code, holder_id, share_class, effective_on DESC, known_at DESC);

CREATE TABLE IF NOT EXISTS core.reference_holding_coverage (
    holding_coverage_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stock_code character(6) NOT NULL CHECK (stock_code ~ '^[0-9A-Z]{6}$'),
    category text NOT NULL CHECK (
        category IN ('TREASURY', 'CONTROLLING_HOLDER', 'STRATEGIC_LOCKUP')
    ),
    coverage_status text NOT NULL
        CHECK (coverage_status IN ('COMPLETE', 'COMPLETE_ZERO', 'INCOMPLETE')),
    effective_on date NOT NULL,
    known_at timestamptz NOT NULL,
    source_snapshot_id bigint NOT NULL
        REFERENCES ingest.reference_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_reference_holding_coverage_source
        UNIQUE (source_snapshot_id, stock_code, category, effective_on)
);

CREATE INDEX IF NOT EXISTS ix_reference_holding_coverage_pit
    ON core.reference_holding_coverage
    (stock_code, category, effective_on DESC, known_at DESC);

CREATE TABLE IF NOT EXISTS core.reference_free_float_revisions (
    free_float_revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stock_code character(6) NOT NULL CHECK (stock_code ~ '^[0-9A-Z]{6}$'),
    effective_on date NOT NULL,
    revision_no integer NOT NULL CHECK (revision_no > 0),
    known_from timestamptz NOT NULL,
    known_to timestamptz,
    quality_state text NOT NULL CHECK (
        quality_state IN (
            'VERIFIED', 'PARTIAL', 'MISSING', 'CONFLICT', 'STALE',
            'CORPORATE_ACTION_UNRESOLVED', 'POINT_IN_TIME_UNAVAILABLE'
        )
    ),
    issued_common_shares bigint CHECK (issued_common_shares > 0),
    deducted_non_float_shares bigint CHECK (deducted_non_float_shares >= 0),
    free_float_shares bigint CHECK (free_float_shares >= 0),
    free_float_ratio numeric(24, 20)
        CHECK (free_float_ratio >= 0 AND free_float_ratio <= 1),
    duplicate_deductions_prevented integer NOT NULL DEFAULT 0
        CHECK (duplicate_deductions_prevented >= 0),
    quality_flags text[] NOT NULL DEFAULT '{}',
    calculation_version text NOT NULL CHECK (btrim(calculation_version) <> ''),
    content_hash character(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    lineage jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_reference_free_float_revision
        UNIQUE (stock_code, effective_on, revision_no),
    CONSTRAINT ck_reference_free_float_interval
        CHECK (known_to IS NULL OR known_to > known_from),
    CONSTRAINT ck_reference_free_float_availability CHECK (
        (
            quality_state = 'VERIFIED'
            AND issued_common_shares IS NOT NULL
            AND deducted_non_float_shares IS NOT NULL
            AND free_float_shares IS NOT NULL
            AND free_float_ratio IS NOT NULL
            AND free_float_shares = issued_common_shares - deducted_non_float_shares
        )
        OR (
            quality_state <> 'VERIFIED'
            AND free_float_ratio IS NULL
            AND free_float_shares IS NULL
        )
    ),
    CONSTRAINT ex_reference_free_float_known_no_overlap EXCLUDE USING gist (
        stock_code WITH =,
        effective_on WITH =,
        tstzrange(known_from, known_to, '[)') WITH &&
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_reference_free_float_current
    ON core.reference_free_float_revisions (stock_code, effective_on)
    WHERE known_to IS NULL;

CREATE TABLE IF NOT EXISTS core.reference_trading_calendar_revisions (
    calendar_revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    market text NOT NULL DEFAULT 'KRX' CHECK (market = 'KRX'),
    market_date date NOT NULL,
    revision_no integer NOT NULL CHECK (revision_no > 0),
    is_trading_day boolean NOT NULL,
    session_open time,
    session_close time,
    calendar_version text NOT NULL CHECK (btrim(calendar_version) <> ''),
    known_from timestamptz NOT NULL,
    known_to timestamptz,
    content_hash character(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    source_snapshot_id bigint NOT NULL
        REFERENCES ingest.reference_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_reference_calendar_revision
        UNIQUE (market, market_date, revision_no),
    CONSTRAINT ck_reference_calendar_session CHECK (
        (is_trading_day AND session_open IS NOT NULL AND session_close IS NOT NULL
            AND session_open < session_close)
        OR (NOT is_trading_day AND session_open IS NULL AND session_close IS NULL)
    ),
    CONSTRAINT ck_reference_calendar_interval
        CHECK (known_to IS NULL OR known_to > known_from),
    CONSTRAINT ex_reference_calendar_known_no_overlap EXCLUDE USING gist (
        market WITH =,
        market_date WITH =,
        tstzrange(known_from, known_to, '[)') WITH &&
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_reference_calendar_current
    ON core.reference_trading_calendar_revisions (market, market_date)
    WHERE known_to IS NULL;

CREATE TABLE IF NOT EXISTS core.reference_daily_prices (
    daily_price_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stock_code character(6) NOT NULL CHECK (stock_code ~ '^[0-9A-Z]{6}$'),
    market text NOT NULL CHECK (market IN ('KOSPI', 'KOSDAQ', 'KONEX')),
    market_date date NOT NULL,
    close numeric(24, 8) NOT NULL CHECK (close > 0),
    change_from_previous numeric(24, 8),
    listed_shares bigint NOT NULL CHECK (listed_shares > 0),
    known_at timestamptz NOT NULL,
    source_snapshot_id bigint NOT NULL
        REFERENCES ingest.reference_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_reference_daily_price_source
        UNIQUE (source_snapshot_id, stock_code, market_date)
);

CREATE INDEX IF NOT EXISTS ix_reference_daily_price_pit
    ON core.reference_daily_prices (stock_code, market_date DESC, known_at DESC);

CREATE TABLE IF NOT EXISTS core.reference_corporate_action_revisions (
    corporate_action_revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stock_code character(6) NOT NULL CHECK (stock_code ~ '^[0-9A-Z]{6}$'),
    effective_on date NOT NULL,
    revision_no integer NOT NULL CHECK (revision_no > 0),
    action_status text NOT NULL CHECK (action_status IN ('CLEAR', 'ADJUSTED', 'UNRESOLVED')),
    adjustment_factor numeric(30, 20),
    known_from timestamptz NOT NULL,
    known_to timestamptz,
    content_hash character(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    source_snapshot_id bigint NOT NULL
        REFERENCES ingest.reference_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_reference_corporate_action_revision
        UNIQUE (stock_code, effective_on, revision_no),
    CONSTRAINT ck_reference_corporate_action_factor CHECK (
        (action_status = 'CLEAR' AND adjustment_factor = 1)
        OR (action_status = 'ADJUSTED' AND adjustment_factor > 0)
        OR (action_status = 'UNRESOLVED' AND adjustment_factor IS NULL)
    ),
    CONSTRAINT ck_reference_corporate_action_interval
        CHECK (known_to IS NULL OR known_to > known_from),
    CONSTRAINT ex_reference_corporate_action_known_no_overlap EXCLUDE USING gist (
        stock_code WITH =,
        effective_on WITH =,
        tstzrange(known_from, known_to, '[)') WITH &&
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_reference_corporate_action_current
    ON core.reference_corporate_action_revisions (stock_code, effective_on)
    WHERE known_to IS NULL;

CREATE TABLE IF NOT EXISTS core.reference_adjusted_price_revisions (
    adjusted_price_revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stock_code character(6) NOT NULL CHECK (stock_code ~ '^[0-9A-Z]{6}$'),
    effective_for date NOT NULL,
    previous_trading_day date,
    previous_adjusted_close numeric(24, 8),
    revision_no integer NOT NULL CHECK (revision_no > 0),
    quality_state text NOT NULL CHECK (
        quality_state IN (
            'VERIFIED', 'MISSING', 'CONFLICT', 'CORPORATE_ACTION_UNRESOLVED',
            'POINT_IN_TIME_UNAVAILABLE'
        )
    ),
    quality_flags text[] NOT NULL DEFAULT '{}',
    price_version text NOT NULL CHECK (btrim(price_version) <> ''),
    known_from timestamptz NOT NULL,
    known_to timestamptz,
    content_hash character(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    lineage jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_reference_adjusted_price_revision
        UNIQUE (stock_code, effective_for, revision_no),
    CONSTRAINT ck_reference_adjusted_price_availability CHECK (
        (quality_state = 'VERIFIED' AND previous_trading_day IS NOT NULL
            AND previous_adjusted_close > 0)
        OR (quality_state <> 'VERIFIED' AND previous_adjusted_close IS NULL)
    ),
    CONSTRAINT ck_reference_adjusted_price_interval
        CHECK (known_to IS NULL OR known_to > known_from),
    CONSTRAINT ex_reference_adjusted_price_known_no_overlap EXCLUDE USING gist (
        stock_code WITH =,
        effective_for WITH =,
        tstzrange(known_from, known_to, '[)') WITH &&
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_reference_adjusted_price_current
    ON core.reference_adjusted_price_revisions (stock_code, effective_for)
    WHERE known_to IS NULL;

REVOKE ALL ON ALL TABLES IN SCHEMA ingest FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA core FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA ingest FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA core FROM PUBLIC;

DO $reference_writer_boundary$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_reference_writer') THEN
        GRANT USAGE ON SCHEMA ingest, core TO dayjaview_reference_writer;
        GRANT SELECT, INSERT
            ON ingest.reference_source_snapshots,
               core.reference_share_observations,
               core.reference_non_float_holdings,
               core.reference_holding_coverage,
               core.reference_daily_prices
            TO dayjaview_reference_writer;
        GRANT SELECT, INSERT, UPDATE
            ON core.reference_free_float_revisions,
               core.reference_trading_calendar_revisions,
               core.reference_corporate_action_revisions,
               core.reference_adjusted_price_revisions
            TO dayjaview_reference_writer;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ingest, core
            TO dayjaview_reference_writer;
    END IF;
END
$reference_writer_boundary$;

COMMENT ON TABLE ingest.reference_source_snapshots IS
    'KRX/OpenDART exact raw snapshot metadata. 인증키는 저장하지 않는다.';
COMMENT ON TABLE core.reference_free_float_revisions IS
    '현재값을 과거에 소급하지 않는 DAYJAVIEW 유동주식비율 revision.';
COMMENT ON TABLE core.reference_adjusted_price_revisions IS
    '거래일·기업행위 상태가 검증된 경우에만 값이 있는 전일 조정종가.';

COMMIT;
