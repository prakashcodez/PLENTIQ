-- Add fields required by the connected donor profile and donation form.
-- Run this once in MySQL Workbench as a database administrator, with plentiq_dev selected.

USE plentiq_dev;

ALTER TABLE donor_profiles
    ADD COLUMN default_city VARCHAR(100) NULL AFTER donor_type;

ALTER TABLE donations
    ADD COLUMN contact_name VARCHAR(120) NOT NULL DEFAULT '' AFTER contact_phone,
    ADD COLUMN contact_email VARCHAR(254) NULL AFTER contact_name;

ALTER TABLE donation_items
    ADD COLUMN serving_size DECIMAL(10,2) NULL AFTER available_quantity,
    ADD COLUMN serving_unit VARCHAR(60) NULL AFTER serving_size;
