from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    abort
)
from werkzeug.security import check_password_hash
import mysql.connector

from db_config import DB_CONFIG


app = Flask(__name__)

# Secret key for Flask session cookies
app.secret_key = os.environ.get("PLENTIQ_SECRET_KEY")
if not app.secret_key:
    raise RuntimeError(
        "PLENTIQ_SECRET_KEY is required. Set it in the process environment before starting Plentiq."
    )

# Demo account switching is opt-in for local assessment only.
app.config["DEMO_MODE"] = os.environ.get("PLENTIQ_DEMO_MODE", "").strip() == "1"

# Predefined demo accounts configured in plentiq_setup.sql
DEMO_ACCOUNTS = {
    "donor": "donor.demo@plentiq.local",
    "ngo": "ngo.demo@plentiq.local",
    "admin": "admin.demo@plentiq.local"
}

VALID_SOURCE_TYPES = [
    "Hotel",
    "Restaurant",
    "Wedding/Function",
    "Catering Service",
    "College/Office Event",
    "Community Event",
    "Other"
]

VALID_STATUSES = [
    "available",
    "fully_allocated",
    "expired",
    "cancelled"
]


def get_db_connection():
    """Establish and return a new MySQL database connection."""
    return mysql.connector.connect(**DB_CONFIG)


# ============================================================================
# ACID Transaction & Stored Procedure Execution Helpers
# ============================================================================

def execute_request_approval(conn, request_id, user_id, remarks=""):
    """
    Execute request approval using either:
    1. The MySQL stored procedure sp_approve_donation_request (if installed in plentiq_db)
    2. Atomic Python/MySQL transaction with SELECT ... FOR UPDATE as a safe fallback
    Returns: (success: bool, status_code: str, message: str)
    """
    cursor = None
    try:
        cursor = conn.cursor()
        result_args = cursor.callproc(
            "sp_approve_donation_request",
            [request_id, user_id, remarks, "", ""]
        )
        status_code = result_args[3]
        status_message = result_args[4]
        conn.commit()
        return (status_code == "SUCCESS"), status_code, status_message
    except mysql.connector.Error as err:
        if err.errno == 1305:  # Stored procedure does not exist in plentiq_db
            app.logger.info("Stored procedure sp_approve_donation_request not installed; using Python transaction.")
            return _execute_request_approval_python_transaction(conn, request_id, user_id, remarks)
        raise
    finally:
        if cursor is not None:
            cursor.close()


def _execute_request_approval_python_transaction(conn, request_id, user_id, remarks=""):
    """
    Pure Python ACID transaction implementation of approval with SELECT ... FOR UPDATE.
    Used when the stored procedure has not yet been executed in MySQL Workbench.
    """
    cursor = None
    try:
        conn.start_transaction()
        cursor = conn.cursor(dictionary=True)

        # 1. Lock and retrieve request
        cursor.execute("""
            SELECT request_id, listing_id, ngo_id, requested_quantity, request_status
            FROM donation_requests
            WHERE request_id = %s
            FOR UPDATE
        """, (request_id,))
        req = cursor.fetchone()

        if not req:
            conn.rollback()
            return False, "NOT_FOUND", "Donation request not found."

        if req["request_status"] != "pending":
            conn.rollback()
            return False, "NOT_PENDING", f"Request is already {req['request_status']}. Duplicate approval prohibited."

        # 2. Lock and retrieve listing
        cursor.execute("""
            SELECT donor_id, quantity_available, status, expiry_at
            FROM food_listings
            WHERE listing_id = %s
            FOR UPDATE
        """, (req["listing_id"],))
        listing = cursor.fetchone()

        if not listing:
            conn.rollback()
            return False, "LISTING_NOT_FOUND", "Associated food listing was not found."

        # 3. Check reviewer authorization
        cursor.execute("SELECT role FROM users WHERE user_id = %s AND is_active = TRUE", (user_id,))
        user = cursor.fetchone()
        if not user:
            conn.rollback()
            return False, "UNAUTHORIZED", "Reviewer user account is invalid or inactive."

        if user["role"] != "admin" and user_id != listing["donor_id"]:
            conn.rollback()
            return False, "UNAUTHORIZED", "Permission denied: Only the listing donor or an administrator may approve this request."

        # 4. Check listing status and expiry
        if listing["status"] != "available":
            conn.rollback()
            return False, "LISTING_UNAVAILABLE", f"Listing is no longer available (current status: {listing['status']})."

        if listing["expiry_at"] <= datetime.now():
            cursor.execute("UPDATE food_listings SET status = 'expired' WHERE listing_id = %s", (req["listing_id"],))
            conn.commit()
            return False, "EXPIRED", "Food listing has passed safe expiry deadline and cannot be approved."

        # 5. Check quantity
        req_qty = req["requested_quantity"]
        qty_avail = listing["quantity_available"]
        if req_qty > qty_avail:
            conn.rollback()
            return False, "INSUFFICIENT_QTY", f"Requested quantity ({req_qty}) exceeds available quantity ({qty_avail})."

        # 6. Update listing
        new_qty = qty_avail - req_qty
        new_status = "fully_allocated" if new_qty == 0 else "available"
        cursor.execute("""
            UPDATE food_listings
            SET quantity_available = %s,
                status = %s
            WHERE listing_id = %s
        """, (new_qty, new_status, req["listing_id"]))

        # 7. Update request
        cursor.execute("""
            UPDATE donation_requests
            SET request_status = 'approved',
                reviewed_by = %s,
                reviewed_at = NOW(),
                remarks = %s
            WHERE request_id = %s
        """, (user_id, remarks or None, request_id))

        conn.commit()
        return True, "SUCCESS", f"Request approved. Quantity deducted: {req_qty}. Remaining: {new_qty}."

    except Exception:
        if conn and conn.is_connected():
            conn.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()


def execute_request_rejection(conn, request_id, user_id, remarks=""):
    """
    Execute request rejection using either stored procedure or Python transaction.
    Does NOT alter listing available quantities.
    """
    cursor = None
    try:
        cursor = conn.cursor()
        result_args = cursor.callproc(
            "sp_reject_donation_request",
            [request_id, user_id, remarks, "", ""]
        )
        status_code = result_args[3]
        status_message = result_args[4]
        conn.commit()
        return (status_code == "SUCCESS"), status_code, status_message
    except mysql.connector.Error as err:
        if err.errno == 1305:  # Stored procedure does not exist in plentiq_db
            return _execute_request_rejection_python_transaction(conn, request_id, user_id, remarks)
        raise
    finally:
        if cursor is not None:
            cursor.close()


def _execute_request_rejection_python_transaction(conn, request_id, user_id, remarks=""):
    """Pure Python ACID transaction implementation of rejection with row locking."""
    cursor = None
    try:
        conn.start_transaction()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT request_id, listing_id, request_status
            FROM donation_requests
            WHERE request_id = %s
            FOR UPDATE
        """, (request_id,))
        req = cursor.fetchone()

        if not req:
            conn.rollback()
            return False, "NOT_FOUND", "Donation request not found."

        if req["request_status"] != "pending":
            conn.rollback()
            return False, "NOT_PENDING", f"Request is already {req['request_status']}. Cannot reject."

        cursor.execute("SELECT donor_id FROM food_listings WHERE listing_id = %s", (req["listing_id"],))
        listing = cursor.fetchone()

        cursor.execute("SELECT role FROM users WHERE user_id = %s AND is_active = TRUE", (user_id,))
        user = cursor.fetchone()

        if user["role"] != "admin" and user_id != listing["donor_id"]:
            conn.rollback()
            return False, "UNAUTHORIZED", "Permission denied: Only the listing donor or an administrator may reject this request."

        cursor.execute("""
            UPDATE donation_requests
            SET request_status = 'rejected',
                reviewed_by = %s,
                reviewed_at = NOW(),
                remarks = %s
            WHERE request_id = %s
        """, (user_id, remarks or None, request_id))

        conn.commit()
        return True, "SUCCESS", "Donation request has been rejected. Listing quantity remains unchanged."

    except Exception:
        if conn and conn.is_connected():
            conn.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()


# ============================================================================
# Stage 3: Collection Scheduling & Distribution Backend Transactions
# ============================================================================

def execute_schedule_collection(conn, request_id, user_id, user_role, scheduled_at_str, notes=None):
    """
    Schedule a pickup for an approved donation request using an ACID transaction.
    - Rejects unapproved (pending, rejected, cancelled) requests.
    - Validates ownership (donor of listing or admin).
    - Prevents duplicate schedules for the same request.
    - Validates datetime format.
    """
    cursor = None
    try:
        if not conn.in_transaction:
            conn.start_transaction()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT dr.request_id, dr.listing_id, dr.request_status,
                   fl.donor_id, fl.food_name, fl.expiry_at
            FROM donation_requests dr
            JOIN food_listings fl ON dr.listing_id = fl.listing_id
            WHERE dr.request_id = %s
            FOR UPDATE
        """, (request_id,))
        req = cursor.fetchone()

        if not req:
            conn.rollback()
            return False, "NOT_FOUND", "Donation request not found."

        if user_role != "admin" and user_id != req["donor_id"]:
            conn.rollback()
            return False, "UNAUTHORIZED", "Permission denied: Only the listing donor or an administrator may schedule collection."

        if req["request_status"] != "approved":
            conn.rollback()
            return False, "INVALID_STATE", f"Cannot schedule collection: Request #{request_id} is {req['request_status']}. Only approved requests can be scheduled."

        # Check for existing schedule
        cursor.execute("SELECT schedule_id FROM collection_schedules WHERE request_id = %s", (request_id,))
        if cursor.fetchone():
            conn.rollback()
            return False, "DUPLICATE", f"A collection schedule already exists for request #{request_id}."

        if not scheduled_at_str or not scheduled_at_str.strip():
            conn.rollback()
            return False, "INVALID_INPUT", "Please specify a valid collection date and time."

        try:
            scheduled_at = datetime.fromisoformat(scheduled_at_str.strip())
        except (ValueError, TypeError):
            conn.rollback()
            return False, "INVALID_INPUT", "Invalid collection date/time format. Please use YYYY-MM-DD HH:MM."

        cursor.execute("""
            INSERT INTO collection_schedules (
                request_id,
                scheduled_at,
                status,
                notes
            )
            VALUES (%s, %s, 'scheduled', %s)
        """, (request_id, scheduled_at, notes.strip() if notes else None))

        conn.commit()
        return True, "SUCCESS", f"Collection pickup successfully scheduled for {scheduled_at.strftime('%d %b %Y, %I:%M %p')}."

    except Exception:
        if conn and conn.is_connected():
            conn.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()


def execute_update_collection_status(conn, schedule_id, user_id, user_role, new_status, notes=None):
    """
    Update status of a collection schedule ('collected' or 'missed') with ACID transaction.
    - Enforces participant authorization (donor, requesting NGO, or admin).
    - Sets collected_at = NOW() when status becomes 'collected'.
    - Prevents invalid status transitions (e.g. reverting collected when distribution exists).
    """
    cursor = None
    try:
        if new_status not in ("collected", "missed"):
            return False, "INVALID_STATUS", f"Invalid collection status {new_status}. Allowed: collected, missed."

        if not conn.in_transaction:
            conn.start_transaction()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT cs.schedule_id, cs.request_id, cs.scheduled_at, cs.status, cs.collected_at,
                   dr.ngo_id, dr.requested_quantity,
                   fl.donor_id, fl.food_name, fl.unit
            FROM collection_schedules cs
            JOIN donation_requests dr ON cs.request_id = dr.request_id
            JOIN food_listings fl ON dr.listing_id = fl.listing_id
            WHERE cs.schedule_id = %s
            FOR UPDATE
        """, (schedule_id,))
        sched = cursor.fetchone()

        if not sched:
            conn.rollback()
            return False, "NOT_FOUND", "Collection schedule not found."

        if user_role != "admin" and user_id not in (sched["donor_id"], sched["ngo_id"]):
            conn.rollback()
            return False, "UNAUTHORIZED", "Permission denied: Only authorized participants (donor, requesting NGO, or admin) may update collection status."

        # Prevent invalid transitions
        if sched["status"] == "collected" and new_status != "collected":
            cursor.execute("SELECT distribution_id FROM distribution_records WHERE schedule_id = %s", (schedule_id,))
            if cursor.fetchone():
                conn.rollback()
                return False, "DISTRIBUTION_EXISTS", "Cannot alter collection status: A community distribution record has already been submitted for this collection."
            conn.rollback()
            return False, "INVALID_TRANSITION", "Collection has already been marked as completed."

        if new_status == "collected":
            cursor.execute("""
                UPDATE collection_schedules
                SET status = 'collected',
                    collected_at = NOW(),
                    notes = COALESCE(%s, notes)
                WHERE schedule_id = %s
            """, (notes.strip() if notes else None, schedule_id))
        elif new_status == "missed":
            cursor.execute("""
                UPDATE collection_schedules
                SET status = 'missed',
                    collected_at = NULL,
                    notes = COALESCE(%s, notes)
                WHERE schedule_id = %s
            """, (notes.strip() if notes else None, schedule_id))

        conn.commit()
        return True, "SUCCESS", f"Collection schedule #{schedule_id} marked as {new_status}."

    except Exception:
        if conn and conn.is_connected():
            conn.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()


def execute_record_distribution(conn, schedule_id, user_id, user_role, quantity_distributed, recipient_count, distributed_at_str, notes=None):
    """
    Record community distribution for a collected food batch.
    - Requires collection status to be 'collected'.
    - Validates NGO ownership (requesting NGO or admin). Donors cannot record distribution.
    - Requires positive quantity.
    - Ensures quantity_distributed <= requested_quantity.
    - Prevents duplicate distribution records for the same schedule.
    """
    cursor = None
    try:
        if not conn.in_transaction:
            conn.start_transaction()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT cs.schedule_id, cs.status AS schedule_status, cs.collected_at,
                   dr.request_id, dr.ngo_id, dr.requested_quantity,
                   fl.food_name, fl.unit,
                   dist.distribution_id
            FROM collection_schedules cs
            JOIN donation_requests dr ON cs.request_id = dr.request_id
            JOIN food_listings fl ON dr.listing_id = fl.listing_id
            LEFT JOIN distribution_records dist ON cs.schedule_id = dist.schedule_id
            WHERE cs.schedule_id = %s
            FOR UPDATE
        """, (schedule_id,))
        item = cursor.fetchone()

        if not item:
            conn.rollback()
            return False, "NOT_FOUND", "Collection schedule not found."

        if user_role != "admin" and user_id != item["ngo_id"]:
            conn.rollback()
            return False, "UNAUTHORIZED", "Permission denied: Only the requesting NGO or an administrator may record food distribution."

        # Must be collected
        if item["schedule_status"] != "collected":
            conn.rollback()
            return False, "NOT_COLLECTED", f"Cannot record distribution: Collection status is {item['schedule_status']}. Food must be marked as collected first."

        # Duplicate check
        if item["distribution_id"] is not None:
            conn.rollback()
            return False, "DUPLICATE", "Distribution has already been recorded for this collection schedule."

        # Quantity checks
        try:
            qty = Decimal(str(quantity_distributed))
            if qty <= 0:
                conn.rollback()
                return False, "INVALID_QTY", "Distribution quantity must be greater than zero."
            if qty > item["requested_quantity"]:
                conn.rollback()
                return False, "QTY_EXCEEDED", f"Distribution quantity ({qty} {item['unit']}) cannot exceed approved collected quantity ({item['requested_quantity']} {item['unit']})."
        except (InvalidOperation, ValueError, TypeError):
            conn.rollback()
            return False, "INVALID_QTY", "Invalid distribution quantity."

        # Recipient count check
        rec_count = None
        if recipient_count is not None and str(recipient_count).strip() != "":
            try:
                rec_count = int(recipient_count)
                if rec_count < 0:
                    conn.rollback()
                    return False, "INVALID_RECIPIENT", "Recipient count cannot be negative."
            except (ValueError, TypeError):
                conn.rollback()
                return False, "INVALID_RECIPIENT", "Recipient count must be a whole number."

        # Datetime check
        if distributed_at_str and str(distributed_at_str).strip():
            try:
                dist_at = datetime.fromisoformat(str(distributed_at_str).strip())
            except (ValueError, TypeError):
                conn.rollback()
                return False, "INVALID_INPUT", "Invalid distribution date/time format."
        else:
            dist_at = datetime.now()

        cursor.execute("""
            INSERT INTO distribution_records (
                schedule_id,
                recorded_by,
                quantity_distributed,
                recipient_count,
                distributed_at,
                notes
            )
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            schedule_id,
            user_id,
            qty,
            rec_count,
            dist_at,
            notes.strip() if notes else None
        ))

        conn.commit()
        return True, "SUCCESS", f"Successfully recorded distribution of {qty} {item['unit']}!"

    except Exception:
        if conn and conn.is_connected():
            conn.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()


# ============================================================================
# Context Processors & Decorators
# ============================================================================

@app.context_processor
def inject_globals():
    """Make current user and system flags accessible across all Jinja templates."""
    return {
        "current_user": {
            "id": session.get("user_id"),
            "name": session.get("user_name"),
            "email": session.get("email"),
            "role": session.get("role"),
            "is_authenticated": "user_id" in session
        },
        "demo_mode": app.config.get("DEMO_MODE", False),
        "now": datetime.now()
    }


def login_required(f):
    """Ensure user is logged in before accessing the view."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please sign in to access this page.", "warning")
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated_function


def role_required(*allowed_roles):
    """Enforce server-side role check for protected endpoints."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "user_id" not in session:
                flash("Please sign in to access this page.", "warning")
                return redirect(url_for("login", next=request.path))
            if session.get("role") not in allowed_roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ============================================================================
# Authentication & Demo Account Switcher
# ============================================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate registered users using email and hashed passwords."""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please enter both email and password.", "error")
            return render_template("login.html", email=email)

        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)

            cursor.execute("""
                SELECT user_id, full_name, email, password_hash, role, is_active
                FROM users
                WHERE email = %s
            """, (email,))

            user = cursor.fetchone()

            if user and user["is_active"] and check_password_hash(user["password_hash"], password):
                session.clear()
                session["user_id"] = user["user_id"]
                session["user_name"] = user["full_name"]
                session["email"] = user["email"]
                session["role"] = user["role"]

                flash(f"Welcome back, {user['full_name']}!", "success")

                next_url = request.args.get("next")
                if next_url and next_url.startswith("/") and not next_url.startswith("//") and not next_url.startswith("/dashboard"):
                    return redirect(next_url)
                if user["role"] == "donor":
                    return redirect(url_for("dashboard_donor"))
                elif user["role"] == "ngo":
                    return redirect(url_for("dashboard_ngo"))
                elif user["role"] == "admin":
                    return redirect(url_for("dashboard_admin"))
                return redirect(url_for("dashboard"))

            flash("Invalid email or password.", "error")
            return render_template("login.html", email=email)

        except mysql.connector.Error:
            app.logger.exception("Database error during login")
            flash("Unable to connect to the database. Please try again.", "error")
            return render_template("login.html", email=email), 500

        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

    if "user_id" in session:
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    """Clear the Flask session and log out the user."""
    session.clear()
    return redirect(url_for("landing"))


# ============================================================================
# Landing Page (unauthenticated entry point)
# ============================================================================

def _fetch_home_featured_listings(cursor, exclude_donor_id=None):
    """Fetch a small preview of listings that can currently be requested."""
    query = """
        SELECT fl.listing_id, fl.food_name, fc.category_name,
               fl.quantity_available, fl.unit, fl.city, fl.expiry_at
        FROM food_listings AS fl
        JOIN food_categories AS fc ON fl.category_id = fc.category_id
        WHERE fl.status = 'available'
          AND fl.quantity_available > 0
          AND fl.expiry_at IS NOT NULL
          AND fl.expiry_at > NOW()
    """
    params = []
    if exclude_donor_id is not None:
        query += " AND fl.donor_id <> %s"
        params.append(exclude_donor_id)
    query += " ORDER BY fl.created_at DESC, fl.listing_id DESC LIMIT 4"
    cursor.execute(query, tuple(params))
    return cursor.fetchall()


@app.route("/landing")
def landing():
    """
    Public landing page shown to unauthenticated visitors.
    Authenticated users are redirected to their role-specific home page.
    """
    if "user_id" in session:
        return redirect(url_for("home"))
    return render_template("landing.html")


@app.route("/demo/switch/<role>", methods=["GET", "POST"])
def demo_switch(role):
    """
    Switch active session to one of the 3 predefined demo accounts.
    Restricted to local demonstration mode and predefined accounts.
    """
    if not app.config.get("DEMO_MODE", False):
        abort(403)

    target_role = role.lower().strip()
    if target_role not in DEMO_ACCOUNTS:
        flash(f"Invalid demo role: {role}. Must be donor, ngo, or admin.", "error")
        return redirect(url_for("home"))

    target_email = DEMO_ACCOUNTS[target_role]
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT user_id, full_name, email, role, is_active
            FROM users
            WHERE email = %s AND is_active = TRUE
        """, (target_email,))

        user = cursor.fetchone()

        if user is None:
            flash(f"Demo account for {target_role} was not found in the database.", "error")
            return redirect(url_for("home"))

        session.clear()
        session["user_id"] = user["user_id"]
        session["user_name"] = user["full_name"]
        session["email"] = user["email"]
        session["role"] = user["role"]

        flash(
            f"Switched session to {user['full_name']} ({user['role'].upper()}).",
            "success"
        )

        return redirect(url_for("home"))

    except mysql.connector.Error:
        app.logger.exception("Database error during demo account switch")
        flash("Could not switch demo account due to a database error.", "error")
        return redirect(url_for("home"))

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


# ============================================================================
# Dashboards & Role Separation
# ============================================================================

@app.route("/dashboard")
@login_required
def dashboard():
    """Route logged-in user to their respective role dashboard."""
    role = session.get("role")
    if role == "donor":
        return redirect(url_for("dashboard_donor"))
    elif role == "ngo":
        return redirect(url_for("dashboard_ngo"))
    elif role == "admin":
        return redirect(url_for("dashboard_admin"))
    else:
        flash("Unrecognized user role.", "error")
        return redirect(url_for("home"))


@app.route("/dashboard/donor")
@role_required("donor", "admin")
def dashboard_donor():
    """Donor dashboard: displays donor's listings and incoming food requests."""
    donor_id = session.get("user_id")
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Retrieve donor's own listings
        cursor.execute("""
            SELECT
                fl.listing_id,
                fl.food_name,
                fl.description,
                fc.category_name,
                fl.source_type,
                fl.quantity_total,
                fl.quantity_available,
                fl.unit,
                fl.expiry_at,
                fl.pickup_address,
                fl.city,
                fl.status,
                fl.created_at
            FROM food_listings AS fl
            JOIN food_categories AS fc
                ON fl.category_id = fc.category_id
            WHERE fl.donor_id = %s
            ORDER BY fl.listing_id DESC
        """, (donor_id,))
        my_listings = cursor.fetchall()

        # Retrieve incoming requests for donor's listings with schedules & distributions
        cursor.execute("""
            SELECT
                dr.request_id,
                dr.listing_id,
                fl.food_name,
                fl.quantity_available,
                fl.unit,
                fl.status AS listing_status,
                fl.expiry_at,
                u_ngo.full_name AS ngo_name,
                u_ngo.email AS ngo_email,
                dr.requested_quantity,
                dr.request_status,
                dr.requested_at,
                dr.reviewed_at,
                dr.remarks,
                cs.schedule_id,
                cs.scheduled_at,
                cs.status AS schedule_status,
                cs.collected_at,
                cs.notes AS schedule_notes,
                dist.distribution_id,
                dist.quantity_distributed,
                dist.recipient_count,
                dist.distributed_at
            FROM donation_requests AS dr
            JOIN food_listings AS fl
                ON dr.listing_id = fl.listing_id
            JOIN users AS u_ngo
                ON dr.ngo_id = u_ngo.user_id
            LEFT JOIN collection_schedules AS cs
                ON dr.request_id = cs.request_id
            LEFT JOIN distribution_records AS dist
                ON cs.schedule_id = dist.schedule_id
            WHERE fl.donor_id = %s
            ORDER BY dr.requested_at DESC
        """, (donor_id,))
        incoming_requests = cursor.fetchall()

        # Donor summary stats
        cursor.execute("""
            SELECT
                COUNT(*) AS total_listings,
                COALESCE(SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END), 0) AS active_listings,
                COALESCE(SUM(quantity_total), 0) AS total_quantity
            FROM food_listings
            WHERE donor_id = %s
        """, (donor_id,))
        stats = cursor.fetchone()

        min_schedule_time = datetime.now().strftime("%Y-%m-%dT%H:%M")

        return render_template(
            "dashboard_donor.html",
            my_listings=my_listings,
            incoming_requests=incoming_requests,
            stats=stats,
            min_schedule_time=min_schedule_time
        )

    except mysql.connector.Error:
        app.logger.exception("Error loading donor dashboard")
        flash("Unable to load donor dashboard.", "error")
        return redirect(url_for("home"))

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/dashboard/ngo")
@role_required("ngo", "admin")
def dashboard_ngo():
    """NGO dashboard: displays available surplus food listings and submitted requests."""
    ngo_id = session.get("user_id")
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Available listings with donor organization names
        cursor.execute("""
            SELECT
                fl.listing_id,
                fl.donor_id,
                fl.food_name,
                fl.description,
                fc.category_name,
                u.full_name AS donor_name,
                fl.source_type,
                fl.quantity_available,
                fl.unit,
                fl.expiry_at,
                fl.pickup_address,
                fl.city,
                fl.handling_notes,
                fl.status
            FROM food_listings AS fl
            JOIN food_categories AS fc
                ON fl.category_id = fc.category_id
            JOIN users AS u
                ON fl.donor_id = u.user_id
            WHERE fl.status = 'available'
              AND fl.expiry_at > NOW()
              AND fl.quantity_available > 0
            ORDER BY fl.expiry_at ASC
        """)
        available_listings = cursor.fetchall()

        # Retrieve requests submitted by this NGO with schedules & distributions
        cursor.execute("""
            SELECT
                dr.request_id,
                dr.listing_id,
                fl.food_name,
                fl.unit,
                fl.expiry_at,
                fl.pickup_address,
                fl.city,
                u_donor.full_name AS donor_name,
                fc.category_name,
                dr.requested_quantity,
                dr.request_status,
                dr.requested_at,
                dr.reviewed_at,
                dr.remarks,
                cs.schedule_id,
                cs.scheduled_at,
                cs.status AS schedule_status,
                cs.collected_at,
                cs.notes AS schedule_notes,
                dist.distribution_id,
                dist.quantity_distributed,
                dist.recipient_count,
                dist.distributed_at,
                dist.notes AS distribution_notes
            FROM donation_requests AS dr
            JOIN food_listings AS fl
                ON dr.listing_id = fl.listing_id
            JOIN food_categories AS fc
                ON fl.category_id = fc.category_id
            JOIN users AS u_donor
                ON fl.donor_id = u_donor.user_id
            LEFT JOIN collection_schedules AS cs
                ON dr.request_id = cs.request_id
            LEFT JOIN distribution_records AS dist
                ON cs.schedule_id = dist.schedule_id
            WHERE dr.ngo_id = %s
            ORDER BY dr.requested_at DESC
        """, (ngo_id,))
        my_requests = cursor.fetchall()

        # Retrieve distributions recorded by this NGO
        cursor.execute("""
            SELECT
                dist.distribution_id,
                dist.schedule_id,
                dist.quantity_distributed,
                dist.recipient_count,
                dist.distributed_at,
                dist.notes,
                fl.food_name,
                fl.unit,
                u_donor.full_name AS donor_name,
                cs.collected_at
            FROM distribution_records dist
            JOIN collection_schedules cs ON dist.schedule_id = cs.schedule_id
            JOIN donation_requests dr ON cs.request_id = dr.request_id
            JOIN food_listings fl ON dr.listing_id = fl.listing_id
            JOIN users u_donor ON fl.donor_id = u_donor.user_id
            WHERE dr.ngo_id = %s
            ORDER BY dist.distributed_at DESC
        """, (ngo_id,))
        my_distributions = cursor.fetchall()

        # NGO rescue overview stats
        cursor.execute("""
            SELECT
                COUNT(*) AS available_count,
                COUNT(DISTINCT city) AS cities_count,
                COALESCE(SUM(quantity_available), 0) AS total_available_portions
            FROM food_listings
            WHERE status = 'available'
              AND expiry_at > NOW()
        """)
        stats = cursor.fetchone()

        return render_template(
            "dashboard_ngo.html",
            available_listings=available_listings,
            my_requests=my_requests,
            my_distributions=my_distributions,
            stats=stats
        )

    except mysql.connector.Error:
        app.logger.exception("Error loading NGO dashboard")
        flash("Unable to load NGO dashboard.", "error")
        return redirect(url_for("home"))

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/dashboard/admin")
@role_required("admin")
def dashboard_admin():
    """Administrator dashboard: system-wide oversight of users, listings and requests."""
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT user_id, full_name, email, role, is_active, created_at
            FROM users
            ORDER BY user_id ASC
        """)
        users = cursor.fetchall()

        cursor.execute("""
            SELECT
                fl.listing_id,
                fl.food_name,
                fc.category_name,
                u.full_name AS donor_name,
                fl.source_type,
                fl.quantity_total,
                fl.quantity_available,
                fl.unit,
                fl.expiry_at,
                fl.city,
                fl.status,
                fl.created_at
            FROM food_listings AS fl
            JOIN food_categories AS fc
                ON fl.category_id = fc.category_id
            JOIN users AS u
                ON fl.donor_id = u.user_id
            ORDER BY fl.listing_id DESC
        """)
        listings = cursor.fetchall()

        # Retrieve all requests platform-wide with schedules and distributions
        cursor.execute("""
            SELECT
                dr.request_id,
                dr.listing_id,
                fl.food_name,
                fl.quantity_available,
                fl.unit,
                fl.status AS listing_status,
                fl.expiry_at,
                fl.pickup_address,
                fl.city,
                u_donor.full_name AS donor_name,
                u_ngo.full_name AS ngo_name,
                dr.requested_quantity,
                dr.request_status,
                dr.requested_at,
                dr.reviewed_at,
                dr.remarks,
                cs.schedule_id,
                cs.scheduled_at,
                cs.status AS schedule_status,
                cs.collected_at,
                cs.notes AS schedule_notes,
                dist.distribution_id,
                dist.quantity_distributed,
                dist.recipient_count,
                dist.distributed_at,
                dist.notes AS distribution_notes
            FROM donation_requests AS dr
            JOIN food_listings AS fl
                ON dr.listing_id = fl.listing_id
            JOIN users AS u_donor
                ON fl.donor_id = u_donor.user_id
            JOIN users AS u_ngo
                ON dr.ngo_id = u_ngo.user_id
            LEFT JOIN collection_schedules AS cs
                ON dr.request_id = cs.request_id
            LEFT JOIN distribution_records AS dist
                ON cs.schedule_id = dist.schedule_id
            ORDER BY dr.requested_at DESC
        """)
        all_requests = cursor.fetchall()

        # Retrieve all distribution records platform-wide
        cursor.execute("""
            SELECT
                dist.distribution_id,
                dist.schedule_id,
                dist.quantity_distributed,
                dist.recipient_count,
                dist.distributed_at,
                dist.notes,
                u_rec.full_name AS recorded_by_name,
                u_ngo.full_name AS ngo_name,
                fl.food_name,
                fl.unit,
                u_donor.full_name AS donor_name,
                cs.collected_at
            FROM distribution_records dist
            JOIN collection_schedules cs ON dist.schedule_id = cs.schedule_id
            JOIN donation_requests dr ON cs.request_id = dr.request_id
            JOIN food_listings fl ON dr.listing_id = fl.listing_id
            JOIN users u_donor ON fl.donor_id = u_donor.user_id
            JOIN users u_ngo ON dr.ngo_id = u_ngo.user_id
            JOIN users u_rec ON dist.recorded_by = u_rec.user_id
            ORDER BY dist.distributed_at DESC
        """)
        all_distributions = cursor.fetchall()

        cursor.execute("""
            SELECT
                (SELECT COUNT(*) FROM users) AS users_count,
                (SELECT COUNT(*) FROM food_categories) AS categories_count,
                (SELECT COUNT(*) FROM food_listings) AS total_listings,
                (SELECT COUNT(*) FROM food_listings WHERE status = 'available' AND expiry_at > NOW()) AS available_listings,
                (SELECT COUNT(*) FROM donation_requests WHERE request_status = 'pending') AS pending_requests_count,
                (SELECT COUNT(*) FROM collection_schedules WHERE status = 'collected') AS collected_count,
                (SELECT COUNT(*) FROM distribution_records) AS distributions_count,
                (SELECT COALESCE(SUM(quantity_distributed), 0) FROM distribution_records) AS total_distributed_portions
        """)
        stats = cursor.fetchone()

        min_schedule_time = datetime.now().strftime("%Y-%m-%dT%H:%M")

        return render_template(
            "dashboard_admin.html",
            users=users,
            listings=listings,
            all_requests=all_requests,
            all_distributions=all_distributions,
            stats=stats,
            min_schedule_time=min_schedule_time
        )

    except mysql.connector.Error:
        app.logger.exception("Error loading Admin dashboard")
        flash("Unable to load administrator dashboard.", "error")
        return redirect(url_for("home"))

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


# ============================================================================
# Public / Shared Views
# ============================================================================

@app.route("/")
def home():
    """
    Render a concise home page for the signed-in role.
    """
    if "user_id" not in session:
        return redirect(url_for("landing"))

    role = session.get("role")
    user_id = session.get("user_id")
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        home_data = {
            "home_role": role,
            "home_name": session.get("user_name") or "there",
            "stats": {},
            "recent_listings": [],
            "recent_requests": [],
            "featured_listings": [],
            "error": False,
        }

        if role == "donor":
            cursor.execute("""
                SELECT COUNT(*) AS listing_count,
                       COALESCE(SUM(CASE WHEN status = 'available'
                         AND quantity_available > 0 AND expiry_at > NOW()
                         THEN 1 ELSE 0 END), 0) AS available_count
                FROM food_listings
                WHERE donor_id = %s
            """, (user_id,))
            home_data["stats"] = cursor.fetchone()

            cursor.execute("""
                SELECT listing_id, food_name, quantity_available, unit,
                       city, expiry_at, status
                FROM food_listings
                WHERE donor_id = %s
                ORDER BY created_at DESC, listing_id DESC
                LIMIT 4
            """, (user_id,))
            home_data["recent_listings"] = cursor.fetchall()

            cursor.execute("""
                SELECT dr.request_id, dr.request_status, dr.requested_at,
                       dr.requested_quantity, fl.food_name, fl.unit
                FROM donation_requests AS dr
                JOIN food_listings AS fl ON dr.listing_id = fl.listing_id
                WHERE fl.donor_id = %s
                ORDER BY dr.requested_at DESC
                LIMIT 5
            """, (user_id,))
            home_data["recent_requests"] = cursor.fetchall()

        elif role == "ngo":
            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM donation_requests
                     WHERE ngo_id = %s AND request_status IN ('pending', 'approved')) AS active_requests,
                    (SELECT COUNT(*) FROM collection_schedules AS cs
                     JOIN donation_requests AS dr ON cs.request_id = dr.request_id
                     WHERE dr.ngo_id = %s AND cs.status = 'scheduled'
                       AND cs.scheduled_at >= NOW()) AS upcoming_collections
            """, (user_id, user_id))
            home_data["stats"] = cursor.fetchone()
            home_data["featured_listings"] = _fetch_home_featured_listings(
                cursor, exclude_donor_id=user_id
            )

            cursor.execute("""
                SELECT dr.request_id, dr.request_status, dr.requested_at,
                       dr.requested_quantity, fl.food_name, fl.unit,
                       cs.scheduled_at, cs.status AS schedule_status
                FROM donation_requests AS dr
                JOIN food_listings AS fl ON dr.listing_id = fl.listing_id
                LEFT JOIN collection_schedules AS cs ON dr.request_id = cs.request_id
                WHERE dr.ngo_id = %s
                ORDER BY dr.requested_at DESC
                LIMIT 5
            """, (user_id,))
            home_data["recent_requests"] = cursor.fetchall()

        elif role == "admin":
            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM users) AS users_count,
                    (SELECT COUNT(*) FROM food_listings) AS listing_count,
                    (SELECT COUNT(*) FROM food_listings WHERE status = 'available'
                     AND quantity_available > 0 AND expiry_at > NOW()) AS available_count,
                    (SELECT COUNT(*) FROM donation_requests
                     WHERE request_status = 'pending') AS pending_requests
            """)
            home_data["stats"] = cursor.fetchone()

            cursor.execute("""
                SELECT dr.request_id, dr.request_status, dr.requested_at,
                       dr.requested_quantity, fl.food_name, fl.unit,
                       u.full_name AS recipient_name
                FROM donation_requests AS dr
                JOIN food_listings AS fl ON dr.listing_id = fl.listing_id
                JOIN users AS u ON dr.ngo_id = u.user_id
                ORDER BY dr.requested_at DESC
                LIMIT 5
            """)
            home_data["recent_requests"] = cursor.fetchall()
        else:
            session.clear()
            flash("Your account role is not recognized. Please sign in again.", "error")
            return redirect(url_for("login"))

        return render_template("index.html", **home_data)

    except mysql.connector.Error:
        app.logger.exception("Could not load role-specific home page")
        return render_template(
            "index.html",
            home_role=role,
            home_name=session.get("user_name") or "there",
            stats={},
            recent_listings=[],
            recent_requests=[],
            featured_listings=[],
            error=True,
        ), 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/listings")
def listings():
    """Browse surplus food listings with optional category, status and city filters."""
    category_filter = request.args.get("category", "").strip()
    status_filter = request.args.get("status", "").strip()
    city_filter = request.args.get("city", "").strip()

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT category_id, category_name
            FROM food_categories
            ORDER BY category_name
        """)
        categories = cursor.fetchall()

        query = """
            SELECT
                fl.listing_id,
                fl.donor_id,
                fl.food_name,
                fl.description,
                fc.category_name,
                u.full_name AS donor_name,
                fl.source_type,
                fl.quantity_total,
                fl.quantity_available,
                fl.unit,
                fl.expiry_at,
                fl.pickup_address,
                fl.city,
                fl.handling_notes,
                fl.status,
                fl.created_at
            FROM food_listings AS fl
            JOIN food_categories AS fc
                ON fl.category_id = fc.category_id
            JOIN users AS u
                ON fl.donor_id = u.user_id
            WHERE fl.status = 'available'
              AND fl.quantity_available > 0
              AND fl.expiry_at IS NOT NULL
              AND fl.expiry_at > NOW()
        """
        params = []

        if category_filter:
            query += " AND fl.category_id = %s"
            params.append(category_filter)

        if status_filter:
            query += " AND fl.status = %s"
            params.append(status_filter)

        if city_filter:
            query += " AND fl.city LIKE %s"
            params.append(f"%{city_filter}%")

        query += " ORDER BY fl.listing_id DESC"

        cursor.execute(query, tuple(params))
        listings_data = cursor.fetchall()

        return render_template(
            "listings.html",
            listings=listings_data,
            categories=categories,
            selected_category=category_filter,
            selected_status=status_filter,
            selected_city=city_filter
        )

    except mysql.connector.Error:
        app.logger.exception("Could not retrieve food listings directory")
        flash("Unable to load listings directory.", "error")
        return redirect(url_for("home"))

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


# ============================================================================
# Donation Creation & Management
# ============================================================================

@app.route("/donations", methods=["GET", "POST"])
def donations():
    """
    Manage surplus food donations:
    - GET: Display donation creation form and all current listings.
    - POST: Validate and record a new surplus listing using authenticated donor ID.
    """
    min_expiry = datetime.now().strftime("%Y-%m-%dT%H:%M")

    # Role-based restriction: NGOs browse and claim food rather than create listings
    if session.get("role") == "ngo":
        flash("Only food donors and administrators can create donation listings.", "error")
        return redirect(url_for("dashboard_ngo"))

    # POST: Save a new food listing
    if request.method == "POST":

        if "user_id" not in session:
            flash("Please sign in to record a food donation.", "warning")
            return redirect(url_for("login", next=url_for("donations")))

        if session.get("role") not in ("donor", "admin"):
            flash("Only food donors and administrators can create donation listings.", "error")
            return redirect(url_for("donations"))

        food_name = request.form.get("food_name", "").strip()
        description = request.form.get("description", "").strip()
        category_id_raw = request.form.get("category_id", "").strip()
        source_type = request.form.get("source_type", "").strip()
        unit = request.form.get("unit", "portions").strip()
        pickup_address = request.form.get("pickup_address", "").strip()
        city = request.form.get("city", "").strip()
        handling_notes = request.form.get("handling_notes", "").strip()
        quantity_raw = request.form.get("quantity_total", "").strip()
        expiry_raw = request.form.get("expiry_at", "").strip()

        try:
            quantity_total = Decimal(quantity_raw)
            if quantity_total <= 0:
                flash("Quantity must be greater than zero.", "error")
                return redirect(url_for("donations"))
        except (InvalidOperation, ValueError):
            flash("Please enter a valid numeric quantity.", "error")
            return redirect(url_for("donations"))

        try:
            expiry_at = datetime.strptime(expiry_raw, "%Y-%m-%dT%H:%M")
            if expiry_at <= datetime.now():
                flash("Safe expiry time must be in the future.", "error")
                return redirect(url_for("donations"))
        except ValueError:
            flash("Please enter a valid date and time for expiry.", "error")
            return redirect(url_for("donations"))

        try:
            category_id = int(category_id_raw)
        except ValueError:
            flash("Please select a valid food category.", "error")
            return redirect(url_for("donations"))

        if source_type not in VALID_SOURCE_TYPES:
            flash("Please select an authorized food source type.", "error")
            return redirect(url_for("donations"))

        required_fields = [food_name, pickup_address, city, unit]
        if not all(required_fields):
            flash("Please complete all required fields.", "error")
            return redirect(url_for("donations"))

        donor_id = session["user_id"]
        connection = None
        cursor = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor()

            cursor.execute("""
                SELECT user_id, role, is_active
                FROM users
                WHERE user_id = %s
            """, (donor_id,))
            donor_record = cursor.fetchone()

            if donor_record is None or not donor_record[2]:
                flash("Your user account is inactive or not found.", "error")
                return redirect(url_for("donations"))

            cursor.execute("""
                SELECT category_id
                FROM food_categories
                WHERE category_id = %s
            """, (category_id,))
            if cursor.fetchone() is None:
                flash("The selected food category does not exist.", "error")
                return redirect(url_for("donations"))

            cursor.execute("""
                INSERT INTO food_listings (
                    donor_id,
                    category_id,
                    food_name,
                    description,
                    source_type,
                    quantity_total,
                    quantity_available,
                    unit,
                    expiry_at,
                    pickup_address,
                    city,
                    handling_notes,
                    status
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, 'available'
                )
            """, (
                donor_id,
                category_id,
                food_name,
                description or None,
                source_type,
                quantity_total,
                quantity_total,
                unit,
                expiry_at,
                pickup_address,
                city,
                handling_notes or None
            ))

            connection.commit()
            flash("Donation listing created successfully!", "success")

        except mysql.connector.Error:
            if connection is not None and connection.is_connected():
                connection.rollback()
            app.logger.exception("Could not save food listing")
            flash("Could not save the donation to MySQL. Please check system logs.", "error")

        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        return redirect(url_for("donations"))

    # GET: Retrieve categories and all listings from MySQL
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT category_id, category_name
            FROM food_categories
            ORDER BY category_name
        """)
        categories = cursor.fetchall()

        cursor.execute("""
            SELECT
                fl.listing_id,
                fl.donor_id,
                fl.food_name,
                fl.description,
                fc.category_name,
                u.full_name AS donor_name,
                fl.source_type,
                fl.quantity_total,
                fl.quantity_available,
                fl.unit,
                fl.expiry_at,
                fl.pickup_address,
                fl.city,
                fl.status
            FROM food_listings AS fl
            JOIN food_categories AS fc
                ON fl.category_id = fc.category_id
            JOIN users AS u
                ON fl.donor_id = u.user_id
            ORDER BY fl.listing_id DESC
        """)
        listings = cursor.fetchall()

        return render_template(
            "donations.html",
            categories=categories,
            listings=listings,
            min_expiry=min_expiry
        )

    except mysql.connector.Error:
        app.logger.exception("Could not retrieve food listings")
        return "Unable to load donations. Check database connection.", 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/listings/<int:listing_id>/edit", methods=["GET", "POST"])
@login_required
def edit_listing(listing_id):
    """
    Edit an existing food listing.
    Enforces record ownership: donors can only edit their own listings.
    Admins can edit any listing.
    """
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT *
            FROM food_listings
            WHERE listing_id = %s
        """, (listing_id,))
        listing = cursor.fetchone()

        if listing is None:
            abort(404)

        user_role = session.get("role")
        user_id = session.get("user_id")

        if user_role != "admin" and listing["donor_id"] != user_id:
            flash("Permission denied: You can only edit food listings that you created.", "error")
            abort(403)

        if request.method == "POST":
            food_name = request.form.get("food_name", "").strip()
            description = request.form.get("description", "").strip()
            category_id = int(request.form.get("category_id", listing["category_id"]))
            source_type = request.form.get("source_type", listing["source_type"])
            status = request.form.get("status", listing["status"])
            pickup_address = request.form.get("pickup_address", "").strip()
            city = request.form.get("city", "").strip()
            handling_notes = request.form.get("handling_notes", "").strip()

            quantity_available = Decimal(request.form.get("quantity_available", listing["quantity_available"]))
            expiry_at = datetime.strptime(request.form.get("expiry_at"), "%Y-%m-%dT%H:%M")

            if quantity_available < 0 or quantity_available > listing["quantity_total"]:
                flash("Available quantity must be between 0 and total quantity.", "error")
                return redirect(url_for("edit_listing", listing_id=listing_id))

            if status not in VALID_STATUSES:
                flash("Invalid listing status.", "error")
                return redirect(url_for("edit_listing", listing_id=listing_id))

            cursor.execute("""
                UPDATE food_listings
                SET
                    food_name = %s,
                    description = %s,
                    category_id = %s,
                    source_type = %s,
                    quantity_available = %s,
                    expiry_at = %s,
                    pickup_address = %s,
                    city = %s,
                    handling_notes = %s,
                    status = %s
                WHERE listing_id = %s
            """, (
                food_name,
                description or None,
                category_id,
                source_type,
                quantity_available,
                expiry_at,
                pickup_address,
                city,
                handling_notes or None,
                status,
                listing_id
            ))
            connection.commit()

            flash("Listing updated successfully!", "success")
            return redirect(url_for("donations"))

        cursor.execute("SELECT category_id, category_name FROM food_categories ORDER BY category_name")
        categories = cursor.fetchall()
        min_expiry = datetime.now().strftime("%Y-%m-%dT%H:%M")

        return render_template(
            "edit_listing.html",
            listing=listing,
            categories=categories,
            min_expiry=min_expiry
        )

    except mysql.connector.Error:
        if connection is not None and connection.is_connected():
            connection.rollback()
        app.logger.exception("Database error editing listing")
        flash("Could not update listing due to a database error.", "error")
        return redirect(url_for("donations"))

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/listings/<int:listing_id>/cancel", methods=["POST"])
@login_required
def cancel_listing(listing_id):
    """
    Cancel an active food listing.
    Enforces record ownership: donors can only cancel their own listings.
    """
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT donor_id, status FROM food_listings WHERE listing_id = %s", (listing_id,))
        listing = cursor.fetchone()

        if listing is None:
            abort(404)

        user_role = session.get("role")
        user_id = session.get("user_id")

        if user_role != "admin" and listing["donor_id"] != user_id:
            flash("Permission denied: You cannot cancel listings belonging to other donors.", "error")
            abort(403)

        cursor.execute("UPDATE food_listings SET status = 'cancelled' WHERE listing_id = %s", (listing_id,))
        connection.commit()

        flash(f"Listing #{listing_id} has been marked as cancelled.", "info")
        return redirect(request.referrer or url_for("donations"))

    except mysql.connector.Error:
        if connection is not None and connection.is_connected():
            connection.rollback()
        app.logger.exception("Error cancelling listing")
        flash("Could not cancel listing.", "error")
        return redirect(url_for("donations"))

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


# ============================================================================
# Stage 2: NGO Food Request & Allocation Workflows
# ============================================================================

@app.route("/listings/<int:listing_id>/request", methods=["POST"])
@login_required
def request_listing(listing_id):
    """
    Allow an authenticated NGO to submit a surplus food request.
    Validates positive quantity, availability, and non-self-request.
    """
    user_role = session.get("role")
    user_id = session.get("user_id")

    if user_role not in ("ngo", "admin"):
        flash("Only verified NGO accounts can request food donations.", "error")
        return redirect(url_for("listings"))

    # Quantity validation
    quantity_raw = request.form.get("requested_quantity", "").strip()
    try:
        requested_quantity = Decimal(quantity_raw)
        if requested_quantity <= 0:
            flash("Requested quantity must be greater than zero.", "error")
            return redirect(request.referrer or url_for("listings"))
    except (InvalidOperation, ValueError):
        flash("Please enter a valid numeric quantity.", "error")
        return redirect(request.referrer or url_for("listings"))

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT listing_id, donor_id, food_name, quantity_available, unit, status, expiry_at
            FROM food_listings
            WHERE listing_id = %s
        """, (listing_id,))
        listing = cursor.fetchone()

        if not listing:
            abort(404)

        # Rule: Organization cannot request food from its own account
        if listing["donor_id"] == user_id:
            flash("An organization cannot request surplus food from its own account.", "error")
            return redirect(request.referrer or url_for("listings"))

        # Recheck availability
        if listing["status"] != "available":
            flash(f"This listing is currently {listing['status']} and cannot accept new requests.", "error")
            return redirect(request.referrer or url_for("listings"))

        # Recheck expiry
        if listing["expiry_at"] <= datetime.now():
            flash("This food listing has expired and can no longer be requested.", "error")
            return redirect(request.referrer or url_for("listings"))

        # Check requested quantity does not exceed currently available
        if requested_quantity > listing["quantity_available"]:
            flash(
                f"Requested quantity ({requested_quantity} {listing['unit']}) exceeds the available quantity ({listing['quantity_available']} {listing['unit']}).",
                "error"
            )
            return redirect(request.referrer or url_for("listings"))

        # Insert new pending request
        cursor.execute("""
            INSERT INTO donation_requests (
                listing_id,
                ngo_id,
                requested_quantity,
                request_status,
                requested_at
            )
            VALUES (%s, %s, %s, 'pending', NOW())
        """, (listing_id, user_id, requested_quantity))

        connection.commit()
        flash(
            f"Successfully submitted request for {requested_quantity} {listing['unit']} of '{listing['food_name']}'! Awaiting donor review.",
            "success"
        )
        return redirect(url_for("dashboard_ngo"))

    except mysql.connector.Error:
        if connection is not None and connection.is_connected():
            connection.rollback()
        app.logger.exception("Database error submitting donation request")
        flash("Could not submit request due to a database error. Please try again.", "error")
        return redirect(request.referrer or url_for("listings"))

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/requests/<int:request_id>/cancel", methods=["POST"])
@login_required
def cancel_request(request_id):
    """Allow requesting NGO or admin to cancel a pending request."""
    user_id = session.get("user_id")
    user_role = session.get("role")

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT request_id, ngo_id, request_status
            FROM donation_requests
            WHERE request_id = %s
        """, (request_id,))
        req = cursor.fetchone()

        if not req:
            abort(404)

        if user_role != "admin" and req["ngo_id"] != user_id:
            flash("Permission denied: You can only cancel your own food requests.", "error")
            abort(403)

        if req["request_status"] != "pending":
            flash(f"Cannot cancel request: status is already '{req['request_status']}'.", "error")
            return redirect(url_for("dashboard_ngo"))

        cursor.execute("""
            UPDATE donation_requests
            SET request_status = 'cancelled'
            WHERE request_id = %s
        """, (request_id,))

        connection.commit()
        flash("Your donation request has been cancelled.", "info")
        return redirect(url_for("dashboard_ngo"))

    except mysql.connector.Error:
        if connection is not None and connection.is_connected():
            connection.rollback()
        app.logger.exception("Error cancelling request")
        flash("Could not cancel request due to a database error.", "error")
        return redirect(url_for("dashboard_ngo"))

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/requests/<int:request_id>/approve", methods=["POST"])
@login_required
def approve_request(request_id):
    """
    Approve an NGO request with ACID transaction and row-level locking.
    Enforces rule: NGOs must not approve donation requests.
    Only listing donor or admin can approve.
    """
    user_role = session.get("role")
    user_id = session.get("user_id")

    if user_role == "ngo":
        flash("Permission denied: NGO accounts cannot approve donation requests.", "error")
        abort(403)

    remarks = request.form.get("remarks", "").strip()
    connection = None

    try:
        connection = get_db_connection()
        success, status_code, message = execute_request_approval(
            connection,
            request_id,
            user_id,
            remarks
        )

        if success:
            flash(f"Request #{request_id} approved! {message}", "success")
        else:
            if status_code == "UNAUTHORIZED":
                flash(message, "error")
                abort(403)
            elif status_code == "NOT_FOUND":
                abort(404)
            else:
                flash(f"Approval failed: {message}", "error")

        return redirect(request.referrer or url_for("dashboard_donor"))

    except mysql.connector.Error:
        app.logger.exception("Database error approving request")
        flash("Could not approve request due to a database error.", "error")
        return redirect(request.referrer or url_for("dashboard_donor"))

    finally:
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/requests/<int:request_id>/reject", methods=["POST"])
@login_required
def reject_request(request_id):
    """
    Reject an NGO request without decrementing available quantity.
    Enforces rule: NGOs must not reject donation requests.
    Only listing donor or admin can reject.
    """
    user_role = session.get("role")
    user_id = session.get("user_id")

    if user_role == "ngo":
        flash("Permission denied: NGO accounts cannot reject donation requests.", "error")
        abort(403)

    remarks = request.form.get("remarks", "").strip()
    connection = None

    try:
        connection = get_db_connection()
        success, status_code, message = execute_request_rejection(
            connection,
            request_id,
            user_id,
            remarks
        )

        if success:
            flash(f"Request #{request_id} has been rejected.", "info")
        else:
            if status_code == "UNAUTHORIZED":
                flash(message, "error")
                abort(403)
            elif status_code == "NOT_FOUND":
                abort(404)
            else:
                flash(f"Rejection failed: {message}", "error")

        return redirect(request.referrer or url_for("dashboard_donor"))

    except mysql.connector.Error:
        app.logger.exception("Database error rejecting request")
        flash("Could not reject request due to a database error.", "error")
        return redirect(request.referrer or url_for("dashboard_donor"))

    finally:
        if connection is not None and connection.is_connected():
            connection.close()


# ============================================================================
# Stage 3: Collection Scheduling & Distribution Web Routes
# ============================================================================

@app.route("/requests/<int:request_id>/schedule", methods=["POST"])
@login_required
def schedule_collection(request_id):
    """
    Allow an authorized donor or admin to schedule collection for an approved request.
    """
    user_id = session.get("user_id")
    user_role = session.get("role")

    if user_role not in ("donor", "admin"):
        flash("Only food donors and administrators can schedule collection pickups.", "error")
        abort(403)

    scheduled_at = request.form.get("scheduled_at", "").strip()
    notes = request.form.get("notes", "").strip()

    connection = None
    try:
        connection = get_db_connection()
        success, code, msg = execute_schedule_collection(
            connection,
            request_id,
            user_id,
            user_role,
            scheduled_at,
            notes
        )

        if success:
            flash(msg, "success")
        else:
            if code == "UNAUTHORIZED":
                flash(msg, "error")
                abort(403)
            elif code == "NOT_FOUND":
                abort(404)
            else:
                flash(f"Scheduling failed: {msg}", "error")

        return redirect(request.referrer or url_for("dashboard_donor"))

    except mysql.connector.Error:
        app.logger.exception("Database error scheduling collection")
        flash("Could not schedule collection due to a database error.", "error")
        return redirect(request.referrer or url_for("dashboard_donor"))

    finally:
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/schedules/<int:schedule_id>/status", methods=["POST"])
@login_required
def update_schedule_status(schedule_id):
    """
    Allow authorized participants (donor, NGO, or admin) to mark collection as collected or missed.
    """
    user_id = session.get("user_id")
    user_role = session.get("role")
    new_status = request.form.get("status", "").strip().lower()
    notes = request.form.get("notes", "").strip()

    connection = None
    try:
        connection = get_db_connection()
        success, code, msg = execute_update_collection_status(
            connection,
            schedule_id,
            user_id,
            user_role,
            new_status,
            notes
        )

        if success:
            flash(msg, "success")
        else:
            if code == "UNAUTHORIZED":
                flash(msg, "error")
                abort(403)
            elif code == "NOT_FOUND":
                abort(404)
            else:
                flash(f"Status update failed: {msg}", "error")

        return redirect(request.referrer or url_for("dashboard"))

    except mysql.connector.Error:
        app.logger.exception("Database error updating collection status")
        flash("Could not update collection status due to a database error.", "error")
        return redirect(request.referrer or url_for("dashboard"))

    finally:
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/schedules/<int:schedule_id>/distribute", methods=["GET", "POST"])
@login_required
def record_distribution(schedule_id):
    """
    Allow the requesting NGO or admin to record distribution after food is collected.
    """
    user_id = session.get("user_id")
    user_role = session.get("role")

    if user_role not in ("ngo", "admin"):
        flash("Only certified NGOs and administrators can record food distributions.", "error")
        abort(403)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT cs.schedule_id, cs.status AS schedule_status, cs.scheduled_at, cs.collected_at,
                   dr.request_id, dr.ngo_id, dr.requested_quantity,
                   fl.listing_id, fl.food_name, fl.unit, fl.pickup_address, fl.city,
                   u_donor.full_name AS donor_name,
                   u_ngo.full_name AS ngo_name,
                   dist.distribution_id
            FROM collection_schedules cs
            JOIN donation_requests dr ON cs.request_id = dr.request_id
            JOIN food_listings fl ON dr.listing_id = fl.listing_id
            JOIN users u_donor ON fl.donor_id = u_donor.user_id
            JOIN users u_ngo ON dr.ngo_id = u_ngo.user_id
            LEFT JOIN distribution_records dist ON cs.schedule_id = dist.schedule_id
            WHERE cs.schedule_id = %s
        """, (schedule_id,))
        schedule = cursor.fetchone()

        if not schedule:
            abort(404)

        if user_role != "admin" and schedule["ngo_id"] != user_id:
            flash("Permission denied: You can only record distribution for your own NGO's collections.", "error")
            abort(403)

        if schedule["schedule_status"] != "collected":
            flash(f"Cannot record distribution: Collection status is {schedule['schedule_status']}. Food must be marked as collected first.", "error")
            return redirect(url_for("dashboard_ngo"))

        if schedule["distribution_id"] is not None:
            flash("Distribution has already been recorded for this collection schedule.", "info")
            return redirect(url_for("dashboard_ngo"))

        if request.method == "POST":
            qty_raw = request.form.get("quantity_distributed", "").strip()
            recipient_raw = request.form.get("recipient_count", "").strip()
            dist_at_raw = request.form.get("distributed_at", "").strip()
            notes = request.form.get("notes", "").strip()

            success, code, msg = execute_record_distribution(
                connection,
                schedule_id,
                user_id,
                user_role,
                qty_raw,
                recipient_raw,
                dist_at_raw,
                notes
            )

            if success:
                flash(msg, "success")
                if user_role == "admin":
                    return redirect(url_for("dashboard_admin"))
                return redirect(url_for("dashboard_ngo"))
            else:
                flash(f"Distribution record failed: {msg}", "error")
                return render_template(
                    "record_distribution.html",
                    schedule=schedule,
                    now_str=datetime.now().strftime("%Y-%m-%dT%H:%M")
                )

        now_str = datetime.now().strftime("%Y-%m-%dT%H:%M")
        return render_template(
            "record_distribution.html",
            schedule=schedule,
            now_str=now_str
        )

    except mysql.connector.Error:
        app.logger.exception("Database error handling distribution")
        flash("Database error occurred while processing distribution.", "error")
        return redirect(url_for("dashboard_ngo"))

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


# ============================================================================
# HTTP Error Handlers
# ============================================================================

@app.errorhandler(403)
def forbidden_error(error):
    """Handle 403 Forbidden errors with role switch guidance."""
    return render_template(
        "403.html",
        error_message="Access Forbidden: You do not have permission to access this resource or perform this operation."
    ), 403


@app.errorhandler(404)
def not_found_error(error):
    """Handle 404 Not Found errors."""
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(debug=False)
