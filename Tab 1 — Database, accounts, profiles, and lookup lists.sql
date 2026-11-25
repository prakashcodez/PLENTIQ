CREATE DATABASE IF NOT EXISTS plentiq_dev
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

USE plentiq_dev;

CREATE TABLE IF NOT EXISTS roles (
    role_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    role_code VARCHAR(30) NOT NULL,
    role_name VARCHAR(60) NOT NULL,
    PRIMARY KEY (role_id),
    UNIQUE KEY uq_roles_code (role_code)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    role_id SMALLINT UNSIGNED NOT NULL,
    full_name VARCHAR(120) NOT NULL,
    email VARCHAR(254) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    phone VARCHAR(32) NULL,
    account_status VARCHAR(12) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id),
    UNIQUE KEY uq_users_email (email),
    KEY idx_users_role_status (role_id, account_status),
    CONSTRAINT fk_users_role FOREIGN KEY (role_id)
        REFERENCES roles(role_id),
    CONSTRAINT chk_users_status
        CHECK (account_status IN ('active', 'blocked'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS donor_profiles (
    user_id BIGINT UNSIGNED NOT NULL,
    donor_type VARCHAR(40) NOT NULL DEFAULT 'individual',
    PRIMARY KEY (user_id),
    CONSTRAINT fk_donor_profiles_user FOREIGN KEY (user_id)
        REFERENCES users(user_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS acceptor_profiles (
    user_id BIGINT UNSIGNED NOT NULL,
    acceptor_type VARCHAR(40) NOT NULL DEFAULT 'organisation',
    organisation_name VARCHAR(160) NULL,
    PRIMARY KEY (user_id),
    CONSTRAINT fk_acceptor_profiles_user FOREIGN KEY (user_id)
        REFERENCES users(user_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS user_locations (
    location_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id BIGINT UNSIGNED NOT NULL,
    label VARCHAR(60) NOT NULL DEFAULT 'Default',
    city VARCHAR(100) NOT NULL,
    area VARCHAR(120) NOT NULL,
    landmark VARCHAR(160) NULL,
    address VARCHAR(500) NOT NULL,
    latitude DECIMAL(10,7) NULL,
    longitude DECIMAL(10,7) NULL,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (location_id),
    KEY idx_locations_user (user_id),
    CONSTRAINT fk_locations_user FOREIGN KEY (user_id)
        REFERENCES users(user_id),
    CONSTRAINT chk_location_coordinates
        CHECK (
            (latitude IS NULL AND longitude IS NULL)
            OR (latitude IS NOT NULL AND longitude IS NOT NULL)
        ),
    CONSTRAINT chk_location_latitude
        CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    CONSTRAINT chk_location_longitude
        CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS food_categories (
    category_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    category_code VARCHAR(40) NOT NULL,
    category_name VARCHAR(80) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (category_id),
    UNIQUE KEY uq_category_code (category_code),
    UNIQUE KEY uq_category_name (category_name)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dietary_options (
    dietary_option_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    dietary_code VARCHAR(40) NOT NULL,
    dietary_name VARCHAR(80) NOT NULL,
    PRIMARY KEY (dietary_option_id),
    UNIQUE KEY uq_dietary_code (dietary_code)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS quantity_units (
    unit_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    unit_code VARCHAR(30) NOT NULL,
    unit_name VARCHAR(60) NOT NULL,
    PRIMARY KEY (unit_id),
    UNIQUE KEY uq_unit_code (unit_code)
) ENGINE=InnoDB;

INSERT IGNORE INTO roles (role_id, role_code, role_name) VALUES
    (1, 'admin', 'Admin'),
    (2, 'donor', 'Donor'),
    (3, 'acceptor', 'Acceptor');

INSERT IGNORE INTO food_categories (category_code, category_name) VALUES
    ('main_course', 'Main Course'),
    ('snacks', 'Snacks'),
    ('bakery', 'Bakery Items'),
    ('fruits_vegetables', 'Fruits & Vegetables'),
    ('beverages', 'Beverages'),
    ('dairy', 'Dairy Products'),
    ('sweets_desserts', 'Sweets & Desserts'),
    ('packaged_foods', 'Packaged Foods'),
    ('other', 'Other');

INSERT IGNORE INTO dietary_options (dietary_code, dietary_name) VALUES
    ('vegetarian', 'Vegetarian'),
    ('vegan', 'Vegan'),
    ('non_vegetarian', 'Non-vegetarian'),
    ('not_specified', 'Not specified');

INSERT IGNORE INTO quantity_units (unit_code, unit_name) VALUES
    ('packet', 'Packets'),
    ('piece', 'Pieces'),
    ('kg', 'Kilograms'),
    ('gram', 'Grams'),
    ('litre', 'Litres'),
    ('serving', 'Servings'),
    ('box', 'Boxes');