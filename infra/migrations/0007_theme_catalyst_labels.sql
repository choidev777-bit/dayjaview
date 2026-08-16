BEGIN;

CREATE SCHEMA IF NOT EXISTS ontology;

REVOKE CREATE ON SCHEMA ontology FROM PUBLIC;

CREATE TABLE IF NOT EXISTS ontology.catalyst_vocabularies (
    vocabulary_version text PRIMARY KEY CHECK (btrim(vocabulary_version) <> ''),
    content_hash character(64) NOT NULL
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    registered_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS ontology.catalyst_types (
    vocabulary_version text NOT NULL
        REFERENCES ontology.catalyst_vocabularies(vocabulary_version) ON DELETE RESTRICT,
    type_id text NOT NULL CHECK (type_id ~ '^[A-Z][A-Z0-9_]+$'),
    name_ko text NOT NULL CHECK (btrim(name_ko) <> ''),
    description_ko text NOT NULL CHECK (btrim(description_ko) <> ''),
    source_order integer NOT NULL CHECK (source_order >= 0),
    PRIMARY KEY (vocabulary_version, type_id),
    CONSTRAINT uq_ontology_catalyst_type_order
        UNIQUE (vocabulary_version, source_order)
);

CREATE TABLE IF NOT EXISTS ontology.theme_history_labels (
    label_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    history_id bigint NOT NULL
        REFERENCES core.infostock_theme_history(history_id) ON DELETE RESTRICT,
    vocabulary_version text NOT NULL
        REFERENCES ontology.catalyst_vocabularies(vocabulary_version) ON DELETE RESTRICT,
    transform_version text NOT NULL CHECK (btrim(transform_version) <> ''),
    type_ids text[] NOT NULL DEFAULT '{}',
    primary_type_id text,
    direction text NOT NULL CHECK (direction IN ('UP', 'DOWN', 'MIXED', 'UNKNOWN')),
    certainty text NOT NULL
        CHECK (certainty IN ('CONFIRMED', 'ANTICIPATION', 'UNSPECIFIED')),
    continuation boolean NOT NULL,
    labeled_at timestamptz NOT NULL,
    CONSTRAINT uq_ontology_theme_history_label
        UNIQUE (history_id, vocabulary_version, transform_version),
    CONSTRAINT ck_ontology_label_primary_matches_first CHECK (
        (cardinality(type_ids) = 0 AND primary_type_id IS NULL)
        OR (cardinality(type_ids) > 0 AND primary_type_id = type_ids[1])
    ),
    CONSTRAINT fk_ontology_label_primary_type
        FOREIGN KEY (vocabulary_version, primary_type_id)
        REFERENCES ontology.catalyst_types(vocabulary_version, type_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS ix_ontology_labels_history
    ON ontology.theme_history_labels (history_id);
CREATE INDEX IF NOT EXISTS ix_ontology_labels_primary_type
    ON ontology.theme_history_labels (vocabulary_version, primary_type_id);
CREATE INDEX IF NOT EXISTS ix_ontology_labels_type_ids
    ON ontology.theme_history_labels USING gin (type_ids);

CREATE TABLE IF NOT EXISTS ontology.theme_history_label_spans (
    span_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    label_id bigint NOT NULL
        REFERENCES ontology.theme_history_labels(label_id) ON DELETE RESTRICT,
    source_order integer NOT NULL CHECK (source_order >= 0),
    field text NOT NULL
        CHECK (field IN ('catalyst_type', 'direction', 'certainty', 'continuation')),
    value text NOT NULL CHECK (btrim(value) <> ''),
    keyword text NOT NULL CHECK (btrim(keyword) <> ''),
    start_offset integer NOT NULL CHECK (start_offset >= 0),
    end_offset integer NOT NULL,
    CONSTRAINT uq_ontology_label_span_order UNIQUE (label_id, source_order),
    CONSTRAINT ck_ontology_label_span_range CHECK (end_offset > start_offset)
);

CREATE INDEX IF NOT EXISTS ix_ontology_label_spans_label
    ON ontology.theme_history_label_spans (label_id);

REVOKE ALL ON ALL TABLES IN SCHEMA ontology FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA ontology FROM PUBLIC;

DO $ontology_boundary$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_infostock_writer') THEN
        GRANT USAGE ON SCHEMA ontology TO dayjaview_infostock_writer;
        GRANT SELECT, INSERT ON ontology.catalyst_vocabularies,
            ontology.catalyst_types, ontology.theme_history_labels,
            ontology.theme_history_label_spans
            TO dayjaview_infostock_writer;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ontology
            TO dayjaview_infostock_writer;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_api_reader') THEN
        GRANT USAGE ON SCHEMA ontology TO dayjaview_api_reader;
        GRANT SELECT ON ontology.catalyst_vocabularies, ontology.catalyst_types,
            ontology.theme_history_labels, ontology.theme_history_label_spans
            TO dayjaview_api_reader;
    END IF;
END
$ontology_boundary$;

COMMENT ON SCHEMA ontology IS
    '사건·소재 온톨로지(E-17) 분류 결과. 원인문 원문은 core에 두고 라벨만 담는다.';
COMMENT ON TABLE ontology.catalyst_vocabularies IS
    '통제어휘 버전과 content hash. 어휘 내용이 바뀌면 새 버전으로만 등록한다.';
COMMENT ON TABLE ontology.theme_history_labels IS
    'history 한 건의 분류 결과. 어휘·변환 버전별로 append하며 덮어쓰지 않는다.';
COMMENT ON COLUMN ontology.theme_history_labels.type_ids IS
    '원문 등장 순서를 유지한 소재 유형 목록. primary_type_id는 그 첫 원소다.';
COMMENT ON TABLE ontology.theme_history_label_spans IS
    '분류 근거 span. 오프셋은 core.infostock_theme_history.raw_text 기준이다.';

COMMIT;
