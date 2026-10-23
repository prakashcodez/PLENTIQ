Query 1 - Table Record Counts

USE plentiq_db;
 
SELECT 'users' AS table_name, COUNT(*) AS record_count FROM users
UNION ALL
SELECT 'food_categories', COUNT(*) FROM food_categories
UNION ALL
SELECT 'food_listings', COUNT(*) FROM food_listings
UNION ALL
SELECT 'donation_requests', COUNT(*) FROM donation_requests
UNION ALL
SELECT 'collection_schedules', COUNT(*) FROM collection_schedules
UNION ALL
SELECT 'distribution_records', COUNT(*) FROM distribution_records;

Query 2 - Table Columns and Key Indicators

SELECT TABLE_NAME, ORDINAL_POSITION, COLUMN_NAME,
       COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, EXTRA
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'plentiq_db'
ORDER BY TABLE_NAME, ORDINAL_POSITION;

Query 3 - Foreign-Key Relationships

SELECT TABLE_NAME, COLUMN_NAME, CONSTRAINT_NAME,
       REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = 'plentiq_db'
  AND REFERENCED_TABLE_NAME IS NOT NULL
ORDER BY TABLE_NAME, CONSTRAINT_NAME;

Query 4 - Stored Procedure Verification

SHOW PROCEDURE STATUS
WHERE Db = 'plentiq_db';


Query 5 - End-to-End Donation Workflow

SELECT
    l.listing_id,
    l.food_name,
    l.quantity_total,
    l.quantity_available,
    l.status AS listing_status,
    r.request_id,
    r.request_status,
    r.requested_quantity,
    cs.schedule_id,
    cs.scheduled_at,
    cs.status AS collection_status,
    cs.collected_at,
    d.distribution_id,
    d.quantity_distributed,
    d.recipient_count,
    d.distributed_at
FROM food_listings AS l
LEFT JOIN donation_requests AS r
       ON r.listing_id = l.listing_id
LEFT JOIN collection_schedules AS cs
       ON cs.request_id = r.request_id
LEFT JOIN distribution_records AS d
       ON d.schedule_id = cs.schedule_id
ORDER BY l.listing_id DESC, r.requested_at DESC
LIMIT 25;


-- TEST

USE plentiq_db;

SELECT
    l.listing_id,
    l.food_name,
    l.quantity_total,
    l.quantity_available,
    l.status AS listing_status,

    u.full_name AS donor_name,

    r.request_id,
    r.requested_quantity,
    r.request_status,

    cs.schedule_id,
    cs.scheduled_at,
    cs.status AS collection_status,

    d.distribution_id,
    d.quantity_distributed,
    d.recipient_count,
    d.distributed_at

FROM food_listings l

JOIN users u
    ON l.donor_id = u.user_id

LEFT JOIN donation_requests r
    ON l.listing_id = r.listing_id

LEFT JOIN collection_schedules cs
    ON r.request_id = cs.request_id

LEFT JOIN distribution_records d
    ON cs.schedule_id = d.schedule_id

ORDER BY l.listing_id;