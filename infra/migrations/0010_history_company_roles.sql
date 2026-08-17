BEGIN;

-- 테마 history 회사 mention·역할 (회사 온톨로지 단계 3).
--
-- 실제 catalyst와 중복 제거는 단계 4에서 만든다. 여기서는 불변 history revision을
-- 기준으로 본문 mention, 구조화 leader, 구조화 membership을 분리하고 역할 규칙
-- 버전별 결과를 append한다. 원문은 core에만 두고 ontology에는 오프셋과 hash만 둔다.

CREATE TABLE IF NOT EXISTS ontology.history_company_role_revisions (
    role_revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    history_id bigint NOT NULL
        REFERENCES core.infostock_theme_history(history_id) ON DELETE RESTRICT,
    revision_no integer NOT NULL CHECK (revision_no > 0),
    company_master_version text NOT NULL
        CHECK (btrim(company_master_version) <> ''),
    role_transform_version text NOT NULL
        CHECK (btrim(role_transform_version) <> ''),
    history_content_hash character(64) NOT NULL
        CHECK (history_content_hash ~ '^[0-9a-f]{64}$'),
    output_hash character(64) NOT NULL
        CHECK (output_hash ~ '^[0-9a-f]{64}$'),
    labeled_at timestamptz NOT NULL,
    CONSTRAINT uq_history_company_role_revision_no
        UNIQUE (history_id, revision_no),
    CONSTRAINT uq_history_company_role_version
        UNIQUE (history_id, company_master_version, role_transform_version)
);

CREATE INDEX IF NOT EXISTS ix_history_company_role_revision_history
    ON ontology.history_company_role_revisions (history_id, revision_no DESC);

CREATE TABLE IF NOT EXISTS ontology.history_company_mentions (
    company_mention_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    role_revision_id bigint NOT NULL
        REFERENCES ontology.history_company_role_revisions(role_revision_id)
        ON DELETE RESTRICT,
    source_order integer NOT NULL CHECK (source_order >= 0),
    mention_kind text NOT NULL
        CHECK (mention_kind IN ('BODY', 'LEADER_LIST', 'MEMBERSHIP')),
    history_leader_id bigint
        REFERENCES core.infostock_theme_history_leaders(history_leader_id)
        ON DELETE RESTRICT,
    history_membership_id bigint
        REFERENCES core.infostock_theme_history_memberships(history_membership_id)
        ON DELETE RESTRICT,
    company_id bigint
        REFERENCES core.company_entities(company_id) ON DELETE RESTRICT,
    resolution_status text NOT NULL CHECK (
        resolution_status IN (
            'RESOLVED', 'SOURCE_CODE_MISSING', 'CODE_INVALID',
            'UNKNOWN_STOCK_CODE', 'AMBIGUOUS_ALIAS', 'OUT_OF_VALIDITY'
        )
    ),
    resolution_basis text NOT NULL
        CHECK (resolution_basis IN ('STOCK_CODE', 'EXACT_ALIAS', 'NONE')),
    suggested_role text CHECK (suggested_role IN ('LEADER', 'RELATED')),
    mention_start integer NOT NULL CHECK (mention_start >= 0),
    mention_end integer NOT NULL,
    evidence_source_hash character(64) NOT NULL
        CHECK (evidence_source_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT uq_history_company_mention_order
        UNIQUE (role_revision_id, source_order),
    -- role fact가 (mention, company)를 복합 FK로 참조해 미해결 mention에 역할이
    -- 붙는 일을 DB에서도 막는다.
    CONSTRAINT uq_history_company_mention_company
        UNIQUE (company_mention_id, company_id),
    CONSTRAINT ck_history_company_mention_span
        CHECK (mention_end > mention_start),
    CONSTRAINT ck_history_company_mention_source CHECK (
        (mention_kind = 'BODY'
            AND history_leader_id IS NULL
            AND history_membership_id IS NULL
            AND suggested_role IS NULL)
        OR (mention_kind = 'LEADER_LIST'
            AND history_leader_id IS NOT NULL
            AND history_membership_id IS NULL
            AND suggested_role = 'LEADER')
        OR (mention_kind = 'MEMBERSHIP'
            AND history_leader_id IS NULL
            AND history_membership_id IS NOT NULL
            AND suggested_role = 'RELATED')
    ),
    CONSTRAINT ck_history_company_mention_resolution CHECK (
        (resolution_status = 'RESOLVED'
            AND company_id IS NOT NULL
            AND resolution_basis IN ('STOCK_CODE', 'EXACT_ALIAS'))
        OR (resolution_status <> 'RESOLVED' AND company_id IS NULL)
    ),
    CONSTRAINT ck_history_company_mention_resolution_basis CHECK (
        (resolution_status IN ('SOURCE_CODE_MISSING', 'CODE_INVALID')
            AND resolution_basis = 'NONE')
        OR (resolution_status = 'UNKNOWN_STOCK_CODE'
            AND resolution_basis IN ('STOCK_CODE', 'EXACT_ALIAS'))
        OR (resolution_status IN ('AMBIGUOUS_ALIAS', 'OUT_OF_VALIDITY')
            AND resolution_basis = 'EXACT_ALIAS')
        OR resolution_status = 'RESOLVED'
    )
);

CREATE INDEX IF NOT EXISTS ix_history_company_mentions_company
    ON ontology.history_company_mentions (company_id, role_revision_id)
    WHERE company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_history_company_mentions_resolution
    ON ontology.history_company_mentions (resolution_status)
    WHERE resolution_status <> 'RESOLVED';
CREATE INDEX IF NOT EXISTS ix_history_company_mentions_leader
    ON ontology.history_company_mentions (history_leader_id)
    WHERE history_leader_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_history_company_mentions_membership
    ON ontology.history_company_mentions (history_membership_id)
    WHERE history_membership_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS ontology.history_company_roles (
    company_role_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_mention_id bigint NOT NULL,
    company_id bigint NOT NULL,
    source_order integer NOT NULL CHECK (source_order >= 0),
    role text NOT NULL CHECK (
        role IN (
            'ACTOR', 'ISSUER', 'CONTRACTOR', 'COUNTERPARTY', 'TARGET',
            'BENEFICIARY', 'ADVERSELY_AFFECTED', 'LEADER', 'RELATED'
        )
    ),
    extraction_basis text NOT NULL CHECK (
        extraction_basis IN (
            'BODY_RULE', 'STRUCTURED_LEADER', 'STRUCTURED_MEMBERSHIP'
        )
    ),
    evidence_start integer NOT NULL CHECK (evidence_start >= 0),
    evidence_end integer NOT NULL,
    CONSTRAINT fk_history_company_role_resolved_mention
        FOREIGN KEY (company_mention_id, company_id)
        REFERENCES ontology.history_company_mentions(company_mention_id, company_id)
        ON DELETE RESTRICT,
    CONSTRAINT uq_history_company_role_order
        UNIQUE (company_mention_id, source_order),
    CONSTRAINT uq_history_company_role_value
        UNIQUE (company_mention_id, role),
    CONSTRAINT ck_history_company_role_span
        CHECK (evidence_end > evidence_start),
    CONSTRAINT ck_history_company_role_basis CHECK (
        (extraction_basis = 'BODY_RULE'
            AND role NOT IN ('LEADER', 'RELATED'))
        OR (extraction_basis = 'STRUCTURED_LEADER' AND role = 'LEADER')
        OR (extraction_basis = 'STRUCTURED_MEMBERSHIP' AND role = 'RELATED')
    )
);

CREATE INDEX IF NOT EXISTS ix_history_company_roles_company_role
    ON ontology.history_company_roles (company_id, role, company_mention_id);

REVOKE ALL ON ontology.history_company_role_revisions,
    ontology.history_company_mentions, ontology.history_company_roles FROM PUBLIC;

DO $history_company_role_boundary$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_infostock_writer') THEN
        GRANT SELECT, INSERT
            ON ontology.history_company_role_revisions,
               ontology.history_company_mentions,
               ontology.history_company_roles
            TO dayjaview_infostock_writer;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ontology
            TO dayjaview_infostock_writer;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_api_reader') THEN
        GRANT USAGE ON SCHEMA ontology TO dayjaview_api_reader;
        GRANT SELECT
            ON ontology.history_company_role_revisions,
               ontology.history_company_mentions,
               ontology.history_company_roles
            TO dayjaview_api_reader;
    END IF;
END
$history_company_role_boundary$;

COMMENT ON TABLE ontology.history_company_role_revisions IS
    'history revision별 회사 mention·역할 결과. master·transform 버전별 append다.';
COMMENT ON TABLE ontology.history_company_mentions IS
    '본문, 주도주 목록, 관련주 원천을 분리한 mention. 미해결 이름도 검수용으로 남긴다.';
COMMENT ON COLUMN ontology.history_company_mentions.mention_start IS
    'BODY는 history.raw_text, 구조화 mention은 source_stock_name 기준 문자 오프셋이다.';
COMMENT ON TABLE ontology.history_company_roles IS
    '해결된 회사 mention에만 붙는 역할 fact. 모든 fact에 회사와 evidence span이 있다.';

COMMIT;
