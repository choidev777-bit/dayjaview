BEGIN;

-- D-14 일일 증분: Daily만 갱신하는 INCREMENTAL run은 theme 컴포넌트를
-- 수집하지 않는다. 그 상태를 거짓 없이 기록하기 위해 core_status에
-- SKIPPED를 허용하고 expected_theme_count 0을 허용한다.
-- (run_type 'INCREMENTAL'은 0001부터 이미 허용돼 있다.)

ALTER TABLE ingest.infostock_import_runs
    DROP CONSTRAINT IF EXISTS infostock_import_runs_core_status_check;
ALTER TABLE ingest.infostock_import_runs
    ADD CONSTRAINT infostock_import_runs_core_status_check CHECK (
        core_status IN ('COMPLETE', 'PARTIAL', 'BLOCKED', 'FAILED', 'SKIPPED')
    );

ALTER TABLE ingest.infostock_import_runs
    DROP CONSTRAINT IF EXISTS infostock_import_runs_expected_theme_count_check;
ALTER TABLE ingest.infostock_import_runs
    ADD CONSTRAINT infostock_import_runs_expected_theme_count_check CHECK (
        expected_theme_count >= 0
    );

COMMENT ON COLUMN ingest.infostock_import_runs.core_status IS
    'SKIPPED = 이 run이 theme 컴포넌트를 수집 대상으로 삼지 않음(일일 증분).';

COMMIT;
