-- Seed the three report reasons used by participant reports.
-- Safe to re-run: the reason_code key prevents duplicate lookup rows.
USE plentiq_dev;

INSERT INTO report_reasons (reason_code, reason_label, is_active)
VALUES
    ('missed_collection', 'Missed collection', TRUE),
    ('unsafe_food', 'Unsafe or misrepresented food', TRUE),
    ('other_conduct', 'Other conduct', TRUE)
ON DUPLICATE KEY UPDATE
    reason_label = VALUES(reason_label),
    is_active = TRUE;
