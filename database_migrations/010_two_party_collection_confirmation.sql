-- Record donor PIN verification separately from the acceptor's receipt confirmation.
-- Apply once in MySQL Workbench, then restart the Flask app.
USE plentiq_dev;

ALTER TABLE collections
    ADD COLUMN donor_confirmed_at DATETIME NULL,
    ADD COLUMN acceptor_confirmed_at DATETIME NULL;
