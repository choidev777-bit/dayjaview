BEGIN;

-- 현실 사건을 만들기 전 원천의 어느 부분을 읽었는지 고정한다.
-- 공통 metadata와 source별 실제 FK를 분리해 검증 불가능한 다형 참조를 피한다.

CREATE TABLE IF NOT EXISTS ontology.source_mentions (
    source_mention_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_kind text NOT NULL CHECK (
        source_kind IN (
            'INFOSTOCK_THEME_HISTORY',
            'INFOSTOCK_DAILY_DESCRIPTION',
            'INFOSTOCK_DAILY_THEME_STOCK',
            'NEWS_CATALYST_EVIDENCE',
            'MANUAL_REVIEW'
        )
    ),
    source_revision_hash character(64) NOT NULL
        CHECK (source_revision_hash ~ '^[0-9a-f]{64}$'),
    source_text_hash character(64) NOT NULL
        CHECK (source_text_hash ~ '^[0-9a-f]{64}$'),
    start_offset integer NOT NULL CHECK (start_offset >= 0),
    end_offset integer NOT NULL,
    transform_version text NOT NULL CHECK (btrim(transform_version) <> ''),
    output_hash character(64) NOT NULL
        CHECK (output_hash ~ '^[0-9a-f]{64}$'),
    review_status text NOT NULL DEFAULT 'AI_DRAFT'
        CHECK (review_status IN ('AI_DRAFT', 'AI_CROSS_CHECKED', 'HUMAN_CONFIRMED')),
    observed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_ontology_source_mention_span
        CHECK (end_offset > start_offset),
    CONSTRAINT uq_ontology_source_mention_version UNIQUE (
        source_kind, source_revision_hash, transform_version, output_hash
    )
);

CREATE INDEX IF NOT EXISTS ix_ontology_source_mentions_kind_revision
    ON ontology.source_mentions (source_kind, source_revision_hash);

CREATE TABLE IF NOT EXISTS ontology.source_mention_history (
    source_mention_id bigint PRIMARY KEY
        REFERENCES ontology.source_mentions(source_mention_id) ON DELETE RESTRICT,
    history_id bigint NOT NULL
        REFERENCES core.infostock_theme_history(history_id) ON DELETE RESTRICT,
    clause_order integer NOT NULL CHECK (clause_order >= 0),
    transform_version text NOT NULL CHECK (btrim(transform_version) <> ''),
    CONSTRAINT uq_ontology_source_mention_history_version
        UNIQUE (history_id, clause_order, transform_version)
);

CREATE INDEX IF NOT EXISTS ix_ontology_source_mention_history_source
    ON ontology.source_mention_history (history_id, clause_order);

CREATE TABLE IF NOT EXISTS ontology.source_mention_daily (
    source_mention_id bigint PRIMARY KEY
        REFERENCES ontology.source_mentions(source_mention_id) ON DELETE RESTRICT,
    daily_relation_id bigint NOT NULL
        REFERENCES core.infostock_daily_relations(daily_relation_id) ON DELETE RESTRICT,
    mention_scope text NOT NULL
        CHECK (mention_scope IN ('HEADLINE', 'DETAIL', 'STOCK_ROW')),
    transform_version text NOT NULL CHECK (btrim(transform_version) <> ''),
    trading_date date,
    published_date date,
    serving_status text NOT NULL CHECK (
        serving_status IN ('ELIGIBLE', 'REVIEW_REQUIRED', 'EXCLUDED')
    ),
    CONSTRAINT uq_ontology_source_mention_daily_version
        UNIQUE (daily_relation_id, mention_scope, transform_version)
);

CREATE INDEX IF NOT EXISTS ix_ontology_source_mention_daily_source
    ON ontology.source_mention_daily (daily_relation_id, source_mention_id);
CREATE INDEX IF NOT EXISTS ix_ontology_source_mention_daily_trading_date
    ON ontology.source_mention_daily (trading_date, source_mention_id);

CREATE TABLE IF NOT EXISTS ontology.source_mention_news (
    source_mention_id bigint PRIMARY KEY
        REFERENCES ontology.source_mentions(source_mention_id) ON DELETE RESTRICT,
    news_id text NOT NULL REFERENCES news.items(news_id) ON DELETE RESTRICT,
    transform_version text NOT NULL CHECK (btrim(transform_version) <> ''),
    CONSTRAINT uq_ontology_source_mention_news_version
        UNIQUE (news_id, transform_version)
);

CREATE INDEX IF NOT EXISTS ix_ontology_source_mention_news_source
    ON ontology.source_mention_news (news_id, source_mention_id);

CREATE TABLE IF NOT EXISTS ontology.source_mention_manual (
    source_mention_id bigint PRIMARY KEY
        REFERENCES ontology.source_mentions(source_mention_id) ON DELETE RESTRICT,
    review_id text NOT NULL
        REFERENCES operations.reviews(review_id) ON DELETE RESTRICT,
    transform_version text NOT NULL CHECK (btrim(transform_version) <> ''),
    CONSTRAINT uq_ontology_source_mention_manual_version
        UNIQUE (review_id, transform_version)
);

CREATE INDEX IF NOT EXISTS ix_ontology_source_mention_manual_source
    ON ontology.source_mention_manual (review_id, source_mention_id);

-- mention 하나가 source별 bridge 중 정확히 하나에 연결됐는지 commit 시점에
-- 검사한다. base와 bridge를 따로 INSERT할 수 있도록 deferred constraint다.
CREATE OR REPLACE FUNCTION ontology.check_source_mention_link()
RETURNS trigger
LANGUAGE plpgsql
AS $source_mention_link$
DECLARE
    checked_id bigint;
    checked_kind text;
    link_count integer;
    daily_type text;
    link_transform text;
BEGIN
    checked_id := COALESCE(NEW.source_mention_id, OLD.source_mention_id);
    SELECT source_kind INTO checked_kind
      FROM ontology.source_mentions
     WHERE source_mention_id = checked_id;

    -- base 행이 이미 사라진 경우는 FK가 처리한다.
    IF checked_kind IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT
        (SELECT count(*) FROM ontology.source_mention_history
          WHERE source_mention_id = checked_id)
      + (SELECT count(*) FROM ontology.source_mention_daily
          WHERE source_mention_id = checked_id)
      + (SELECT count(*) FROM ontology.source_mention_news
          WHERE source_mention_id = checked_id)
      + (SELECT count(*) FROM ontology.source_mention_manual
          WHERE source_mention_id = checked_id)
      INTO link_count;

    IF link_count <> 1 THEN
        RAISE EXCEPTION
            'source_mention_id % has % typed links, expected exactly one',
            checked_id, link_count;
    END IF;

    IF checked_kind = 'INFOSTOCK_THEME_HISTORY' AND NOT EXISTS (
        SELECT 1 FROM ontology.source_mention_history
         WHERE source_mention_id = checked_id
    ) THEN
        RAISE EXCEPTION 'history source mention % has wrong typed link', checked_id;
    ELSIF checked_kind IN (
        'INFOSTOCK_DAILY_DESCRIPTION', 'INFOSTOCK_DAILY_THEME_STOCK'
    ) THEN
        SELECT relation.relation_type INTO daily_type
          FROM ontology.source_mention_daily link
          JOIN core.infostock_daily_relations relation
            ON relation.daily_relation_id = link.daily_relation_id
         WHERE link.source_mention_id = checked_id;
        IF daily_type IS NULL THEN
            RAISE EXCEPTION 'daily source mention % has wrong typed link', checked_id;
        ELSIF checked_kind = 'INFOSTOCK_DAILY_DESCRIPTION'
           AND daily_type NOT IN ('DESCRIPTION', 'SECTION_DETAIL') THEN
            RAISE EXCEPTION 'daily description mention % links to %',
                checked_id, daily_type;
        ELSIF checked_kind = 'INFOSTOCK_DAILY_THEME_STOCK'
              AND daily_type <> 'THEME_STOCK' THEN
            RAISE EXCEPTION 'daily stock mention % links to %',
                checked_id, daily_type;
        END IF;
    ELSIF checked_kind = 'NEWS_CATALYST_EVIDENCE' AND NOT EXISTS (
        SELECT 1 FROM ontology.source_mention_news
         WHERE source_mention_id = checked_id
    ) THEN
        RAISE EXCEPTION 'news source mention % has wrong typed link', checked_id;
    ELSIF checked_kind = 'MANUAL_REVIEW' AND NOT EXISTS (
        SELECT 1 FROM ontology.source_mention_manual
         WHERE source_mention_id = checked_id
    ) THEN
        RAISE EXCEPTION 'manual source mention % has wrong typed link', checked_id;
    END IF;

    SELECT transform_version INTO link_transform FROM (
        SELECT transform_version FROM ontology.source_mention_history
         WHERE source_mention_id = checked_id
        UNION ALL
        SELECT transform_version FROM ontology.source_mention_daily
         WHERE source_mention_id = checked_id
        UNION ALL
        SELECT transform_version FROM ontology.source_mention_news
         WHERE source_mention_id = checked_id
        UNION ALL
        SELECT transform_version FROM ontology.source_mention_manual
         WHERE source_mention_id = checked_id
    ) typed_link;
    IF link_transform <> (
        SELECT transform_version FROM ontology.source_mentions
         WHERE source_mention_id = checked_id
    ) THEN
        RAISE EXCEPTION 'source mention % transform version differs from typed link',
            checked_id;
    END IF;
    RETURN NULL;
END
$source_mention_link$;

CREATE CONSTRAINT TRIGGER ck_ontology_source_mention_base_link
AFTER INSERT OR UPDATE ON ontology.source_mentions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION ontology.check_source_mention_link();

CREATE CONSTRAINT TRIGGER ck_ontology_source_mention_history_link
AFTER INSERT OR UPDATE OR DELETE ON ontology.source_mention_history
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION ontology.check_source_mention_link();

CREATE CONSTRAINT TRIGGER ck_ontology_source_mention_daily_link
AFTER INSERT OR UPDATE OR DELETE ON ontology.source_mention_daily
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION ontology.check_source_mention_link();

CREATE CONSTRAINT TRIGGER ck_ontology_source_mention_news_link
AFTER INSERT OR UPDATE OR DELETE ON ontology.source_mention_news
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION ontology.check_source_mention_link();

CREATE CONSTRAINT TRIGGER ck_ontology_source_mention_manual_link
AFTER INSERT OR UPDATE OR DELETE ON ontology.source_mention_manual
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION ontology.check_source_mention_link();

REVOKE ALL ON ontology.source_mentions,
    ontology.source_mention_history,
    ontology.source_mention_daily,
    ontology.source_mention_news,
    ontology.source_mention_manual FROM PUBLIC;

DO $source_mention_boundary$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_infostock_writer') THEN
        GRANT SELECT, INSERT
            ON ontology.source_mentions,
               ontology.source_mention_history,
               ontology.source_mention_daily,
               ontology.source_mention_news,
               ontology.source_mention_manual
            TO dayjaview_infostock_writer;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ontology
            TO dayjaview_infostock_writer;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_api_reader') THEN
        GRANT USAGE ON SCHEMA ontology TO dayjaview_api_reader;
        GRANT SELECT
            ON ontology.source_mentions,
               ontology.source_mention_history,
               ontology.source_mention_daily,
               ontology.source_mention_news,
               ontology.source_mention_manual
            TO dayjaview_api_reader;
    END IF;
END
$source_mention_boundary$;

COMMENT ON TABLE ontology.source_mentions IS
    '현실 사건이 읽은 원천 span의 공통 metadata. 원문은 source 테이블에만 둔다.';
COMMENT ON TABLE ontology.source_mention_daily IS
    'Daily DESCRIPTION·SECTION_DETAIL·THEME_STOCK과 source mention의 typed FK.';

COMMIT;
