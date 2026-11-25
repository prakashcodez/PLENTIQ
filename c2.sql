-- Add schedule information for acceptor food needs.
-- Run this once in MySQL Workbench with a database administrator account.

USE plentiq_dev;

ALTER TABLE acceptor_needs
    ADD COLUMN needed_for DATE NULL AFTER people_count,
    ADD COLUMN meal_period VARCHAR(12) NULL AFTER needed_for,
    ADD CONSTRAINT chk_acceptor_need_meal_period
        CHECK (meal_period IS NULL OR meal_period IN ('breakfast', 'lunch', 'dinner'));
