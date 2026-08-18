BEGIN;

-- append-only revision은 과거 정책을 보존하지만, 서비스의 "현재" 조회는 가장
-- 최근에 원자 적재된 버전 묶음 하나만 읽어야 한다. catalyst_id별 최신 행만
-- 고르면 dedup 정책 변경으로 사라진 이전 catalyst가 계속 노출된다.
CREATE OR REPLACE VIEW ontology.current_catalyst_revisions AS
WITH active_version AS (
    SELECT
        dataset_hash,
        vocabulary_version,
        classification_transform_version,
        event_structure_transform_version,
        company_master_version,
        dedup_policy_version
    FROM ontology.catalyst_revisions
    ORDER BY created_at DESC, catalyst_revision_id DESC
    LIMIT 1
)
SELECT DISTINCT ON (revision.catalyst_id) revision.*
FROM ontology.catalyst_revisions revision
JOIN active_version active
  ON active.dataset_hash = revision.dataset_hash
 AND active.vocabulary_version = revision.vocabulary_version
 AND active.classification_transform_version =
     revision.classification_transform_version
 AND active.event_structure_transform_version =
     revision.event_structure_transform_version
 AND active.company_master_version = revision.company_master_version
 AND active.dedup_policy_version = revision.dedup_policy_version
ORDER BY revision.catalyst_id, revision.revision_no DESC;

COMMENT ON VIEW ontology.current_catalyst_revisions IS
    '가장 최근 원자 적재 버전 묶음에 속한 catalyst revision만 제공한다.';

DO $current_catalyst_artifact_boundary$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_infostock_writer') THEN
        GRANT SELECT ON ontology.current_catalyst_revisions
            TO dayjaview_infostock_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dayjaview_api_reader') THEN
        GRANT SELECT ON ontology.current_catalyst_revisions
            TO dayjaview_api_reader;
    END IF;
END
$current_catalyst_artifact_boundary$;

COMMIT;
