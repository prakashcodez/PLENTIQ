-- ============================================================================
-- PLENTIQ: Surplus Food Redistribution and Donation Management System
-- Stored Procedures: ACID Transaction & Concurrency Control Workflow
-- File: plentiq_workflow.sql
-- ============================================================================
-- 
-- INSTRUCTIONS FOR EXECUTING AND VERIFYING IN MYSQL WORKBENCH:
-- 1. Open MySQL Workbench and connect to your local MySQL 8.0 instance.
-- 2. Click File -> Open SQL Script... (or press Ctrl + Shift + O).
-- 3. Navigate to and select this file: C:\CODING FILES\PLENTIQ\plentiq_workflow.sql
-- 4. Click the yellow Lightning Bolt icon (Execute) or press Ctrl + Shift + Enter.
-- 5. In the Navigator panel on the left, expand "plentiq_db" -> "Stored Procedures".
-- 6. Confirm that the following procedures appear:
--      - sp_approve_donation_request
--      - sp_reject_donation_request
-- 7. To verify via SQL query in Workbench, run:
--      USE plentiq_db;
--      SHOW PROCEDURE STATUS WHERE Db = 'plentiq_db';
-- ============================================================================

USE plentiq_db;

DROP PROCEDURE IF EXISTS sp_approve_donation_request;
DROP PROCEDURE IF EXISTS sp_reject_donation_request;

DELIMITER //

-- ============================================================================
-- Stored Procedure: sp_approve_donation_request
-- Purpose: Safely allocate surplus food quantity to an NGO request using
--          row-level locking (FOR UPDATE) and an atomic ACID transaction.
-- 
-- Parameters:
--   IN  p_request_id       INT           - The ID of the request to approve
--   IN  p_user_id          INT           - Reviewer ID (must be listing owner or admin)
--   IN  p_remarks          VARCHAR(500)  - Optional approval notes
--   OUT p_status_code      VARCHAR(30)   - Result code (SUCCESS, NOT_PENDING, etc.)
--   OUT p_status_message   VARCHAR(255)  - Human-readable result explanation
-- ============================================================================
CREATE PROCEDURE sp_approve_donation_request(
    IN  p_request_id      INT,
    IN  p_user_id         INT,
    IN  p_remarks         VARCHAR(500),
    OUT p_status_code     VARCHAR(30),
    OUT p_status_message  VARCHAR(255)
)
proc_label: BEGIN
    -- Local variables
    DECLARE v_req_id          INT;
    DECLARE v_listing_id      INT;
    DECLARE v_ngo_id          INT;
    DECLARE v_req_qty         DECIMAL(10,2);
    DECLARE v_req_status      VARCHAR(20);

    DECLARE v_donor_id        INT;
    DECLARE v_qty_avail       DECIMAL(10,2);
    DECLARE v_listing_status  VARCHAR(20);
    DECLARE v_expiry_at       DATETIME;

    DECLARE v_user_role       VARCHAR(20);
    DECLARE v_new_qty         DECIMAL(10,2);
    DECLARE v_new_status      VARCHAR(20);

    -- Exception Handler: Automatic Rollback on any SQL error
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        SET p_status_code = 'ERROR';
        SET p_status_message = 'Database error occurred during approval transaction. Changes rolled back.';
    END;

    -- Begin atomic transaction
    START TRANSACTION;

    -- 1. Lock and retrieve the donation request
    SELECT
        request_id,
        listing_id,
        ngo_id,
        requested_quantity,
        request_status
    INTO
        v_req_id,
        v_listing_id,
        v_ngo_id,
        v_req_qty,
        v_req_status
    FROM donation_requests
    WHERE request_id = p_request_id
    FOR UPDATE;

    -- Check if request exists
    IF v_req_id IS NULL THEN
        ROLLBACK;
        SET p_status_code = 'NOT_FOUND';
        SET p_status_message = 'Donation request not found.';
        LEAVE proc_label;
    END IF;

    -- 2. Verify that the request is currently in pending status
    IF v_req_status != 'pending' THEN
        ROLLBACK;
        SET p_status_code = 'NOT_PENDING';
        SET p_status_message = CONCAT('Request is already ', v_req_status, '. Duplicate approval prohibited.');
        LEAVE proc_label;
    END IF;

    -- 3. Lock and retrieve the associated food listing
    SELECT
        donor_id,
        quantity_available,
        status,
        expiry_at
    INTO
        v_donor_id,
        v_qty_avail,
        v_listing_status,
        v_expiry_at
    FROM food_listings
    WHERE listing_id = v_listing_id
    FOR UPDATE;

    IF v_donor_id IS NULL THEN
        ROLLBACK;
        SET p_status_code = 'LISTING_NOT_FOUND';
        SET p_status_message = 'Associated food listing was not found.';
        LEAVE proc_label;
    END IF;

    -- 4. Verify reviewer authorization: must be listing owner or administrator
    SELECT role INTO v_user_role FROM users WHERE user_id = p_user_id AND is_active = TRUE;

    IF v_user_role IS NULL THEN
        ROLLBACK;
        SET p_status_code = 'UNAUTHORIZED';
        SET p_status_message = 'Reviewer user account is invalid or inactive.';
        LEAVE proc_label;
    END IF;

    IF v_user_role != 'admin' AND p_user_id != v_donor_id THEN
        ROLLBACK;
        SET p_status_code = 'UNAUTHORIZED';
        SET p_status_message = 'Permission denied: Only the listing donor or an administrator may approve this request.';
        LEAVE proc_label;
    END IF;

    -- 5. Recheck listing status
    IF v_listing_status != 'available' THEN
        ROLLBACK;
        SET p_status_code = 'LISTING_UNAVAILABLE';
        SET p_status_message = CONCAT('Listing is no longer available (current status: ', v_listing_status, ').');
        LEAVE proc_label;
    END IF;

    -- 6. Recheck listing expiry deadline
    IF v_expiry_at <= NOW() THEN
        -- Mark listing expired
        UPDATE food_listings SET status = 'expired' WHERE listing_id = v_listing_id;
        ROLLBACK;
        SET p_status_code = 'EXPIRED';
        SET p_status_message = 'Food listing has passed safe expiry deadline and cannot be approved.';
        LEAVE proc_label;
    END IF;

    -- 7. Ensure requested quantity does not exceed available quantity
    IF v_req_qty > v_qty_avail THEN
        ROLLBACK;
        SET p_status_code = 'INSUFFICIENT_QTY';
        SET p_status_message = CONCAT('Requested quantity (', v_req_qty, ') exceeds available quantity (', v_qty_avail, ').');
        LEAVE proc_label;
    END IF;

    -- 8. Calculate new remaining quantity and updated listing status
    SET v_new_qty = v_qty_avail - v_req_qty;
    IF v_new_qty = 0 THEN
        SET v_new_status = 'fully_allocated';
    ELSE
        SET v_new_status = 'available';
    END IF;

    -- 9. Decrease available quantity exactly once
    UPDATE food_listings
    SET quantity_available = v_new_qty,
        status = v_new_status
    WHERE listing_id = v_listing_id;

    -- 10. Mark the donation request approved with reviewer audit details
    UPDATE donation_requests
    SET request_status = 'approved',
        reviewed_by = p_user_id,
        reviewed_at = NOW(),
        remarks = p_remarks
    WHERE request_id = p_request_id;

    -- Commit all related changes together
    COMMIT;

    SET p_status_code = 'SUCCESS';
    SET p_status_message = CONCAT('Request approved. Quantity deducted: ', v_req_qty, '. Remaining: ', v_new_qty, '.');
END //


-- ============================================================================
-- Stored Procedure: sp_reject_donation_request
-- Purpose: Safely reject an NGO donation request without altering quantities.
-- ============================================================================
CREATE PROCEDURE sp_reject_donation_request(
    IN  p_request_id      INT,
    IN  p_user_id         INT,
    IN  p_remarks         VARCHAR(500),
    OUT p_status_code     VARCHAR(30),
    OUT p_status_message  VARCHAR(255)
)
proc_label: BEGIN
    DECLARE v_req_id          INT;
    DECLARE v_listing_id      INT;
    DECLARE v_req_status      VARCHAR(20);
    DECLARE v_donor_id        INT;
    DECLARE v_user_role       VARCHAR(20);

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        SET p_status_code = 'ERROR';
        SET p_status_message = 'Database error occurred during rejection. Changes rolled back.';
    END;

    START TRANSACTION;

    -- Lock and retrieve request
    SELECT request_id, listing_id, request_status
    INTO v_req_id, v_listing_id, v_req_status
    FROM donation_requests
    WHERE request_id = p_request_id
    FOR UPDATE;

    IF v_req_id IS NULL THEN
        ROLLBACK;
        SET p_status_code = 'NOT_FOUND';
        SET p_status_message = 'Donation request not found.';
        LEAVE proc_label;
    END IF;

    IF v_req_status != 'pending' THEN
        ROLLBACK;
        SET p_status_code = 'NOT_PENDING';
        SET p_status_message = CONCAT('Request is already ', v_req_status, '. Cannot reject.');
        LEAVE proc_label;
    END IF;

    -- Retrieve listing donor
    SELECT donor_id INTO v_donor_id FROM food_listings WHERE listing_id = v_listing_id;

    -- Retrieve user role
    SELECT role INTO v_user_role FROM users WHERE user_id = p_user_id AND is_active = TRUE;

    IF v_user_role != 'admin' AND p_user_id != v_donor_id THEN
        ROLLBACK;
        SET p_status_code = 'UNAUTHORIZED';
        SET p_status_message = 'Permission denied: Only the listing donor or an administrator may reject this request.';
        LEAVE proc_label;
    END IF;

    -- Update request status to rejected (NO alteration to food_listings quantity)
    UPDATE donation_requests
    SET request_status = 'rejected',
        reviewed_by = p_user_id,
        reviewed_at = NOW(),
        remarks = p_remarks
    WHERE request_id = p_request_id;

    COMMIT;

    SET p_status_code = 'SUCCESS';
    SET p_status_message = 'Donation request has been rejected. Listing quantity remains unchanged.';
END //

DELIMITER ;

