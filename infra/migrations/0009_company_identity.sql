BEGIN;

-- 회사 정체성과 alias (회사 온톨로지 단계 2).
--
-- 종목(Instrument)과 회사(Company)를 분리한다. 상장 종목 정본은 계속
-- core.infostock_stocks이며 여기서 새 종목 ID를 만들지 않는다. 한 회사가 여러
-- 종목을 가질 수 있고(보통주·우선주), 이름은 시점에 따라 달라지므로 alias와
-- 회사-종목 연결에 유효기간을 둔다.
--
-- company_id는 외부 코드나 이름을 의미로 인코딩하지 않는 surrogate다.
-- seed_stock_code는 그 회사를 처음 만든 종목코드로 재실행 계보일 뿐 식별자가
-- 아니다.

CREATE TABLE IF NOT EXISTS core.company_entities (
    company_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seed_stock_code character(6) NOT NULL UNIQUE
        CHECK (seed_stock_code ~ '^[0-9A-Z]{6}$'),
    canonical_name text NOT NULL CHECK (btrim(canonical_name) <> ''),
    name_basis text NOT NULL CHECK (
        name_basis IN (
            'KRX_LISTING', 'CURRENT_MEMBERSHIP', 'HISTORICAL_REFERENCE',
            'DAILY_REFERENCE', 'SOURCE_DECLARED', 'OPERATOR_CONFIRMED', 'UNKNOWN'
        )
    ),
    dart_corp_code character(8) CHECK (dart_corp_code ~ '^[0-9]{8}$'),
    master_version text NOT NULL CHECK (btrim(master_version) <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_company_entities_dart_corp_code
    ON core.company_entities (dart_corp_code) WHERE dart_corp_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS core.company_aliases (
    company_alias_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id bigint NOT NULL
        REFERENCES core.company_entities(company_id) ON DELETE RESTRICT,
    alias text NOT NULL CHECK (btrim(alias) <> ''),
    normalized_alias text NOT NULL CHECK (btrim(normalized_alias) <> ''),
    alias_type text NOT NULL
        CHECK (alias_type IN ('CURRENT_NAME', 'PAST_NAME', 'SHARE_CLASS_NAME')),
    validity_basis text NOT NULL CHECK (
        validity_basis IN (
            'KRX_LISTING', 'OBSERVED_MENTION', 'SOURCE_DECLARED', 'OPERATOR_CONFIRMED'
        )
    ),
    source_authority text NOT NULL CHECK (
        source_authority IN (
            'KRX_LISTING', 'CURRENT_MEMBERSHIP', 'HISTORICAL_REFERENCE',
            'DAILY_REFERENCE', 'SOURCE_DECLARED', 'OPERATOR_CONFIRMED'
        )
    ),
    valid_from date,
    valid_to date,
    mention_count integer NOT NULL CHECK (mention_count >= 0),
    master_version text NOT NULL CHECK (btrim(master_version) <> ''),
    recorded_at timestamptz NOT NULL,
    CONSTRAINT uq_company_alias UNIQUE (company_id, normalized_alias, alias_type),
    CONSTRAINT ck_company_alias_interval
        CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from),
    -- 현재 이름은 끝나지 않는다. 끝났다면 PAST_NAME이다.
    CONSTRAINT ck_company_alias_current_open
        CHECK (alias_type <> 'CURRENT_NAME' OR valid_to IS NULL)
);

CREATE INDEX IF NOT EXISTS ix_company_aliases_normalized
    ON core.company_aliases (normalized_alias, valid_from, valid_to);
CREATE INDEX IF NOT EXISTS ix_company_aliases_company
    ON core.company_aliases (company_id);

CREATE TABLE IF NOT EXISTS core.company_instruments (
    company_instrument_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id bigint NOT NULL
        REFERENCES core.company_entities(company_id) ON DELETE RESTRICT,
    stock_id bigint NOT NULL
        REFERENCES core.infostock_stocks(stock_id) ON DELETE RESTRICT,
    share_class text NOT NULL
        CHECK (share_class IN ('COMMON', 'PREFERRED', 'UNKNOWN')),
    link_basis text NOT NULL CHECK (
        link_basis IN (
            'STOCK_CODE', 'SHARE_CLASS_NAME_AND_CODE', 'SOURCE_DECLARED',
            'OPERATOR_CONFIRMED'
        )
    ),
    valid_from date,
    valid_to date,
    master_version text NOT NULL CHECK (btrim(master_version) <> ''),
    recorded_at timestamptz NOT NULL,
    CONSTRAINT uq_company_instrument UNIQUE (company_id, stock_id),
    CONSTRAINT ck_company_instrument_interval
        CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from),
    -- 한 종목은 한 시점에 한 회사에만 속한다.
    CONSTRAINT ex_company_instrument_no_overlap EXCLUDE USING gist (
        stock_id WITH =,
        daterange(valid_from, valid_to, '[]') WITH &&
    )
);

CREATE INDEX IF NOT EXISTS ix_company_instruments_stock
    ON core.company_instruments (stock_id);

CREATE TABLE IF NOT EXISTS core.company_revisions (
    company_revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id bigint NOT NULL
        REFERENCES core.company_entities(company_id) ON DELETE RESTRICT,
    revision_no integer NOT NULL CHECK (revision_no > 0),
    change_type text NOT NULL CHECK (
        change_type IN (
            'CREATED', 'NAME_CHANGED', 'INSTRUMENT_LINKED', 'DELISTED',
            'MERGED', 'SPLIT'
        )
    ),
    effective_on date,
    previous_value text,
    new_value text,
    evidence_basis text NOT NULL CHECK (
        evidence_basis IN ('OBSERVED_MENTION', 'SOURCE_DECLARED', 'OPERATOR_CONFIRMED')
    ),
    master_version text NOT NULL CHECK (btrim(master_version) <> ''),
    recorded_at timestamptz NOT NULL,
    content_hash character(64) NOT NULL
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT uq_company_revision_no UNIQUE (company_id, revision_no),
    CONSTRAINT uq_company_revision_content UNIQUE (company_id, content_hash)
);

CREATE INDEX IF NOT EXISTS ix_company_revisions_company
    ON core.company_revisions (company_id, revision_no);

CREATE TABLE IF NOT EXISTS core.company_resolution_reviews (
    company_review_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_kind text NOT NULL
        CHECK (source_kind IN ('HISTORY_LEADER', 'HISTORY_MEMBER')),
    source_name text NOT NULL CHECK (btrim(source_name) <> ''),
    normalized_name text NOT NULL CHECK (btrim(normalized_name) <> ''),
    reason text NOT NULL CHECK (
        reason IN (
            'SOURCE_CODE_MISSING', 'CODE_INVALID', 'AMBIGUOUS_ALIAS', 'NO_CANDIDATE'
        )
    ),
    mention_count integer NOT NULL CHECK (mention_count > 0),
    first_event_date date,
    last_event_date date,
    status text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RESOLVED', 'REJECTED')),
    resolved_company_id bigint
        REFERENCES core.company_entities(company_id) ON DELETE RESTRICT,
    resolved_at timestamptz,
    master_version text NOT NULL CHECK (btrim(master_version) <> ''),
    recorded_at timestamptz NOT NULL,
    CONSTRAINT uq_company_review UNIQUE (master_version, source_kind, normalized_name),
    CONSTRAINT ck_company_review_resolution CHECK (
        (status = 'RESOLVED') = (resolved_company_id IS NOT NULL AND resolved_at IS NOT NULL)
    ),
    CONSTRAINT ck_company_review_event_order
        CHECK (first_event_date IS NULL OR last_event_date IS NULL
               OR last_event_date >= first_event_date)
);

REVOKE ALL ON core.company_entities, core.company_aliases,
    core.company_instruments, core.company_revisions,
    core.company_resolution_reviews FROM PUBLIC;

DO $company_identity_boundary$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_infostock_writer') THEN
        GRANT SELECT, INSERT, UPDATE
            ON core.company_entities, core.company_aliases,
               core.company_instruments, core.company_revisions,
               core.company_resolution_reviews
            TO dayjaview_infostock_writer;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core
            TO dayjaview_infostock_writer;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_api_reader') THEN
        GRANT USAGE ON SCHEMA core TO dayjaview_api_reader;
        GRANT SELECT
            ON core.company_entities, core.company_aliases,
               core.company_instruments, core.company_revisions,
               core.company_resolution_reviews
            TO dayjaview_api_reader;
    END IF;
END
$company_identity_boundary$;

COMMENT ON TABLE core.company_entities IS
    '법인·발행사 정체성. 상장 종목 정본은 core.infostock_stocks에 그대로 둔다.';
COMMENT ON COLUMN core.company_entities.name_basis IS
    'UNKNOWN은 어느 원천도 이름을 주지 않아 종목코드를 대표 이름에 둔 상태다.';
COMMENT ON COLUMN core.company_entities.seed_stock_code IS
    '이 회사를 처음 만든 종목코드. 재실행 계보이며 식별자가 아니다.';
COMMENT ON TABLE core.company_aliases IS
    '현재·과거 사명과 관측 유효기간. 기간 밖 이름으로는 자동 연결하지 않는다.';
COMMENT ON COLUMN core.company_aliases.validity_basis IS
    'KRX_LISTING은 그 이름으로 거래된 거래일 구간이다. OBSERVED_MENTION은 원천에 '
    '그 이름이 등장한 사건일 구간이며 공식 유효기간이 아니다.';
COMMENT ON TABLE core.company_instruments IS
    '회사와 상장 종목의 유효기간 관계. 보통주·우선주가 한 회사로 모인다.';
COMMENT ON COLUMN core.company_instruments.valid_from IS
    '언제부터 이 회사의 종목이었는지 원천이 없으면 비운다. 관측 시작을 넣지 않는다.';
COMMENT ON TABLE core.company_revisions IS
    '사명 변경·종목 연결·상장폐지·합병·분할 기록. append만 하고 덮어쓰지 않는다.';
COMMENT ON TABLE core.company_resolution_reviews IS
    '코드로 연결되지 않은 원천 종목 참조. 임의 연결하지 않고 검수 대상으로 남긴다.';

COMMIT;
