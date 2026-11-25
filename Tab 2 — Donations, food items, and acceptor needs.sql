USE plentiq_dev;

CREATE TABLE IF NOT EXISTS donations (
    donation_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    donor_user_id BIGINT UNSIGNED NOT NULL,
    city VARCHAR(100) NOT NULL,
    area VARCHAR(120) NOT NULL,
    landmark VARCHAR(160) NULL,
    pickup_address VARCHAR(500) NOT NULL,
    latitude DECIMAL(10,7) NULL,
    longitude DECIMAL(10,7) NULL,
    contact_phone VARCHAR(32) NOT NULL,
    pickup_starts_at DATETIME NOT NULL,
    pickup_ends_at DATETIME NOT NULL,
    status_code VARCHAR(24) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (donation_id),
    KEY idx_donations_donor_status (donor_user_id, status_code),
    KEY idx_donations_pickup_end (pickup_ends_at),
    CONSTRAINT fk_donations_donor FOREIGN KEY (donor_user_id)
        REFERENCES users(user_id),
    CONSTRAINT chk_donation_status
        CHECK (status_code IN ('active', 'fully_allocated', 'cancelled')),
    CONSTRAINT chk_pickup_window
        CHECK (pickup_ends_at > pickup_starts_at),
    CONSTRAINT chk_donation_coordinates
        CHECK (
            (latitude IS NULL AND longitude IS NULL)
            OR (latitude IS NOT NULL AND longitude IS NOT NULL)
        )
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS donation_items (
    item_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    donation_id BIGINT UNSIGNED NOT NULL,
    category_id SMALLINT UNSIGNED NOT NULL,
    dietary_option_id SMALLINT UNSIGNED NOT NULL,
    unit_id SMALLINT UNSIGNED NOT NULL,
    food_name VARCHAR(120) NOT NULL,
    description TEXT NULL,
    photo_reference VARCHAR(1024) NULL,
    total_quantity DECIMAL(10,2) NOT NULL,
    available_quantity DECIMAL(10,2) NOT NULL,
    prepared_at DATETIME NOT NULL,
    approximate_expiry_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (item_id),
    KEY idx_items_donation (donation_id),
    KEY idx_items_category_expiry (category_id, approximate_expiry_at),
    CONSTRAINT fk_items_donation FOREIGN KEY (donation_id)
        REFERENCES donations(donation_id),
    CONSTRAINT fk_items_category FOREIGN KEY (category_id)
        REFERENCES food_categories(category_id),
    CONSTRAINT fk_items_dietary FOREIGN KEY (dietary_option_id)
        REFERENCES dietary_options(dietary_option_id),
    CONSTRAINT fk_items_unit FOREIGN KEY (unit_id)
        REFERENCES quantity_units(unit_id),
    CONSTRAINT chk_item_quantities
        CHECK (
            total_quantity > 0
            AND available_quantity >= 0
            AND available_quantity <= total_quantity
        ),
    CONSTRAINT chk_item_expiry
        CHECK (
            approximate_expiry_at IS NULL
            OR approximate_expiry_at > prepared_at
        )
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS acceptor_needs (
    need_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    acceptor_user_id BIGINT UNSIGNED NOT NULL,
    dietary_option_id SMALLINT UNSIGNED NOT NULL,
    city VARCHAR(100) NOT NULL,
    area VARCHAR(120) NOT NULL,
    address VARCHAR(500) NULL,
    latitude DECIMAL(10,7) NULL,
    longitude DECIMAL(10,7) NULL,
    people_count INT UNSIGNED NOT NULL,
    notes VARCHAR(500) NULL,
    status_code VARCHAR(16) NOT NULL DEFAULT 'open',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (need_id),
    KEY idx_needs_acceptor_status (acceptor_user_id, status_code),
    CONSTRAINT fk_needs_acceptor FOREIGN KEY (acceptor_user_id)
        REFERENCES users(user_id),
    CONSTRAINT fk_needs_dietary FOREIGN KEY (dietary_option_id)
        REFERENCES dietary_options(dietary_option_id),
    CONSTRAINT chk_need_people CHECK (people_count > 0),
    CONSTRAINT chk_need_status
        CHECK (status_code IN ('open', 'fulfilled', 'closed', 'cancelled')),
    CONSTRAINT chk_need_coordinates
        CHECK (
            (latitude IS NULL AND longitude IS NULL)
            OR (latitude IS NOT NULL AND longitude IS NOT NULL)
        )
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS acceptor_need_categories (
    need_id BIGINT UNSIGNED NOT NULL,
    category_id SMALLINT UNSIGNED NOT NULL,
    PRIMARY KEY (need_id, category_id),
    CONSTRAINT fk_need_categories_need FOREIGN KEY (need_id)
        REFERENCES acceptor_needs(need_id),
    CONSTRAINT fk_need_categories_category FOREIGN KEY (category_id)
        REFERENCES food_categories(category_id)
) ENGINE=InnoDB;