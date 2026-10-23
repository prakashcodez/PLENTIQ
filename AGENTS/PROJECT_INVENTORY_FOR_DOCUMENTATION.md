# PLENTIQ — Project Inventory for Documentation
**Surplus Food Redistribution and Donation Management System**  
*Prepared: 21 October 2026 — Read-only inspection, no project files modified*

---

## 1. Project Structure

### 1.1 Full File Listing

| Path | Type | Size | Purpose |
|---|---|---|---|
| `app.py` | Python | 77 KB | Main Flask application — all routes, business logic, DB access |
| `db_config.py` | Python | 139 B | MySQL connection parameters |
| `test_connection.py` | Python | 573 B | Manual DB connectivity check (prompts for password) |
| `scratch/test_stage3.py` | Python | 19 KB | Automated unit tests for Stage 3 (collection & distribution) |
| `plentiq_setup.sql` | SQL | 5.9 KB | DB creation: tables, constraints, indexes, demo user inserts |
| `plentiq_workflow.sql` | SQL | 9.8 KB | Stored procedures: `sp_approve_donation_request`, `sp_reject_donation_request` |
| `db_cleanup_recommendation.sql` | SQL | 2.2 KB | Documented cleanup script for synthetic test records |
| `static/css/style.css` | CSS | 19 KB | Main application stylesheet (dashboards, forms, nav, tables) |
| `static/css/landing.css` | CSS | 6.2 KB | Landing page stylesheet (standalone, independent of base.html) |
| `static/images/plentiq-logo.png` | Image | 205 KB | Plentiq wordmark logo (1024×341 px, RGBA) |
| `static/images/donor.png` | Image | 693 KB | Donor role illustration (671×588 px, RGBA) |
| `static/images/ngo.png` | Image | 262 KB | NGO role illustration, cropped (499×458 px, RGBA) |
| `static/images/admin.png` | Image | 33 KB | Admin role illustration (512×512 px, RGBA) |
| `templates/base.html` | HTML | 8 KB | Master layout: nav bar, demo switcher, flash messages, footer |
| `templates/landing.html` | HTML | 5.1 KB | Public landing page with 3 role cards and email login link |
| `templates/login.html` | HTML | 3.2 KB | Email + password sign-in form (extends base.html) |
| `templates/index.html` | HTML | 3.3 KB | Home page: food categories grid and platform statistics |
| `templates/listings.html` | HTML | 7.2 KB | Public surplus food listings with category/status/city filters |
| `templates/donations.html` | HTML | 13 KB | Donor food listing submission form (multi-field) |
| `templates/edit_listing.html` | HTML | 5.1 KB | Donor edit form for existing food listings |
| `templates/dashboard_donor.html` | HTML | 18 KB | Donor dashboard: my listings, incoming requests, schedules |
| `templates/dashboard_ngo.html` | HTML | 16 KB | NGO dashboard: available food, my requests, collections, distributions |
| `templates/dashboard_admin.html` | HTML | 21 KB | Admin dashboard: all listings, requests, distributions, platform stats |
| `templates/record_distribution.html` | HTML | 5.4 KB | Distribution recording form for NGO after collection |
| `templates/403.html` | HTML | 1.9 KB | HTTP 403 Forbidden error page |
| `templates/404.html` | HTML | 811 B | HTTP 404 Not Found error page |

### 1.2 Unused / Obsolete Files

| File | Status | Reason |
|---|---|---|
| `test_connection.py` | Development utility | Prompts for password interactively; not integrated into test suite |
| `scratch/test_stage3.py` | Test file in `scratch/` | Was the automated test suite; inserts test records into live DB — should not be re-run against demo DB |
| `db_cleanup_recommendation.sql` | Documentation artifact | Superseded by the Python cleanup script; may be kept as a reference |

---

## 2. Technology Stack

| Component | Technology | Verified Version |
|---|---|---|
| Programming Language | Python | 3.12.0 |
| Web Framework | Flask | 3.1.3 |
| Template Engine | Jinja2 | 3.1.6 |
| WSGI Toolkit | Werkzeug | 3.1.9 |
| Database | MySQL Server | 8.0.46 |
| DB Connector | mysql-connector-python | 26.7.0 |
| Password Hashing | Werkzeug (scrypt) | via `check_password_hash` |
| Image Processing | Pillow | 13.0.0 |
| Testing Framework | pytest | 9.1.1 |
| Frontend | HTML5, CSS3 | — |
| JavaScript | None | Not used (no JS files present) |
| MySQL GUI | MySQL Workbench | 8.0 (installed) |
| Development OS | Windows | — |

> [!NOTE]
> No JavaScript framework is used. All interactivity is server-side (form posts, redirects, Jinja2 conditionals).

---

## 3. Database Design

**Database name:** `plentiq_db`  
**Storage engine:** InnoDB (all tables)  
**Character set:** Default MySQL 8.0 (utf8mb4)

### 3.1 Tables

#### `users`
| Column | Type | Notes |
|---|---|---|
| `user_id` | INT AUTO_INCREMENT | **PK** |
| `full_name` | VARCHAR(100) | NOT NULL |
| `email` | VARCHAR(150) | NOT NULL, UNIQUE INDEX |
| `password_hash` | VARCHAR(255) | scrypt-hashed (Werkzeug) |
| `phone` | VARCHAR(20) | Nullable |
| `role` | ENUM('donor','ngo','admin') | NOT NULL |
| `is_active` | BOOLEAN | DEFAULT TRUE |
| `created_at` | TIMESTAMP | DEFAULT NOW() |

#### `food_categories`
| Column | Type | Notes |
|---|---|---|
| `category_id` | INT AUTO_INCREMENT | **PK** |
| `category_name` | VARCHAR(50) | NOT NULL, UNIQUE INDEX |
| `description` | VARCHAR(255) | — |

**7 seeded categories:** Cooked Meals, Rice and Biryani, Curries and Side Dishes, Packed Meals, Bakery Items, Sweets and Desserts, Other Prepared Food.

#### `food_listings`
| Column | Type | Notes |
|---|---|---|
| `listing_id` | INT AUTO_INCREMENT | **PK** |
| `donor_id` | INT | **FK → users.user_id** (RESTRICT) |
| `category_id` | INT | **FK → food_categories.category_id** (RESTRICT) |
| `food_name` | VARCHAR(120) | NOT NULL |
| `description` | TEXT | — |
| `source_type` | ENUM | Hotel / Restaurant / Wedding/Function / Catering Service / College/Office Event / Community Event / Other |
| `quantity_total` | DECIMAL(10,2) | CHECK > 0 |
| `quantity_available` | DECIMAL(10,2) | CHECK >= 0 AND <= quantity_total |
| `unit` | VARCHAR(30) | DEFAULT 'portions' |
| `expiry_at` | DATETIME | NOT NULL |
| `pickup_address` | VARCHAR(255) | NOT NULL |
| `city` | VARCHAR(100) | NOT NULL |
| `handling_notes` | VARCHAR(500) | Nullable |
| `status` | ENUM | available / fully_allocated / expired / cancelled |
| `created_at` | TIMESTAMP | — |
| `updated_at` | TIMESTAMP | ON UPDATE CURRENT_TIMESTAMP |

**Indexes:** `idx_food_status_expiry(status, expiry_at)`, `idx_food_category(category_id)`, `idx_food_city(city)`

#### `donation_requests`
| Column | Type | Notes |
|---|---|---|
| `request_id` | INT AUTO_INCREMENT | **PK** |
| `listing_id` | INT | **FK → food_listings.listing_id** (RESTRICT) |
| `ngo_id` | INT | **FK → users.user_id** (RESTRICT) |
| `requested_quantity` | DECIMAL(10,2) | CHECK > 0 |
| `request_status` | ENUM | pending / approved / rejected / cancelled |
| `requested_at` | TIMESTAMP | DEFAULT NOW() |
| `reviewed_by` | INT | **FK → users.user_id** (nullable) |
| `reviewed_at` | DATETIME | Nullable |
| `remarks` | VARCHAR(500) | Nullable |

**Indexes:** `idx_request_ngo_status(ngo_id, request_status)`, `idx_request_listing_status(listing_id, request_status)`

#### `collection_schedules`
| Column | Type | Notes |
|---|---|---|
| `schedule_id` | INT AUTO_INCREMENT | **PK** |
| `request_id` | INT | **FK → donation_requests.request_id** (RESTRICT), UNIQUE |
| `scheduled_at` | DATETIME | NOT NULL |
| `status` | ENUM | scheduled / collected / missed |
| `collected_at` | DATETIME | Nullable; set when status = collected |
| `notes` | VARCHAR(500) | Nullable |

> [!IMPORTANT]
> `request_id` has a UNIQUE constraint — one schedule per request maximum.

#### `distribution_records`
| Column | Type | Notes |
|---|---|---|
| `distribution_id` | INT AUTO_INCREMENT | **PK** |
| `schedule_id` | INT | **FK → collection_schedules.schedule_id** (RESTRICT), UNIQUE |
| `recorded_by` | INT | **FK → users.user_id** (RESTRICT) |
| `quantity_distributed` | DECIMAL(10,2) | CHECK > 0 |
| `recipient_count` | INT | CHECK IS NULL OR >= 0 |
| `distributed_at` | DATETIME | DEFAULT NOW() |
| `notes` | VARCHAR(500) | Nullable |

### 3.2 Foreign Key Chain (Cascade Order)

```
users
  └─ food_listings (donor_id)
       └─ donation_requests (listing_id)
            └─ collection_schedules (request_id)
                  └─ distribution_records (schedule_id)
food_categories
  └─ food_listings (category_id)
```

### 3.3 CHECK Constraints

| Constraint | Table | Rule |
|---|---|---|
| `chk_quantity_total` | food_listings | quantity_total > 0 |
| `chk_quantity_available` | food_listings | quantity_available >= 0 AND <= quantity_total |
| `chk_requested_quantity` | donation_requests | requested_quantity > 0 |
| `chk_distribution_quantity` | distribution_records | quantity_distributed > 0 |
| `chk_recipient_count` | distribution_records | recipient_count IS NULL OR >= 0 |

### 3.4 Stored Procedures

Both procedures defined in `plentiq_workflow.sql` and verified present in the database.

#### `sp_approve_donation_request(p_request_id, p_user_id, p_remarks, OUT p_status_code, OUT p_status_message)`
- Opens an ACID transaction (`START TRANSACTION`)
- Locks donation request row with `SELECT ... FOR UPDATE`
- Locks food listing row with `SELECT ... FOR UPDATE` (concurrency control)
- Validates: request is pending, listing is available and not expired, reviewer is authorized (donor or admin), quantity is sufficient
- Decrements `quantity_available` exactly once
- Sets listing status to `fully_allocated` when remaining quantity reaches zero
- Commits all changes atomically; rolls back on any error

#### `sp_reject_donation_request(p_request_id, p_user_id, p_remarks, OUT p_status_code, OUT p_status_message)`
- Row-level locking with `SELECT ... FOR UPDATE`
- Validates request is pending; checks reviewer authorization
- Sets `request_status = 'rejected'` without modifying quantities
- Commits atomically; rolls back on error

### 3.5 Triggers
None (verified: `SHOW TRIGGERS` returned 0 results).

---

## 4. Application Modules

### 4.1 Feature Implementation Status

| Feature | Status | Location |
|---|---|---|
| **Authentication** | ✅ IMPLEMENTED | `app.py` L585–L647 |
| — Email + password login | ✅ | `login()`, `templates/login.html` |
| — Session management (Flask session) | ✅ | `inject_globals()`, session keys: user_id, user_name, email, role |
| — Password verification (scrypt) | ✅ | `check_password_hash` from Werkzeug |
| — Logout | ✅ | `logout()` L650 |
| — Demo account switcher | ✅ | `demo_switch()` L673; restricted to `DEMO_MODE=True` |
| **Role-based access control** | ✅ IMPLEMENTED | |
| — `login_required` decorator | ✅ | L555 |
| — `role_required(*roles)` decorator | ✅ | L566 |
| — Backend ownership checks | ✅ | All routes verify session user_id vs record owner |
| **Landing page** | ✅ IMPLEMENTED | `landing()` L662, `templates/landing.html`, `landing.css` |
| **Home page** | ✅ IMPLEMENTED | `home()` L1159, `templates/index.html` |
| — Redirects unauthenticated users to /landing | ✅ | L1165 |
| — Food categories grid from MySQL | ✅ | SQL: SELECT from food_categories |
| — Platform statistics | ✅ | Subquery counts (listings, users) |
| **Food listing submission (Donor)** | ✅ IMPLEMENTED | `donations()` L1303, `templates/donations.html` |
| — Form validation (server-side) | ✅ | All fields validated in Python before INSERT |
| — Category dropdown from DB | ✅ | Dynamic SELECT from food_categories |
| — Source type validation | ✅ | Against VALID_SOURCE_TYPES whitelist |
| — Expiry datetime validation | ✅ | Must be in the future |
| — INSERT into food_listings | ✅ | Parameterized query |
| **Listing edit (Donor)** | ✅ IMPLEMENTED | `edit_listing()` L1510, `templates/edit_listing.html` |
| — Ownership check | ✅ | donor_id == session user_id |
| — Prevents editing non-editable statuses | ✅ | Only 'available' listings editable |
| **Listing cancellation (Donor)** | ✅ IMPLEMENTED | `cancel_listing()` L1620 |
| — Ownership + status validation | ✅ | — |
| **Community listings browse** | ✅ IMPLEMENTED | `listings()` L1213, `templates/listings.html` |
| — Category filter | ✅ | GET param, dynamic SQL WHERE |
| — Status filter | ✅ | GET param |
| — City filter | ✅ | GET param, LIKE query |
| — Expiry filter (non-expired) | ✅ | `expiry_at > NOW()` |
| **NGO request submission** | ✅ IMPLEMENTED | `request_food()` L1671 |
| — Quantity validation | ✅ | > 0, <= quantity_available |
| — Expiry check | ✅ | Server-side |
| — Self-request prevention | ✅ | ngo_id != donor_id check |
| — INSERT into donation_requests | ✅ | — |
| **NGO request cancellation** | ✅ IMPLEMENTED | `cancel_request()` L1769 |
| **Donor request approval** | ✅ IMPLEMENTED | `approve_request()` L1825 |
| — ACID transaction via stored procedure | ✅ | `sp_approve_donation_request` |
| — Python fallback transaction | ✅ | `_execute_request_approval_python_transaction()` |
| — Row-level locking (FOR UPDATE) | ✅ | Both SP and Python fallback |
| — Quantity deduction | ✅ | Atomic, exactly once |
| — Listing status update | ✅ | available → fully_allocated when qty = 0 |
| **Donor request rejection** | ✅ IMPLEMENTED | `reject_request()` L1875 |
| — Stored procedure | ✅ | `sp_reject_donation_request` |
| **Collection scheduling (Donor)** | ✅ IMPLEMENTED | `schedule_collection()` L1929 |
| — Only for approved requests | ✅ | — |
| — Duplicate prevention | ✅ | UNIQUE constraint on request_id |
| — Datetime validation | ✅ | — |
| **Collection status update** | ✅ IMPLEMENTED | `update_collection_status()` L1980 |
| — collected / missed transitions | ✅ | — |
| — `collected_at` timestamp | ✅ | Set on collected, NULL on missed |
| — Role permissions | ✅ | Donor or NGO involved in request |
| **Distribution recording (NGO)** | ✅ IMPLEMENTED | `record_distribution()` L2026, `templates/record_distribution.html` |
| — Only after collection | ✅ | schedule.status must be 'collected' |
| — Quantity <= approved quantity | ✅ | Server-side check |
| — Duplicate prevention | ✅ | UNIQUE on schedule_id in distribution_records |
| — Role check | ✅ | NGO only (role_required) |
| **Donor dashboard** | ✅ IMPLEMENTED | `dashboard_donor()` L759, `templates/dashboard_donor.html` |
| — My listings | ✅ | JOIN with food_categories |
| — Incoming requests with schedule & distribution | ✅ | Multi-table LEFT JOIN |
| — Summary statistics | ✅ | COUNT/SUM aggregates |
| **NGO dashboard** | ✅ IMPLEMENTED | `dashboard_ngo()` L868, `templates/dashboard_ngo.html` |
| — Available listings (not own donor's) | ✅ | Filter: donor_id != ngo_id |
| — My submitted requests | ✅ | Filter: ngo_id = session user |
| — Collection & distribution history | ✅ | LEFT JOINs |
| **Admin dashboard** | ✅ IMPLEMENTED | `dashboard_admin()` L1007, `templates/dashboard_admin.html` |
| — All listings | ✅ | No donor filter |
| — All requests | ✅ | — |
| — Platform-wide distribution log | ✅ | All distribution_records |
| — Platform statistics | ✅ | Aggregated counts |
| **Search and filters** | ✅ IMPLEMENTED | `listings()` — category, status, city |
| **Analytics/Stats** | ✅ IMPLEMENTED | Donor stats, NGO impact metrics, Admin platform log |
| **JavaScript interactivity** | ❌ MISSING | No JS files; all UI is server-rendered |
| **AJAX/async operations** | ❌ MISSING | All forms use standard POST |
| **Email notifications** | ❌ MISSING | Not implemented |
| **Pagination** | ❌ MISSING | All records loaded at once |
| **File uploads** | ❌ MISSING | — |

---

## 5. Backend and Frontend Mapping

### 5.1 Route → Template → CSS Map

| Route | Method | Handler | Template | CSS Files |
|---|---|---|---|---|
| `/` | GET | `home()` | `index.html` extends `base.html` | `style.css` |
| `/landing` | GET | `landing()` | `landing.html` (standalone) | `landing.css` |
| `/login` | GET/POST | `login()` | `login.html` extends `base.html` | `style.css` |
| `/logout` | GET | `logout()` | — (redirect) | — |
| `/demo/switch/<role>` | GET/POST | `demo_switch()` | — (redirect) | — |
| `/dashboard` | GET | `dashboard()` | — (redirect by role) | — |
| `/dashboard/donor` | GET | `dashboard_donor()` | `dashboard_donor.html` extends `base.html` | `style.css` |
| `/dashboard/ngo` | GET | `dashboard_ngo()` | `dashboard_ngo.html` extends `base.html` | `style.css` |
| `/dashboard/admin` | GET | `dashboard_admin()` | `dashboard_admin.html` extends `base.html` | `style.css` |
| `/listings` | GET | `listings()` | `listings.html` extends `base.html` | `style.css` |
| `/donations` | GET/POST | `donations()` | `donations.html` extends `base.html` | `style.css` |
| `/listings/<id>/edit` | GET/POST | `edit_listing()` | `edit_listing.html` extends `base.html` | `style.css` |
| `/listings/<id>/cancel` | POST | `cancel_listing()` | — (redirect) | — |
| `/listings/<id>/request` | POST | `request_food()` | — (redirect) | — |
| `/requests/<id>/cancel` | POST | `cancel_request()` | — (redirect) | — |
| `/requests/<id>/approve` | POST | `approve_request()` | — (redirect) | — |
| `/requests/<id>/reject` | POST | `reject_request()` | — (redirect) | — |
| `/requests/<id>/schedule` | POST | `schedule_collection()` | — (redirect) | — |
| `/schedules/<id>/status` | POST | `update_collection_status()` | — (redirect) | — |
| `/schedules/<id>/distribute` | GET/POST | `record_distribution()` | `record_distribution.html` extends `base.html` | `style.css` |

### 5.2 Template Inheritance

```
base.html (master layout)
  ├─ login.html
  ├─ index.html
  ├─ listings.html
  ├─ donations.html
  ├─ edit_listing.html
  ├─ dashboard_donor.html
  ├─ dashboard_ngo.html
  ├─ dashboard_admin.html
  ├─ record_distribution.html
  ├─ 403.html
  └─ 404.html

landing.html (standalone — does NOT extend base.html)
```

---

## 6. SQL and DBMS Concepts Used

### 6.1 SQL Query Types

| Concept | Where Used | Academic Relevance |
|---|---|---|
| `SELECT` with `JOIN` | All dashboards (multi-table queries) | Relational algebra, normalization |
| `LEFT JOIN` | Dashboard queries linking optional schedules/distributions | Outer joins |
| Multi-level `LEFT JOIN` | Donor dashboard: listings → requests → schedules → distributions | 4-table join chain |
| Subquery (scalar) | Home page stats (`SELECT COUNT(*) FROM ...`) | Subqueries |
| `WHERE` with `AND`/`OR` | Listings filter, dashboard ownership | Predicate logic |
| `LIKE` | City filter in `/listings` | Pattern matching |
| `ORDER BY` | All listing queries | Sorting |
| `COUNT`, `SUM`, `COALESCE` | Stats aggregation | Aggregate functions |
| `INSERT` | All submission forms | DML |
| `UPDATE` | Approval, status transitions | DML |
| `DELETE` | Cleanup script | DML |
| Parameterized queries (`%s`) | All queries via mysql-connector-python | SQL injection prevention |
| `SELECT ... FOR UPDATE` | Approval/rejection transactions | Row-level locking, concurrency control |
| `START TRANSACTION / COMMIT / ROLLBACK` | Approval and scheduling transactions | ACID properties |
| `ENUM` | role, status, source_type, schedule status | Domain integrity |
| `CHECK` constraints | Quantity validations | Integrity constraints |
| `FOREIGN KEY` with `ON DELETE RESTRICT` | All cross-table references | Referential integrity |
| `UNIQUE` constraint | email, schedule per request, distribution per schedule | Entity integrity |
| Composite index | `idx_food_status_expiry(status, expiry_at)`, `idx_request_ngo_status` | Index optimization |
| Stored procedures with `OUT` parameters | sp_approve_donation_request, sp_reject_donation_request | Server-side business logic |
| `DECLARE EXIT HANDLER FOR SQLEXCEPTION` | Both stored procedures | Exception handling in SQL |
| `LEAVE proc_label` | Stored procedure early exit | Procedural SQL |
| `IF / THEN / ELSE` in SP | All validation blocks | Conditional logic in SQL |

### 6.2 ACID Transaction Implementation

Two implementations co-exist:

1. **MySQL Stored Procedure** (preferred): Full transaction in SQL with `FOR UPDATE` row locking.
2. **Python Fallback** (`_execute_request_approval_python_transaction`): Identical logic in Python using `conn.start_transaction()` and `cursor.execute("SELECT ... FOR UPDATE")`.

The application tries the stored procedure first; if it is not installed (`error 1305`), it falls back automatically.

---

## 7. Testing

### 7.1 Test Files

| File | Framework | Records Modified |
|---|---|---|
| `test_connection.py` | Manual script | None (read-only) |
| `scratch/test_stage3.py` | `unittest` | **Inserts test records into live DB — do NOT re-run** |

### 7.2 test_stage3.py — Test Scenarios

| Test | Description |
|---|---|
| `test_01_schedule_approved_request` | Schedule succeeds for approved request; verified in MySQL |
| `test_02_reject_scheduling_unapproved_request` | Pending/rejected requests cannot be scheduled |
| `test_03_prevent_duplicate_schedules` | Second schedule for same request is rejected (UNIQUE constraint) |
| `test_04_record_completed_collection` | Collected status set; collected_at timestamp stored |
| `test_05_record_missed_collection` | Missed status set; collected_at remains NULL |
| `test_06_prevent_distribution_before_collection` | Distribution blocked if schedule not yet collected |
| `test_07_record_valid_distribution` | Distribution recorded with quantity, recipient count, notes |
| `test_08_reject_distribution_quantity_exceeding_approved` | Quantity > approved_qty is rejected server-side |
| `test_09_prevent_duplicate_distribution_records` | Second distribution on same schedule is rejected (UNIQUE) |
| `test_10_reject_unauthorized_operations` | Donors cannot distribute; NGOs cannot schedule — 403 returned |
| `test_11_dashboard_and_database_verification` | Distribution appears on NGO, Donor and Admin dashboards |

> [!CAUTION]
> `test_stage3.py` creates food listings and requests directly in the live MySQL database using `INSERT` statements. Re-running it against the demo database will pollute the demo data again.

### 7.3 Previous Test Results

All 11 Stage 3 tests were reported as passing (PASS) during development. Test artifacts and console output are not preserved in the project folder.

---

## 8. Documentation Assets

### 8.1 Available Image Assets

| File | Dimensions | Use |
|---|---|---|
| `static/images/plentiq-logo.png` | 1024×341 px | Landing page, login page, nav bar |
| `static/images/donor.png` | 671×588 px | Donor role card on landing page |
| `static/images/ngo.png` | 499×458 px | NGO role card on landing page |
| `static/images/admin.png` | 512×512 px | Admin role card on landing page |

### 8.2 SQL Scripts Available for Report

| File | Content |
|---|---|
| `plentiq_setup.sql` | CREATE TABLE statements, constraints, indexes, demo user inserts |
| `plentiq_workflow.sql` | Complete stored procedure source code with inline documentation |
| `db_cleanup_recommendation.sql` | Data cleanup approach documentation |

### 8.3 Screenshots / Diagrams Not Yet Available

- Application screen captures (landing page, dashboards, forms)
- MySQL Workbench EER diagram
- Database output screenshots (SELECT results, SHOW PROCEDURE STATUS)
- ER diagram (entity-relationship)
- DFD / system architecture diagram
- Test execution console output

---

## 9. Database — Demo Data State (Post-Cleanup)

**Backup created:** `plentiq_db_backup_20261021_123941.sql` (61,554 bytes) in project root.

### Before / After Record Counts

| Table | Before Cleanup | After Cleanup | After Demo Seed | Net Change |
|---|---|---|---|---|
| users | 3 | 3 | 3 | **0** (preserved) |
| food_categories | 7 | 7 | 7 | **0** (preserved) |
| food_listings | 163 | 0 | **4** | -159 |
| donation_requests | 159 | 0 | **3** | -156 |
| collection_schedules | 79 | 0 | **2** | -77 |
| distribution_records | 16 | 0 | **1** | -15 |

### Demo Dataset Summary

| # | Food Item | Total Qty | Available Qty | Status | Workflow Stage |
|---|---|---|---|---|---|
| 1 | Vegetable Biryani | 80 portions | 80 | available | NGO has pending request (40 portions) |
| 2 | Mixed Curry & Rice Combo | 120 portions | 70 | available | 50 approved, collection scheduled tomorrow |
| 3 | Chicken Biryani Surplus | 50 portions | 0 | fully_allocated | Complete: approved → collected → distributed to 85 people |
| 4 | Assorted Bread & Buns | 40 kg | 40 | available | No requests yet; expiring soonest |

**Demo user accounts (unchanged):**

| Role | Name | Email | Password |
|---|---|---|---|
| Donor | Green Leaf Catering | donor.demo@plentiq.local | Demo@123 |
| NGO | Hope Foundation | ngo.demo@plentiq.local | Demo@123 |
| Admin | Plentiq Admin | admin.demo@plentiq.local | Demo@123 |

---

## 10. Missing Evidence for Report

The following items are **not yet available** in the project and will need to be captured or created:

| Item | Type | Priority |
|---|---|---|
| Landing page screenshot | Screen capture | High |
| Login page screenshot | Screen capture | High |
| Donor dashboard screenshot | Screen capture | High |
| NGO dashboard screenshot | Screen capture | High |
| Admin dashboard screenshot | Screen capture | High |
| Food listing form screenshot | Screen capture | High |
| Request approval workflow screenshot | Screen capture | High |
| Collection schedule screenshot | Screen capture | Medium |
| Distribution recording screenshot | Screen capture | Medium |
| MySQL Workbench EER diagram | Diagram | High |
| `SHOW PROCEDURE STATUS` output screenshot | DB output | Medium |
| Stored procedure SQL excerpt | Code screenshot | Medium |
| `SELECT ... FOR UPDATE` transaction excerpt | Code screenshot | Medium |
| ER diagram (drawn or Workbench export) | Diagram | High |
| Data flow diagram | Diagram | Medium |
| System architecture diagram | Diagram | Low |
| Test execution console output | Screenshot | Medium |
| `EXPLAIN` output for a JOIN query | DB output | Low |

---

## Summary

**What is fully implemented:**
- Complete 5-stage donation workflow: listing → request → approval → collection → distribution
- ACID transactions with row-level locking (stored procedures + Python fallback)
- Role-based access control (donor, NGO, admin) enforced server-side
- Three-role landing page with demo account switching
- All six MySQL tables with correct FK chain, CHECK constraints, composite indexes

**Main technical highlights for the report:**
- `SELECT ... FOR UPDATE` prevents double-approval race conditions (concurrency control)
- Stored procedures encapsulate business logic at the DB layer
- Werkzeug scrypt password hashing (industry-standard)
- Full separation of concerns: Python backend, Jinja2 templates, CSS styling

**Key items still needed:**
1. Application screenshots for each page/role
2. MySQL Workbench EER diagram export
3. A brief explanation of why no JavaScript was used (by design: server-rendered)
4. Pagination note (not implemented — acceptable for demo scale)

