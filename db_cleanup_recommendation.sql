-- ============================================================================
-- PLENTIQ: Demonstration Data Cleanup Recommendation (Stage 2.1)
-- ============================================================================
-- PURPOSE:
-- Safely purge synthetic test records generated during automated test suites
-- while preserving authentic, presentation-ready demonstration records.
--
-- DO NOT EXECUTE AUTOMATICALLY.
-- Review this script in MySQL Workbench and execute when ready.
-- ============================================================================

USE plentiq_db;

START TRANSACTION;

-- Step 1: Preview records targeted for removal (run these SELECTs first to verify)
SELECT listing_id, food_name, quantity_available, status, expiry_at 
FROM food_listings 
WHERE food_name LIKE 'Test Food Batch %'
   OR food_name LIKE 'Insufficient Qty%'
   OR food_name LIKE 'Fully Allocated%'
   OR food_name LIKE 'Expired Test%'
   OR food_name IN ('Dup Test', 'Python Fallback Test', 'Rotii');

-- Step 2: Delete associated donation requests for synthetic test listings
DELETE FROM donation_requests 
WHERE listing_id IN (
    SELECT listing_id FROM (
        SELECT listing_id FROM food_listings 
        WHERE food_name LIKE 'Test Food Batch %'
           OR food_name LIKE 'Insufficient Qty%'
           OR food_name LIKE 'Fully Allocated%'
           OR food_name LIKE 'Expired Test%'
           OR food_name IN ('Dup Test', 'Python Fallback Test', 'Rotii')
    ) AS temp_ids
);

-- Step 3: Delete synthetic test listings
DELETE FROM food_listings 
WHERE food_name LIKE 'Test Food Batch %'
   OR food_name LIKE 'Insufficient Qty%'
   OR food_name LIKE 'Fully Allocated%'
   OR food_name LIKE 'Expired Test%'
   OR food_name IN ('Dup Test', 'Python Fallback Test', 'Rotii');

-- Step 4: Verify remaining presentation-ready records
SELECT listing_id, donor_id, food_name, quantity_available, quantity_total, unit, status, expiry_at
FROM food_listings
ORDER BY listing_id ASC;

SELECT request_id, listing_id, ngo_id, requested_quantity, status
FROM donation_requests
ORDER BY request_id ASC;

-- If satisfied with the result, commit; otherwise execute ROLLBACK;
COMMIT;

