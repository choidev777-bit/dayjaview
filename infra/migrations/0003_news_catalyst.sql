BEGIN;

CREATE SCHEMA IF NOT EXISTS news;

CREATE TABLE IF NOT EXISTS news.items (
    news_id text PRIMARY KEY CHECK (btrim(news_id) <> ''),
    source_id text NOT NULL CHECK (btrim(source_id) <> ''),
    source_type text NOT NULL
        CHECK (source_type IN ('RSS', 'NAVER_NEWS_SEARCH', 'SUPPLEMENTAL_SEARCH')),
    source_item_id text NOT NULL CHECK (btrim(source_item_id) <> ''),
    canonical_url text NOT NULL UNIQUE CHECK (btrim(canonical_url) <> ''),
    original_url text NOT NULL CHECK (btrim(original_url) <> ''),
    publisher text NOT NULL CHECK (btrim(publisher) <> ''),
    title text NOT NULL CHECK (btrim(title) <> ''),
    description text NOT NULL DEFAULT '',
    published_at timestamptz,
    retrieved_at timestamptz NOT NULL,
    normalized_title_hash text NOT NULL CHECK (char_length(normalized_title_hash) = 64),
    content_hash text NOT NULL CHECK (char_length(content_hash) = 64),
    rights_scope text NOT NULL
        CHECK (rights_scope IN ('METADATA_ONLY', 'SUMMARY_ALLOWED', 'FULL_TEXT_ALLOWED')),
    ingestion_status text NOT NULL
        CHECK (ingestion_status IN ('STORED', 'DUPLICATE', 'REJECTED')),
    stock_ids text[] NOT NULL DEFAULT '{}',
    entities text[] NOT NULL DEFAULT '{}',
    body text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_news_items_source_item UNIQUE (source_id, source_item_id),
    CONSTRAINT uq_news_items_title_dedupe
        UNIQUE (normalized_title_hash, publisher, published_at),
    CONSTRAINT ck_news_items_published_not_after_retrieved
        CHECK (published_at IS NULL OR published_at <= retrieved_at),
    CONSTRAINT ck_news_items_body_rights
        CHECK (body = '' OR rights_scope = 'FULL_TEXT_ALLOWED')
);

CREATE INDEX IF NOT EXISTS ix_news_items_published
    ON news.items (published_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS ix_news_items_stock_ids
    ON news.items USING gin (stock_ids);

CREATE TABLE IF NOT EXISTS news.collection_cursors (
    source_id text PRIMARY KEY CHECK (btrim(source_id) <> ''),
    source_type text NOT NULL
        CHECK (source_type IN ('RSS', 'NAVER_NEWS_SEARCH', 'SUPPLEMENTAL_SEARCH')),
    last_source_item_id text,
    last_published_at timestamptz,
    last_polled_at timestamptz,
    next_poll_at timestamptz,
    status text NOT NULL DEFAULT 'HEALTHY'
        CHECK (status IN ('HEALTHY', 'RETRYING', 'FAILED')),
    last_error text,
    consecutive_failures integer NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS news.theme_matches (
    match_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    news_id text NOT NULL REFERENCES news.items(news_id) ON DELETE RESTRICT,
    event_id text NOT NULL CHECK (btrim(event_id) <> ''),
    theme_id text NOT NULL CHECK (btrim(theme_id) <> ''),
    matched_stock_ids text[] NOT NULL DEFAULT '{}',
    match_basis text[] NOT NULL CHECK (cardinality(match_basis) > 0),
    trigger_type text NOT NULL
        CHECK (trigger_type IN ('THEME_TO_NEWS', 'NEWS_TO_EVENT')),
    rule_score numeric(6, 4) NOT NULL CHECK (rule_score >= 0),
    relevance_score numeric(6, 4) NOT NULL CHECK (relevance_score >= 0),
    match_model_version text NOT NULL CHECK (btrim(match_model_version) <> ''),
    matched_at timestamptz NOT NULL,
    CONSTRAINT uq_news_theme_match UNIQUE (news_id, event_id, matched_at)
);

CREATE INDEX IF NOT EXISTS ix_news_theme_matches_event
    ON news.theme_matches (event_id, matched_at DESC);

CREATE TABLE IF NOT EXISTS news.llm_calls (
    llm_call_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_name text NOT NULL CHECK (btrim(model_name) <> ''),
    prompt_version text NOT NULL CHECK (btrim(prompt_version) <> ''),
    news_ids text[] NOT NULL CHECK (cardinality(news_ids) > 0),
    request_fingerprint text NOT NULL CHECK (char_length(request_fingerprint) = 64),
    raw_output text,
    accepted boolean NOT NULL,
    rejection text,
    called_at timestamptz NOT NULL,
    CONSTRAINT ck_news_llm_calls_rejection
        CHECK ((accepted AND rejection IS NULL) OR (NOT accepted AND rejection IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS news.catalyst_evidence (
    evidence_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id text NOT NULL CHECK (btrim(event_id) <> ''),
    news_id text NOT NULL REFERENCES news.items(news_id) ON DELETE RESTRICT,
    summary text NOT NULL CHECK (btrim(summary) <> ''),
    match_basis text[] NOT NULL CHECK (cardinality(match_basis) > 0),
    entities text[] NOT NULL DEFAULT '{}',
    quality_flags text[] NOT NULL DEFAULT '{}',
    extraction_method text NOT NULL CHECK (extraction_method IN ('RULE', 'LLM_GROUNDED')),
    llm_call_id bigint REFERENCES news.llm_calls(llm_call_id) ON DELETE RESTRICT,
    confidence numeric(4, 3) CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    generated_at timestamptz NOT NULL,
    CONSTRAINT uq_news_catalyst_evidence UNIQUE (event_id, news_id),
    CONSTRAINT ck_news_catalyst_evidence_llm
        CHECK (extraction_method <> 'LLM_GROUNDED' OR llm_call_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS news.evidence_revisions (
    event_id text NOT NULL CHECK (btrim(event_id) <> ''),
    revision integer NOT NULL CHECK (revision > 0),
    evidence_status text NOT NULL
        CHECK (evidence_status IN (
            'SEARCHING', 'SINGLE_SOURCE', 'MULTI_SOURCE_CONFIRMED',
            'NO_NEW_CATALYST', 'REEMERGENCE', 'AFTER_CLOSE_CONFIRMED'
        )),
    summary text,
    news_ids text[] NOT NULL DEFAULT '{}',
    catalyst_key text,
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    policy_version text NOT NULL CHECK (btrim(policy_version) <> ''),
    decided_at timestamptz NOT NULL,
    evidence_confirmed_at timestamptz,
    PRIMARY KEY (event_id, revision),
    CONSTRAINT ck_news_evidence_revisions_summary
        CHECK (
            summary IS NULL
            OR (btrim(summary) <> '' AND cardinality(news_ids) > 0)
            OR evidence_status = 'AFTER_CLOSE_CONFIRMED'
        )
);

CREATE INDEX IF NOT EXISTS ix_news_evidence_revisions_current
    ON news.evidence_revisions (event_id, revision DESC);

REVOKE ALL ON ALL TABLES IN SCHEMA news FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA news FROM PUBLIC;

DO $news_writer_boundary$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_news_writer') THEN
        GRANT USAGE ON SCHEMA news TO dayjaview_news_writer;
        GRANT SELECT, INSERT ON news.items, news.theme_matches,
            news.llm_calls, news.catalyst_evidence, news.evidence_revisions
            TO dayjaview_news_writer;
        GRANT SELECT, INSERT, UPDATE ON news.collection_cursors
            TO dayjaview_news_writer;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA news
            TO dayjaview_news_writer;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_api_reader') THEN
        GRANT USAGE ON SCHEMA news TO dayjaview_api_reader;
        GRANT SELECT ON news.items, news.catalyst_evidence,
            news.evidence_revisions TO dayjaview_api_reader;
    END IF;
END
$news_writer_boundary$;

COMMENT ON TABLE news.items IS
    '원문 URL·정규화 제목·매체·발행 시각으로 중복 제거된 특징주 뉴스 저장 단위.';
COMMENT ON TABLE news.catalyst_evidence IS
    '한 Event에 제출된 기사별 근거. 요약은 자체 생성물이며 기사 원문이 아니다.';
COMMENT ON TABLE news.evidence_revisions IS
    'Event별 append-only 근거 이력. 기존 revision을 덮어쓰지 않는다.';

COMMIT;
