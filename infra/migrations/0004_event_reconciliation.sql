BEGIN;

-- D-13 장후 정합: state_logs가 lifecycle 외에 reconciliation 축의 전이도
-- 기록한다. 축별로 허용 상태 집합이 다르므로 CHECK를 축 조건으로 바꾼다.

ALTER TABLE event.state_logs
    ADD COLUMN IF NOT EXISTS axis text NOT NULL DEFAULT 'lifecycleStatus';

ALTER TABLE event.state_logs
    DROP CONSTRAINT IF EXISTS state_logs_to_status_check;
ALTER TABLE event.state_logs
    DROP CONSTRAINT IF EXISTS ck_event_state_log_from_status;
ALTER TABLE event.state_logs
    DROP CONSTRAINT IF EXISTS ck_event_state_log_axis;
ALTER TABLE event.state_logs
    DROP CONSTRAINT IF EXISTS ck_event_state_log_axis_to_status;
ALTER TABLE event.state_logs
    DROP CONSTRAINT IF EXISTS ck_event_state_log_axis_from_status;

ALTER TABLE event.state_logs
    ADD CONSTRAINT ck_event_state_log_axis CHECK (
        axis IN ('lifecycleStatus', 'reconciliationStatus')
    );
ALTER TABLE event.state_logs
    ADD CONSTRAINT ck_event_state_log_axis_to_status CHECK (
        (axis = 'lifecycleStatus' AND to_status IN (
            'CANDIDATE', 'ACTIVE', 'WEAKENING', 'CLOSED', 'DISCARDED'
        ))
        OR (axis = 'reconciliationStatus' AND to_status IN (
            'MATCHED', 'UNMATCHED'
        ))
    );
ALTER TABLE event.state_logs
    ADD CONSTRAINT ck_event_state_log_axis_from_status CHECK (
        from_status IS NULL
        OR (axis = 'lifecycleStatus' AND from_status IN (
            'CANDIDATE', 'ACTIVE', 'WEAKENING', 'CLOSED', 'DISCARDED'
        ))
        OR (axis = 'reconciliationStatus' AND from_status IN (
            'PENDING', 'MATCHED', 'UNMATCHED'
        ))
    );

COMMENT ON COLUMN event.state_logs.axis IS
    '전이 축: lifecycleStatus 또는 reconciliationStatus(장후 정합 revision)';

COMMIT;
