-- Allow produce listings when the harvest or packing time is not known.
-- This changes no existing values. Run once with plentiq_dev selected.

USE plentiq_dev;

ALTER TABLE donation_items
    MODIFY COLUMN prepared_at DATETIME NULL;
