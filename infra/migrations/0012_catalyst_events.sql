BEGIN;

-- 현실 사건, 장기 프로젝트, 일반 참여자, 근거 fact (회사 온톨로지 단계 4).
-- 원문은 core에만 둔다. ontology에는 typed source mention과 문자 오프셋,
-- 변환 버전, hash를 저장한다. 분류 결과는 revision append 방식이다.

CREATE TABLE IF NOT EXISTS ontology.geographies (
    geography_code text PRIMARY KEY CHECK (geography_code ~ '^[A-Z]{2}$'),
    name_ko text NOT NULL CHECK (btrim(name_ko) <> ''),
    geography_kind text NOT NULL
        CHECK (geography_kind IN ('COUNTRY', 'SUPRANATIONAL', 'REGION')),
    parent_geography_code text
        REFERENCES ontology.geographies(geography_code) ON DELETE RESTRICT,
    CONSTRAINT ck_ontology_geography_parent
        CHECK (parent_geography_code IS NULL OR parent_geography_code <> geography_code)
);

INSERT INTO ontology.geographies (geography_code, name_ko, geography_kind) VALUES
    ('AE', '아랍에미리트', 'COUNTRY'),
    ('AU', '호주', 'COUNTRY'),
    ('CA', '캐나다', 'COUNTRY'),
    ('CN', '중국', 'COUNTRY'),
    ('DE', '독일', 'COUNTRY'),
    ('EU', '유럽연합', 'SUPRANATIONAL'),
    ('FR', '프랑스', 'COUNTRY'),
    ('GB', '영국', 'COUNTRY'),
    ('IN', '인도', 'COUNTRY'),
    ('JP', '일본', 'COUNTRY'),
    ('KR', '대한민국', 'COUNTRY'),
    ('PL', '폴란드', 'COUNTRY'),
    ('RU', '러시아', 'COUNTRY'),
    ('SA', '사우디아라비아', 'COUNTRY'),
    ('UA', '우크라이나', 'COUNTRY'),
    ('US', '미국', 'COUNTRY'),
    ('VN', '베트남', 'COUNTRY')
ON CONFLICT (geography_code) DO NOTHING;

CREATE TABLE IF NOT EXISTS ontology.actor_entities (
    actor_id text PRIMARY KEY CHECK (actor_id ~ '^actor_[0-9a-f]{24}$'),
    actor_kind text NOT NULL CHECK (
        actor_kind IN (
            'COMPANY', 'GOVERNMENT', 'PUBLIC_INSTITUTION', 'PERSON',
            'INTERNATIONAL_ORGANIZATION', 'COUNTRY', 'OTHER'
        )
    ),
    canonical_name text NOT NULL CHECK (btrim(canonical_name) <> ''),
    normalized_name text NOT NULL CHECK (btrim(normalized_name) <> ''),
    geography_code text
        REFERENCES ontology.geographies(geography_code) ON DELETE RESTRICT,
    company_id bigint
        REFERENCES core.company_entities(company_id) ON DELETE RESTRICT,
    identity_hash character(64) NOT NULL UNIQUE
        CHECK (identity_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_ontology_actor_company CHECK (
        (actor_kind = 'COMPANY' AND company_id IS NOT NULL)
        OR (actor_kind <> 'COMPANY' AND company_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_ontology_actor_geography
    ON ontology.actor_entities (geography_code, actor_kind);

CREATE TABLE IF NOT EXISTS ontology.actor_aliases (
    actor_alias_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id text NOT NULL
        REFERENCES ontology.actor_entities(actor_id) ON DELETE RESTRICT,
    alias text NOT NULL CHECK (btrim(alias) <> ''),
    normalized_alias text NOT NULL CHECK (btrim(normalized_alias) <> ''),
    valid_from date,
    valid_to date,
    source_kind text NOT NULL CHECK (
        source_kind IN ('CONTROLLED', 'SOURCE_EXACT', 'HUMAN_CONFIRMED')
    ),
    CONSTRAINT ck_ontology_actor_alias_dates
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
    CONSTRAINT uq_ontology_actor_alias_window
        UNIQUE (actor_id, normalized_alias, valid_from, valid_to)
);

CREATE INDEX IF NOT EXISTS ix_ontology_actor_alias_lookup
    ON ontology.actor_aliases (normalized_alias, valid_from, valid_to);

CREATE TABLE IF NOT EXISTS ontology.projects (
    project_id text PRIMARY KEY CHECK (project_id ~ '^project_[0-9a-f]{24}$'),
    project_fingerprint character(64) NOT NULL UNIQUE
        CHECK (project_fingerprint ~ '^[0-9a-f]{64}$'),
    canonical_name text NOT NULL CHECK (btrim(canonical_name) <> ''),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ontology.project_aliases (
    project_alias_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id text NOT NULL
        REFERENCES ontology.projects(project_id) ON DELETE RESTRICT,
    alias text NOT NULL CHECK (btrim(alias) <> ''),
    normalized_alias text NOT NULL CHECK (btrim(normalized_alias) <> ''),
    source_mention_id bigint
        REFERENCES ontology.source_mentions(source_mention_id) ON DELETE RESTRICT,
    CONSTRAINT uq_ontology_project_alias_source
        UNIQUE (project_id, normalized_alias, source_mention_id)
);

CREATE INDEX IF NOT EXISTS ix_ontology_project_alias_lookup
    ON ontology.project_aliases (normalized_alias, project_id);

CREATE TABLE IF NOT EXISTS ontology.catalysts (
    catalyst_id text PRIMARY KEY CHECK (catalyst_id ~ '^catalyst_[0-9a-f]{24}$'),
    dedup_key character(64) NOT NULL
        CHECK (dedup_key ~ '^[0-9a-f]{64}$'),
    dedup_policy_version text NOT NULL CHECK (btrim(dedup_policy_version) <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_ontology_catalyst_dedup
        UNIQUE (dedup_key, dedup_policy_version)
);

CREATE TABLE IF NOT EXISTS ontology.catalyst_revisions (
    catalyst_revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    catalyst_id text NOT NULL
        REFERENCES ontology.catalysts(catalyst_id) ON DELETE RESTRICT,
    revision_no integer NOT NULL CHECK (revision_no > 0),
    primary_source_mention_id bigint NOT NULL
        REFERENCES ontology.source_mentions(source_mention_id) ON DELETE RESTRICT,
    occurred_on date,
    known_on date,
    vocabulary_version text NOT NULL
        REFERENCES ontology.catalyst_vocabularies(vocabulary_version) ON DELETE RESTRICT,
    primary_type_id text,
    type_ids text[] NOT NULL DEFAULT '{}',
    event_stage text NOT NULL CHECK (
        event_stage IN (
            'RUMOR', 'REVIEW', 'DISCUSSION', 'BID', 'SHORTLIST',
            'PREFERRED_BIDDER', 'SIGNED', 'EXECUTING', 'COMPLETED',
            'DELAYED', 'CANCELLED', 'UNSPECIFIED'
        )
    ),
    stage_keyword text,
    stage_evidence_start integer,
    stage_evidence_end integer,
    certainty text NOT NULL
        CHECK (certainty IN ('CONFIRMED', 'ANTICIPATION', 'UNSPECIFIED')),
    novelty_type text NOT NULL
        CHECK (novelty_type IN ('NEW', 'REEMERGENCE', 'UNSPECIFIED')),
    action text,
    object_text text,
    project_id text
        REFERENCES ontology.projects(project_id) ON DELETE RESTRICT,
    officiality text NOT NULL
        CHECK (officiality IN ('OFFICIAL', 'REPORTED', 'UNSPECIFIED')),
    continuation boolean NOT NULL,
    classification_transform_version text NOT NULL
        CHECK (btrim(classification_transform_version) <> ''),
    event_structure_transform_version text NOT NULL
        CHECK (btrim(event_structure_transform_version) <> ''),
    company_master_version text NOT NULL CHECK (btrim(company_master_version) <> ''),
    dedup_policy_version text NOT NULL CHECK (btrim(dedup_policy_version) <> ''),
    dataset_hash character(64) NOT NULL
        CHECK (dataset_hash ~ '^[0-9a-f]{64}$'),
    content_hash character(64) NOT NULL
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    output_hash character(64) NOT NULL
        CHECK (output_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL,
    CONSTRAINT uq_ontology_catalyst_revision_no
        UNIQUE (catalyst_id, revision_no),
    CONSTRAINT uq_ontology_catalyst_revision_output
        UNIQUE (catalyst_id, output_hash),
    CONSTRAINT ck_ontology_catalyst_primary_matches_first CHECK (
        (cardinality(type_ids) = 0 AND primary_type_id IS NULL)
        OR (cardinality(type_ids) > 0 AND primary_type_id = type_ids[1])
    ),
    CONSTRAINT fk_ontology_catalyst_primary_type
        FOREIGN KEY (vocabulary_version, primary_type_id)
        REFERENCES ontology.catalyst_types(vocabulary_version, type_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_ontology_catalyst_stage_evidence CHECK (
        (event_stage = 'UNSPECIFIED'
            AND stage_keyword IS NULL
            AND stage_evidence_start IS NULL
            AND stage_evidence_end IS NULL)
        OR (event_stage <> 'UNSPECIFIED'
            AND stage_keyword IS NOT NULL
            AND btrim(stage_keyword) <> ''
            AND stage_evidence_start IS NOT NULL
            AND stage_evidence_start >= 0
            AND stage_evidence_end IS NOT NULL
            AND stage_evidence_end > stage_evidence_start)
    )
);

CREATE INDEX IF NOT EXISTS ix_ontology_catalyst_revision_date_stage
    ON ontology.catalyst_revisions (occurred_on, event_stage, catalyst_id);
CREATE INDEX IF NOT EXISTS ix_ontology_catalyst_revision_project
    ON ontology.catalyst_revisions (project_id, occurred_on, event_stage)
    WHERE project_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_ontology_catalyst_revision_types
    ON ontology.catalyst_revisions USING gin (type_ids);

CREATE TABLE IF NOT EXISTS ontology.catalyst_source_mentions (
    catalyst_revision_id bigint NOT NULL
        REFERENCES ontology.catalyst_revisions(catalyst_revision_id) ON DELETE RESTRICT,
    source_mention_id bigint NOT NULL
        REFERENCES ontology.source_mentions(source_mention_id) ON DELETE RESTRICT,
    source_order integer NOT NULL CHECK (source_order >= 0),
    PRIMARY KEY (catalyst_revision_id, source_mention_id),
    CONSTRAINT uq_ontology_catalyst_source_order
        UNIQUE (catalyst_revision_id, source_order)
);

CREATE INDEX IF NOT EXISTS ix_ontology_catalyst_source_reverse
    ON ontology.catalyst_source_mentions (source_mention_id, catalyst_revision_id);

CREATE TABLE IF NOT EXISTS ontology.catalyst_revision_spans (
    catalyst_span_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    catalyst_revision_id bigint NOT NULL,
    source_mention_id bigint NOT NULL,
    source_order integer NOT NULL CHECK (source_order >= 0),
    field text NOT NULL CHECK (
        field IN ('catalyst_type', 'direction', 'certainty', 'continuation')
    ),
    value text NOT NULL CHECK (btrim(value) <> ''),
    keyword text NOT NULL CHECK (btrim(keyword) <> ''),
    start_offset integer NOT NULL CHECK (start_offset >= 0),
    end_offset integer NOT NULL,
    CONSTRAINT fk_ontology_catalyst_span_source
        FOREIGN KEY (catalyst_revision_id, source_mention_id)
        REFERENCES ontology.catalyst_source_mentions(
            catalyst_revision_id, source_mention_id
        ) ON DELETE RESTRICT,
    CONSTRAINT uq_ontology_catalyst_span_order
        UNIQUE (catalyst_revision_id, source_mention_id, source_order),
    CONSTRAINT ck_ontology_catalyst_span_range
        CHECK (end_offset > start_offset)
);

CREATE TABLE IF NOT EXISTS ontology.catalyst_company_roles (
    catalyst_company_role_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    catalyst_revision_id bigint NOT NULL,
    source_mention_id bigint NOT NULL,
    company_id bigint NOT NULL
        REFERENCES core.company_entities(company_id) ON DELETE RESTRICT,
    role text NOT NULL CHECK (
        role IN (
            'ACTOR', 'ISSUER', 'CONTRACTOR', 'COUNTERPARTY', 'TARGET',
            'BENEFICIARY', 'ADVERSELY_AFFECTED'
        )
    ),
    impact text NOT NULL
        CHECK (impact IN ('POSITIVE', 'NEGATIVE', 'MIXED', 'UNKNOWN')),
    evidence_start integer NOT NULL CHECK (evidence_start >= 0),
    evidence_end integer NOT NULL,
    CONSTRAINT fk_ontology_catalyst_company_role_source
        FOREIGN KEY (catalyst_revision_id, source_mention_id)
        REFERENCES ontology.catalyst_source_mentions(
            catalyst_revision_id, source_mention_id
        ) ON DELETE RESTRICT,
    CONSTRAINT uq_ontology_catalyst_company_role_fact UNIQUE (
        catalyst_revision_id, source_mention_id, company_id, role,
        evidence_start, evidence_end
    ),
    CONSTRAINT ck_ontology_catalyst_company_role_span
        CHECK (evidence_end > evidence_start)
);

CREATE INDEX IF NOT EXISTS ix_ontology_catalyst_company_query
    ON ontology.catalyst_company_roles (company_id, role, catalyst_revision_id);

CREATE TABLE IF NOT EXISTS ontology.catalyst_participants (
    catalyst_participant_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    catalyst_revision_id bigint NOT NULL,
    source_mention_id bigint NOT NULL,
    actor_id text NOT NULL
        REFERENCES ontology.actor_entities(actor_id) ON DELETE RESTRICT,
    participant_role text NOT NULL CHECK (
        participant_role IN (
            'ACTOR', 'COUNTERPARTY', 'ANNOUNCER', 'REGULATOR',
            'TARGET', 'LOCATION', 'PARTICIPANT'
        )
    ),
    evidence_start integer NOT NULL CHECK (evidence_start >= 0),
    evidence_end integer NOT NULL,
    CONSTRAINT fk_ontology_catalyst_participant_source
        FOREIGN KEY (catalyst_revision_id, source_mention_id)
        REFERENCES ontology.catalyst_source_mentions(
            catalyst_revision_id, source_mention_id
        ) ON DELETE RESTRICT,
    CONSTRAINT uq_ontology_catalyst_participant_fact UNIQUE (
        catalyst_revision_id, source_mention_id, actor_id, participant_role,
        evidence_start, evidence_end
    ),
    CONSTRAINT ck_ontology_catalyst_participant_span
        CHECK (evidence_end > evidence_start)
);

CREATE INDEX IF NOT EXISTS ix_ontology_catalyst_participant_actor
    ON ontology.catalyst_participants (actor_id, participant_role, catalyst_revision_id);

CREATE TABLE IF NOT EXISTS ontology.catalyst_geographies (
    catalyst_revision_id bigint NOT NULL
        REFERENCES ontology.catalyst_revisions(catalyst_revision_id) ON DELETE RESTRICT,
    geography_code text NOT NULL
        REFERENCES ontology.geographies(geography_code) ON DELETE RESTRICT,
    PRIMARY KEY (catalyst_revision_id, geography_code)
);

CREATE INDEX IF NOT EXISTS ix_ontology_catalyst_geography_query
    ON ontology.catalyst_geographies (geography_code, catalyst_revision_id);

CREATE TABLE IF NOT EXISTS ontology.catalyst_values (
    catalyst_value_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    catalyst_revision_id bigint NOT NULL,
    source_mention_id bigint NOT NULL,
    fact_type text NOT NULL CHECK (
        fact_type IN (
            'CONTRACT_VALUE', 'INVESTMENT_VALUE', 'CAPACITY',
            'QUANTITY', 'STAKE_PERCENT'
        )
    ),
    reported_value text NOT NULL CHECK (btrim(reported_value) <> ''),
    normalized_value numeric NOT NULL,
    unit text NOT NULL CHECK (btrim(unit) <> ''),
    currency character(3) CHECK (currency ~ '^[A-Z]{3}$'),
    value_basis text NOT NULL CHECK (
        value_basis IN (
            'EXACT', 'ESTIMATE', 'UP_TO', 'LOWER_BOUND', 'RANGE',
            'TOTAL_PROJECT', 'COMPANY_SHARE'
        )
    ),
    eligible_for_sum boolean NOT NULL,
    effective_on date,
    evidence_start integer NOT NULL CHECK (evidence_start >= 0),
    evidence_end integer NOT NULL,
    CONSTRAINT fk_ontology_catalyst_value_source
        FOREIGN KEY (catalyst_revision_id, source_mention_id)
        REFERENCES ontology.catalyst_source_mentions(
            catalyst_revision_id, source_mention_id
        ) ON DELETE RESTRICT,
    CONSTRAINT uq_ontology_catalyst_value_fact UNIQUE (
        catalyst_revision_id, source_mention_id, fact_type, normalized_value,
        unit, evidence_start, evidence_end
    ),
    CONSTRAINT ck_ontology_catalyst_value_span
        CHECK (evidence_end > evidence_start)
);

CREATE INDEX IF NOT EXISTS ix_ontology_catalyst_value_query
    ON ontology.catalyst_values (
        fact_type, currency, eligible_for_sum, catalyst_revision_id
    );

CREATE TABLE IF NOT EXISTS ontology.theme_reactions (
    reaction_id text PRIMARY KEY CHECK (reaction_id ~ '^reaction_[0-9a-f]{24}$'),
    created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS ontology.theme_reaction_revisions (
    reaction_revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reaction_id text NOT NULL
        REFERENCES ontology.theme_reactions(reaction_id) ON DELETE RESTRICT,
    revision_no integer NOT NULL CHECK (revision_no > 0),
    history_id bigint NOT NULL
        REFERENCES core.infostock_theme_history(history_id) ON DELETE RESTRICT,
    occurred_on date,
    direction text NOT NULL CHECK (direction IN ('UP', 'DOWN', 'MIXED', 'UNKNOWN')),
    transform_version text NOT NULL CHECK (btrim(transform_version) <> ''),
    output_hash character(64) NOT NULL
        CHECK (output_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL,
    CONSTRAINT uq_ontology_theme_reaction_revision_no
        UNIQUE (reaction_id, revision_no),
    CONSTRAINT uq_ontology_theme_reaction_source_version
        UNIQUE (reaction_id, history_id, transform_version)
);

CREATE TABLE IF NOT EXISTS ontology.catalyst_theme_reactions (
    catalyst_revision_id bigint NOT NULL
        REFERENCES ontology.catalyst_revisions(catalyst_revision_id) ON DELETE RESTRICT,
    reaction_revision_id bigint NOT NULL
        REFERENCES ontology.theme_reaction_revisions(reaction_revision_id)
        ON DELETE RESTRICT,
    PRIMARY KEY (catalyst_revision_id, reaction_revision_id)
);

CREATE INDEX IF NOT EXISTS ix_ontology_theme_reaction_reverse
    ON ontology.catalyst_theme_reactions (
        reaction_revision_id, catalyst_revision_id
    );

CREATE TABLE IF NOT EXISTS ontology.theme_reaction_company_roles (
    reaction_revision_id bigint NOT NULL
        REFERENCES ontology.theme_reaction_revisions(reaction_revision_id)
        ON DELETE RESTRICT,
    company_id bigint NOT NULL
        REFERENCES core.company_entities(company_id) ON DELETE RESTRICT,
    role text NOT NULL CHECK (role IN ('LEADER', 'RELATED')),
    PRIMARY KEY (reaction_revision_id, company_id, role)
);

CREATE INDEX IF NOT EXISTS ix_ontology_theme_reaction_company
    ON ontology.theme_reaction_company_roles (
        company_id, role, reaction_revision_id
    );

CREATE TABLE IF NOT EXISTS ontology.catalyst_relations (
    relation_id text PRIMARY KEY CHECK (relation_id ~ '^relation_[0-9a-f]{24}$'),
    from_catalyst_id text NOT NULL
        REFERENCES ontology.catalysts(catalyst_id) ON DELETE RESTRICT,
    to_catalyst_id text NOT NULL
        REFERENCES ontology.catalysts(catalyst_id) ON DELETE RESTRICT,
    relation_type text NOT NULL
        CHECK (relation_type IN ('ADVANCES', 'POSSIBLE_DUPLICATE')),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    dedup_policy_version text NOT NULL CHECK (btrim(dedup_policy_version) <> ''),
    review_status text NOT NULL DEFAULT 'AI_DRAFT'
        CHECK (review_status IN ('AI_DRAFT', 'AI_CROSS_CHECKED', 'HUMAN_CONFIRMED')),
    created_at timestamptz NOT NULL,
    CONSTRAINT ck_ontology_catalyst_relation_distinct
        CHECK (from_catalyst_id <> to_catalyst_id),
    CONSTRAINT uq_ontology_catalyst_relation_fact UNIQUE (
        from_catalyst_id, to_catalyst_id, relation_type, dedup_policy_version
    )
);

CREATE INDEX IF NOT EXISTS ix_ontology_catalyst_relation_from
    ON ontology.catalyst_relations (from_catalyst_id, relation_type);
CREATE INDEX IF NOT EXISTS ix_ontology_catalyst_relation_to
    ON ontology.catalyst_relations (to_catalyst_id, relation_type);

CREATE TABLE IF NOT EXISTS ontology.artifacts (
    artifact_id text PRIMARY KEY CHECK (artifact_id ~ '^artifact_[0-9a-f]{24}$'),
    artifact_hash character(64) NOT NULL UNIQUE
        CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
    dataset_hashes text[] NOT NULL,
    company_master_version text NOT NULL CHECK (btrim(company_master_version) <> ''),
    vocabulary_version text NOT NULL
        REFERENCES ontology.catalyst_vocabularies(vocabulary_version) ON DELETE RESTRICT,
    classification_transform_version text NOT NULL
        CHECK (btrim(classification_transform_version) <> ''),
    event_structure_transform_version text NOT NULL
        CHECK (btrim(event_structure_transform_version) <> ''),
    dedup_policy_version text NOT NULL CHECK (btrim(dedup_policy_version) <> ''),
    query_contract_version text NOT NULL CHECK (btrim(query_contract_version) <> ''),
    code_commit character(40) NOT NULL CHECK (code_commit ~ '^[0-9a-f]{40}$'),
    review_status text NOT NULL DEFAULT 'AI_DRAFT'
        CHECK (review_status IN ('AI_DRAFT', 'AI_CROSS_CHECKED', 'HUMAN_CONFIRMED')),
    generated_at timestamptz NOT NULL,
    CONSTRAINT ck_ontology_artifact_dataset_hashes CHECK (
        cardinality(dataset_hashes) > 0
    )
);

-- primary source mention이 같은 revision의 source bridge에 실제로 포함됐는지
-- transaction 끝에 확인한다. base와 bridge의 INSERT 순서는 강제하지 않는다.
CREATE OR REPLACE FUNCTION ontology.check_catalyst_primary_source_link()
RETURNS trigger
LANGUAGE plpgsql
AS $catalyst_primary_source_link$
DECLARE
    checked_revision_id bigint;
    checked_mention_id bigint;
BEGIN
    checked_revision_id := COALESCE(NEW.catalyst_revision_id, OLD.catalyst_revision_id);
    SELECT primary_source_mention_id INTO checked_mention_id
      FROM ontology.catalyst_revisions
     WHERE catalyst_revision_id = checked_revision_id;
    IF checked_mention_id IS NULL THEN
        RETURN NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM ontology.catalyst_source_mentions
         WHERE catalyst_revision_id = checked_revision_id
           AND source_mention_id = checked_mention_id
    ) THEN
        RAISE EXCEPTION
            'catalyst revision % primary source mention % is not linked',
            checked_revision_id, checked_mention_id;
    END IF;
    RETURN NULL;
END
$catalyst_primary_source_link$;

CREATE CONSTRAINT TRIGGER ck_ontology_catalyst_revision_primary_source
AFTER INSERT OR UPDATE ON ontology.catalyst_revisions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION ontology.check_catalyst_primary_source_link();

CREATE CONSTRAINT TRIGGER ck_ontology_catalyst_source_primary
AFTER INSERT OR UPDATE OR DELETE ON ontology.catalyst_source_mentions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION ontology.check_catalyst_primary_source_link();

CREATE OR REPLACE VIEW ontology.current_catalyst_revisions AS
SELECT DISTINCT ON (revision.catalyst_id) revision.*
  FROM ontology.catalyst_revisions revision
 ORDER BY revision.catalyst_id, revision.revision_no DESC;

REVOKE ALL ON ontology.geographies, ontology.actor_entities,
    ontology.actor_aliases, ontology.projects, ontology.project_aliases,
    ontology.catalysts, ontology.catalyst_revisions,
    ontology.catalyst_source_mentions, ontology.catalyst_revision_spans,
    ontology.catalyst_company_roles, ontology.catalyst_participants,
    ontology.catalyst_geographies, ontology.catalyst_values,
    ontology.theme_reactions, ontology.theme_reaction_revisions,
    ontology.catalyst_theme_reactions,
    ontology.theme_reaction_company_roles, ontology.catalyst_relations,
    ontology.artifacts, ontology.current_catalyst_revisions FROM PUBLIC;

DO $catalyst_event_boundary$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_infostock_writer') THEN
        GRANT SELECT, INSERT ON ontology.geographies, ontology.actor_entities,
            ontology.actor_aliases, ontology.projects, ontology.project_aliases,
            ontology.catalysts, ontology.catalyst_revisions,
            ontology.catalyst_source_mentions, ontology.catalyst_revision_spans,
            ontology.catalyst_company_roles, ontology.catalyst_participants,
            ontology.catalyst_geographies, ontology.catalyst_values,
            ontology.theme_reactions, ontology.theme_reaction_revisions,
            ontology.catalyst_theme_reactions,
            ontology.theme_reaction_company_roles, ontology.catalyst_relations,
            ontology.artifacts TO dayjaview_infostock_writer;
        GRANT SELECT ON ontology.current_catalyst_revisions
            TO dayjaview_infostock_writer;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ontology
            TO dayjaview_infostock_writer;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_api_reader') THEN
        GRANT USAGE ON SCHEMA ontology TO dayjaview_api_reader;
        GRANT SELECT ON ontology.geographies, ontology.actor_entities,
            ontology.actor_aliases, ontology.projects, ontology.project_aliases,
            ontology.catalysts, ontology.catalyst_revisions,
            ontology.catalyst_source_mentions, ontology.catalyst_revision_spans,
            ontology.catalyst_company_roles, ontology.catalyst_participants,
            ontology.catalyst_geographies, ontology.catalyst_values,
            ontology.theme_reactions, ontology.theme_reaction_revisions,
            ontology.catalyst_theme_reactions,
            ontology.theme_reaction_company_roles, ontology.catalyst_relations,
            ontology.artifacts, ontology.current_catalyst_revisions
            TO dayjaview_api_reader;
    END IF;
END
$catalyst_event_boundary$;

COMMENT ON TABLE ontology.catalysts IS
    '중복 보도를 합친 현실 사건의 안정 식별자. 세부 분류는 revision에 append한다.';
COMMENT ON TABLE ontology.catalyst_revisions IS
    '현실 사건의 버전별 구조. 단계가 다른 후속 사건은 같은 행을 덮어쓰지 않는다.';
COMMENT ON TABLE ontology.catalyst_source_mentions IS
    '고유 사건 revision을 뒷받침하는 typed source mention 목록.';
COMMENT ON TABLE ontology.theme_reactions IS
    '현실 사건과 분리된 테마 반응의 안정 식별자.';
COMMENT ON TABLE ontology.theme_reaction_revisions IS
    '날짜·방향·원천 history를 변환 버전별로 append한 시장 반응 관측.';
COMMENT ON TABLE ontology.catalyst_relations IS
    '프로젝트 진행(ADVANCES)과 사람이 판단할 중복 후보를 보존한다.';
COMMENT ON TABLE ontology.artifacts IS
    'dataset·master·어휘·변환·질의 계약·commit을 고정한 재현 가능 발행 단위.';

COMMIT;
