CREATE DATABASE plentiq_db;
USE plentiq_db;

CREATE TABLE users (
    user_id INT AUTO_INCREMENT PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    email VARCHAR(150) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    phone VARCHAR(20),
    role ENUM('donor', 'ngo', 'admin') NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE food_categories (
    category_id INT AUTO_INCREMENT PRIMARY KEY,
    category_name VARCHAR(50) NOT NULL UNIQUE,
    description VARCHAR(255)
) ENGINE=InnoDB;

INSERT INTO food_categories (category_name, description)
VALUES
('Cooked Meals', 'Surplus prepared meals ready for serving'),
('Rice and Biryani', 'Rice dishes and biryani from restaurants or events'),
('Curries and Side Dishes', 'Prepared curries and accompanying dishes'),
('Packed Meals', 'Individually packed surplus meal portions'),
('Bakery Items', 'Surplus bread and bakery products'),
('Sweets and Desserts', 'Sweets and desserts from restaurants or functions'),
('Other Prepared Food', 'Other suitable surplus prepared food');

CREATE TABLE food_listings (
    listing_id INT AUTO_INCREMENT PRIMARY KEY,

    donor_id INT NOT NULL,
    category_id INT NOT NULL,

    food_name VARCHAR(120) NOT NULL,
    description TEXT,

    source_type ENUM(
        'Hotel',
        'Restaurant',
        'Wedding/Function',
        'Catering Service',
        'College/Office Event',
        'Community Event',
        'Other'
    ) NOT NULL,

    quantity_total DECIMAL(10,2) NOT NULL,
    quantity_available DECIMAL(10,2) NOT NULL,

    unit VARCHAR(30) NOT NULL DEFAULT 'portions',

    expiry_at DATETIME NOT NULL,

    pickup_address VARCHAR(255) NOT NULL,
    city VARCHAR(100) NOT NULL,

    handling_notes VARCHAR(500),

    status ENUM(
        'available',
        'fully_allocated',
        'expired',
        'cancelled'
    ) NOT NULL DEFAULT 'available',

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_food_donor
        FOREIGN KEY (donor_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_food_category
        FOREIGN KEY (category_id)
        REFERENCES food_categories(category_id)
        ON DELETE RESTRICT,

    CONSTRAINT chk_quantity_total
        CHECK (quantity_total > 0),

    CONSTRAINT chk_quantity_available
        CHECK (
            quantity_available >= 0
            AND quantity_available <= quantity_total
        ),

    INDEX idx_food_status_expiry (status, expiry_at),
    INDEX idx_food_category (category_id),
    INDEX idx_food_city (city)

) ENGINE=InnoDB;

CREATE TABLE donation_requests (
    request_id INT AUTO_INCREMENT PRIMARY KEY,

    listing_id INT NOT NULL,
    ngo_id INT NOT NULL,

    requested_quantity DECIMAL(10,2) NOT NULL,

    request_status ENUM(
        'pending',
        'approved',
        'rejected',
        'cancelled'
    ) NOT NULL DEFAULT 'pending',

    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    reviewed_by INT,
    reviewed_at DATETIME,
    remarks VARCHAR(500),

    CONSTRAINT fk_request_listing
        FOREIGN KEY (listing_id)
        REFERENCES food_listings(listing_id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_request_ngo
        FOREIGN KEY (ngo_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_request_reviewer
        FOREIGN KEY (reviewed_by)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CONSTRAINT chk_requested_quantity
        CHECK (requested_quantity > 0),

    INDEX idx_request_ngo_status (ngo_id, request_status),
    INDEX idx_request_listing_status (listing_id, request_status)

) ENGINE=InnoDB;


CREATE TABLE collection_schedules (
    schedule_id INT AUTO_INCREMENT PRIMARY KEY,

    request_id INT NOT NULL UNIQUE,

    scheduled_at DATETIME NOT NULL,

    status ENUM(
        'scheduled',
        'collected',
        'missed'
    ) NOT NULL DEFAULT 'scheduled',

    collected_at DATETIME,
    notes VARCHAR(500),

    CONSTRAINT fk_schedule_request
        FOREIGN KEY (request_id)
        REFERENCES donation_requests(request_id)
        ON DELETE RESTRICT

) ENGINE=InnoDB;


CREATE TABLE distribution_records (
    distribution_id INT AUTO_INCREMENT PRIMARY KEY,

    schedule_id INT NOT NULL UNIQUE,
    recorded_by INT NOT NULL,

    quantity_distributed DECIMAL(10,2) NOT NULL,
    recipient_count INT,

    distributed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes VARCHAR(500),

    CONSTRAINT fk_distribution_schedule
        FOREIGN KEY (schedule_id)
        REFERENCES collection_schedules(schedule_id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_distribution_user
        FOREIGN KEY (recorded_by)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CONSTRAINT chk_distribution_quantity
        CHECK (quantity_distributed > 0),

    CONSTRAINT chk_recipient_count
        CHECK (recipient_count IS NULL OR recipient_count >= 0)

) ENGINE=InnoDB;

-- USERNAMES

INSERT INTO users
    (full_name, email, password_hash, phone, role)
VALUES
(
    'Green Leaf Catering',
    'donor.demo@plentiq.local',
    'scrypt:32768:8:1$Y6FJcVYDnThV0rc6$7d060db1454c8ccabbabb75b36d2ae250073d87d94c790713fe99f1fea35c9526b69dbf4d3f2d713e882a228f2c2ee85adae48fa221ad05852c330e2df484cc4',
    NULL,
    'donor'
),
(
    'Hope Foundation',
    'ngo.demo@plentiq.local',
    'scrypt:32768:8:1$Y6FJcVYDnThV0rc6$7d060db1454c8ccabbabb75b36d2ae250073d87d94c790713fe99f1fea35c9526b69dbf4d3f2d713e882a228f2c2ee85adae48fa221ad05852c330e2df484cc4',
    NULL,
    'ngo'
),
(
    'Plentiq Admin',
    'admin.demo@plentiq.local',
    'scrypt:32768:8:1$Y6FJcVYDnThV0rc6$7d060db1454c8ccabbabb75b36d2ae250073d87d94c790713fe99f1fea35c9526b69dbf4d3f2d713e882a228f2c2ee85adae48fa221ad05852c330e2df484cc4',
    NULL,
    'admin'
);
