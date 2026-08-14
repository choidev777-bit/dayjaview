-- S1-INFOSTOCK: PostgreSQL 16 raw lineage, revision, membership, and Daily scope.
-- LOCAL_AUDITED_IMPORT is offline-only. Production collection/serving stays closed.

BEGIN;

CREATE SCHEMA IF NOT EXISTS ingest;
CREATE SCHEMA IF NOT EXISTS core;
CREATE EXTENSION IF NOT EXISTS btree_gist;

REVOKE CREATE ON SCHEMA ingest FROM PUBLIC;
REVOKE CREATE ON SCHEMA core FROM PUBLIC;

CREATE TABLE IF NOT EXISTS ingest.infostock_import_runs (
    import_run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    input_hash character(64) NOT NULL UNIQUE
        CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    dataset_hash character(64) NOT NULL
        CHECK (dataset_hash ~ '^[0-9a-f]{64}$'),
    dataset text NOT NULL CHECK (btrim(dataset) <> ''),
    source_provider text NOT NULL CHECK (source_provider = 'INFOSTOCK'),
    parser_version text NOT NULL CHECK (btrim(parser_version) <> ''),
    rights_scope text NOT NULL
        CHECK (rights_scope IN ('FIXTURE_ONLY', 'LOCAL_AUDITED_IMPORT')),
    run_type text NOT NULL DEFAULT 'FULL'
        CHECK (run_type IN ('FULL', 'INCREMENTAL', 'MANUAL')),
    status text NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')),
    core_status text NOT NULL CHECK (core_status IN ('COMPLETE', 'PARTIAL', 'BLOCKED', 'FAILED')),
    daily_status text NOT NULL CHECK (daily_status IN ('COMPLETE', 'PARTIAL', 'BLOCKED', 'FAILED')),
    blockers text[] NOT NULL DEFAULT '{}',
    expected_theme_count integer NOT NULL CHECK (expected_theme_count > 0),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    themes_imported integer NOT NULL DEFAULT 0 CHECK (themes_imported >= 0),
    snapshots_linked integer NOT NULL DEFAULT 0 CHECK (snapshots_linked >= 0),
    history_rows_seen integer NOT NULL DEFAULT 0 CHECK (history_rows_seen >= 0),
    related_stocks_seen integer NOT NULL DEFAULT 0 CHECK (related_stocks_seen >= 0),
    leaders_seen integer NOT NULL DEFAULT 0 CHECK (leaders_seen >= 0),
    historical_memberships_seen integer NOT NULL DEFAULT 0
        CHECK (historical_memberships_seen >= 0),
    daily_list_entries_seen integer NOT NULL DEFAULT 0
        CHECK (daily_list_entries_seen >= 0),
    daily_posts_seen integer NOT NULL DEFAULT 0 CHECK (daily_posts_seen >= 0),
    daily_bodies_seen integer NOT NULL DEFAULT 0 CHECK (daily_bodies_seen >= 0),
    daily_relations_seen integer NOT NULL DEFAULT 0 CHECK (daily_relations_seen >= 0),
    theme_revisions_created integer NOT NULL DEFAULT 0
        CHECK (theme_revisions_created >= 0),
    membership_revisions_created integer NOT NULL DEFAULT 0
        CHECK (membership_revisions_created >= 0),
    history_revisions_created integer NOT NULL DEFAULT 0
        CHECK (history_revisions_created >= 0),
    history_leaders_created integer NOT NULL DEFAULT 0
        CHECK (history_leaders_created >= 0),
    history_memberships_created integer NOT NULL DEFAULT 0
        CHECK (history_memberships_created >= 0),
    quality_issues_created integer NOT NULL DEFAULT 0
        CHECK (quality_issues_created >= 0),
    daily_post_revisions_created integer NOT NULL DEFAULT 0
        CHECK (daily_post_revisions_created >= 0),
    quality_summary jsonb NOT NULL DEFAULT '{}',
    human_summary text NOT NULL DEFAULT '',
    CONSTRAINT ck_infostock_import_run_finish CHECK (
        (status = 'RUNNING' AND finished_at IS NULL)
        OR (status <> 'RUNNING' AND finished_at IS NOT NULL)
    ),
    CONSTRAINT ck_infostock_import_run_core_count CHECK (
        status = 'RUNNING'
        OR core_status <> 'COMPLETE'
        OR themes_imported = expected_theme_count
    )
);

COMMENT ON TABLE ingest.infostock_import_runs IS
    'FULL run에서 280-theme와 DailyFeaturedTheme 상태를 독립적으로 보고한다.';

CREATE TABLE IF NOT EXISTS ingest.infostock_sync_components (
    import_run_id bigint NOT NULL
        REFERENCES ingest.infostock_import_runs(import_run_id) ON DELETE RESTRICT,
    component text NOT NULL
        CHECK (component IN ('THEME_DATABASE', 'DAILY_FEATURED_THEME')),
    status text NOT NULL CHECK (status IN ('COMPLETE', 'PARTIAL', 'BLOCKED', 'FAILED')),
    expected_count integer CHECK (expected_count IS NULL OR expected_count >= 0),
    discovered_count integer NOT NULL DEFAULT 0 CHECK (discovered_count >= 0),
    imported_count integer NOT NULL DEFAULT 0 CHECK (imported_count >= 0),
    page_count integer NOT NULL DEFAULT 0 CHECK (page_count >= 0),
    body_count integer NOT NULL DEFAULT 0 CHECK (body_count >= 0),
    relation_count integer NOT NULL DEFAULT 0 CHECK (relation_count >= 0),
    pagination_range jsonb NOT NULL DEFAULT '{}',
    blockers text[] NOT NULL DEFAULT '{}',
    quality_summary jsonb NOT NULL DEFAULT '{}',
    human_summary text NOT NULL,
    PRIMARY KEY (import_run_id, component)
);

CREATE TABLE IF NOT EXISTS ingest.infostock_source_blobs (
    source_blob_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_provider text NOT NULL CHECK (source_provider = 'INFOSTOCK'),
    content_hash character(64) NOT NULL
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    source_content_hash character(64)
        CHECK (source_content_hash IS NULL OR source_content_hash ~ '^[0-9a-f]{64}$'),
    raw_format text NOT NULL CHECK (raw_format IN ('JSON', 'HTML')),
    raw_payload_text text NOT NULL,
    raw_payload jsonb,
    rights_scope text NOT NULL
        CHECK (rights_scope IN ('FIXTURE_ONLY', 'LOCAL_AUDITED_IMPORT')),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_infostock_source_blob UNIQUE (source_provider, content_hash),
    CONSTRAINT ck_infostock_source_blob_format CHECK (
        (raw_format = 'JSON' AND raw_payload IS NOT NULL)
        OR raw_format = 'HTML'
    )
);

COMMENT ON TABLE ingest.infostock_source_blobs IS
    'Exact UTF-8 source text and its independent byte hash; JSONB is a query projection only.';

CREATE TABLE IF NOT EXISTS ingest.infostock_source_snapshots (
    source_snapshot_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    first_import_run_id bigint NOT NULL
        REFERENCES ingest.infostock_import_runs(import_run_id) ON DELETE RESTRICT,
    source_blob_id bigint NOT NULL
        REFERENCES ingest.infostock_source_blobs(source_blob_id) ON DELETE RESTRICT,
    source_provider text NOT NULL CHECK (source_provider = 'INFOSTOCK'),
    page_type text NOT NULL CHECK (
        page_type IN (
            'IMPORT_MANIFEST', 'THEME_LIST', 'THEME_DETAIL',
            'DAILY_LIST', 'DAILY_DETAIL'
        )
    ),
    source_entity_id text,
    source_url text NOT NULL CHECK (btrim(source_url) <> ''),
    collected_at timestamptz NOT NULL,
    as_of timestamptz NOT NULL,
    parser_version text NOT NULL CHECK (btrim(parser_version) <> ''),
    is_complete boolean NOT NULL,
    quality_status text NOT NULL CHECK (btrim(quality_status) <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_infostock_snapshot_entity CHECK (
        (page_type = 'THEME_LIST' AND source_entity_id IS NULL)
        OR (page_type <> 'THEME_LIST' AND source_entity_id IS NOT NULL)
    ),
    CONSTRAINT uq_infostock_source_observation UNIQUE NULLS NOT DISTINCT (
        source_provider, page_type, source_entity_id, collected_at
    )
);

CREATE INDEX IF NOT EXISTS ix_infostock_snapshots_entity_collected
    ON ingest.infostock_source_snapshots (
        page_type, source_entity_id, collected_at DESC
    );

CREATE TABLE IF NOT EXISTS ingest.infostock_import_run_snapshots (
    import_run_id bigint NOT NULL
        REFERENCES ingest.infostock_import_runs(import_run_id) ON DELETE RESTRICT,
    source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    PRIMARY KEY (import_run_id, source_snapshot_id)
);

CREATE TABLE IF NOT EXISTS ingest.infostock_quality_issues (
    quality_issue_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    import_run_id bigint NOT NULL
        REFERENCES ingest.infostock_import_runs(import_run_id) ON DELETE RESTRICT,
    component text NOT NULL
        CHECK (component IN ('THEME_DATABASE', 'DAILY_FEATURED_THEME')),
    issue_code text NOT NULL CHECK (btrim(issue_code) <> ''),
    severity text NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'ERROR', 'BLOCKER')),
    entity_type text NOT NULL CHECK (btrim(entity_type) <> ''),
    source_entity_key text,
    source_order integer CHECK (source_order IS NULL OR source_order >= 0),
    source_snapshot_id bigint
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    detail jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_infostock_quality_issue UNIQUE NULLS NOT DISTINCT (
        import_run_id, component, issue_code, entity_type,
        source_entity_key, source_order
    )
);

CREATE INDEX IF NOT EXISTS ix_infostock_quality_issue_code
    ON ingest.infostock_quality_issues (component, issue_code, severity);

CREATE TABLE IF NOT EXISTS core.infostock_themes (
    theme_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_provider text NOT NULL CHECK (source_provider = 'INFOSTOCK'),
    source_theme_id text NOT NULL CHECK (btrim(source_theme_id) <> ''),
    current_name text NOT NULL CHECK (btrim(current_name) <> ''),
    source_url text NOT NULL CHECK (btrim(source_url) <> ''),
    source_order integer NOT NULL CHECK (source_order >= 0),
    is_active boolean NOT NULL DEFAULT true,
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    first_source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    last_source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_infostock_theme_source UNIQUE (source_provider, source_theme_id),
    CONSTRAINT ck_infostock_theme_seen_order CHECK (last_seen_at >= first_seen_at)
);

CREATE INDEX IF NOT EXISTS ix_infostock_themes_current_name
    ON core.infostock_themes (current_name);

CREATE TABLE IF NOT EXISTS core.infostock_theme_revisions (
    theme_revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    theme_id bigint NOT NULL
        REFERENCES core.infostock_themes(theme_id) ON DELETE RESTRICT,
    revision_no integer NOT NULL CHECK (revision_no > 0),
    theme_name text NOT NULL CHECK (btrim(theme_name) <> ''),
    description text NOT NULL,
    normalized_hash character(64) NOT NULL
        CHECK (normalized_hash ~ '^[0-9a-f]{64}$'),
    observed_from timestamptz NOT NULL,
    observed_to timestamptz,
    last_seen_at timestamptz NOT NULL,
    source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    last_source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    CONSTRAINT uq_infostock_theme_revision_no UNIQUE (theme_id, revision_no),
    CONSTRAINT ck_infostock_theme_revision_interval
        CHECK (observed_to IS NULL OR observed_to > observed_from),
    CONSTRAINT ck_infostock_theme_revision_last_seen
        CHECK (last_seen_at >= observed_from),
    CONSTRAINT ex_infostock_theme_revision_no_overlap EXCLUDE USING gist (
        theme_id WITH =,
        tstzrange(observed_from, observed_to, '[)') WITH &&
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_infostock_theme_revision_current
    ON core.infostock_theme_revisions (theme_id) WHERE observed_to IS NULL;

CREATE TABLE IF NOT EXISTS core.infostock_stocks (
    stock_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stock_code character(6) NOT NULL UNIQUE
        CHECK (stock_code ~ '^[0-9A-Z]{6}$'),
    current_name text NOT NULL CHECK (btrim(current_name) <> ''),
    name_authority text NOT NULL
        CHECK (name_authority IN ('HISTORICAL_REFERENCE', 'CURRENT_MEMBERSHIP', 'DAILY_REFERENCE')),
    name_authority_rank integer NOT NULL CHECK (name_authority_rank BETWEEN 1 AND 100),
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    first_source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    last_source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_infostock_stock_seen_order CHECK (last_seen_at >= first_seen_at)
);

CREATE TABLE IF NOT EXISTS core.infostock_stock_name_observations (
    stock_name_observation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stock_id bigint NOT NULL
        REFERENCES core.infostock_stocks(stock_id) ON DELETE RESTRICT,
    source_name text NOT NULL CHECK (btrim(source_name) <> ''),
    authority text NOT NULL
        CHECK (authority IN ('HISTORICAL_REFERENCE', 'CURRENT_MEMBERSHIP', 'DAILY_REFERENCE')),
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    first_source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    last_source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    CONSTRAINT uq_infostock_stock_name UNIQUE (stock_id, source_name, authority),
    CONSTRAINT ck_infostock_stock_name_seen CHECK (last_seen_at >= first_seen_at)
);

CREATE TABLE IF NOT EXISTS core.infostock_theme_stock_memberships (
    membership_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    theme_id bigint NOT NULL
        REFERENCES core.infostock_themes(theme_id) ON DELETE RESTRICT,
    stock_id bigint
        REFERENCES core.infostock_stocks(stock_id) ON DELETE RESTRICT,
    source_stock_code text,
    source_stock_name text NOT NULL CHECK (btrim(source_stock_name) <> ''),
    rationale text NOT NULL,
    source_rank integer NOT NULL CHECK (source_rank >= 0),
    source_index text,
    quality_status text NOT NULL CHECK (btrim(quality_status) <> ''),
    content_hash character(64) NOT NULL
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    observed_from timestamptz NOT NULL,
    observed_to timestamptz,
    last_seen_at timestamptz NOT NULL,
    source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    last_source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    CONSTRAINT ck_infostock_membership_interval
        CHECK (observed_to IS NULL OR observed_to > observed_from),
    CONSTRAINT ck_infostock_membership_last_seen
        CHECK (last_seen_at >= observed_from),
    CONSTRAINT ex_infostock_membership_no_overlap EXCLUDE USING gist (
        theme_id WITH =,
        stock_id WITH =,
        tstzrange(observed_from, observed_to, '[)') WITH &&
    ) WHERE (stock_id IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_infostock_membership_current
    ON core.infostock_theme_stock_memberships (theme_id, stock_id)
    WHERE observed_to IS NULL AND stock_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_infostock_membership_theme_interval
    ON core.infostock_theme_stock_memberships (theme_id, observed_from, observed_to);
CREATE INDEX IF NOT EXISTS ix_infostock_membership_stock_interval
    ON core.infostock_theme_stock_memberships (stock_id, observed_from, observed_to);

CREATE TABLE IF NOT EXISTS core.infostock_theme_history (
    history_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    theme_id bigint NOT NULL
        REFERENCES core.infostock_themes(theme_id) ON DELETE RESTRICT,
    source_history_key text NOT NULL CHECK (btrim(source_history_key) <> ''),
    source_history_id text,
    revision_no integer NOT NULL CHECK (revision_no > 0),
    event_date date,
    source_date text,
    source_created_at timestamptz,
    source_updated_at timestamptz,
    raw_text text NOT NULL,
    direction text NOT NULL CHECK (direction IN ('UP', 'DOWN', 'MIXED', 'UNKNOWN')),
    source_order integer NOT NULL CHECK (source_order >= 0),
    source_fingerprint character(64) NOT NULL
        CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    quality_status text NOT NULL CHECK (btrim(quality_status) <> ''),
    author text,
    chart_flag text,
    content_hash character(64) NOT NULL
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    observed_from timestamptz NOT NULL,
    observed_to timestamptz,
    last_seen_at timestamptz NOT NULL,
    point_in_time_safe boolean NOT NULL DEFAULT false,
    source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    last_source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    CONSTRAINT uq_infostock_history_revision
        UNIQUE (theme_id, source_history_key, revision_no),
    CONSTRAINT ck_infostock_history_interval
        CHECK (observed_to IS NULL OR observed_to > observed_from),
    CONSTRAINT ck_infostock_history_last_seen
        CHECK (last_seen_at >= observed_from),
    CONSTRAINT ck_infostock_history_pit_gate CHECK (NOT point_in_time_safe),
    CONSTRAINT ex_infostock_history_no_overlap EXCLUDE USING gist (
        theme_id WITH =,
        source_history_key WITH =,
        tstzrange(observed_from, observed_to, '[)') WITH &&
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_infostock_history_current_key
    ON core.infostock_theme_history (theme_id, source_history_key)
    WHERE observed_to IS NULL;
CREATE INDEX IF NOT EXISTS ix_infostock_history_theme_date
    ON core.infostock_theme_history (theme_id, event_date DESC);
CREATE INDEX IF NOT EXISTS ix_infostock_history_source_fingerprint
    ON core.infostock_theme_history (theme_id, source_fingerprint);

CREATE TABLE IF NOT EXISTS core.infostock_theme_history_leaders (
    history_leader_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    history_id bigint NOT NULL
        REFERENCES core.infostock_theme_history(history_id) ON DELETE RESTRICT,
    source_order integer NOT NULL CHECK (source_order >= 0),
    stock_id bigint
        REFERENCES core.infostock_stocks(stock_id) ON DELETE RESTRICT,
    source_stock_code text,
    source_stock_name text NOT NULL CHECK (btrim(source_stock_name) <> ''),
    source_url text,
    display_value text NOT NULL CHECK (btrim(display_value) <> ''),
    resolution_status text NOT NULL
        CHECK (resolution_status IN ('RESOLVED', 'SOURCE_CODE_MISSING', 'CODE_INVALID')),
    quality_status text NOT NULL CHECK (btrim(quality_status) <> ''),
    resolved_at timestamptz,
    CONSTRAINT uq_infostock_history_leader_order UNIQUE (history_id, source_order),
    CONSTRAINT ck_infostock_history_leader_resolution CHECK (
        (resolution_status = 'RESOLVED' AND stock_id IS NOT NULL AND resolved_at IS NOT NULL)
        OR resolution_status <> 'RESOLVED'
    )
);

CREATE INDEX IF NOT EXISTS ix_infostock_history_leaders_stock
    ON core.infostock_theme_history_leaders (stock_id);

CREATE TABLE IF NOT EXISTS core.infostock_theme_history_memberships (
    history_membership_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    history_id bigint NOT NULL
        REFERENCES core.infostock_theme_history(history_id) ON DELETE RESTRICT,
    source_order integer NOT NULL CHECK (source_order >= 0),
    stock_id bigint
        REFERENCES core.infostock_stocks(stock_id) ON DELETE RESTRICT,
    source_stock_code text,
    source_stock_name text NOT NULL CHECK (btrim(source_stock_name) <> ''),
    source_url text,
    display_value text NOT NULL CHECK (btrim(display_value) <> ''),
    resolution_status text NOT NULL
        CHECK (resolution_status IN ('RESOLVED', 'SOURCE_CODE_MISSING', 'CODE_INVALID')),
    quality_status text NOT NULL CHECK (btrim(quality_status) <> ''),
    resolved_at timestamptz,
    CONSTRAINT uq_infostock_history_membership_order UNIQUE (history_id, source_order),
    CONSTRAINT ck_infostock_history_membership_resolution CHECK (
        (resolution_status = 'RESOLVED' AND stock_id IS NOT NULL AND resolved_at IS NOT NULL)
        OR resolution_status <> 'RESOLVED'
    )
);

CREATE INDEX IF NOT EXISTS ix_infostock_history_memberships_stock
    ON core.infostock_theme_history_memberships (stock_id);

CREATE TABLE IF NOT EXISTS core.infostock_daily_posts (
    daily_post_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_provider text NOT NULL CHECK (source_provider = 'INFOSTOCK'),
    source_post_key text NOT NULL CHECK (btrim(source_post_key) <> ''),
    source_post_id text,
    canonical_url text,
    current_title text NOT NULL CHECK (btrim(current_title) <> ''),
    published_date date,
    visibility_status text NOT NULL
        CHECK (visibility_status IN ('VISIBLE', 'NOT_VISIBLE', 'UNKNOWN')),
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    first_source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    last_source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_infostock_daily_post_key UNIQUE (source_provider, source_post_key),
    CONSTRAINT uq_infostock_daily_post_source_id
        UNIQUE (source_provider, source_post_id),
    CONSTRAINT ck_infostock_daily_post_seen CHECK (last_seen_at >= first_seen_at)
);

CREATE TABLE IF NOT EXISTS core.infostock_daily_post_revisions (
    daily_post_revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    daily_post_id bigint NOT NULL
        REFERENCES core.infostock_daily_posts(daily_post_id) ON DELETE RESTRICT,
    revision_no integer NOT NULL CHECK (revision_no > 0),
    title text NOT NULL CHECK (btrim(title) <> ''),
    published_date date,
    source_date text,
    raw_body text,
    body_hash character(64)
        CHECK (body_hash IS NULL OR body_hash ~ '^[0-9a-f]{64}$'),
    normalized_hash character(64) NOT NULL
        CHECK (normalized_hash ~ '^[0-9a-f]{64}$'),
    body_status text NOT NULL
        CHECK (body_status IN ('OK', 'MISSING', 'PARSE_PARTIAL', 'PARSE_FAILED')),
    visibility_status text NOT NULL
        CHECK (visibility_status IN ('VISIBLE', 'NOT_VISIBLE', 'UNKNOWN')),
    observed_from timestamptz NOT NULL,
    observed_to timestamptz,
    last_seen_at timestamptz NOT NULL,
    source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    last_source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    CONSTRAINT uq_infostock_daily_revision_no UNIQUE (daily_post_id, revision_no),
    CONSTRAINT ck_infostock_daily_revision_interval
        CHECK (observed_to IS NULL OR observed_to > observed_from),
    CONSTRAINT ck_infostock_daily_revision_seen CHECK (last_seen_at >= observed_from),
    CONSTRAINT ex_infostock_daily_revision_no_overlap EXCLUDE USING gist (
        daily_post_id WITH =,
        tstzrange(observed_from, observed_to, '[)') WITH &&
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_infostock_daily_revision_current
    ON core.infostock_daily_post_revisions (daily_post_id)
    WHERE observed_to IS NULL;

CREATE TABLE IF NOT EXISTS core.infostock_daily_relations (
    daily_relation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    daily_post_revision_id bigint NOT NULL
        REFERENCES core.infostock_daily_post_revisions(daily_post_revision_id) ON DELETE RESTRICT,
    source_order integer NOT NULL CHECK (source_order >= 0),
    relation_type text NOT NULL
        CHECK (relation_type IN ('THEME', 'STOCK', 'THEME_STOCK', 'DESCRIPTION')),
    theme_id bigint
        REFERENCES core.infostock_themes(theme_id) ON DELETE RESTRICT,
    stock_id bigint
        REFERENCES core.infostock_stocks(stock_id) ON DELETE RESTRICT,
    source_theme_name text,
    source_stock_name text,
    source_stock_code text,
    description text NOT NULL,
    raw_text text NOT NULL,
    quality_status text NOT NULL CHECK (btrim(quality_status) <> ''),
    CONSTRAINT uq_infostock_daily_relation_order
        UNIQUE (daily_post_revision_id, source_order)
);

CREATE INDEX IF NOT EXISTS ix_infostock_daily_relations_theme
    ON core.infostock_daily_relations (theme_id);
CREATE INDEX IF NOT EXISTS ix_infostock_daily_relations_stock
    ON core.infostock_daily_relations (stock_id);

CREATE TABLE IF NOT EXISTS ingest.infostock_daily_list_entries (
    daily_list_entry_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_snapshot_id bigint NOT NULL
        REFERENCES ingest.infostock_source_snapshots(source_snapshot_id) ON DELETE RESTRICT,
    source_order integer NOT NULL CHECK (source_order >= 0),
    daily_post_id bigint NOT NULL
        REFERENCES core.infostock_daily_posts(daily_post_id) ON DELETE RESTRICT,
    source_post_id text,
    source_url text,
    title text NOT NULL CHECK (btrim(title) <> ''),
    published_date date,
    source_date text,
    quality_status text NOT NULL CHECK (btrim(quality_status) <> ''),
    CONSTRAINT uq_infostock_daily_list_order UNIQUE (source_snapshot_id, source_order),
    CONSTRAINT uq_infostock_daily_list_post UNIQUE (source_snapshot_id, daily_post_id)
);

CREATE TABLE IF NOT EXISTS ingest.infostock_daily_backfill_checkpoints (
    import_run_id bigint PRIMARY KEY
        REFERENCES ingest.infostock_import_runs(import_run_id) ON DELETE RESTRICT,
    status text NOT NULL
        CHECK (status IN ('COMPLETE', 'PARTIAL', 'BLOCKED', 'FAILED')),
    first_page integer CHECK (first_page IS NULL OR first_page > 0),
    last_page integer CHECK (last_page IS NULL OR last_page > 0),
    next_page integer CHECK (next_page IS NULL OR next_page > 0),
    earliest_date date,
    latest_date date,
    listed_count integer NOT NULL DEFAULT 0 CHECK (listed_count >= 0),
    detailed_count integer NOT NULL DEFAULT 0 CHECK (detailed_count >= 0),
    coverage_complete boolean NOT NULL,
    cursor_json jsonb NOT NULL,
    blockers text[] NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_infostock_daily_checkpoint_dates CHECK (
        earliest_date IS NULL OR latest_date IS NULL OR earliest_date <= latest_date
    )
);

REVOKE ALL ON ALL TABLES IN SCHEMA ingest FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA core FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA ingest FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA core FROM PUBLIC;

DO $infostock_writer_boundary$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_infostock_writer') THEN
        GRANT USAGE ON SCHEMA ingest, core TO dayjaview_infostock_writer;
        GRANT SELECT, INSERT, UPDATE
            ON ingest.infostock_import_runs,
               ingest.infostock_sync_components,
               ingest.infostock_daily_backfill_checkpoints
            TO dayjaview_infostock_writer;
        GRANT SELECT, INSERT
            ON ingest.infostock_source_blobs,
               ingest.infostock_source_snapshots,
               ingest.infostock_import_run_snapshots,
               ingest.infostock_quality_issues,
               ingest.infostock_daily_list_entries
            TO dayjaview_infostock_writer;
        GRANT SELECT, INSERT, UPDATE
            ON core.infostock_themes,
               core.infostock_theme_revisions,
               core.infostock_stocks,
               core.infostock_stock_name_observations,
               core.infostock_theme_stock_memberships,
               core.infostock_theme_history,
               core.infostock_theme_history_leaders,
               core.infostock_theme_history_memberships,
               core.infostock_daily_posts,
               core.infostock_daily_post_revisions,
               core.infostock_daily_relations
            TO dayjaview_infostock_writer;
        GRANT USAGE, SELECT
            ON ALL SEQUENCES IN SCHEMA ingest, core
            TO dayjaview_infostock_writer;
    END IF;
END
$infostock_writer_boundary$;

COMMENT ON SCHEMA ingest IS
    'Infostock raw lineage와 품질 보고; writer는 DML만 가능하며 DELETE 권한이 없다.';
COMMENT ON SCHEMA core IS
    'Canonical projection과 revision; 원본 관계명·누락 상태는 별도 보존한다.';

COMMIT;
