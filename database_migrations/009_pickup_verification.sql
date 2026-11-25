-- Add one-time pickup verification metadata and failed-attempt throttling.
-- The PIN itself is derived from this random nonce and PLENTIQ_SECRET_KEY;
-- the clear PIN is never stored in the database.
USE plentiq_dev;

ALTER TABLE collections
    ADD COLUMN pickup_pin_nonce CHAR(32) NULL,
    ADD COLUMN pickup_pin_attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
    ADD COLUMN pickup_pin_attempted_at DATETIME NULL;

-- Give already-approved, still-scheduled collections a PIN after the migration.
UPDATE collections
SET pickup_pin_nonce = LOWER(HEX(RANDOM_BYTES(16)))
WHERE status_code = 'scheduled'
  AND pickup_pin_nonce IS NULL
  AND collection_id > 0;
