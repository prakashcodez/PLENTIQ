USE plentiq_dev;

CREATE TABLE IF NOT EXISTS donation_requests (
    request_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    item_id BIGINT UNSIGNED NOT NULL,
    acceptor_user_id BIGINT UNSIGNED NOT NULL,
    quantity_requested DECIMAL(10,2) NOT NULL,
    status_code VARCHAR(16) NOT NULL DEFAULT 'pending',
    requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_by_user_id BIGINT UNSIGNED NULL,
    decided_at DATETIME NULL,
    rejection_reason VARCHAR(500) NULL,
    PRIMARY KEY (request_id),
    KEY idx_requests_item_status (item_id, status_code),
    KEY idx_requests_acceptor_status (acceptor_user_id, status_code),
    CONSTRAINT fk_requests_item FOREIGN KEY (item_id)
        REFERENCES donation_items(item_id),
    CONSTRAINT fk_requests_acceptor FOREIGN KEY (acceptor_user_id)
        REFERENCES users(user_id),
    CONSTRAINT fk_requests_decider FOREIGN KEY (decided_by_user_id)
        REFERENCES users(user_id),
    CONSTRAINT chk_request_quantity CHECK (quantity_requested > 0),
    CONSTRAINT chk_request_status
        CHECK (status_code IN ('pending', 'approved', 'rejected', 'cancelled', 'expired'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS collections (
    collection_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    request_id BIGINT UNSIGNED NOT NULL,
    status_code VARCHAR(16) NOT NULL DEFAULT 'scheduled',
    scheduled_pickup_at DATETIME NULL,
    collected_at DATETIME NULL,
    notes VARCHAR(500) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (collection_id),
    UNIQUE KEY uq_collection_request (request_id),
    CONSTRAINT fk_collections_request FOREIGN KEY (request_id)
        REFERENCES donation_requests(request_id),
    CONSTRAINT chk_collection_status
        CHECK (status_code IN ('scheduled', 'collected', 'missed', 'cancelled'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS report_reasons (
    reason_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    reason_code VARCHAR(40) NOT NULL,
    reason_label VARCHAR(120) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (reason_id),
    UNIQUE KEY uq_report_reason_code (reason_code)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS reports (
    report_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    request_id BIGINT UNSIGNED NOT NULL,
    collection_id BIGINT UNSIGNED NULL,
    reporter_user_id BIGINT UNSIGNED NOT NULL,
    reported_user_id BIGINT UNSIGNED NOT NULL,
    reason_id SMALLINT UNSIGNED NOT NULL,
    details VARCHAR(1000) NULL,
    status_code VARCHAR(16) NOT NULL DEFAULT 'open',
    reviewed_by_user_id BIGINT UNSIGNED NULL,
    reviewed_at DATETIME NULL,
    resolution_note VARCHAR(1000) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (report_id),
    KEY idx_reports_status_created (status_code, created_at),
    KEY idx_reports_reported_user (reported_user_id),
    CONSTRAINT fk_reports_request FOREIGN KEY (request_id)
        REFERENCES donation_requests(request_id),
    CONSTRAINT fk_reports_collection FOREIGN KEY (collection_id)
        REFERENCES collections(collection_id),
    CONSTRAINT fk_reports_reporter FOREIGN KEY (reporter_user_id)
        REFERENCES users(user_id),
    CONSTRAINT fk_reports_reported FOREIGN KEY (reported_user_id)
        REFERENCES users(user_id),
    CONSTRAINT fk_reports_reason FOREIGN KEY (reason_id)
        REFERENCES report_reasons(reason_id),
    CONSTRAINT fk_reports_reviewer FOREIGN KEY (reviewed_by_user_id)
        REFERENCES users(user_id),
    CONSTRAINT chk_report_different_users
        CHECK (reporter_user_id <> reported_user_id),
    CONSTRAINT chk_report_status
        CHECK (status_code IN ('open', 'under_review', 'resolved', 'dismissed'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS moderation_actions (
    action_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    admin_user_id BIGINT UNSIGNED NOT NULL,
    target_user_id BIGINT UNSIGNED NOT NULL,
    action_code VARCHAR(12) NOT NULL,
    reason VARCHAR(1000) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (action_id),
    KEY idx_moderation_target (target_user_id, created_at),
    CONSTRAINT fk_moderation_admin FOREIGN KEY (admin_user_id)
        REFERENCES users(user_id),
    CONSTRAINT fk_moderation_target FOREIGN KEY (target_user_id)
        REFERENCES users(user_id),
    CONSTRAINT chk_moderation_action
        CHECK (action_code IN ('block', 'unblock'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS notifications (
    notification_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    recipient_user_id BIGINT UNSIGNED NOT NULL,
    event_code VARCHAR(40) NOT NULL,
    message VARCHAR(500) NOT NULL,
    donation_id BIGINT UNSIGNED NULL,
    request_id BIGINT UNSIGNED NULL,
    read_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (notification_id),
    KEY idx_notifications_user_unread (recipient_user_id, read_at),
    CONSTRAINT fk_notifications_user FOREIGN KEY (recipient_user_id)
        REFERENCES users(user_id),
    CONSTRAINT fk_notifications_donation FOREIGN KEY (donation_id)
        REFERENCES donations(donation_id),
    CONSTRAINT fk_notifications_request FOREIGN KEY (request_id)
        REFERENCES donation_requests(request_id)
) ENGINE=InnoDB;