"""Plentiq Flask application with database-backed donor workflows."""

import json
import hashlib
import hmac
import math
import os
import re
import secrets
from functools import lru_cache, wraps
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from db import get_db_connection
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from mysql.connector import Error as MySQLError
from werkzeug.security import check_password_hash


app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("PLENTIQ_SECRET_KEY"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,
)


def _new_pickup_pin_nonce():
    """Create a random nonce; the displayed PIN is derived using the app secret."""
    return secrets.token_hex(16)


def _pickup_pin_for(collection_id, nonce):
    """Derive a four-digit PIN without storing the PIN itself in MySQL."""
    secret = app.config.get("SECRET_KEY")
    if not secret or not nonce:
        return None
    message = f"plentiq-pickup-v1:{int(collection_id)}:{nonce}".encode("utf-8")
    digest = hmac.new(str(secret).encode("utf-8"), message, hashlib.sha256).digest()
    return f"{1000 + int.from_bytes(digest[:8], 'big') % 9000:04d}"


@lru_cache(maxsize=1)
def _pickup_pin_schema_ready():
    """Return whether migration 009 is present; restart the app after applying it."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """SELECT COUNT(DISTINCT COLUMN_NAME)
               FROM information_schema.columns
               WHERE table_schema = DATABASE() AND table_name = 'collections'
                 AND column_name IN ('pickup_pin_nonce', 'pickup_pin_attempts', 'pickup_pin_attempted_at')"""
        )
        row = cursor.fetchone()
        return bool(row and row[0] == 3)
    except MySQLError:
        return False
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@lru_cache(maxsize=1)
def _collection_confirmation_schema_ready():
    """Return whether the two-party collection confirmation migration is present."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """SELECT COUNT(DISTINCT COLUMN_NAME)
               FROM information_schema.columns
               WHERE table_schema = DATABASE() AND table_name = 'collections'
                 AND column_name IN ('donor_confirmed_at', 'acceptor_confirmed_at')"""
        )
        row = cursor.fetchone()
        return bool(row and row[0] == 2)
    except MySQLError:
        return False
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _push_settings():
    public_key = os.getenv("PLENTIQ_VAPID_PUBLIC_KEY", "").strip()
    private_key_file = os.getenv("PLENTIQ_VAPID_PRIVATE_KEY_FILE", "").strip()
    subject = os.getenv("PLENTIQ_VAPID_SUBJECT", "").strip()
    if private_key_file:
        private_key_path = Path(private_key_file)
        if not private_key_path.is_absolute():
            private_key_path = Path(__file__).resolve().parent / private_key_path
        private_key_file = str(private_key_path)
    return public_key, private_key_file, subject


def _send_browser_push(user_ids, title, body, target_url):
    """Deliver best-effort Web Push without interrupting a saved workflow."""
    user_ids = sorted({int(user_id) for user_id in user_ids if user_id})
    public_key, private_key_file, subject = _push_settings()
    if not user_ids or not public_key or not private_key_file or not subject:
        return
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        placeholders = ",".join(["%s"] * len(user_ids))
        cursor.execute(
            f"SELECT subscription_id, endpoint, p256dh_key, auth_key FROM push_subscriptions WHERE user_id IN ({placeholders})",
            tuple(user_ids),
        )
        subscriptions = cursor.fetchall()
    except Exception:
        return
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    expired_ids = []
    payload = json.dumps({"title": title, "body": body, "url": target_url})
    for subscription_id, endpoint, p256dh_key, auth_key in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": endpoint,
                    "keys": {"p256dh": p256dh_key, "auth": auth_key},
                },
                data=payload,
                vapid_private_key=private_key_file,
                vapid_claims={"sub": subject},
                ttl=60,
            )
        except WebPushException as exc:
            response = getattr(exc, "response", None)
            if getattr(response, "status_code", None) in {404, 410}:
                expired_ids.append(subscription_id)
        except Exception:
            # A push service outage must not undo the donation/request workflow.
            continue

    if expired_ids:
        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            placeholders = ",".join(["%s"] * len(expired_ids))
            cursor.execute(
                f"DELETE FROM push_subscriptions WHERE subscription_id IN ({placeholders})",
                tuple(expired_ids),
            )
            connection.commit()
        except Exception:
            if connection is not None and connection.is_connected():
                connection.rollback()
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()


def _nearby_user_ids(cursor, role_code, latitude, longitude, radius_km=25):
    """Return active account ids with a saved default location in range."""
    if latitude is None or longitude is None:
        return []
    cursor.execute(
        """SELECT u.user_id, l.latitude, l.longitude
           FROM user_locations AS l
           JOIN users AS u ON u.user_id = l.user_id
           JOIN roles AS r ON r.role_id = u.role_id
           WHERE r.role_code = %s AND u.account_status = 'active'
             AND l.is_default = TRUE
             AND l.latitude IS NOT NULL AND l.longitude IS NOT NULL""",
        (role_code,),
    )
    return sorted({
        int(user_id)
        for user_id, user_latitude, user_longitude in cursor.fetchall()
        if _distance_km(latitude, longitude, user_latitude, user_longitude) <= radius_km
    })


def _insert_notifications(cursor, user_ids, event_code, message, donation_id=None, request_id=None):
    if user_ids:
        cursor.executemany(
            """INSERT INTO notifications
               (recipient_user_id, event_code, message, donation_id, request_id)
               VALUES (%s, %s, %s, %s, %s)""",
            [(user_id, event_code, message[:500], donation_id, request_id) for user_id in user_ids],
        )


def _demo_donations():
    now = datetime.now()
    return [
        {
            "id": "demo-biryani",
            "food_name": "Veg Biryani",
            "category": "Main Course",
            "diet": "Vegetarian",
            "description": "Freshly prepared vegetarian biryani from today's event.",
            "quantity": 20,
            "unit": "Packets",
            "area": "Hitech City",
            "city": "Hyderabad",
            "created_at": now - timedelta(hours=4),
            "deadline_at": now + timedelta(hours=4),
            "deadline_label": (now + timedelta(hours=4)).strftime("%d %b %Y, %I:%M %p"),
            "status": "Active",
            "status_key": "active",
            "request_count": 2,
            "image": "food-biryani.png",
            "image_alt": "Vegetarian vegetable biryani",
        },
        {
            "id": "demo-bread",
            "food_name": "Bread Loaves",
            "category": "Bakery",
            "diet": "Vegetarian",
            "description": "Fresh bread loaves from our bakery.",
            "quantity": 15,
            "unit": "Pieces",
            "area": "Madhapur",
            "city": "Hyderabad",
            "created_at": now - timedelta(days=1),
            "deadline_at": now + timedelta(hours=8),
            "deadline_label": (now + timedelta(hours=8)).strftime("%d %b %Y, %I:%M %p"),
            "status": "Active",
            "status_key": "active",
            "request_count": 1,
            "image": "food-bread.png",
            "image_alt": "Fresh bakery bread loaves",
        },
        {
            "id": "demo-fruit",
            "food_name": "Mixed Fruits",
            "category": "Fruits",
            "diet": "Vegetarian",
            "description": "Assorted fresh fruits including apples, bananas and oranges.",
            "quantity": 10,
            "unit": "Kg",
            "area": "Gachibowli",
            "city": "Hyderabad",
            "created_at": now - timedelta(days=2),
            "deadline_at": now + timedelta(minutes=90),
            "deadline_label": (now + timedelta(minutes=90)).strftime("%d %b %Y, %I:%M %p"),
            "status": "Expiring Soon",
            "status_key": "expiring",
            "request_count": 3,
            "image": "food-fruit.png",
            "image_alt": "A basket of fresh mixed fruit",
        },
        {
            "id": "demo-rice",
            "food_name": "Cooked Rice",
            "category": "Main Course",
            "diet": "Vegetarian",
            "description": "Steamed rice prepared fresh today.",
            "quantity": 30,
            "unit": "Packets",
            "area": "Kondapur",
            "city": "Hyderabad",
            "created_at": now - timedelta(days=3),
            "deadline_at": now + timedelta(hours=6),
            "deadline_label": (now + timedelta(hours=6)).strftime("%d %b %Y, %I:%M %p"),
            "status": "Pending Collection",
            "status_key": "pending_collection",
            "request_count": 0,
            "image": "food-rice.png",
            "image_alt": "Steamed white rice in a bowl",
        },
        {
            "id": "demo-curry",
            "food_name": "Mixed Vegetable Curry",
            "category": "Main Course",
            "diet": "Vegetarian",
            "description": "Nutritious mixed vegetable curry with mild spice.",
            "quantity": 18,
            "unit": "Packets",
            "area": "Gachibowli",
            "city": "Hyderabad",
            "created_at": now - timedelta(days=4),
            "deadline_at": now - timedelta(days=1),
            "deadline_label": (now - timedelta(days=1)).strftime("%d %b %Y, %I:%M %p"),
            "collected_label": (now - timedelta(days=1)).strftime("Collected on %d %b %Y"),
            "status": "Completed",
            "status_key": "completed",
            "request_count": 0,
            "image": "food-vegetable-curry.png",
            "image_alt": "Mixed vegetable curry in a bowl",
        },
    ]


def _demo_requests():
    return [
        {
            "id": "demo-request-1",
            "donation_id": "demo-biryani",
            "acceptor_name": "Helping Hands",
            "food_name": "Veg Biryani",
            "quantity": 10,
            "unit": "Packets",
            "elapsed": "2 hours ago",
            "image": "food-biryani.png",
            "image_alt": "Vegetarian vegetable biryani",
            "status": "Pending",
        },
        {
            "id": "demo-request-2",
            "donation_id": "demo-fruit",
            "acceptor_name": "Care & Share Foundation",
            "food_name": "Mixed Fruits",
            "quantity": 5,
            "unit": "Kg",
            "elapsed": "5 hours ago",
            "image": "food-fruit.png",
            "image_alt": "A basket of fresh mixed fruit",
            "status": "Pending",
        },
    ]


def _filter_records(records, query, fields):
    if not query:
        return records
    needle = query.casefold()
    return [
        record for record in records
        if any(needle in str(record.get(field, "")).casefold() for field in fields)
    ]


def _sample_mode():
    return request.args.get("demo") == "1"


@app.context_processor
def donor_template_context():
    demo_mode = _sample_mode()

    def donor_url(endpoint, **values):
        if demo_mode and "demo" not in values:
            values["demo"] = "1"
        return url_for(endpoint, **values)

    return {"demo_mode": demo_mode, "donor_url": donor_url}


@app.context_processor
def notification_template_context():
    unread_count = 0
    user_id = session.get("user_id")
    if user_id and session.get("role") in {"donor", "acceptor"}:
        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM notifications WHERE recipient_user_id = %s AND read_at IS NULL",
                (user_id,),
            )
            unread_count = int(cursor.fetchone()[0] or 0)
        except MySQLError:
            # Notification counts must not break a page if the database is unavailable.
            unread_count = 0
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()
    return {"unread_notification_count": unread_count}


def _session_role_is_active(user_id, expected_role):
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """SELECT u.account_status, r.role_code
               FROM users u JOIN roles r ON r.role_id = u.role_id
               WHERE u.user_id = %s""",
            (user_id,),
        )
        row = cursor.fetchone()
        return bool(row and row[0] == "active" and row[1] == expected_role)
    finally:
        cursor.close()
        connection.close()


def donor_required(view):
    """Require an active donor session for donor workspace pages."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "donor":
            if session.get("user_id"):
                return redirect(url_for("role_home"))
            return redirect(url_for("login"))
        try:
            if not _session_role_is_active(session["user_id"], "donor"):
                session.clear()
                return redirect(url_for("login", blocked="1"))
        except (MySQLError, RuntimeError):
            abort(503)
        return view(*args, **kwargs)

    return wrapped


def acceptor_required(view):
    """Restrict acceptor workspace pages to a signed-in acceptor account."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "acceptor" or not session.get("user_id"):
            if session.get("user_id"):
                return redirect(url_for("role_home"))
            return redirect(url_for("login"))
        try:
            if not _session_role_is_active(session["user_id"], "acceptor"):
                session.clear()
                return redirect(url_for("login", blocked="1"))
        except (MySQLError, RuntimeError):
            abort(503)
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    """Restrict administration tools to signed-in admin accounts."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "admin" or not session.get("user_id"):
            if session.get("user_id"):
                return redirect(url_for("role_home"))
            return redirect(url_for("login"))
        try:
            if not _session_role_is_active(session["user_id"], "admin"):
                session.clear()
                return redirect(url_for("login", blocked="1"))
        except (MySQLError, RuntimeError):
            abort(503)
        return view(*args, **kwargs)

    return wrapped


@app.get("/")
@app.get("/landing")
def landing():
    return render_template("landing.html")


@app.get("/service-worker.js")
def service_worker():
    response = send_from_directory(
        Path(app.static_folder) / "js", "service-worker.js",
        mimetype="application/javascript",
    )
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


def _push_api_user():
    if session.get("role") not in {"donor", "acceptor"} or not session.get("user_id"):
        return None
    return int(session["user_id"])


def _push_api_same_origin():
    origin = request.headers.get("Origin", "").rstrip("/")
    return bool(origin and origin == request.host_url.rstrip("/"))


def _push_storage_ready():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("SHOW TABLES LIKE 'push_subscriptions'")
        return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.get("/api/push/public-key")
def push_public_key():
    if _push_api_user() is None:
        return jsonify(enabled=False, message="Sign in to configure browser notifications."), 401
    public_key, private_key_file, subject = _push_settings()
    try:
        from pywebpush import webpush  # noqa: F401
        package_ready = True
    except ImportError:
        package_ready = False
    configured = bool(
        public_key and private_key_file and Path(private_key_file).is_file() and subject
    )
    storage_ready = _push_storage_ready()
    if not package_ready:
        message = "Install the pywebpush dependency in this project's virtual environment."
    elif not configured:
        message = "Configure the VAPID key settings in the local environment file."
    elif not storage_ready:
        message = "Run database_migrations/006_push_subscriptions.sql in MySQL Workbench."
    else:
        message = "Browser notifications are ready to enable."
    enabled = package_ready and configured and storage_ready
    return jsonify(
        enabled=enabled,
        public_key=public_key if enabled else "",
        message=message,
    )


@app.post("/api/push/subscribe")
def push_subscribe():
    user_id = _push_api_user()
    if user_id is None:
        return jsonify(ok=False, message="Sign in to enable browser notifications."), 401
    if not _push_api_same_origin():
        return jsonify(ok=False, message="This request could not be verified."), 403
    public_key, private_key_file, subject = _push_settings()
    try:
        from pywebpush import webpush  # noqa: F401
    except ImportError:
        return jsonify(ok=False, message="Install pywebpush before enabling browser notifications."), 503
    if not public_key or not private_key_file or not Path(private_key_file).is_file() or not subject:
        return jsonify(ok=False, message="Configure VAPID keys before enabling browser notifications."), 503
    payload = request.get_json(silent=True) or {}
    subscription = payload.get("subscription") or {}
    endpoint = subscription.get("endpoint", "")
    keys = subscription.get("keys") or {}
    p256dh_key = keys.get("p256dh", "")
    auth_key = keys.get("auth", "")
    if (
        not isinstance(endpoint, str) or not endpoint.startswith("https://") or len(endpoint) > 2048
        or not isinstance(p256dh_key, str) or not p256dh_key or len(p256dh_key) > 256
        or not isinstance(auth_key, str) or not auth_key or len(auth_key) > 128
    ):
        return jsonify(ok=False, message="The browser returned an invalid push subscription."), 400
    endpoint_hash = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """INSERT INTO push_subscriptions
               (user_id, endpoint, endpoint_hash, p256dh_key, auth_key)
               VALUES (%s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE user_id = VALUES(user_id), endpoint = VALUES(endpoint),
                                       p256dh_key = VALUES(p256dh_key), auth_key = VALUES(auth_key)""",
            (user_id, endpoint, endpoint_hash, p256dh_key, auth_key),
        )
        connection.commit()
        return jsonify(ok=True, message="Browser notifications are enabled for this device.")
    except MySQLError:
        if connection is not None and connection.is_connected():
            connection.rollback()
        return jsonify(ok=False, message="Push storage is unavailable. Apply migration 006 and try again."), 503
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.post("/api/push/unsubscribe")
def push_unsubscribe():
    user_id = _push_api_user()
    if user_id is None:
        return jsonify(ok=False, message="Sign in to manage browser notifications."), 401
    if not _push_api_same_origin():
        return jsonify(ok=False, message="This request could not be verified."), 403
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint", "")
    if not isinstance(endpoint, str) or not endpoint.startswith("https://") or len(endpoint) > 2048:
        return jsonify(ok=False, message="The browser returned an invalid push subscription."), 400
    endpoint_hash = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM push_subscriptions WHERE user_id = %s AND endpoint_hash = %s",
            (user_id, endpoint_hash),
        )
        connection.commit()
        return jsonify(ok=True, message="Browser notifications are disabled for this device.")
    except MySQLError:
        if connection is not None and connection.is_connected():
            connection.rollback()
        return jsonify(ok=False, message="Push storage is unavailable. Apply migration 006 and try again."), 503
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/login", methods=["GET", "POST"])
def login():
    notice = (
        "You have been signed out." if request.args.get("logged_out") == "1"
        else "This account is blocked or unavailable. Contact the Plentiq administrator." if request.args.get("blocked") == "1"
        else None
    )
    email = ""

    if request.method == "GET" and session.get("user_id"):
        if session.get("role") == "donor":
            return redirect(url_for("donor_dashboard"))
        return redirect(url_for("role_home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not app.config.get("SECRET_KEY"):
            notice = "Login sessions are not configured. Set PLENTIQ_SECRET_KEY in the local .env file."
        elif not email or not password:
            notice = "Enter your email address and password."
        else:
            connection = None
            cursor = None
            try:
                connection = get_db_connection()
                cursor = connection.cursor()
                cursor.execute(
                    """SELECT u.user_id, u.full_name, u.password_hash,
                              u.account_status, r.role_code
                       FROM users AS u
                       JOIN roles AS r ON r.role_id = u.role_id
                       WHERE u.email = %s
                       LIMIT 1""",
                    (email,),
                )
                account = cursor.fetchone()
                if (
                    account
                    and account[3] == "active"
                    and check_password_hash(account[2], password)
                ):
                    session.clear()
                    session["user_id"] = int(account[0])
                    session["full_name"] = account[1]
                    session["role"] = account[4]
                    if account[4] == "donor":
                        return redirect(url_for("donor_dashboard"))
                    return redirect(url_for("role_home"))

                notice = "Email or password is incorrect, or this account is unavailable."
            except (MySQLError, RuntimeError, ValueError):
                # Keep credentials and connection details out of browser messages.
                notice = "Login is temporarily unavailable. Check the database configuration and try again."
            finally:
                if cursor is not None:
                    cursor.close()
                if connection is not None and connection.is_connected():
                    connection.close()

    return render_template("login.html", login_notice=notice, email=email)


@app.get("/account/home")
def role_home():
    role = session.get("role")
    if not session.get("user_id") or role not in {"admin", "acceptor"}:
        if role == "donor":
            return redirect(url_for("donor_dashboard"))
        session.clear()
        return redirect(url_for("login"))
    if role == "acceptor":
        return redirect(url_for("acceptor_dashboard"))
    return redirect(url_for("admin_dashboard"))


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login", logged_out="1"))


def _admin_dashboard_data():
    data = {
        "stats": {"users": 0, "active_users": 0, "blocked_users": 0, "donations": 0,
                  "active_donations": 0, "pending_requests": 0, "approved_requests": 0,
                  "completed_collections": 0, "open_reports": 0, "new_users": 0},
        "role_counts": [], "donation_statuses": [], "trend": [],
        "trend_points": "",
        "collected_quantity_by_unit": [],
        "recent_users": [], "recent_donations": [], "open_reports": [], "moderation_actions": [],
    }
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """SELECT COUNT(*), SUM(account_status = 'active'), SUM(account_status = 'blocked'),
                      SUM(created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY))
               FROM users"""
        )
        row = cursor.fetchone()
        data["stats"].update({
            "users": int(row[0] or 0), "active_users": int(row[1] or 0),
            "blocked_users": int(row[2] or 0), "new_users": int(row[3] or 0),
        })
        cursor.execute(
            """SELECT r.role_code, r.role_name, COUNT(u.user_id)
               FROM roles r LEFT JOIN users u ON u.role_id = r.role_id
               GROUP BY r.role_id, r.role_code, r.role_name ORDER BY r.role_id"""
        )
        data["role_counts"] = [
            {"code": row[0], "label": row[1], "count": int(row[2])}
            for row in cursor.fetchall()
        ]
        cursor.execute("SELECT COUNT(*), SUM(status_code = 'active') FROM donations")
        row = cursor.fetchone()
        data["stats"]["donations"] = int(row[0] or 0)
        data["stats"]["active_donations"] = int(row[1] or 0)
        cursor.execute(
            """SELECT COUNT(*) FROM donation_requests r
               JOIN donation_items i ON i.item_id = r.item_id
               JOIN donations d ON d.donation_id = i.donation_id
               WHERE r.status_code = 'pending'
                 AND (d.pickup_ends_at IS NULL OR d.pickup_ends_at > NOW())
                 AND (i.approximate_expiry_at IS NULL OR i.approximate_expiry_at > NOW())
                 AND (d.pickup_ends_at IS NOT NULL OR i.approximate_expiry_at IS NOT NULL)"""
        )
        data["stats"]["pending_requests"] = int(cursor.fetchone()[0] or 0)
        cursor.execute("SELECT COUNT(*) FROM donation_requests WHERE status_code = 'approved'")
        data["stats"]["approved_requests"] = int(cursor.fetchone()[0] or 0)
        cursor.execute("SELECT COUNT(*) FROM collections WHERE status_code = 'collected'")
        data["stats"]["completed_collections"] = int(cursor.fetchone()[0] or 0)
        cursor.execute(
            """SELECT qu.unit_name, SUM(r.quantity_requested)
               FROM collections c
               JOIN donation_requests r ON r.request_id = c.request_id
               JOIN donation_items i ON i.item_id = r.item_id
               JOIN quantity_units qu ON qu.unit_id = i.unit_id
               WHERE c.status_code = 'collected'
               GROUP BY qu.unit_id, qu.unit_name ORDER BY qu.unit_name"""
        )
        data["collected_quantity_by_unit"] = [
            {"unit": row[0], "quantity": row[1]} for row in cursor.fetchall()
        ]
        cursor.execute("SELECT COUNT(*) FROM reports WHERE status_code IN ('open', 'under_review')")
        data["stats"]["open_reports"] = int(cursor.fetchone()[0] or 0)

        cursor.execute("SELECT status_code, COUNT(*) FROM donations GROUP BY status_code")
        raw_statuses = dict(cursor.fetchall())
        status_labels = {
            "active": ("Active", "#0d9467"),
            "fully_allocated": ("Fully allocated", "#3979e8"),
            "cancelled": ("Cancelled", "#f07862"),
        }
        total_statuses = sum(int(count) for count in raw_statuses.values()) or 1
        for code, count in sorted(raw_statuses.items()):
            label, color = status_labels.get(code, (code.replace("_", " ").title(), "#8871cf"))
            count = int(count)
            data["donation_statuses"].append({
                "label": label, "count": count, "percent": round(count * 100 / total_statuses),
                "color": color,
            })

        now = datetime.now()
        months = []
        year, month = now.year, now.month
        for offset in range(5, -1, -1):
            bucket_month = month - offset
            bucket_year = year
            while bucket_month <= 0:
                bucket_month += 12
                bucket_year -= 1
            months.append(datetime(bucket_year, bucket_month, 1))
        cursor.execute(
            """SELECT DATE_FORMAT(created_at, '%Y-%m'), COUNT(*) FROM donations
               WHERE created_at >= DATE_FORMAT(DATE_SUB(CURRENT_DATE, INTERVAL 5 MONTH), '%Y-%m-01')
               GROUP BY DATE_FORMAT(created_at, '%Y-%m') ORDER BY DATE_FORMAT(created_at, '%Y-%m')"""
        )
        counts_by_month = {row[0]: int(row[1]) for row in cursor.fetchall()}
        trend_counts = [counts_by_month.get(item.strftime("%Y-%m"), 0) for item in months]
        trend_max = max(trend_counts or [0]) or 1
        trend = []
        for index, (item, count) in enumerate(zip(months, trend_counts)):
            x = 52 + index * 100
            y = 180 - round((count / trend_max) * 125)
            trend.append({"label": item.strftime("%b"), "count": count, "x": x, "y": y})
        data["trend"] = trend
        data["trend_points"] = " ".join(f"{point['x']},{point['y']}" for point in trend)

        cursor.execute(
            """SELECT u.user_id, u.full_name, u.email, r.role_name, u.account_status, u.created_at
               FROM users u JOIN roles r ON r.role_id = u.role_id
               ORDER BY u.created_at DESC, u.user_id DESC LIMIT 6"""
        )
        data["recent_users"] = cursor.fetchall()
        cursor.execute(
            """SELECT d.donation_id, d.city, d.area, d.status_code, d.created_at,
                      u.full_name, COUNT(i.item_id), COALESCE(GROUP_CONCAT(i.food_name ORDER BY i.item_id SEPARATOR ', '), '')
               FROM donations d JOIN users u ON u.user_id = d.donor_user_id
               LEFT JOIN donation_items i ON i.donation_id = d.donation_id
               GROUP BY d.donation_id, d.city, d.area, d.status_code, d.created_at, u.full_name
               ORDER BY d.created_at DESC, d.donation_id DESC LIMIT 6"""
        )
        data["recent_donations"] = cursor.fetchall()
        cursor.execute(
            """SELECT rp.report_id, reporter.full_name, target.full_name,
                      rr.reason_label, rp.status_code, rp.created_at, rp.details
               FROM reports rp
               JOIN users reporter ON reporter.user_id = rp.reporter_user_id
               JOIN users target ON target.user_id = rp.reported_user_id
               JOIN report_reasons rr ON rr.reason_id = rp.reason_id
               WHERE rp.status_code IN ('open', 'under_review')
               ORDER BY rp.created_at ASC LIMIT 5"""
        )
        data["open_reports"] = cursor.fetchall()
        cursor.execute(
            """SELECT ma.action_code, admin.full_name, target.full_name, ma.reason, ma.created_at
               FROM moderation_actions ma
               JOIN users admin ON admin.user_id = ma.admin_user_id
               JOIN users target ON target.user_id = ma.target_user_id
               ORDER BY ma.created_at DESC, ma.action_id DESC LIMIT 5"""
        )
        data["moderation_actions"] = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()
    return data


def _admin_user_rows(query="", status_filter="all"):
    sql = """SELECT u.user_id, u.full_name, u.email, COALESCE(u.phone, ''),
                    r.role_code, r.role_name, u.account_status, u.created_at,
                    COALESCE(l.city, ''), COALESCE(l.area, ''),
                    (SELECT ma.action_code FROM moderation_actions ma
                     WHERE ma.target_user_id = u.user_id
                     ORDER BY ma.created_at DESC, ma.action_id DESC LIMIT 1) AS last_action
             FROM users u JOIN roles r ON r.role_id = u.role_id
             LEFT JOIN user_locations l ON l.location_id = (
                 SELECT ul.location_id FROM user_locations ul
                 WHERE ul.user_id = u.user_id AND ul.is_default = TRUE
                 ORDER BY ul.location_id DESC LIMIT 1
             ) WHERE 1 = 1"""
    params = []
    if status_filter != "all":
        sql += " AND u.account_status = %s"
        params.append(status_filter)
    if query:
        sql += " AND (u.full_name LIKE %s OR u.email LIKE %s)"
        params.extend((f"%{query}%", f"%{query}%"))
    sql += " ORDER BY FIELD(u.account_status, 'active', 'blocked'), u.created_at DESC, u.user_id DESC"
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(sql, tuple(params))
        return cursor.fetchall()
    finally:
        cursor.close()
        connection.close()


def _admin_donation_rows(query="", status_filter="all"):
    sql = """SELECT d.donation_id, d.city, d.area, d.status_code, d.created_at,
                    d.pickup_ends_at, u.full_name, COUNT(i.item_id),
                    COALESCE(GROUP_CONCAT(i.food_name ORDER BY i.item_id SEPARATOR ', '), '')
             FROM donations d JOIN users u ON u.user_id = d.donor_user_id
             LEFT JOIN donation_items i ON i.donation_id = d.donation_id WHERE 1 = 1"""
    params = []
    if status_filter != "all":
        sql += " AND d.status_code = %s"
        params.append(status_filter)
    if query:
        sql += " AND (u.full_name LIKE %s OR d.city LIKE %s OR d.area LIKE %s OR i.food_name LIKE %s)"
        params.extend([f"%{query}%"] * 4)
    sql += " GROUP BY d.donation_id, d.city, d.area, d.status_code, d.created_at, d.pickup_ends_at, u.full_name ORDER BY d.created_at DESC, d.donation_id DESC"
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(sql, tuple(params))
        return cursor.fetchall()
    finally:
        cursor.close()
        connection.close()


def _admin_request_rows(query="", status_filter="all"):
    sql = """SELECT r.request_id, r.quantity_requested, r.status_code, r.requested_at,
                    acceptor.full_name, donor.full_name, i.food_name, qu.unit_name,
                    d.city, d.area, c.status_code, c.scheduled_pickup_at, c.collected_at,
                    d.pickup_ends_at, i.approximate_expiry_at
             FROM donation_requests r
             JOIN donation_items i ON i.item_id = r.item_id
             JOIN donations d ON d.donation_id = i.donation_id
             JOIN users acceptor ON acceptor.user_id = r.acceptor_user_id
             JOIN users donor ON donor.user_id = d.donor_user_id
             JOIN quantity_units qu ON qu.unit_id = i.unit_id
             LEFT JOIN collections c ON c.request_id = r.request_id WHERE 1 = 1"""
    params = []
    if query:
        sql += " AND (acceptor.full_name LIKE %s OR donor.full_name LIKE %s OR i.food_name LIKE %s OR d.city LIKE %s OR d.area LIKE %s)"
        params.extend([f"%{query}%"] * 5)
    sql += " ORDER BY r.requested_at DESC, r.request_id DESC"
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()
    now = datetime.now()
    records = []
    for row in rows:
        deadline = min(
            [value for value in (row[13], row[14]) if value is not None],
            default=None,
        )
        status = "expired" if row[2] == "pending" and (deadline is None or deadline <= now) else row[2]
        if status_filter != "all" and status != status_filter:
            continue
        records.append((*row[:2], status, *row[3:13]))
    return records


def _admin_report_rows(query="", status_filter="all"):
    sql = """SELECT rp.report_id, rp.request_id, rp.collection_id,
                    reporter.user_id, reporter.full_name, target.user_id, target.full_name,
                    target.account_status, rr.reason_label, rp.details, rp.status_code,
                    rp.created_at, rp.resolution_note
             FROM reports rp
             JOIN users reporter ON reporter.user_id = rp.reporter_user_id
             JOIN users target ON target.user_id = rp.reported_user_id
             JOIN report_reasons rr ON rr.reason_id = rp.reason_id WHERE 1 = 1"""
    params = []
    if status_filter != "all":
        sql += " AND rp.status_code = %s"
        params.append(status_filter)
    if query:
        sql += " AND (reporter.full_name LIKE %s OR target.full_name LIKE %s OR rr.reason_label LIKE %s)"
        params.extend([f"%{query}%"] * 3)
    sql += " ORDER BY FIELD(rp.status_code, 'open', 'under_review', 'resolved', 'dismissed'), rp.created_at DESC"
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(sql, tuple(params))
        return cursor.fetchall()
    finally:
        cursor.close()
        connection.close()


@app.get("/admin/dashboard")
@admin_required
def admin_dashboard():
    try:
        data = _admin_dashboard_data()
        data_error = None
    except MySQLError:
        data = None
        data_error = "Dashboard analytics could not be loaded from MySQL."
    return render_template(
        "admin/dashboard.html", active_page="dashboard", data=data, data_error=data_error,
        admin_name=session.get("full_name", "Admin"),
    )


@app.get("/admin/users")
@admin_required
def admin_users():
    query = request.args.get("q", "").strip()[:120]
    status_filter = request.args.get("status", "all").strip()
    if status_filter not in {"all", "active", "blocked"}:
        status_filter = "all"
    try:
        users = _admin_user_rows(query, status_filter)
        page_error = None
    except MySQLError:
        users = []
        page_error = "User accounts could not be loaded from MySQL."
    return render_template(
        "admin/users.html", active_page="users", users=users, query=query,
        status_filter=status_filter, page_error=page_error,
    )


@app.post("/admin/users/<int:user_id>/status")
@admin_required
def admin_user_status(user_id):
    action = request.form.get("action", "").strip()
    reason = request.form.get("reason", "").strip()
    if action not in {"block", "unblock"}:
        abort(400)
    if action == "block" and (len(reason) < 5 or len(reason) > 1000):
        flash("Enter a moderation reason between 5 and 1,000 characters before blocking an account.", "error")
        return redirect(url_for("admin_users"))
    if len(reason) > 1000:
        flash("The moderation note must be 1,000 characters or fewer.", "error")
        return redirect(url_for("admin_users"))
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor()
        cursor.execute(
            """SELECT u.role_id, u.account_status, r.role_code
               FROM users u JOIN roles r ON r.role_id = u.role_id
               WHERE u.user_id = %s FOR UPDATE""",
            (user_id,),
        )
        target = cursor.fetchone()
        if target is None:
            connection.rollback()
            flash("That account was not found.", "error")
            return redirect(url_for("admin_users"))
        if user_id == session["user_id"] or target[2] == "admin":
            connection.rollback()
            flash("Admin accounts cannot be blocked from this screen.", "error")
            return redirect(url_for("admin_users"))
        next_status = "blocked" if action == "block" else "active"
        if target[1] == next_status:
            connection.rollback()
            flash(f"This account is already {next_status}.", "info")
            return redirect(url_for("admin_users"))
        cursor.execute(
            "INSERT INTO moderation_actions (admin_user_id, target_user_id, action_code, reason) VALUES (%s, %s, %s, %s)",
            (session["user_id"], user_id, action, reason or None),
        )
        cursor.execute("UPDATE users SET account_status = %s WHERE user_id = %s", (next_status, user_id))
        connection.commit()
        flash(f"Account {next_status}. Moderation action recorded.", "success")
    except MySQLError:
        if connection is not None and connection.is_connected():
            connection.rollback()
        flash("The account status could not be changed. No update was saved.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    return redirect(url_for("admin_users"))


@app.get("/admin/donations")
@admin_required
def admin_donations():
    query = request.args.get("q", "").strip()[:120]
    status_filter = request.args.get("status", "all").strip()
    if status_filter not in {"all", "active", "fully_allocated", "cancelled"}:
        status_filter = "all"
    try:
        donations = _admin_donation_rows(query, status_filter)
        page_error = None
    except MySQLError:
        donations = []
        page_error = "Donation listings could not be loaded from MySQL."
    return render_template(
        "admin/donations.html", active_page="donations", donations=donations,
        query=query, status_filter=status_filter, page_error=page_error,
    )


@app.get("/admin/requests")
@admin_required
def admin_requests():
    query = request.args.get("q", "").strip()[:120]
    status_filter = request.args.get("status", "all").strip()
    if status_filter not in {"all", "pending", "approved", "rejected", "cancelled", "expired"}:
        status_filter = "all"
    try:
        records = _admin_request_rows(query, status_filter)
        page_error = None
    except MySQLError:
        records = []
        page_error = "Donation requests and collection status could not be loaded from MySQL."
    return render_template(
        "admin/requests.html", active_page="requests", records=records,
        query=query, status_filter=status_filter, page_error=page_error,
    )


@app.get("/admin/reports")
@admin_required
def admin_reports():
    query = request.args.get("q", "").strip()[:120]
    status_filter = request.args.get("status", "open").strip()
    if status_filter not in {"all", "open", "under_review", "resolved", "dismissed"}:
        status_filter = "open"
    try:
        reports = _admin_report_rows(query, status_filter)
        page_error = None
    except MySQLError:
        reports = []
        page_error = "Reports could not be loaded from MySQL."
    return render_template(
        "admin/reports.html", active_page="reports", reports=reports,
        query=query, status_filter=status_filter, page_error=page_error,
    )


@app.post("/admin/reports/<int:report_id>/review")
@admin_required
def admin_report_review(report_id):
    status = request.form.get("status", "").strip()
    note = request.form.get("resolution_note", "").strip()
    if status not in {"under_review", "resolved", "dismissed"}:
        abort(400)
    if len(note) > 1000:
        flash("The review note must be 1,000 characters or fewer.", "error")
        return redirect(url_for("admin_reports"))
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor()
        cursor.execute(
            """SELECT reporter_user_id, reported_user_id, request_id
               FROM reports
               WHERE report_id = %s AND status_code IN ('open', 'under_review')
               FOR UPDATE""",
            (report_id,),
        )
        report = cursor.fetchone()
        if report is None:
            connection.rollback()
            flash("This report was already reviewed or is unavailable.", "info")
            return redirect(url_for("admin_reports"))
        cursor.execute(
            """UPDATE reports SET status_code = %s, reviewed_by_user_id = %s,
                      reviewed_at = NOW(), resolution_note = %s
               WHERE report_id = %s AND status_code IN ('open', 'under_review')""",
            (status, session["user_id"], note or None, report_id),
        )
        changed = cursor.rowcount
        if changed:
            review_label = {
                "under_review": "under review",
                "resolved": "resolved",
                "dismissed": "dismissed",
            }[status]
            _insert_notifications(
                cursor, [report[0], report[1]], "report_reviewed",
                f"An admin marked a report involving you as {review_label}. View My Reports for the review note.",
                request_id=report[2],
            )
        connection.commit()
        flash("Report review saved." if changed else "This report was not open for review.", "success" if changed else "info")
    except MySQLError:
        if connection is not None and connection.is_connected():
            connection.rollback()
        flash("The report review could not be saved. No report was changed.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    return redirect(url_for("admin_reports"))


@app.get("/donor/dashboard")
@donor_required
def donor_dashboard():
    demo_mode = _sample_mode()
    query = request.args.get("q", "").strip()
    donations = _demo_donations() if demo_mode else _load_donor_donations(session["user_id"])
    pending_requests = _demo_requests() if demo_mode else [
        item for item in _load_donor_requests(session["user_id"], "pending")
    ]
    collection_records = [] if demo_mode else _load_donor_collections(session["user_id"])
    nearby_needs = []
    has_nearby_location = False
    if not demo_mode:
        try:
            nearby_location, nearby_needs = _load_nearby_needs_for_donor(session["user_id"])
            has_nearby_location = nearby_location is not None
        except MySQLError:
            nearby_needs = []
    visible_donations = _filter_records(
        donations, query, ("food_name", "category", "description", "area", "city", "status")
    )
    visible_requests = _filter_records(
        pending_requests, query, ("acceptor_name", "food_name", "status")
    )
    return render_template(
        "donor/dashboard.html",
        active_page="dashboard",
        query=query,
        donations=visible_donations[:3],
        pending_requests=visible_requests,
        collections=collection_records[:3],
        nearby_need_count=(len(nearby_needs) if has_nearby_location else None),
        has_nearby_location=has_nearby_location,
        stats={
            "active": sum(item["status_key"] == "active" for item in donations),
            "pending": len(pending_requests),
            "completed": sum(item["status_key"] == "collected" for item in collection_records),
            "expiring": sum(item["status_key"] == "expiring" for item in donations),
        },
    )


@app.get("/donor/donations")
@donor_required
def donor_donations():
    demo_mode = _sample_mode()
    records = _demo_donations() if demo_mode else _load_donor_donations(session["user_id"])
    valid_tabs = {"all", "active", "pending_requests", "completed", "expiring", "cancelled"}
    tab = request.args.get("tab", "").strip()
    if tab not in valid_tabs:
        legacy_tab = request.args.get("status", "all").strip()
        tab = legacy_tab if legacy_tab in {"active", "expiring"} else "all"
    query = request.args.get("q", "").strip()
    category_filter = request.args.get("category", "all").strip()
    status_filter = request.args.get("listing_status", "all").strip()
    date_from_raw = request.args.get("date_from", "").strip()
    date_to_raw = request.args.get("date_to", "").strip()
    sort_order = request.args.get("sort", "newest").strip()
    filter_error = None

    def parse_filter_date(value, label):
        nonlocal filter_error
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            filter_error = f"Enter a valid {label.lower()} date."
            return None

    date_from = parse_filter_date(date_from_raw, "Start")
    date_to = parse_filter_date(date_to_raw, "End")
    if date_from and date_to and date_from > date_to:
        filter_error = "The start date must be on or before the end date."

    tab_counts = {
        "all": len(records),
        "active": sum(item["status_key"] == "active" for item in records),
        "pending_requests": sum(item["request_count"] > 0 for item in records),
        "completed": sum(item["status_key"] == "completed" for item in records),
        "expiring": sum(item["status_key"] == "expiring" for item in records),
        "cancelled": sum(item["status_key"] == "cancelled" for item in records),
    }
    filtered = records
    if tab == "active":
        filtered = [item for item in filtered if item["status_key"] == "active"]
    elif tab == "pending_requests":
        filtered = [item for item in filtered if item["request_count"] > 0]
    elif tab in {"completed", "expiring", "cancelled"}:
        filtered = [item for item in filtered if item["status_key"] == tab]
    if category_filter != "all":
        filtered = [item for item in filtered if item["category"] == category_filter]
    if status_filter != "all":
        filtered = [item for item in filtered if item["status_key"] == status_filter]
    filtered = _filter_records(filtered, query, ("food_name", "category"))
    if date_from:
        filtered = [item for item in filtered if item["created_at"].date() >= date_from]
    if date_to:
        filtered = [item for item in filtered if item["created_at"].date() <= date_to]
    if filter_error:
        filtered = []
    if sort_order == "oldest":
        filtered.sort(key=lambda item: item["created_at"])
    elif sort_order == "expiring":
        filtered.sort(key=lambda item: (
            item["status_key"] in {"completed", "cancelled"},
            item["deadline_at"] is None,
            item["deadline_at"],
        ))
    else:
        sort_order = "newest"
        filtered.sort(key=lambda item: item["created_at"], reverse=True)

    category_options = sorted({item["category"] for item in records})
    common_filters = {
        "q": query or None,
        "category": category_filter if category_filter != "all" else None,
        "listing_status": status_filter if status_filter != "all" else None,
        "date_from": date_from_raw or None,
        "date_to": date_to_raw or None,
        "sort": sort_order if sort_order != "newest" else None,
        "demo": "1" if demo_mode else None,
    }
    tab_urls = {}
    for tab_name in valid_tabs:
        values = {key: value for key, value in common_filters.items() if value is not None}
        values["tab"] = tab_name
        tab_urls[tab_name] = url_for("donor_donations", **values)
    search_values = {key: value for key, value in common_filters.items() if key != "q" and value is not None}
    search_values["tab"] = tab
    global_search_action = url_for("donor_donations", **search_values)
    filter_action = url_for("donor_donations")
    return render_template(
        "donor/donations.html",
        active_page="donations",
        donations=filtered,
        tab=tab,
        tab_counts=tab_counts,
        tab_urls=tab_urls,
        category_options=category_options,
        category_filter=category_filter,
        status_filter=status_filter,
        query=query,
        date_from=date_from_raw,
        date_to=date_to_raw,
        sort_order=sort_order,
        filter_error=filter_error,
        filter_action=filter_action,
        global_search_action=global_search_action,
        global_search_placeholder="Search your donations…",
        saved_notice=("Donation saved to your account." if request.args.get("saved") == "1" else None),
    )


DONATION_CATEGORIES = (
    "Main Course",
    "Snacks",
    "Bakery Items",
    "Fruits and Vegetables",
    "Beverages",
    "Dairy Products",
    "Sweets and Desserts",
    "Packaged Foods",
    "Other",
)
DONATION_UNITS = ("Portions", "Packets", "Pieces", "Kilograms", "Grams", "Boxes", "Litres")
DIETARY_TYPES = ("Vegetarian", "Vegan", "Non-Vegetarian", "Not specified")
DONATION_STEPS = ("food", "pickup", "contact", "review")
DONOR_TYPE_OPTIONS = (
    "Individual", "Restaurant or hotel", "Catering service", "Event host",
    "Business or institution", "Household", "Other",
)

CATEGORY_CODES = {
    "Main Course": "main_course",
    "Snacks": "snacks",
    "Bakery Items": "bakery",
    "Fruits and Vegetables": "fruits_vegetables",
    "Beverages": "beverages",
    "Dairy Products": "dairy",
    "Sweets and Desserts": "sweets_desserts",
    "Packaged Foods": "packaged_foods",
    "Other": "other",
}
UNIT_CODES = {
    "Portions": "serving",
    "Packets": "packet",
    "Pieces": "piece",
    "Kilograms": "kg",
    "Grams": "gram",
    "Boxes": "box",
    "Litres": "litre",
}
DIETARY_CODES = {
    "Vegetarian": "vegetarian",
    "Vegan": "vegan",
    "Non-Vegetarian": "non_vegetarian",
    "Not specified": "not_specified",
    "": "not_specified",
}


def _load_profile_contact(user_id):
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT full_name, email, COALESCE(phone, '') FROM users WHERE user_id = %s",
            (user_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return {"name": "", "email": "", "phone": ""}
        return {"name": row[0], "email": row[1], "phone": row[2]}
    finally:
        cursor.close()
        connection.close()


def _load_donor_profile(user_id):
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """SELECT u.email, COALESCE(u.phone, ''),
                      COALESCE(dp.donor_type, 'Individual'),
                      COALESCE(NULLIF(dp.default_city, ''), l.city, ''),
                      COALESCE(l.area, ''), COALESCE(l.landmark, ''),
                      COALESCE(l.address, ''), l.latitude, l.longitude
               FROM users AS u
               LEFT JOIN donor_profiles AS dp ON dp.user_id = u.user_id
               LEFT JOIN user_locations AS l ON l.location_id = (
                   SELECT ul.location_id FROM user_locations AS ul
                   WHERE ul.user_id = u.user_id AND ul.is_default = TRUE
                   ORDER BY ul.location_id DESC LIMIT 1
               )
               WHERE u.user_id = %s""",
            (user_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return {
                "email": "", "phone": "", "donor_type": "Individual", "city": "",
                "area": "", "landmark": "", "address": "",
                "latitude": None, "longitude": None,
            }
        donor_type = "Individual" if row[2].lower() == "individual" else row[2]
        return {
            "email": row[0], "phone": row[1], "donor_type": donor_type,
            "city": row[3], "area": row[4], "landmark": row[5],
            "address": row[6], "latitude": row[7], "longitude": row[8],
        }
    finally:
        cursor.close()
        connection.close()


def _load_donor_donations(user_id):
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """SELECT d.donation_id, i.item_id, i.food_name, fc.category_name,
                      do.dietary_name, COALESCE(i.description, ''), i.total_quantity,
                      qu.unit_name, d.area, d.city, d.created_at, d.pickup_ends_at,
                      i.approximate_expiry_at, d.status_code, i.available_quantity,
                      i.photo_reference,
                      (SELECT COUNT(*) FROM donation_requests r
                       WHERE r.item_id = i.item_id AND r.status_code = 'pending'
                         AND d.pickup_ends_at > NOW()
                         AND (i.approximate_expiry_at IS NULL OR i.approximate_expiry_at > NOW())),
                      (SELECT COUNT(*) FROM donation_requests r
                       WHERE r.item_id = i.item_id AND r.status_code = 'approved'),
                      (SELECT COUNT(*) FROM donation_requests r
                       JOIN collections c ON c.request_id = r.request_id
                       WHERE r.item_id = i.item_id AND r.status_code = 'approved'
                         AND c.status_code = 'collected'),
                      (SELECT MAX(c.collected_at) FROM donation_requests r
                       JOIN collections c ON c.request_id = r.request_id
                       WHERE r.item_id = i.item_id AND c.status_code = 'collected')
               FROM donations d
               JOIN donation_items i ON i.donation_id = d.donation_id
               JOIN food_categories fc ON fc.category_id = i.category_id
               JOIN dietary_options do ON do.dietary_option_id = i.dietary_option_id
               JOIN quantity_units qu ON qu.unit_id = i.unit_id
               WHERE d.donor_user_id = %s
               ORDER BY d.created_at DESC, i.item_id DESC""",
            (user_id,),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    now = datetime.now()
    records = []
    for row in rows:
        expiry = row[12]
        effective_deadline = min(
            [value for value in (row[11], expiry) if value is not None],
            default=None,
        )
        donation_status = row[13]
        approved_count = row[17]
        collected_count = row[18]
        collected_at = row[19]
        if donation_status == "cancelled":
            status, status_key = "Cancelled", "cancelled"
        elif approved_count and collected_count >= approved_count:
            status, status_key = "Completed", "completed"
        elif effective_deadline is None or effective_deadline <= now:
            status, status_key = "Expired", "expired"
        elif row[14] <= 0:
            status, status_key = "Fully Allocated", "fully_allocated"
        elif effective_deadline and effective_deadline <= now + timedelta(hours=24):
            status, status_key = "Expiring Soon", "expiring"
        else:
            status, status_key = "Active", "active"
        deadline = effective_deadline
        records.append({
            "id": str(row[1]),
            "donation_id": int(row[0]),
            "item_id": int(row[1]),
            "food_name": row[2],
            "category": row[3],
            "diet": row[4],
            "description": row[5],
            "quantity": row[6],
            "unit": row[7],
            "area": row[8],
            "city": row[9],
            "created_at": row[10],
            "deadline_at": deadline,
            "deadline_label": deadline.strftime("%d %b %Y, %I:%M %p") if deadline else "Not set",
            "collected_label": collected_at.strftime("Collected on %d %b %Y") if status_key == "completed" and collected_at else None,
            "status": status,
            "status_key": status_key,
            "request_count": row[16],
            "image": "food-fallback.svg",
            "uploaded_image": row[15],
            "image_alt": row[2],
        })
    return records


def _load_donor_donation_detail(user_id, item_id):
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """SELECT i.item_id, d.donation_id, i.food_name, fc.category_name,
                      do.dietary_name, COALESCE(i.description, ''), i.photo_reference,
                      i.total_quantity, i.available_quantity, qu.unit_name,
                      i.serving_size, i.serving_unit, i.prepared_at,
                      i.approximate_expiry_at, d.status_code, d.city, d.area,
                      d.landmark, d.pickup_address, d.latitude, d.longitude,
                      d.contact_phone, d.contact_name, d.contact_email,
                      d.pickup_starts_at, d.pickup_ends_at, d.created_at,
                      (SELECT COUNT(*) FROM donation_requests r
                       WHERE r.item_id = i.item_id AND r.status_code = 'pending'
                         AND d.pickup_ends_at > NOW()
                         AND (i.approximate_expiry_at IS NULL OR i.approximate_expiry_at > NOW())),
                      (SELECT COUNT(*) FROM donation_requests r
                       WHERE r.item_id = i.item_id AND r.status_code = 'approved'),
                      (SELECT COUNT(*) FROM donation_requests r
                       JOIN collections c ON c.request_id = r.request_id
                       WHERE r.item_id = i.item_id AND r.status_code = 'approved'
                         AND c.status_code = 'collected'),
                      (SELECT MAX(c.collected_at) FROM donation_requests r
                       JOIN collections c ON c.request_id = r.request_id
                       WHERE r.item_id = i.item_id AND c.status_code = 'collected')
               FROM donation_items i
               JOIN donations d ON d.donation_id = i.donation_id
               JOIN food_categories fc ON fc.category_id = i.category_id
               JOIN dietary_options do ON do.dietary_option_id = i.dietary_option_id
               JOIN quantity_units qu ON qu.unit_id = i.unit_id
               WHERE i.item_id = %s AND d.donor_user_id = %s""",
            (item_id, user_id),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()
    if row is None:
        return None

    now = datetime.now()
    expiry = row[13]
    deadline = min(
        [value for value in (expiry, row[25]) if value is not None],
        default=None,
    )
    if row[14] == "cancelled":
        status, status_key = "Cancelled", "cancelled"
    elif row[28] and row[29] >= row[28]:
        status, status_key = "Completed", "completed"
    elif deadline is None or deadline <= now:
        status, status_key = "Expired", "expired"
    elif row[8] <= 0:
        status, status_key = "Fully Allocated", "fully_allocated"
    elif deadline and deadline <= now + timedelta(hours=24):
        status, status_key = "Expiring Soon", "expiring"
    else:
        status, status_key = "Active", "active"
    return {
        "id": row[0], "donation_id": row[1], "item_id": row[0],
        "food_name": row[2], "category": row[3], "diet": row[4],
        "description": row[5], "uploaded_image": row[6] or "images/food-fallback.svg",
        "quantity": row[7], "available_quantity": row[8], "unit": row[9],
        "serving_size": row[10], "serving_unit": row[11],
        "prepared_at": row[12], "approximate_expiry_at": expiry,
        "status": status, "status_key": status_key, "city": row[15],
        "area": row[16], "landmark": row[17], "pickup_address": row[18],
        "latitude": row[19], "longitude": row[20], "contact_phone": row[21],
        "contact_name": row[22], "contact_email": row[23],
        "pickup_starts_at": row[24], "pickup_ends_at": row[25],
        "created_at": row[26], "request_count": row[27],
        "deadline_at": deadline,
        "deadline_label": deadline.strftime("%d %b %Y, %I:%M %p") if deadline else "Not set",
        "collected_label": row[30].strftime("Collected on %d %b %Y") if row[30] else None,
        "image": "food-fallback.svg", "image_alt": row[2],
    }


def _load_acceptor_profile(user_id):
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """SELECT u.full_name, u.email, COALESCE(u.phone, ''),
                      COALESCE(ap.acceptor_type, 'organisation'),
                      COALESCE(ap.organisation_name, ''), l.city, l.area,
                      COALESCE(l.landmark, ''), l.address, l.latitude, l.longitude
               FROM users AS u
               LEFT JOIN acceptor_profiles AS ap ON ap.user_id = u.user_id
               LEFT JOIN user_locations AS l ON l.location_id = (
                   SELECT ul.location_id FROM user_locations AS ul
                   WHERE ul.user_id = u.user_id AND ul.is_default = TRUE
                   ORDER BY ul.location_id DESC LIMIT 1
               )
               WHERE u.user_id = %s""",
            (user_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return {
                "full_name": "", "email": "", "phone": "", "acceptor_type": "organisation",
                "organisation_name": "", "city": "", "area": "", "landmark": "",
                "address": "", "latitude": None, "longitude": None,
            }
        return {
            "full_name": row[0], "email": row[1], "phone": row[2],
            "acceptor_type": row[3], "organisation_name": row[4],
            "city": row[5] or "", "area": row[6] or "", "landmark": row[7] or "",
            "address": row[8] or "", "latitude": row[9], "longitude": row[10],
        }
    finally:
        cursor.close()
        connection.close()


def _distance_km(latitude_a, longitude_a, latitude_b, longitude_b):
    """Return the great-circle distance between two coordinate pairs."""
    lat_a, lon_a, lat_b, lon_b = map(
        math.radians,
        (float(latitude_a), float(longitude_a), float(latitude_b), float(longitude_b)),
    )
    lat_delta = lat_b - lat_a
    lon_delta = lon_b - lon_a
    haversine = (
        math.sin(lat_delta / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(lon_delta / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(min(1.0, haversine)))


def _load_nearby_donations(latitude, longitude, query="", category_code="all", dietary_code="all", acceptor_id=None):
    if latitude is None or longitude is None:
        return []

    effective_deadline_sql = (
        "COALESCE(LEAST(d.pickup_ends_at, i.approximate_expiry_at), "
        "d.pickup_ends_at, i.approximate_expiry_at)"
    )
    distance_expression = (
        "6371 * 2 * ASIN(SQRT(LEAST(1, "
        "POW(SIN(RADIANS(d.latitude - %s) / 2), 2) + "
        "COS(RADIANS(%s)) * COS(RADIANS(d.latitude)) * "
        "POW(SIN(RADIANS(d.longitude - %s) / 2), 2))))"
    )
    sql = f"""SELECT i.item_id, d.donation_id, i.food_name, fc.category_name,
                      do.dietary_name, COALESCE(i.description, ''),
                      GREATEST(0, i.available_quantity - COALESCE(pending.pending_quantity, 0)),
                      qu.unit_name, d.area, d.city, d.pickup_ends_at,
                      i.approximate_expiry_at, i.photo_reference,
                      my_request.status_code, {distance_expression} AS distance_km,
                      {effective_deadline_sql} AS effective_deadline,
                      CASE WHEN {effective_deadline_sql} < DATE_ADD(NOW(), INTERVAL 2 HOUR)
                           THEN 1 ELSE 0 END AS is_expiring_soon
               FROM donation_items AS i
               JOIN donations AS d ON d.donation_id = i.donation_id
               JOIN food_categories AS fc ON fc.category_id = i.category_id
               JOIN dietary_options AS do ON do.dietary_option_id = i.dietary_option_id
               JOIN quantity_units AS qu ON qu.unit_id = i.unit_id
               LEFT JOIN (
                   SELECT item_id, SUM(quantity_requested) AS pending_quantity
                   FROM donation_requests WHERE status_code = 'pending'
                   GROUP BY item_id
               ) AS pending ON pending.item_id = i.item_id
               LEFT JOIN (
                   SELECT item_id, MAX(request_id) AS request_id
                   FROM donation_requests
                   WHERE acceptor_user_id = %s AND status_code IN ('pending', 'approved')
                   GROUP BY item_id
               ) AS my_requests ON my_requests.item_id = i.item_id
               LEFT JOIN donation_requests AS my_request ON my_request.request_id = my_requests.request_id
               WHERE d.status_code IN ('active', 'fully_allocated')
                 AND d.pickup_ends_at > NOW()
                 AND i.available_quantity > COALESCE(pending.pending_quantity, 0)
                 AND (i.approximate_expiry_at IS NULL OR i.approximate_expiry_at > NOW())
                 AND d.latitude IS NOT NULL AND d.longitude IS NOT NULL"""
    # SELECT's distance expression appears before the request-status join in
    # the SQL text, so bind its placeholders before the acceptor id.
    params = [float(latitude), float(latitude), float(longitude), acceptor_id or 0]
    if category_code != "all":
        sql += " AND fc.category_code = %s"
        params.append(category_code)
    if dietary_code != "all":
        sql += " AND do.dietary_code = %s"
        params.append(dietary_code)
    if query:
        sql += " AND (LOWER(i.food_name) LIKE %s OR LOWER(fc.category_name) LIKE %s)"
        search = f"%{query.casefold()}%"
        params.extend((search, search))
    sql += (
        " HAVING distance_km <= %s "
        "ORDER BY is_expiring_soon DESC, effective_deadline ASC, distance_km ASC LIMIT 100"
    )
    params.append(25)

    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    return [{
        "item_id": row[0], "donation_id": row[1], "food_name": row[2],
        "category": row[3], "diet": row[4], "description": row[5],
        "available_quantity": row[6], "unit": row[7], "area": row[8],
        "city": row[9], "pickup_ends_at": row[10],
        "expiry_at": row[11] or row[10],
        "photo": row[12] or "images/food-fallback.svg",
        "my_request_status": row[13], "distance_km": round(float(row[14]), 1),
        "effective_deadline": row[15], "is_expiring_soon": bool(row[16]),
    } for row in rows]


def _load_acceptor_needs(user_id, status_code="all"):
    sql = """SELECT n.need_id, n.people_count, n.needed_for, n.meal_period,
                    do.dietary_name, n.city, n.area, n.notes, n.status_code, n.created_at,
                    (SELECT GROUP_CONCAT(fc.category_name ORDER BY fc.category_name SEPARATOR ', ')
                     FROM acceptor_need_categories AS nc
                     JOIN food_categories AS fc ON fc.category_id = nc.category_id
                     WHERE nc.need_id = n.need_id) AS categories
             FROM acceptor_needs AS n
             JOIN dietary_options AS do ON do.dietary_option_id = n.dietary_option_id
             WHERE n.acceptor_user_id = %s"""
    params = [user_id]
    if status_code != "all":
        sql += " AND n.status_code = %s"
        params.append(status_code)
    sql += " ORDER BY n.created_at DESC, n.need_id DESC"
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()
    return [{
        "id": row[0], "people_count": row[1], "needed_for": row[2],
        "meal_period": row[3], "diet": row[4], "city": row[5], "area": row[6],
        "notes": row[7] or "", "status": row[8], "created_at": row[9],
        "categories": row[10] or "",
    } for row in rows]


def _load_acceptor_requests(user_id):
    connection = get_db_connection()
    cursor = connection.cursor()
    pin_nonce_expression = "c.pickup_pin_nonce" if _pickup_pin_schema_ready() else "NULL"
    confirmation_columns = (
        "c.donor_confirmed_at, c.acceptor_confirmed_at"
        if _collection_confirmation_schema_ready() else "NULL, NULL"
    )
    try:
        cursor.execute(
            f"""SELECT r.request_id, r.quantity_requested, r.status_code, r.requested_at,
                      r.rejection_reason, i.food_name, fc.category_name, qu.unit_name,
                      d.city, d.area, d.pickup_ends_at,
                      CASE WHEN c.collection_id IS NOT NULL THEN d.pickup_address END,
                      CASE WHEN c.collection_id IS NOT NULL THEN d.contact_phone END,
                      c.status_code, c.scheduled_pickup_at, c.collected_at,
                      d.pickup_starts_at, donor.full_name, i.approximate_expiry_at,
                      {pin_nonce_expression} AS pickup_pin_nonce,
                      c.collection_id, {confirmation_columns}
               FROM donation_requests AS r
               JOIN donation_items AS i ON i.item_id = r.item_id
               JOIN donations AS d ON d.donation_id = i.donation_id
               JOIN users AS donor ON donor.user_id = d.donor_user_id
               JOIN food_categories AS fc ON fc.category_id = i.category_id
               JOIN quantity_units AS qu ON qu.unit_id = i.unit_id
               LEFT JOIN collections AS c ON c.request_id = r.request_id
               WHERE r.acceptor_user_id = %s
               ORDER BY r.requested_at DESC, r.request_id DESC""",
            (user_id,),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()
    now = datetime.now()
    records = []
    for row in rows:
        effective_deadline = min(
            [value for value in (row[10], row[18]) if value is not None],
            default=None,
        )
        status = (
            "Expired" if row[2] == "pending" and (effective_deadline is None or effective_deadline <= now)
            else "Declined" if row[2] == "rejected"
            else row[2].title()
        )
        records.append({
            "id": row[0], "quantity": row[1], "status": status,
            "status_key": "rejected" if row[2] == "rejected" else status.lower().replace(" ", "_"),
            "requested_at": row[3],
            "rejection_reason": row[4], "food_name": row[5], "category": row[6],
            "unit": row[7], "city": row[8], "area": row[9],
            "pickup_ends_at": row[10], "pickup_address": row[11],
            "contact_phone": row[12], "collection_status": row[13],
            "scheduled_pickup_at": row[14], "collected_at": row[15],
            "pickup_starts_at": row[16], "donor_name": row[17],
            "donor_label": row[17], "effective_deadline": effective_deadline,
            "collection_overdue": row[13] == "scheduled" and (effective_deadline is None or effective_deadline <= now),
            # The PIN is scoped to the collection ID, not the request ID.
            "pickup_pin": _pickup_pin_for(row[20], row[19]) if row[13] == "scheduled" and row[19] else None,
            "collection_id": row[20], "donor_confirmed_at": row[21],
            "acceptor_confirmed_at": row[22],
            "can_confirm_collection": (
                row[13] == "scheduled" and row[21] is not None and row[22] is None
                and (row[16] or row[14]) is not None
                and (row[16] or row[14]) <= now
            ),
        })
    return records


def _load_donor_requests(user_id, status_filter="all"):
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        sql = """SELECT r.request_id, r.quantity_requested, r.status_code, r.requested_at,
                        r.rejection_reason, i.food_name, i.available_quantity,
                        qu.unit_name, fc.category_name, do.dietary_name, d.city, d.area,
                        d.pickup_ends_at, u.full_name,
                        COALESCE(ap.organisation_name, ''), i.approximate_expiry_at
                 FROM donation_requests AS r
                 JOIN donation_items AS i ON i.item_id = r.item_id
                 JOIN donations AS d ON d.donation_id = i.donation_id
                 JOIN users AS u ON u.user_id = r.acceptor_user_id
                 JOIN quantity_units AS qu ON qu.unit_id = i.unit_id
                 JOIN food_categories AS fc ON fc.category_id = i.category_id
                 JOIN dietary_options AS do ON do.dietary_option_id = i.dietary_option_id
                 LEFT JOIN acceptor_profiles AS ap ON ap.user_id = u.user_id
                 WHERE d.donor_user_id = %s"""
        params = [user_id]
        if status_filter == "expired":
            sql += " AND r.status_code = 'pending' AND ((d.pickup_ends_at IS NULL AND i.approximate_expiry_at IS NULL) OR (d.pickup_ends_at IS NOT NULL AND d.pickup_ends_at <= NOW()) OR (i.approximate_expiry_at IS NOT NULL AND i.approximate_expiry_at <= NOW()))"
        elif status_filter != "all":
            sql += " AND r.status_code = %s"
            params.append(status_filter)
        sql += " ORDER BY r.requested_at DESC, r.request_id DESC"
        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()
    now = datetime.now()
    records = []
    for row in rows:
        effective_deadline = min(
            [value for value in (row[12], row[15]) if value is not None],
            default=None,
        )
        expired = row[2] == "pending" and (effective_deadline is None or effective_deadline <= now)
        status_key = "expired" if expired else row[2]
        records.append({
            "id": row[0], "quantity": row[1],
            "status": "Expired" if expired else {"rejected": "Declined"}.get(row[2], row[2].title()),
            "status_key": status_key, "requested_at": row[3], "rejection_reason": row[4],
            "food_name": row[5], "available_quantity": row[6], "unit": row[7],
            "category": row[8], "diet": row[9], "city": row[10], "area": row[11],
            "pickup_ends_at": row[12], "acceptor_name": row[14] or row[13],
        })
    if status_filter == "pending":
        return [item for item in records if item["status_key"] == "pending"]
    if status_filter == "expired":
        return [item for item in records if item["status_key"] == "expired"]
    if status_filter != "all":
        return [item for item in records if item["status_key"] == status_filter]
    return records


def _load_donor_collections(user_id):
    connection = get_db_connection()
    cursor = connection.cursor()
    confirmation_columns = (
        "c.donor_confirmed_at, c.acceptor_confirmed_at"
        if _collection_confirmation_schema_ready() else "NULL, NULL"
    )
    try:
        cursor.execute(
            f"""SELECT c.collection_id, r.request_id, c.status_code,
                      c.scheduled_pickup_at, c.collected_at, c.notes,
                      r.quantity_requested, i.food_name, fc.category_name, qu.unit_name,
                      d.city, d.area, d.pickup_address, d.pickup_starts_at, d.pickup_ends_at,
                      acceptor.full_name, COALESCE(ap.organisation_name, ''), acceptor.phone,
                      i.approximate_expiry_at, {confirmation_columns}
               FROM collections c
               JOIN donation_requests r ON r.request_id = c.request_id
               JOIN donation_items i ON i.item_id = r.item_id
               JOIN donations d ON d.donation_id = i.donation_id
               JOIN food_categories fc ON fc.category_id = i.category_id
               JOIN quantity_units qu ON qu.unit_id = i.unit_id
               JOIN users acceptor ON acceptor.user_id = r.acceptor_user_id
               LEFT JOIN acceptor_profiles ap ON ap.user_id = acceptor.user_id
               WHERE d.donor_user_id = %s
               ORDER BY CASE c.status_code WHEN 'scheduled' THEN 0 ELSE 1 END,
                        COALESCE(c.scheduled_pickup_at, c.created_at) DESC, c.collection_id DESC""",
            (user_id,),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()
    now = datetime.now()
    records = [{
        "id": row[0], "request_id": row[1], "status_key": row[2],
        "status": {"scheduled": "Yet to collect", "collected": "Collected",
                   "missed": "Missed pickup", "cancelled": "Cancelled"}.get(row[2], row[2].title()),
        "scheduled_at": row[3], "collected_at": row[4], "notes": row[5] or "",
        "quantity": row[6], "food_name": row[7], "category": row[8], "unit": row[9],
        "city": row[10], "area": row[11], "pickup_address": row[12],
        "pickup_starts_at": row[13], "pickup_ends_at": row[14],
        "acceptor_name": row[16] or row[15], "acceptor_phone": row[17],
        "safe_deadline": min([value for value in (row[14], row[18]) if value is not None], default=row[14]),
        "donor_confirmed_at": row[19], "acceptor_confirmed_at": row[20],
    } for row in rows]
    for record in records:
        record["is_overdue"] = (
            record["status_key"] == "scheduled"
            and (record["safe_deadline"] is None or record["safe_deadline"] <= now)
        )
        if record["is_overdue"]:
            record["status"] = "Pickup window ended"
        collection_start = record["pickup_starts_at"] or record["scheduled_at"]
        record["can_mark_collected"] = (
            record["status_key"] == "scheduled"
            and record["donor_confirmed_at"] is None
            and collection_start is not None
            and collection_start <= now
        )
        record["can_mark_missed"] = (
            record["status_key"] == "scheduled"
            and record["donor_confirmed_at"] is None
            and ((record["scheduled_at"] is not None and record["scheduled_at"] <= now)
                 or record["is_overdue"])
        )
        record["can_cancel"] = (
            record["status_key"] == "scheduled" and record["donor_confirmed_at"] is None
        )
        if record["status_key"] == "scheduled" and record["donor_confirmed_at"] is not None:
            record["status"] = "Awaiting acceptor confirmation"
    return records


REPORT_REASON_LABELS_BY_ROLE = {
    "donor": {
        "missed_collection": "Acceptor did not collect the approved food",
        "other_conduct": "Other acceptor conduct",
    },
    "acceptor": {
        "missed_collection": "Donor did not make the approved pickup available",
        "unsafe_food": "Food was unsafe or mislabelled",
        "other_conduct": "Other donor conduct",
    },
}


def _load_report_reasons(reporter_role):
    labels = REPORT_REASON_LABELS_BY_ROLE.get(reporter_role, {})
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT reason_code, reason_label FROM report_reasons WHERE is_active = TRUE ORDER BY reason_id"
        )
        return [
            {"code": row[0], "label": labels[row[0]]}
            for row in cursor.fetchall() if row[0] in labels
        ]
    finally:
        cursor.close()
        connection.close()


def _load_participant_reports(user_id):
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """SELECT rp.report_id, rp.request_id, d.donation_id, i.food_name,
                      rr.reason_code, reporter_role.role_code, rp.details,
                      rp.status_code, rp.created_at, rp.reviewed_at, rp.resolution_note,
                      CASE WHEN rp.reporter_user_id = %s THEN target.full_name ELSE reporter.full_name END,
                      CASE WHEN rp.reporter_user_id = %s THEN 'Submitted by you' ELSE 'Filed about you' END
               FROM reports rp
               JOIN donation_requests r ON r.request_id = rp.request_id
               JOIN donation_items i ON i.item_id = r.item_id
               JOIN donations d ON d.donation_id = i.donation_id
               JOIN users reporter ON reporter.user_id = rp.reporter_user_id
               JOIN roles reporter_role ON reporter_role.role_id = reporter.role_id
               JOIN users target ON target.user_id = rp.reported_user_id
               JOIN report_reasons rr ON rr.reason_id = rp.reason_id
               WHERE rp.reporter_user_id = %s OR rp.reported_user_id = %s
               ORDER BY rp.created_at DESC, rp.report_id DESC""",
            (user_id, user_id, user_id, user_id),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()
    return [{
        "id": row[0], "request_id": row[1], "donation_id": row[2],
        "food_name": row[3],
        "reason": REPORT_REASON_LABELS_BY_ROLE.get(row[5], {}).get(
            row[4], row[4].replace("_", " ").title()
        ),
        "details": row[6], "status": row[7], "created_at": row[8],
        "reviewed_at": row[9], "resolution_note": row[10],
        "other_party": row[11], "relationship": row[12],
    } for row in rows]


def _load_nearby_needs_for_donor(user_id):
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """SELECT latitude, longitude FROM user_locations
               WHERE user_id = %s AND is_default = TRUE
                 AND latitude IS NOT NULL AND longitude IS NOT NULL
               ORDER BY location_id DESC LIMIT 1""",
            (user_id,),
        )
        profile_location = cursor.fetchone()
        cursor.execute(
            """SELECT latitude, longitude FROM donations
               WHERE donor_user_id = %s AND status_code = 'active'
                 AND pickup_ends_at > NOW() AND latitude IS NOT NULL AND longitude IS NOT NULL
               ORDER BY created_at DESC""",
            (user_id,),
        )
        locations = list(dict.fromkeys(
            ([profile_location] if profile_location else []) + cursor.fetchall()
        ))
        if not locations:
            return None, []

        cursor.execute(
            """SELECT n.need_id, n.people_count, n.needed_for, n.meal_period,
                       do.dietary_name, n.city, n.area, n.status_code,
                       COALESCE(NULLIF(ap.organisation_name, ''), 'Local acceptor'),
                       (SELECT GROUP_CONCAT(fc.category_name ORDER BY fc.category_name SEPARATOR ', ')
                        FROM acceptor_need_categories AS nc
                        JOIN food_categories AS fc ON fc.category_id = nc.category_id
                        WHERE nc.need_id = n.need_id),
                       n.latitude, n.longitude
                FROM acceptor_needs AS n
                JOIN dietary_options AS do ON do.dietary_option_id = n.dietary_option_id
                LEFT JOIN acceptor_profiles AS ap ON ap.user_id = n.acceptor_user_id
                WHERE n.status_code = 'open' AND n.needed_for >= CURRENT_DATE
                  AND n.latitude IS NOT NULL AND n.longitude IS NOT NULL
                ORDER BY n.needed_for LIMIT 500"""
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    needs = []
    for row in rows:
        distance = min(_distance_km(lat, lon, row[10], row[11]) for lat, lon in locations)
        if distance > 25:
            continue
        needs.append({
            "id": row[0], "people_count": row[1], "needed_for": row[2],
            "meal_period": row[3], "diet": row[4], "city": row[5], "area": row[6],
            "organisation": row[8], "categories": row[9] or "Any category",
            "distance_km": round(distance, 1),
        })
    needs.sort(key=lambda item: (item["distance_km"], item["needed_for"]))
    return {"location_count": len(locations)}, needs[:100]


def _submit_request_report(request_id, reporter_role):
    reason_code = request.form.get("reason_code", "").strip()
    details = request.form.get("details", "").strip()
    return_to = request.form.get("return_to", "requests")
    list_endpoint = (
        "donor_collections" if reporter_role == "donor" and return_to == "collections"
        else "acceptor_collections" if reporter_role == "acceptor" and return_to == "collections"
        else "donor_requests" if reporter_role == "donor" else "acceptor_requests"
    )
    if reporter_role != session.get("role"):
        abort(403)
    if reason_code not in REPORT_REASON_LABELS_BY_ROLE.get(reporter_role, {}):
        flash("Choose a report reason that applies to this participant.", "error")
        return redirect(url_for(list_endpoint))
    if len(details) > 1000:
        flash("Keep report details to 1,000 characters or fewer.", "error")
        return redirect(url_for(list_endpoint))

    connection = None
    cursor = None
    admin_ids = []
    report_saved = False
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor()
        cursor.execute(
            """SELECT d.donor_user_id, r.acceptor_user_id, c.collection_id, d.donation_id
               FROM donation_requests r
               JOIN donation_items i ON i.item_id = r.item_id
               JOIN donations d ON d.donation_id = i.donation_id
               LEFT JOIN collections c ON c.request_id = r.request_id
               WHERE r.request_id = %s FOR UPDATE""",
            (request_id,),
        )
        participants = cursor.fetchone()
        if participants is None:
            connection.rollback()
            abort(404)
        donor_id, acceptor_id, collection_id, donation_id = participants
        reporter_id = session["user_id"]
        if reporter_role == "donor" and donor_id == reporter_id:
            reported_id = acceptor_id
        elif reporter_role == "acceptor" and acceptor_id == reporter_id:
            reported_id = donor_id
        else:
            connection.rollback()
            abort(403)
        cursor.execute(
            "SELECT reason_id FROM report_reasons WHERE reason_code = %s AND is_active = TRUE",
            (reason_code,),
        )
        reason = cursor.fetchone()
        if reason is None:
            connection.rollback()
            flash("Choose one of the listed report reasons.", "error")
            return redirect(url_for(list_endpoint))
        cursor.execute(
            """SELECT 1 FROM reports WHERE request_id = %s AND reporter_user_id = %s
               AND status_code IN ('open', 'under_review') LIMIT 1""",
            (request_id, reporter_id),
        )
        if cursor.fetchone():
            connection.rollback()
            flash("You already have an open report for this request.", "info")
            return redirect(url_for(list_endpoint))
        cursor.execute(
            """INSERT INTO reports
               (request_id, collection_id, reporter_user_id, reported_user_id, reason_id, details)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (request_id, collection_id, reporter_id, reported_id, reason[0], details or None),
        )
        cursor.execute(
            """SELECT u.user_id FROM users u JOIN roles r ON r.role_id = u.role_id
               WHERE r.role_code = 'admin' AND u.account_status = 'active'"""
        )
        admin_ids = [row[0] for row in cursor.fetchall()]
        _insert_notifications(
            cursor, admin_ids, "user_reported",
            "A participant submitted a report for a food request. Review it in Admin Reports.",
            donation_id=donation_id, request_id=request_id,
        )
        connection.commit()
        report_saved = True
        flash("Report sent to the admin team for review.", "success")
    except MySQLError:
        if connection is not None and connection.is_connected():
            connection.rollback()
        flash("The report could not be saved. No report was created.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    if report_saved and admin_ids:
        _send_browser_push(admin_ids, "New participant report", "A Plentiq report is waiting for admin review.", url_for("admin_reports"))
    return redirect(url_for(list_endpoint))


def _mark_user_notifications_read(user_id, notification_id=None):
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        if notification_id is None:
            cursor.execute(
                "UPDATE notifications SET read_at = NOW() WHERE recipient_user_id = %s AND read_at IS NULL",
                (user_id,),
            )
        else:
            cursor.execute(
                "UPDATE notifications SET read_at = NOW() WHERE recipient_user_id = %s AND notification_id = %s AND read_at IS NULL",
                (user_id, notification_id),
            )
        changed = cursor.rowcount
        connection.commit()
        return changed
    except MySQLError:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def _wants_ajax_json():
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    )


def _text_value(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _valid_phone(number):
    if not re.fullmatch(r"\+?[0-9\s().-]+", number):
        return False
    digits = re.sub(r"[^0-9]", "", number)
    return 7 <= len(digits) <= 15


def _empty_food_item():
    return {
        "food_name": "",
        "category": "",
        "description": "",
        "dietary_type": "",
        "quantity": "",
        "unit": "",
        "serving_size": "",
        "serving_unit": "",
        "prepared_at": "",
        "item_deadline": "",
    }


def _maximum_expiry_days(category):
    return 7 if category == "Fruits and Vegetables" else 30


def _parse_local_datetime(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?", value):
        raise ValueError("Expected a local date and time.")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise ValueError("Timezone offsets are not accepted for local form dates.")
    return parsed


def _donation_payload_from_request():
    if request.is_json:
        payload = request.get_json(silent=True)
        payload = payload if isinstance(payload, dict) else {}
        item_rows = payload.get("items", [])
        pickup = payload.get("pickup", {})
        contact = payload.get("contact", {})
        confirmation = payload.get("confirm_accuracy", False)
    elif request.form.get("payload"):
        try:
            payload = json.loads(request.form["payload"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        item_rows = payload.get("items", [])
        pickup = payload.get("pickup", {})
        contact = payload.get("contact", {})
        confirmation = payload.get("confirm_accuracy", False)
    else:
        indexed_items = {}
        for key, value in request.form.items():
            match = re.fullmatch(r"items\[(\d+)\]\[([a-z_]+)\]", key)
            if match:
                index, field = int(match.group(1)), match.group(2)
                indexed_items.setdefault(index, {})[field] = value
        item_rows = [indexed_items[index] for index in sorted(indexed_items)]
        pickup = {
            field: request.form.get(f"pickup[{field}]", "")
            for field in (
                "city", "area", "landmark", "pickup_address", "latitude", "longitude",
                "pickup_start", "pickup_end",
            )
        }
        contact = {
            field: request.form.get(f"contact[{field}]", "")
            for field in (
                "contact_name", "phone", "email", "use_profile", "save_to_profile",
            )
        }
        confirmation = request.form.get("confirm_accuracy", "")

    if not isinstance(item_rows, list):
        item_rows = []
    if not isinstance(pickup, dict):
        pickup = {}
    if not isinstance(contact, dict):
        contact = {}

    item_fields = tuple(_empty_food_item())
    items = []
    for row in item_rows:
        if isinstance(row, dict):
            items.append({field: _text_value(row.get(field)) for field in item_fields})

    pickup_fields = (
        "city", "area", "landmark", "pickup_address", "latitude", "longitude",
        "pickup_start", "pickup_end",
    )
    contact_fields = (
        "contact_name", "phone", "email", "use_profile", "save_to_profile",
    )
    return {
        "items": items,
        "pickup": {field: _text_value(pickup.get(field)) for field in pickup_fields},
        "contact": {field: _text_value(contact.get(field)) for field in contact_fields},
        "confirm_accuracy": confirmation is True or str(confirmation).lower() in ("on", "true", "1"),
    }


def _validate_donation_submission(payload):
    errors = []

    def add_error(field, message, step):
        errors.append({"field": field, "message": message, "step": step})

    items = payload["items"]
    pickup = payload["pickup"]
    contact = payload["contact"]
    now = datetime.now()
    if not items:
        add_error("items", "Add at least one food item.", "food")

    item_deadlines = []
    item_preparations = []
    for index, item in enumerate(items):
        prefix = f"items.{index}."
        if not item["food_name"]:
            add_error(prefix + "food_name", "Enter a food name.", "food")
        elif len(item["food_name"]) > 120:
            add_error(prefix + "food_name", "Food names must be 120 characters or fewer.", "food")
        if item["category"] not in DONATION_CATEGORIES:
            add_error(prefix + "category", "Choose a food category from the list.", "food")
        if not item["description"]:
            add_error(prefix + "description", "Enter a short description.", "food")
        elif len(item["description"]) > 300:
            add_error(prefix + "description", "Descriptions must be 300 characters or fewer.", "food")
        if item["dietary_type"] and item["dietary_type"] not in DIETARY_TYPES:
            add_error(prefix + "dietary_type", "Choose a dietary type from the list.", "food")
        try:
            quantity = Decimal(item["quantity"])
            if not quantity.is_finite() or quantity <= 0:
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            add_error(prefix + "quantity", "Quantity must be a valid number greater than zero.", "food")
        if item["unit"] not in DONATION_UNITS:
            add_error(prefix + "unit", "Choose a quantity unit from the list.", "food")
        if item["serving_size"]:
            try:
                serving_size = Decimal(item["serving_size"])
                if not serving_size.is_finite() or serving_size <= 0:
                    raise InvalidOperation
            except (InvalidOperation, TypeError, ValueError):
                add_error(prefix + "serving_size", "Serving size must be greater than zero.", "food")
        is_produce = item["category"] == "Fruits and Vegetables"
        prepared_at_for_limit = None
        if not item["prepared_at"] and not is_produce:
            add_error(prefix + "prepared_at", "Enter when this food item was prepared or packed.", "food")
        else:
            if item["prepared_at"]:
                try:
                    prepared_at = _parse_local_datetime(item["prepared_at"])
                    prepared_at_for_limit = prepared_at
                    item_preparations.append((index, prepared_at))
                    if prepared_at > now:
                        add_error(prefix + "prepared_at", "Prepared date and time cannot be in the future.", "food")
                except ValueError:
                    add_error(prefix + "prepared_at", "Enter a valid local prepared date and time.", "food")
        if not item["item_deadline"]:
            add_error(prefix + "item_deadline", "Enter an approximate expiry for this item.", "food")
        else:
            try:
                item_deadline = _parse_local_datetime(item["item_deadline"])
                item_deadlines.append((index, item_deadline))
                if item_deadline <= now:
                    add_error(prefix + "item_deadline", "Approximate expiry must be in the future.", "food")
                elif item_deadline > (prepared_at_for_limit or now) + timedelta(days=_maximum_expiry_days(item["category"])):
                    days = _maximum_expiry_days(item["category"])
                    add_error(
                        prefix + "item_deadline",
                        f"Set this item's approximate expiry within {days} days of preparation (or today when produce preparation is unknown). This limit keeps listings realistic; follow the food label or storage guidance.",
                        "food",
                    )
            except ValueError:
                add_error(prefix + "item_deadline", "Enter a valid local date and time for this item's approximate expiry.", "food")

    preparation_by_item = dict(item_preparations)
    for index, item_deadline in item_deadlines:
        prepared_at = preparation_by_item.get(index)
        if prepared_at and item_deadline <= prepared_at:
            add_error(
                f"items.{index}.item_deadline",
                "Approximate expiry must be after this item's prepared date and time.",
                "food",
            )

    if item_deadlines:
        earliest_index, earliest_deadline = min(item_deadlines, key=lambda entry: entry[1])
        if now < earliest_deadline <= now + timedelta(hours=1):
            add_error(
                f"items.{earliest_index}.item_deadline",
                "Approximate expiry must be more than one hour from now so pickup can end one hour earlier.",
                "food",
            )
    else:
        earliest_deadline = None

    pickup_start = now.replace(second=0, microsecond=0)
    pickup["pickup_start"] = pickup_start.isoformat(timespec="minutes")
    pickup["pickup_end"] = (
        (earliest_deadline - timedelta(hours=1)).isoformat(timespec="minutes")
        if earliest_deadline else ""
    )

    for field, label, limit in (
        ("city", "City", 100),
        ("area", "Area", 120),
        ("pickup_address", "Full pickup address", 300),
    ):
        value = pickup[field]
        if not value:
            add_error("pickup." + field, f"{label} is required.", "pickup")
        elif len(value) > limit:
            add_error("pickup." + field, f"{label} must be {limit} characters or fewer.", "pickup")
    if len(pickup["landmark"]) > 160:
        add_error("pickup.landmark", "Landmark must be 160 characters or fewer.", "pickup")

    latitude_raw, longitude_raw = pickup["latitude"], pickup["longitude"]
    if bool(latitude_raw) != bool(longitude_raw):
        add_error("pickup.latitude", "Enter both latitude and longitude, or leave both blank.", "pickup")
    elif latitude_raw and longitude_raw:
        try:
            latitude, longitude = float(latitude_raw), float(longitude_raw)
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError
        except (TypeError, ValueError):
            add_error("pickup.latitude", "Enter valid coordinates (latitude −90 to 90 and longitude −180 to 180).", "pickup")
    else:
        add_error(
            "pickup.latitude",
            "Choose your pickup point with Use my current location, or enter both coordinates, so nearby acceptors can find it.",
            "pickup",
        )

    if not contact["contact_name"]:
        add_error("contact.contact_name", "Contact name is required.", "contact")
    elif len(contact["contact_name"]) > 120:
        add_error("contact.contact_name", "Contact name must be 120 characters or fewer.", "contact")

    if not _valid_phone(contact["phone"]):
        add_error("contact.phone", "Enter a valid phone number using 7 to 15 digits.", "contact")
    if contact["email"] and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", contact["email"]):
        add_error("contact.email", "Enter a valid email address or leave it blank.", "contact")
    if len(contact["email"]) > 254:
        add_error("contact.email", "Email addresses must be 254 characters or fewer.", "contact")
    if not payload["confirm_accuracy"]:
        add_error("confirm_accuracy", "Confirm that the details are accurate and the food is safe.", "review")

    return errors


def _read_food_photos(item_count):
    photos = {}
    errors = []
    maximum_bytes = 5 * 1024 * 1024
    signatures = {
        ".png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": lambda data: data.startswith(b"\xff\xd8\xff"),
        ".jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
        ".webp": lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    }
    for index in range(item_count):
        upload = request.files.get(f"items[{index}][food_photo]")
        if upload is None or not upload.filename:
            continue
        extension = Path(upload.filename).suffix.lower()
        data = upload.stream.read(maximum_bytes + 1)
        if extension not in signatures or not signatures[extension](data):
            errors.append({
                "field": f"items.{index}.food_photo",
                "message": "Upload a valid PNG, JPEG, or WebP food photo.",
                "step": "food",
            })
        elif len(data) > maximum_bytes:
            errors.append({
                "field": f"items.{index}.food_photo",
                "message": "Each food photo must be 5 MB or smaller.",
                "step": "food",
            })
        else:
            photos[index] = (data, extension)
    return photos, errors


def _save_donation(payload, photos, user_id):
    connection = None
    cursor = None
    stored_photo_refs = []
    nearby_acceptor_ids = []
    donation_id = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor()

        cursor.execute("SELECT category_code, category_id FROM food_categories WHERE is_active = TRUE")
        category_ids = {code: category_id for code, category_id in cursor.fetchall()}
        cursor.execute("SELECT unit_code, unit_id FROM quantity_units")
        unit_ids = {code: unit_id for code, unit_id in cursor.fetchall()}
        cursor.execute("SELECT dietary_code, dietary_option_id FROM dietary_options")
        dietary_ids = {code: option_id for code, option_id in cursor.fetchall()}

        required_categories = set(CATEGORY_CODES.values())
        required_units = set(UNIT_CODES.values())
        required_dietary = set(DIETARY_CODES.values())
        if not required_categories.issubset(category_ids):
            raise RuntimeError("The food category lookup rows are incomplete.")
        if not required_units.issubset(unit_ids):
            raise RuntimeError("The quantity unit lookup rows are incomplete.")
        if not required_dietary.issubset(dietary_ids):
            raise RuntimeError("The dietary lookup rows are incomplete.")

        pickup = payload["pickup"]
        contact = payload["contact"]
        pickup_start = _parse_local_datetime(pickup["pickup_start"])
        pickup_end = _parse_local_datetime(pickup["pickup_end"])
        latitude = Decimal(pickup["latitude"]) if pickup["latitude"] else None
        longitude = Decimal(pickup["longitude"]) if pickup["longitude"] else None
        cursor.execute(
            """INSERT INTO donations
               (donor_user_id, city, area, landmark, pickup_address, latitude,
                longitude, contact_phone, contact_name, contact_email,
                pickup_starts_at, pickup_ends_at, status_code)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')""",
            (
                user_id, pickup["city"], pickup["area"], pickup["landmark"] or None,
                pickup["pickup_address"], latitude, longitude, contact["phone"],
                contact["contact_name"], contact["email"] or None,
                pickup_start, pickup_end,
            ),
        )
        donation_id = cursor.lastrowid

        upload_directory = Path(app.static_folder) / "uploads"
        for index, item in enumerate(payload["items"]):
            photo_reference = None
            if index in photos:
                photo_data, extension = photos[index]
                filename = f"{uuid4().hex}{extension}"
                upload_directory.mkdir(parents=True, exist_ok=True)
                (upload_directory / filename).write_bytes(photo_data)
                photo_reference = f"uploads/{filename}"
                stored_photo_refs.append(photo_reference)

            prepared_at = _parse_local_datetime(item["prepared_at"]) if item["prepared_at"] else None
            approximate_expiry = (
                _parse_local_datetime(item["item_deadline"])
                if item["item_deadline"] else None
            )
            quantity = Decimal(item["quantity"])
            serving_size = Decimal(item["serving_size"]) if item["serving_size"] else None
            cursor.execute(
                """INSERT INTO donation_items
                   (donation_id, category_id, dietary_option_id, unit_id,
                    food_name, description, photo_reference, total_quantity,
                    available_quantity, serving_size, serving_unit, prepared_at,
                    approximate_expiry_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    donation_id,
                    category_ids[CATEGORY_CODES[item["category"]]],
                    dietary_ids[DIETARY_CODES[item["dietary_type"]]],
                    unit_ids[UNIT_CODES[item["unit"]]],
                    item["food_name"], item["description"], photo_reference,
                    quantity, quantity, serving_size, item["serving_unit"] or None,
                    prepared_at, approximate_expiry,
                ),
            )

        if contact["save_to_profile"] == "1":
            cursor.execute(
                "UPDATE users SET phone = %s WHERE user_id = %s",
                (contact["phone"], user_id),
            )
            cursor.execute(
                """INSERT INTO donor_profiles (user_id, default_city)
                   VALUES (%s, %s)
                   ON DUPLICATE KEY UPDATE default_city = VALUES(default_city)""",
                (user_id, pickup["city"]),
            )

        nearby_acceptor_ids = _nearby_user_ids(cursor, "acceptor", latitude, longitude)
        _insert_notifications(
            cursor,
            nearby_acceptor_ids,
            "nearby_donation",
            "A new food donation is available near your saved location.",
            donation_id=donation_id,
        )
        connection.commit()
    except Exception:
        if connection is not None and connection.is_connected():
            connection.rollback()
        for photo_reference in stored_photo_refs:
            photo_path = Path(app.static_folder) / photo_reference
            if photo_path.is_file():
                photo_path.unlink()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    _send_browser_push(
        nearby_acceptor_ids,
        "New nearby food donation",
        "A donor added food near your saved location. Open Plentiq to view it.",
        url_for("acceptor_food"),
    )
    return int(donation_id)


@app.route("/donor/donations/new", methods=["GET", "POST"])
@donor_required
def donor_create_donation():
    location_defaults = {
        "city": "", "area": "", "landmark": "", "address": "",
        "latitude": None, "longitude": None,
    }
    notice = None
    try:
        location_defaults.update(_load_donor_profile(session["user_id"]))
    except MySQLError:
        notice = "Saved profile location could not be loaded. You can still choose a location below."
    payload = {
        "items": [_empty_food_item()],
        "pickup": {
            "city": location_defaults["city"], "area": location_defaults["area"],
            "landmark": location_defaults["landmark"], "pickup_address": location_defaults["address"],
            "latitude": str(location_defaults["latitude"]) if location_defaults["latitude"] is not None else "",
            "longitude": str(location_defaults["longitude"]) if location_defaults["longitude"] is not None else "",
            "pickup_start": "", "pickup_end": "",
        },
        "contact": {
            "contact_name": "", "phone": "", "email": "",
            "use_profile": "", "save_to_profile": "",
        },
        "confirm_accuracy": False,
    }
    errors = []
    saved_donation_id = None
    current_step = "food"
    profile_contact = {"name": "", "email": "", "phone": ""}
    try:
        profile_contact = _load_profile_contact(session["user_id"])
    except MySQLError:
        notice = "Profile contact details could not be loaded. You can still enter them for this donation."

    if request.method == "POST":
        payload = _donation_payload_from_request()
        errors = _validate_donation_submission(payload)
        if not errors:
            photos, photo_errors = _read_food_photos(len(payload["items"]))
            errors.extend(photo_errors)
            if not errors:
                try:
                    donation_id = _save_donation(payload, photos, session["user_id"])
                    saved_donation_id = donation_id
                    notice = f"Donation #{donation_id} was saved to MySQL."
                    current_step = "review"
                except MySQLError as exc:
                    message = (
                        "Run database_migrations/004_profile_and_donation_fields.sql in Workbench, then submit again. No donation was saved."
                        if getattr(exc, "errno", None) == 1054
                        else "Run database_migrations/008_optional_produce_prepared_at.sql with a MySQL administrator before saving fruit or vegetable items without a prepared time. No donation was saved."
                        if getattr(exc, "errno", None) == 1048 and "prepared_at" in str(exc)
                        else "MySQL could not save this donation. No donation was saved; check the database and try again."
                    )
                    errors.append({"field": "form", "message": message, "step": "review"})
                    current_step = "review"
                except OSError:
                    errors.append({
                        "field": "form",
                        "message": "A food photo could not be stored. No donation was saved; check the app's static/uploads folder and try again.",
                        "step": "review",
                    })
                    current_step = "review"
                except RuntimeError:
                    errors.append({
                        "field": "form",
                        "message": "Required database settings or lookup values are unavailable. No donation was saved; check the MySQL setup and try again.",
                        "step": "review",
                    })
                    current_step = "review"
                except (ValueError, InvalidOperation):
                    errors.append({
                        "field": "form",
                        "message": "The donation could not be saved. Check the category, unit, and date details, then try again.",
                        "step": "review",
                    })
                    current_step = "review"
        else:
            current_step = errors[0]["step"]

        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            response = {
                "valid": not errors,
                "errors": errors,
                "first_step": current_step,
                "notice": notice,
                "redirect": url_for("donor_donations", saved="1") if saved_donation_id else None,
            }
            return jsonify(response), (422 if errors else 200)

        if saved_donation_id:
            return redirect(url_for("donor_donations", saved="1"))

    field_errors = {error["field"]: error["message"] for error in errors}
    return render_template(
        "donor/donation_form.html",
        active_page="create",
        errors=errors,
        field_errors=field_errors,
        notice=notice,
        profile_contact=profile_contact,
        items=payload["items"] or [_empty_food_item()],
        pickup=payload["pickup"],
        contact=payload["contact"],
        confirm_accuracy=payload["confirm_accuracy"],
        current_step=current_step,
        categories=DONATION_CATEGORIES,
        units=DONATION_UNITS,
        dietary_types=DIETARY_TYPES,
        maps_configured=False,
    ), (422 if errors else 200)


@app.get("/donor/donations/<donation_id>")
@donor_required
def donor_donation_detail(donation_id):
    if _sample_mode():
        records = _demo_donations()
        donation = next((item for item in records if item["id"] == donation_id), None)
    else:
        try:
            donation = _load_donor_donation_detail(session["user_id"], int(donation_id))
        except (TypeError, ValueError):
            donation = None
    if donation is None:
        abort(404)
    return render_template(
        "donor/donation_detail.html",
        active_page="donations",
        donation=donation,
        demo_mode=_sample_mode(),
    )


@app.get("/donor/requests")
@donor_required
def donor_requests():
    demo_mode = _sample_mode()
    query = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "pending").strip()
    if status_filter not in {"pending", "approved", "rejected", "cancelled", "expired", "all"}:
        status_filter = "pending"
    if demo_mode:
        records = _demo_requests()
        if status_filter not in {"all", "pending"}:
            records = []
        records = _filter_records(records, query, ("acceptor_name", "food_name", "status"))
        return render_template(
            "donor/records.html", active_page="requests", page_title="Requests",
            page_intro="Sample request cards for visual review.", records=records,
            record_type="requests", status_filter=status_filter, query=query,
        )
    records = _load_donor_requests(session["user_id"], status_filter)
    records = _filter_records(records, query, ("acceptor_name", "food_name", "status", "category"))
    report_reasons = _load_report_reasons("donor")
    return render_template("donor/requests.html", active_page="requests", records=records,
                           status_filter=status_filter, query=query, report_reasons=report_reasons,
                           pickup_pin_ready=_pickup_pin_schema_ready(),
                           collection_confirmation_ready=_collection_confirmation_schema_ready())


@app.get("/donor/collections")
@donor_required
def donor_collections():
    status_filter = request.args.get("status", "all").strip()
    if status_filter not in {"all", "scheduled", "window_ended", "collected", "missed", "cancelled"}:
        status_filter = "all"
    records = _load_donor_collections(session["user_id"])
    query = request.args.get("q", "").strip()
    if status_filter == "window_ended":
        records = [item for item in records if item["is_overdue"]]
    elif status_filter == "scheduled":
        records = [item for item in records if item["status_key"] == "scheduled" and not item["is_overdue"]]
    elif status_filter != "all":
        records = [item for item in records if item["status_key"] == status_filter]
    records = _filter_records(records, query, ("food_name", "acceptor_name", "city", "area", "status"))
    minimum_pickup_at = datetime.now().replace(second=0, microsecond=0)
    if minimum_pickup_at < datetime.now():
        minimum_pickup_at += timedelta(minutes=1)
    for item in records:
        item["minimum_schedule_at"] = max(
            value for value in (item["pickup_starts_at"], minimum_pickup_at) if value is not None
        )
    return render_template(
        "donor/collections.html", active_page="collections", records=records,
        status_filter=status_filter, query=query, report_reasons=_load_report_reasons("donor"),
        minimum_pickup_at=minimum_pickup_at,
        pickup_pin_ready=_pickup_pin_schema_ready(),
        collection_confirmation_ready=_collection_confirmation_schema_ready(),
    )


@app.post("/donor/collections/<int:collection_id>/schedule")
@donor_required
def donor_schedule_collection(collection_id):
    raw_pickup = request.form.get("scheduled_pickup_at", "").strip()
    try:
        pickup_at = datetime.fromisoformat(raw_pickup)
        if pickup_at.tzinfo is not None:
            raise ValueError
    except ValueError:
        flash("Choose a valid pickup date and time.", "error")
        return redirect(url_for("donor_collections"))

    connection = None
    cursor = None
    acceptor_id = None
    schedule_saved = False
    donor_confirmation_column = (
        "c.donor_confirmed_at" if _collection_confirmation_schema_ready() else "NULL"
    )
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor()
        cursor.execute(
            f"""SELECT c.status_code, r.acceptor_user_id, d.donor_user_id,
                      d.pickup_starts_at, d.pickup_ends_at, i.approximate_expiry_at,
                      d.donation_id, {donor_confirmation_column}
               FROM collections c
               JOIN donation_requests r ON r.request_id = c.request_id
               JOIN donation_items i ON i.item_id = r.item_id
               JOIN donations d ON d.donation_id = i.donation_id
               WHERE c.collection_id = %s FOR UPDATE""",
            (collection_id,),
        )
        row = cursor.fetchone()
        if row is None or row[2] != session["user_id"] or row[0] != "scheduled":
            connection.rollback()
            flash("This collection is no longer available to schedule.", "error")
            return redirect(url_for("donor_collections"))
        if row[7] is not None:
            connection.rollback()
            flash("The PIN was already verified. The pickup time can no longer be changed while the acceptor confirms receipt.", "error")
            return redirect(url_for("donor_collections"))
        start_at, end_at = row[3], min(
            [value for value in (row[4], row[5]) if value is not None],
            default=row[4],
        )
        now = datetime.now()
        minimum_pickup_at = now.replace(second=0, microsecond=0)
        if minimum_pickup_at < now:
            minimum_pickup_at += timedelta(minutes=1)
        if end_at is not None and end_at <= now:
            connection.rollback()
            flash("The pickup window has ended. You can no longer set or change an agreed pickup time.", "error")
            return redirect(url_for("donor_collections", status="window_ended"))
        if start_at is None or end_at is None or pickup_at < start_at or pickup_at > end_at or pickup_at < minimum_pickup_at:
            connection.rollback()
            flash("Choose a future pickup time within the listing’s pickup window.", "error")
            return redirect(url_for("donor_collections"))
        acceptor_id = row[1]
        cursor.execute(
            "UPDATE collections SET scheduled_pickup_at = %s WHERE collection_id = %s AND status_code = 'scheduled'",
            (pickup_at, collection_id),
        )
        schedule_message = f"Pickup for your approved food request is scheduled for {pickup_at:%d %b %Y, %I:%M %p}."
        _insert_notifications(
            cursor, [acceptor_id, row[2]], "collection_scheduled", schedule_message,
            donation_id=row[6], request_id=_collection_request_id(cursor, collection_id),
        )
        connection.commit()
        schedule_saved = True
        flash("Pickup time saved and shared with the acceptor.", "success")
    except MySQLError:
        if connection is not None and connection.is_connected():
            connection.rollback()
        flash("Pickup time could not be saved. No collection details were changed.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    if schedule_saved and acceptor_id:
        _send_browser_push([acceptor_id], "Pickup time scheduled", "Your donor has set an agreed pickup time.", url_for("acceptor_collections"))
    return redirect(url_for("donor_collections"))


def _collection_request_id(cursor, collection_id):
    cursor.execute("SELECT request_id FROM collections WHERE collection_id = %s", (collection_id,))
    row = cursor.fetchone()
    return row[0] if row else None


@app.post("/donor/collections/<int:collection_id>/<action>")
@donor_required
def donor_update_collection(collection_id, action):
    if action not in {"collected", "missed", "cancelled"}:
        abort(404)
    pin_schema_ready = _pickup_pin_schema_ready()
    confirmation_schema_ready = _collection_confirmation_schema_ready()
    if action == "collected" and not pin_schema_ready:
        flash("Pickup PIN verification is not enabled yet. Apply database_migrations/009_pickup_verification.sql in MySQL Workbench, then restart the app.", "error")
        return redirect(url_for("donor_collections"))
    if action == "collected" and not confirmation_schema_ready:
        flash("Two-party collection confirmation is not enabled yet. Apply database_migrations/010_two_party_collection_confirmation.sql in MySQL Workbench, then restart the app.", "error")
        return redirect(url_for("donor_collections"))
    pin_columns = (
        "c.pickup_pin_nonce, c.pickup_pin_attempts, c.pickup_pin_attempted_at"
        if pin_schema_ready else "NULL, 0, NULL"
    )
    confirmation_columns = (
        "c.donor_confirmed_at, c.acceptor_confirmed_at"
        if confirmation_schema_ready else "NULL, NULL"
    )
    connection = None
    cursor = None
    acceptor_id = None
    update_saved = False
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor()
        cursor.execute(
            f"""SELECT c.status_code, c.scheduled_pickup_at, r.acceptor_user_id,
                      r.quantity_requested, i.item_id, d.donation_id, d.donor_user_id,
                      d.pickup_ends_at, i.approximate_expiry_at, d.pickup_starts_at,
                      {pin_columns}, {confirmation_columns}
               FROM collections c
               JOIN donation_requests r ON r.request_id = c.request_id
               JOIN donation_items i ON i.item_id = r.item_id
               JOIN donations d ON d.donation_id = i.donation_id
               WHERE c.collection_id = %s FOR UPDATE""",
            (collection_id,),
        )
        row = cursor.fetchone()
        if row is None or row[6] != session["user_id"] or row[0] != "scheduled":
            connection.rollback()
            flash("Only a collection still awaiting pickup can be changed.", "error")
            return redirect(url_for("donor_collections"))
        now = datetime.now()
        pickup_start = row[9]
        safe_deadline = min(
            [value for value in (row[7], row[8]) if value is not None],
            default=None,
        )
        collection_start = pickup_start or row[1]
        if action == "collected" and (collection_start is None or collection_start > now):
            connection.rollback()
            flash("Collection can be confirmed once the pickup window has started.", "error")
            return redirect(url_for("donor_collections"))
        if action == "collected":
            if row[13] is not None:
                connection.rollback()
                flash("You already verified the PIN. The acceptor must confirm receipt to finish this collection.", "info")
                return redirect(url_for("donor_collections"))
            if not row[10]:
                connection.rollback()
                flash("Pickup verification is not initialized for this collection. Apply database_migrations/009_pickup_verification.sql, then try again.", "error")
                return redirect(url_for("donor_collections"))
            attempts = int(row[11] or 0)
            last_attempt = row[12]
            if attempts >= 5 and last_attempt and now - last_attempt < timedelta(minutes=15):
                connection.rollback()
                remaining = max(1, int((last_attempt + timedelta(minutes=15) - now).total_seconds() // 60) + 1)
                flash(f"Too many incorrect PIN attempts. Try again in about {remaining} minute(s).", "error")
                return redirect(url_for("donor_collections"))
            if attempts >= 5:
                attempts = 0
            expected_pin = _pickup_pin_for(collection_id, row[10])
            supplied_pin = request.form.get("pickup_pin", "").strip()
            if expected_pin is None:
                connection.rollback()
                flash("Pickup verification is unavailable because the Flask secret key is not configured.", "error")
                return redirect(url_for("donor_collections"))
            if not re.fullmatch(r"\d{4}", supplied_pin) or not hmac.compare_digest(supplied_pin, expected_pin):
                attempts += 1
                cursor.execute(
                    "UPDATE collections SET pickup_pin_attempts = %s, pickup_pin_attempted_at = NOW() WHERE collection_id = %s AND status_code = 'scheduled'",
                    (attempts, collection_id),
                )
                connection.commit()
                if attempts >= 5:
                    flash("Incorrect PIN. Verification is paused for 15 minutes after five failed attempts.", "error")
                else:
                    flash(f"Incorrect pickup PIN. {5 - attempts} attempt(s) remain before a 15-minute pause.", "error")
                return redirect(url_for("donor_collections"))
        if action == "missed":
            agreed_time_passed = row[1] is not None and row[1] <= now
            pickup_window_ended = safe_deadline is None or safe_deadline <= now
            if not (agreed_time_passed or pickup_window_ended):
                connection.rollback()
                flash("Mark a pickup missed after its agreed time or the full pickup window has passed.", "error")
                return redirect(url_for("donor_collections"))
        if action in {"missed", "cancelled"} and row[13] is not None:
            connection.rollback()
            flash("The PIN has been verified. Wait for the acceptor to confirm receipt; this pickup can no longer be cancelled or marked missed.", "error")
            return redirect(url_for("donor_collections"))
        acceptor_id = row[2]
        request_id = _collection_request_id(cursor, collection_id)
        if action == "collected":
            cursor.execute(
                "UPDATE collections SET donor_confirmed_at = NOW(), pickup_pin_attempts = 0, pickup_pin_attempted_at = NULL WHERE collection_id = %s AND status_code = 'scheduled' AND donor_confirmed_at IS NULL",
                (collection_id,),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                flash("This collection was already confirmed or changed. Refresh the page and check its status.", "error")
                return redirect(url_for("donor_collections"))
            message = "The donor verified your pickup PIN. Confirm receipt in My Collections to complete the handover."
            event_code = "collection_donor_confirmed"
        else:
            cursor.execute(
                "UPDATE donation_items SET available_quantity = available_quantity + %s WHERE item_id = %s",
                (row[3], row[4]),
            )
            cursor.execute(
                "UPDATE donation_requests SET status_code = 'cancelled' WHERE request_id = %s AND status_code = 'approved'",
                (request_id,),
            )
            now = datetime.now()
            if row[7] and row[7] > now and (row[8] is None or row[8] > now):
                cursor.execute(
                    "UPDATE donations SET status_code = 'active' WHERE donation_id = %s AND status_code = 'fully_allocated'",
                    (row[5],),
                )
            cursor.execute(
                "UPDATE collections SET status_code = %s WHERE collection_id = %s AND status_code = 'scheduled'",
                (action, collection_id),
            )
            message = "The donor marked this pickup as missed." if action == "missed" else "The donor cancelled this pickup. The food quantity was returned to the listing when it was still within its pickup deadline."
            event_code = "collection_missed" if action == "missed" else "collection_cancelled"
        if action == "collected":
            _insert_notifications(cursor, [acceptor_id], event_code, message, row[5], request_id)
            _insert_notifications(cursor, [row[6]], event_code, "You verified the pickup PIN. The collection will be complete when the acceptor confirms receipt.", row[5], request_id)
        else:
            _insert_notifications(cursor, [acceptor_id, row[6]], event_code, message, row[5], request_id)
        connection.commit()
        update_saved = True
        flash({"collected": "PIN verified. Waiting for the acceptor to confirm receipt.", "missed": "Pickup marked as missed; available quantity was returned.", "cancelled": "Pickup cancelled; available quantity was returned when the listing was still valid."}[action], "success")
    except MySQLError:
        if connection is not None and connection.is_connected():
            connection.rollback()
        flash("Collection status could not be saved. No status or quantity was changed.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    if update_saved and acceptor_id:
        _send_browser_push([acceptor_id], "Collection updated", message, url_for("acceptor_collections"))
    return redirect(url_for("donor_collections"))


@app.get("/donor/collections/<int:collection_id>/receipt")
@donor_required
def donor_collection_receipt(collection_id):
    if _sample_mode():
        abort(404)
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """SELECT c.collection_id, r.request_id, d.donation_id, i.food_name,
                      fc.category_name, do.dietary_name, r.quantity_requested,
                      qu.unit_name, c.collected_at,
                      COALESCE(NULLIF(ap.organisation_name, ''), acceptor.full_name),
                      donor.full_name, d.city, d.area
               FROM collections c
               JOIN donation_requests r ON r.request_id = c.request_id
               JOIN donation_items i ON i.item_id = r.item_id
               JOIN donations d ON d.donation_id = i.donation_id
               JOIN food_categories fc ON fc.category_id = i.category_id
               JOIN dietary_options do ON do.dietary_option_id = i.dietary_option_id
               JOIN quantity_units qu ON qu.unit_id = i.unit_id
               JOIN users acceptor ON acceptor.user_id = r.acceptor_user_id
               LEFT JOIN acceptor_profiles ap ON ap.user_id = acceptor.user_id
               JOIN users donor ON donor.user_id = d.donor_user_id
               WHERE c.collection_id = %s AND c.status_code = 'collected'
                 AND d.donor_user_id = %s""",
            (collection_id, session["user_id"]),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()
    if row is None:
        abort(404)
    receipt = {
        "collection_id": row[0], "request_id": row[1], "donation_id": row[2],
        "food_name": row[3], "category": row[4], "diet": row[5],
        "quantity": row[6], "unit": row[7], "collected_at": row[8],
        "recipient_name": row[9], "donor_name": row[10],
        "city": row[11], "area": row[12],
        "receipt_number": f"PL-{row[2]}-{row[0]}",
    }
    return render_template("donor/receipt.html", receipt=receipt)


@app.route("/donor/profile", methods=["GET", "POST"])
@donor_required
def donor_profile():
    user_id = session["user_id"]
    notice = "Profile saved." if request.args.get("saved") == "1" else None
    values = {
        "donor_type": "Individual", "email": "", "phone": "", "city": "",
        "area": "", "landmark": "", "address": "", "latitude": None,
        "longitude": None,
    }
    try:
        values.update(_load_donor_profile(user_id))
    except MySQLError as exc:
        if getattr(exc, "errno", None) == 1054:
            notice = "Run database_migrations/004_profile_and_donation_fields.sql in Workbench, then reload this page."
        else:
            notice = "Profile details could not be loaded from MySQL. Please try again."

    if request.method == "POST":
        submitted = {
            "donor_type": request.form.get("donor_type", "").strip(),
            "phone": request.form.get("phone", "").strip(),
            "city": request.form.get("city", "").strip(),
            "area": request.form.get("area", "").strip(),
            "landmark": request.form.get("landmark", "").strip(),
            "address": request.form.get("address", "").strip(),
            "latitude": request.form.get("latitude", "").strip(),
            "longitude": request.form.get("longitude", "").strip(),
            "email": values.get("email", ""),
        }
        values.update(submitted)
        latitude = longitude = None
        if submitted["donor_type"] not in DONOR_TYPE_OPTIONS:
            notice = "Choose a donor type. Nothing was saved."
        elif not _valid_phone(submitted["phone"]):
            notice = "Enter a valid phone number using 7 to 15 digits. Nothing was saved."
        elif not submitted["city"] or len(submitted["city"]) > 100:
            notice = "Enter a city of 100 characters or fewer. Nothing was saved."
        elif not submitted["area"] or len(submitted["area"]) > 120:
            notice = "Enter an area of 120 characters or fewer. Nothing was saved."
        elif len(submitted["landmark"]) > 160 or len(submitted["address"]) > 500:
            notice = "Keep the landmark under 160 characters and address under 500 characters. Nothing was saved."
        elif bool(submitted["latitude"]) != bool(submitted["longitude"]):
            notice = "Choose Use my current location or enter both coordinates. Nothing was saved."
        elif not submitted["latitude"]:
            notice = "Select your current location or enter coordinates to enable nearby matching. Nothing was saved."
        else:
            if submitted["latitude"] and submitted["longitude"]:
                try:
                    latitude = Decimal(submitted["latitude"])
                    longitude = Decimal(submitted["longitude"])
                    if not latitude.is_finite() or not longitude.is_finite() or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                        raise InvalidOperation
                except (InvalidOperation, TypeError, ValueError):
                    latitude = longitude = None
                    notice = "Enter valid latitude and longitude coordinates. Nothing was saved."
            connection = None
            cursor = None
            if notice is None:
                try:
                    connection = get_db_connection()
                    connection.start_transaction()
                    cursor = connection.cursor()
                    cursor.execute("UPDATE users SET phone = %s WHERE user_id = %s", (submitted["phone"], user_id))
                    cursor.execute(
                        """INSERT INTO donor_profiles (user_id, donor_type, default_city)
                           VALUES (%s, %s, %s)
                           ON DUPLICATE KEY UPDATE donor_type = VALUES(donor_type),
                                                   default_city = VALUES(default_city)""",
                        (user_id, submitted["donor_type"], submitted["city"]),
                    )
                    cursor.execute(
                        "SELECT location_id FROM user_locations WHERE user_id = %s AND is_default = TRUE ORDER BY location_id DESC LIMIT 1 FOR UPDATE",
                        (user_id,),
                    )
                    location_row = cursor.fetchone()
                    cursor.execute("UPDATE user_locations SET is_default = FALSE WHERE user_id = %s AND is_default = TRUE", (user_id,))
                    location_values = (
                        submitted["city"], submitted["area"], submitted["landmark"] or None,
                        submitted["address"], latitude, longitude,
                    )
                    if location_row:
                        cursor.execute(
                            """UPDATE user_locations SET label = 'Default', city = %s, area = %s,
                                      landmark = %s, address = %s, latitude = %s, longitude = %s,
                                      is_default = TRUE WHERE location_id = %s""",
                            (*location_values, location_row[0]),
                        )
                    else:
                        cursor.execute(
                            """INSERT INTO user_locations
                               (user_id, label, city, area, landmark, address, latitude, longitude, is_default)
                               VALUES (%s, 'Default', %s, %s, %s, %s, %s, %s, TRUE)""",
                            (user_id, *location_values),
                        )
                    connection.commit()
                    return redirect(url_for("donor_profile", saved="1"))
                except MySQLError as exc:
                    if connection is not None and connection.is_connected():
                        connection.rollback()
                    notice = (
                        "Run database_migrations/004_profile_and_donation_fields.sql in Workbench, then try again. Nothing was saved."
                        if getattr(exc, "errno", None) == 1054
                        else "Profile and location could not be saved. Nothing was changed; please try again."
                    )
                finally:
                    if cursor is not None:
                        cursor.close()
                    if connection is not None and connection.is_connected():
                        connection.close()

    return render_template("donor/profile.html", active_page="profile", notice=notice, values=values)


@app.get("/donor/notifications")
@donor_required
def donor_notifications():
    connection = None
    cursor = None
    rows = []
    notification_error = False
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """SELECT notification_id, event_code, message, created_at, read_at
               FROM notifications WHERE recipient_user_id = %s
               ORDER BY created_at DESC LIMIT 100""",
            (session["user_id"],),
        )
        rows = cursor.fetchall()
    except MySQLError:
        notification_error = True
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    nearby_needs = []
    location_error = False
    try:
        _location, nearby_needs = _load_nearby_needs_for_donor(session["user_id"])
    except MySQLError:
        location_error = True
    return render_template(
        "donor/notifications.html", active_page="notifications", notifications=rows,
        nearby_needs=nearby_needs, notification_error=notification_error,
        location_error=location_error,
    )


@app.post("/donor/notifications/<int:notification_id>/read")
@donor_required
def donor_notification_read(notification_id):
    try:
        changed = _mark_user_notifications_read(session["user_id"], notification_id)
    except MySQLError:
        if _wants_ajax_json():
            return jsonify(ok=False, message="Notification status could not be updated."), 503
        flash("Notification status could not be updated.", "error")
        return redirect(url_for("donor_notifications"))
    message = "Notification marked as read." if changed else "This notification was already read or is unavailable."
    if _wants_ajax_json():
        return jsonify(ok=True, changed=bool(changed), message=message)
    flash(message, "success" if changed else "info")
    return redirect(url_for("donor_notifications"))


@app.post("/donor/notifications/read-all")
@donor_required
def donor_notifications_read_all():
    try:
        count = _mark_user_notifications_read(session["user_id"])
    except MySQLError:
        if _wants_ajax_json():
            return jsonify(ok=False, message="Notifications could not be marked as read."), 503
        flash("Notifications could not be marked as read.", "error")
        return redirect(url_for("donor_notifications"))
    message = f"Marked {count} notification{'s' if count != 1 else ''} as read."
    if _wants_ajax_json():
        return jsonify(ok=True, changed_count=count, message=message)
    flash(message, "success")
    return redirect(url_for("donor_notifications"))


@app.get("/donor/nearby-acceptors")
@donor_required
def donor_nearby_acceptors():
    location = None
    needs = []
    if not _sample_mode():
        try:
            location, needs = _load_nearby_needs_for_donor(session["user_id"])
        except MySQLError:
            flash("Nearby acceptor needs are unavailable. Apply the acceptor needs migration, then reload.", "error")
    return render_template("donor/nearby.html", active_page="nearby", location=location, needs=needs)


@app.get("/donor/logout")
def donor_logout():
    return logout()


@app.get("/acceptor/dashboard")
@acceptor_required
def acceptor_dashboard():
    user_id = session["user_id"]
    profile = _load_acceptor_profile(user_id)
    foods = []
    needs = []
    requests = []
    nearby_error = False
    needs_error = False
    try:
        foods = _load_nearby_donations(
            profile["latitude"], profile["longitude"], acceptor_id=user_id
        )
    except MySQLError:
        nearby_error = True
    try:
        needs = _load_acceptor_needs(user_id, "open")
    except MySQLError:
        needs_error = True
    try:
        requests = _load_acceptor_requests(user_id)
    except MySQLError:
        pass
    return render_template(
        "acceptor/dashboard.html", active_page="dashboard", profile=profile,
        foods=foods[:3], needs=needs[:3],
        stats={
            "nearby_food": len(foods),
            "open_needs": len(needs),
            "pending_requests": sum(row["status"] == "Pending" for row in requests),
            "approved_requests": sum(row["status"] == "Approved" for row in requests),
        },
        nearby_error=nearby_error, needs_error=needs_error,
    )


@app.get("/acceptor/food")
@acceptor_required
def acceptor_food():
    profile = _load_acceptor_profile(session["user_id"])
    query = request.args.get("q", "").strip()[:100]
    category_filter = request.args.get("category", "all").strip()
    dietary_filter = request.args.get("dietary", "all").strip()
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT category_code, category_name FROM food_categories WHERE is_active = TRUE ORDER BY category_name")
        categories = cursor.fetchall()
        cursor.execute("SELECT dietary_code, dietary_name FROM dietary_options ORDER BY dietary_option_id")
        dietary_options = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()
    valid_categories = {row[0] for row in categories}
    valid_dietary = {row[0] for row in dietary_options}
    if category_filter not in valid_categories | {"all"}:
        category_filter = "all"
    if dietary_filter not in valid_dietary | {"all"}:
        dietary_filter = "all"
    foods = []
    if profile["latitude"] is not None and profile["longitude"] is not None:
        foods = _load_nearby_donations(
            profile["latitude"], profile["longitude"], query,
            category_filter, dietary_filter, session["user_id"],
        )
    return render_template(
        "acceptor/food.html", active_page="food", profile=profile, foods=foods,
        query=query, category_filter=category_filter, dietary_filter=dietary_filter,
        categories=categories, dietary_options=dietary_options,
    )


@app.get("/acceptor/needs")
@acceptor_required
def acceptor_needs():
    status_filter = request.args.get("status", "open").strip()
    if status_filter not in {"open", "closed", "fulfilled", "cancelled", "all"}:
        status_filter = "open"
    try:
        needs = _load_acceptor_needs(session["user_id"], status_filter)
        needs_error = False
    except MySQLError:
        needs = []
        needs_error = True
    return render_template(
        "acceptor/needs.html", active_page="needs", needs=needs,
        status_filter=status_filter, needs_error=needs_error,
    )


@app.route("/acceptor/needs/new", methods=["GET", "POST"])
@acceptor_required
def acceptor_need_new():
    profile = _load_acceptor_profile(session["user_id"])
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT category_code, category_name FROM food_categories WHERE is_active = TRUE ORDER BY category_name")
        categories = cursor.fetchall()
        cursor.execute("SELECT dietary_code, dietary_name FROM dietary_options ORDER BY dietary_option_id")
        dietary_options = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()
    form = {
        "dietary_code": "not_specified", "people_count": "", "needed_for": "",
        "meal_period": "", "notes": "", "categories": [],
    }
    errors = []
    if request.method == "POST":
        form = {
            "dietary_code": request.form.get("dietary_code", "").strip(),
            "people_count": request.form.get("people_count", "").strip(),
            "needed_for": request.form.get("needed_for", "").strip(),
            "meal_period": request.form.get("meal_period", "").strip(),
            "notes": request.form.get("notes", "").strip(),
            "categories": request.form.getlist("categories"),
        }
        form["categories"] = list(dict.fromkeys(form["categories"]))
        allowed_categories = {row[0] for row in categories}
        allowed_dietary = {row[0] for row in dietary_options}
        if not profile["city"] or not profile["area"]:
            errors.append("Save a city and area in your profile before posting a food need.")
        if profile["latitude"] is None or profile["longitude"] is None:
            errors.append("Save your current location in your profile before posting a need visible within 25 km.")
        if not form["categories"] or any(code not in allowed_categories for code in form["categories"]):
            errors.append("Choose at least one food category.")
        if form["dietary_code"] not in allowed_dietary:
            errors.append("Choose a valid dietary preference.")
        try:
            people_count = int(form["people_count"])
            if not 1 <= people_count <= 100000:
                raise ValueError
        except (TypeError, ValueError):
            people_count = 0
            errors.append("Enter a number of people between 1 and 100,000.")
        try:
            needed_for = datetime.strptime(form["needed_for"], "%Y-%m-%d").date()
            if needed_for < date.today():
                raise ValueError
        except ValueError:
            needed_for = None
            errors.append("Choose today or a future date for this need.")
        if form["meal_period"] not in {"", "breakfast", "lunch", "dinner"}:
            errors.append("Choose breakfast, lunch, dinner, or any meal.")
        if len(form["notes"]) > 500:
            errors.append("Notes must be 500 characters or fewer.")
        if not errors:
            connection = None
            cursor = None
            try:
                connection = get_db_connection()
                connection.start_transaction()
                cursor = connection.cursor()
                cursor.execute("SELECT dietary_option_id FROM dietary_options WHERE dietary_code = %s", (form["dietary_code"],))
                dietary_row = cursor.fetchone()
                cursor.execute("SELECT category_code, category_id FROM food_categories WHERE is_active = TRUE")
                category_ids = dict(cursor.fetchall())
                cursor.execute(
                    """INSERT INTO acceptor_needs
                       (acceptor_user_id, dietary_option_id, city, area, address,
                        latitude, longitude, people_count, needed_for, meal_period,
                        notes, status_code)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')""",
                    (
                        session["user_id"], dietary_row[0], profile["city"], profile["area"],
                        profile["address"], profile["latitude"], profile["longitude"],
                        people_count, needed_for, form["meal_period"] or None,
                        form["notes"] or None,
                    ),
                )
                need_id = cursor.lastrowid
                cursor.executemany(
                    "INSERT INTO acceptor_need_categories (need_id, category_id) VALUES (%s, %s)",
                    [(need_id, category_ids[code]) for code in form["categories"]],
                )
                nearby_donor_ids = _nearby_user_ids(
                    cursor, "donor", profile["latitude"], profile["longitude"]
                )
                _insert_notifications(
                    cursor,
                    nearby_donor_ids,
                    "nearby_acceptor_need",
                    "A nearby acceptor shared a food need. Open Plentiq to see the requested categories.",
                )
                connection.commit()
                _send_browser_push(
                    nearby_donor_ids,
                    "A nearby community needs food",
                    "An acceptor shared a food need near your saved location. Open Plentiq to view it.",
                    url_for("donor_nearby_acceptors"),
                )
                flash("Your food need is now visible to nearby donors.", "success")
                return redirect(url_for("acceptor_needs"))
            except MySQLError as exc:
                if connection is not None and connection.is_connected():
                    connection.rollback()
                if getattr(exc, "errno", None) == 1054:
                    errors.append("Apply database_migrations/005_acceptor_needs_schedule.sql in Workbench, then try again.")
                else:
                    errors.append("The need could not be saved to MySQL. No need was created; try again.")
            finally:
                if cursor is not None:
                    cursor.close()
                if connection is not None and connection.is_connected():
                    connection.close()
    return render_template(
        "acceptor/need_form.html", active_page="needs", profile=profile,
        categories=categories, dietary_options=dietary_options, form=form, errors=errors,
        today=date.today().isoformat(),
    ), (422 if errors else 200)


@app.post("/acceptor/needs/<int:need_id>/close")
@acceptor_required
def acceptor_need_close(need_id):
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE acceptor_needs SET status_code = 'closed' WHERE need_id = %s AND acceptor_user_id = %s AND status_code = 'open'",
            (need_id, session["user_id"]),
        )
        connection.commit()
        flash("Food need closed." if cursor.rowcount else "That open need was not found.", "success" if cursor.rowcount else "error")
    except MySQLError:
        connection.rollback()
        flash("Could not close this need. Please try again.", "error")
    finally:
        cursor.close()
        connection.close()
    return redirect(url_for("acceptor_needs"))


@app.post("/acceptor/donations/<int:item_id>/request")
@acceptor_required
def acceptor_request_food(item_id):
    try:
        quantity = Decimal(request.form.get("quantity", ""))
        if not quantity.is_finite() or quantity <= 0:
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        flash("Enter a request quantity greater than zero.", "error")
        return redirect(url_for("acceptor_food"))

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor()
        cursor.execute(
            """SELECT latitude, longitude FROM user_locations
               WHERE user_id = %s AND is_default = TRUE
               ORDER BY location_id DESC LIMIT 1 FOR UPDATE""",
            (session["user_id"],),
        )
        acceptor_location = cursor.fetchone()
        if not acceptor_location or acceptor_location[0] is None or acceptor_location[1] is None:
            connection.rollback()
            flash("Save your current location in Profile before requesting nearby food.", "error")
            return redirect(url_for("acceptor_profile"))

        cursor.execute(
            """SELECT d.donation_id, d.donor_user_id, d.latitude, d.longitude,
                      d.pickup_ends_at, d.status_code, i.available_quantity,
                      i.approximate_expiry_at, i.food_name
               FROM donation_items AS i JOIN donations AS d ON d.donation_id = i.donation_id
               WHERE i.item_id = %s FOR UPDATE""",
            (item_id,),
        )
        item = cursor.fetchone()
        if item is None:
            connection.rollback()
            flash("This food listing is no longer available.", "error")
            return redirect(url_for("acceptor_food"))
        donation_id, donor_id, donor_lat, donor_lon, pickup_end, donation_status, available, expiry, food_name = item
        if donation_status not in {"active", "fully_allocated"} or pickup_end <= datetime.now() or (expiry and expiry <= datetime.now()):
            connection.rollback()
            flash("This food listing has expired or is no longer active.", "error")
            return redirect(url_for("acceptor_food"))
        if donor_lat is None or donor_lon is None or _distance_km(
            acceptor_location[0], acceptor_location[1], donor_lat, donor_lon
        ) > 25:
            connection.rollback()
            flash("This food listing is outside your saved 25 km location range.", "error")
            return redirect(url_for("acceptor_food"))
        cursor.execute(
            """SELECT COUNT(*) FROM donation_requests
               WHERE item_id = %s AND acceptor_user_id = %s AND status_code IN ('pending', 'approved')""",
            (item_id, session["user_id"]),
        )
        if cursor.fetchone()[0]:
            connection.rollback()
            flash("You already have an open request for this food listing.", "error")
            return redirect(url_for("acceptor_food"))
        cursor.execute(
            "SELECT COALESCE(SUM(quantity_requested), 0) FROM donation_requests WHERE item_id = %s AND status_code = 'pending'",
            (item_id,),
        )
        reserved = Decimal(cursor.fetchone()[0])
        requestable = Decimal(available) - reserved
        if quantity > requestable:
            connection.rollback()
            flash(f"Only {requestable:g} is currently available to request.", "error")
            return redirect(url_for("acceptor_food"))

        cursor.execute(
            "INSERT INTO donation_requests (item_id, acceptor_user_id, quantity_requested, status_code) VALUES (%s, %s, %s, 'pending')",
            (item_id, session["user_id"], quantity),
        )
        request_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO notifications (recipient_user_id, event_code, message, donation_id, request_id) VALUES (%s, 'donation_request', %s, %s, %s)",
            (donor_id, f"A nearby acceptor requested {quantity:g} of {food_name}.", donation_id, request_id),
        )
        connection.commit()
        _send_browser_push(
            [donor_id],
            "New donation request",
            f"An acceptor requested {quantity:g} of {food_name}. Open Plentiq to review it.",
            url_for("donor_requests"),
        )
        flash("Request sent to the donor. You can track it under My Requests.", "success")
    except MySQLError:
        if connection is not None and connection.is_connected():
            connection.rollback()
        flash("The request could not be saved. Check the database and try again.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    return redirect(url_for("acceptor_requests"))


@app.get("/acceptor/requests")
@acceptor_required
def acceptor_requests():
    requests_saved = _load_acceptor_requests(session["user_id"])
    return render_template(
        "acceptor/requests.html", active_page="requests", requests=requests_saved,
        collections_only=False, report_reasons=_load_report_reasons("acceptor"),
        pickup_pin_ready=_pickup_pin_schema_ready(),
        collection_confirmation_ready=_collection_confirmation_schema_ready(),
    )


@app.get("/acceptor/collections")
@acceptor_required
def acceptor_collections():
    requests_saved = _load_acceptor_requests(session["user_id"])
    collections = [row for row in requests_saved if row["collection_status"] in {"scheduled", "collected", "missed", "cancelled"}]
    return render_template(
        "acceptor/requests.html", active_page="collections", requests=collections,
        collections_only=True, report_reasons=_load_report_reasons("acceptor"),
        pickup_pin_ready=_pickup_pin_schema_ready(),
        collection_confirmation_ready=_collection_confirmation_schema_ready(),
    )


@app.post("/acceptor/collections/<int:collection_id>/confirm")
@acceptor_required
def acceptor_confirm_collection(collection_id):
    if not _collection_confirmation_schema_ready():
        flash("Two-party collection confirmation is not enabled yet. Apply database_migrations/010_two_party_collection_confirmation.sql in MySQL Workbench, then restart the app.", "error")
        return redirect(url_for("acceptor_collections"))

    connection = None
    cursor = None
    donor_id = None
    request_id = None
    donation_id = None
    confirmation_saved = False
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor()
        cursor.execute(
            """SELECT c.status_code, c.donor_confirmed_at, c.acceptor_confirmed_at,
                      r.acceptor_user_id, d.donor_user_id, r.request_id, d.donation_id,
                      COALESCE(d.pickup_starts_at, c.scheduled_pickup_at)
               FROM collections c
               JOIN donation_requests r ON r.request_id = c.request_id
               JOIN donation_items i ON i.item_id = r.item_id
               JOIN donations d ON d.donation_id = i.donation_id
               WHERE c.collection_id = %s FOR UPDATE""",
            (collection_id,),
        )
        row = cursor.fetchone()
        if row is None or row[3] != session["user_id"] or row[0] != "scheduled":
            connection.rollback()
            flash("Only your collection that is awaiting confirmation can be confirmed.", "error")
            return redirect(url_for("acceptor_collections"))
        if row[1] is None:
            connection.rollback()
            flash("The donor must verify your pickup PIN before you can confirm receipt.", "error")
            return redirect(url_for("acceptor_collections"))
        if row[2] is not None:
            connection.rollback()
            flash("You have already confirmed this collection.", "info")
            return redirect(url_for("acceptor_collections"))
        if row[7] is not None and row[7] > datetime.now():
            connection.rollback()
            flash("You can confirm receipt after the pickup window starts.", "error")
            return redirect(url_for("acceptor_collections"))

        donor_id, request_id, donation_id = row[4], row[5], row[6]
        cursor.execute(
            """UPDATE collections
               SET acceptor_confirmed_at = NOW(), status_code = 'collected', collected_at = NOW()
               WHERE collection_id = %s AND status_code = 'scheduled'
                 AND donor_confirmed_at IS NOT NULL AND acceptor_confirmed_at IS NULL""",
            (collection_id,),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            flash("This collection changed before confirmation. Refresh and check its status.", "error")
            return redirect(url_for("acceptor_collections"))
        message = "Both the donor and acceptor confirmed this food collection."
        _insert_notifications(
            cursor, [donor_id, session["user_id"]], "collection_collected", message,
            donation_id=donation_id, request_id=request_id,
        )
        connection.commit()
        confirmation_saved = True
        flash("Collection confirmed by both parties.", "success")
    except MySQLError:
        if connection is not None and connection.is_connected():
            connection.rollback()
        flash("Collection confirmation could not be saved. The status was not changed.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    if confirmation_saved and donor_id:
        _send_browser_push(
            [donor_id], "Collection confirmed",
            "The acceptor confirmed receipt. The collection is now complete.",
            url_for("donor_collections"),
        )
    return redirect(url_for("acceptor_collections"))


@app.post("/acceptor/requests/<int:request_id>/cancel")
@acceptor_required
def acceptor_request_cancel(request_id):
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE donation_requests SET status_code = 'cancelled' WHERE request_id = %s AND acceptor_user_id = %s AND status_code = 'pending'",
            (request_id, session["user_id"]),
        )
        connection.commit()
        flash("Request cancelled." if cursor.rowcount else "Only a pending request can be cancelled.", "success" if cursor.rowcount else "error")
    except MySQLError:
        connection.rollback()
        flash("Could not cancel the request. Please try again.", "error")
    finally:
        cursor.close()
        connection.close()
    return redirect(url_for("acceptor_requests"))


@app.route("/acceptor/profile", methods=["GET", "POST"])
@acceptor_required
def acceptor_profile():
    values = _load_acceptor_profile(session["user_id"])
    errors = []
    if request.method == "POST":
        submitted = {
            "full_name": request.form.get("full_name", "").strip(),
            "phone": request.form.get("phone", "").strip(),
            "acceptor_type": request.form.get("acceptor_type", "").strip(),
            "organisation_name": request.form.get("organisation_name", "").strip(),
            "city": request.form.get("city", "").strip(),
            "area": request.form.get("area", "").strip(),
            "landmark": request.form.get("landmark", "").strip(),
            "address": request.form.get("address", "").strip(),
            "latitude": request.form.get("latitude", "").strip(),
            "longitude": request.form.get("longitude", "").strip(),
            "email": values["email"],
        }
        values.update(submitted)
        if not submitted["full_name"] or len(submitted["full_name"]) > 120:
            errors.append("Enter a name of 120 characters or fewer.")
        if not _valid_phone(submitted["phone"]):
            errors.append("Enter a valid phone number using 7 to 15 digits.")
        if submitted["acceptor_type"] not in {"individual", "ngo", "community_group", "shelter", "other"}:
            errors.append("Choose a valid acceptor type.")
        if len(submitted["organisation_name"]) > 160:
            errors.append("Organisation name must be 160 characters or fewer.")
        for field, label, maximum in (("city", "City", 100), ("area", "Area", 120), ("landmark", "Landmark", 160), ("address", "Address", 500)):
            if field != "landmark" and not submitted[field]:
                errors.append(f"{label} is required for your location.")
            if len(submitted[field]) > maximum:
                errors.append(f"{label} must be {maximum} characters or fewer.")
        latitude = longitude = None
        if bool(submitted["latitude"]) != bool(submitted["longitude"]):
            errors.append("Enter both location coordinates, or leave both blank.")
        elif not submitted["latitude"]:
            errors.append("Choose Use my current location or enter coordinates to enable nearby matching.")
        elif submitted["latitude"] and submitted["longitude"]:
            try:
                latitude = Decimal(submitted["latitude"])
                longitude = Decimal(submitted["longitude"])
                if not latitude.is_finite() or not longitude.is_finite() or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                    raise InvalidOperation
            except (InvalidOperation, TypeError, ValueError):
                latitude = longitude = None
                errors.append("Enter valid latitude and longitude coordinates.")
        if not errors:
            connection = None
            cursor = None
            try:
                connection = get_db_connection()
                connection.start_transaction()
                cursor = connection.cursor()
                cursor.execute("UPDATE users SET full_name = %s, phone = %s WHERE user_id = %s", (submitted["full_name"], submitted["phone"], session["user_id"]))
                cursor.execute(
                    """INSERT INTO acceptor_profiles (user_id, acceptor_type, organisation_name)
                       VALUES (%s, %s, %s)
                       ON DUPLICATE KEY UPDATE acceptor_type = VALUES(acceptor_type),
                                               organisation_name = VALUES(organisation_name)""",
                    (session["user_id"], submitted["acceptor_type"], submitted["organisation_name"] or None),
                )
                cursor.execute("SELECT location_id FROM user_locations WHERE user_id = %s AND is_default = TRUE ORDER BY location_id DESC LIMIT 1 FOR UPDATE", (session["user_id"],))
                location_row = cursor.fetchone()
                cursor.execute("UPDATE user_locations SET is_default = FALSE WHERE user_id = %s AND is_default = TRUE", (session["user_id"],))
                location_values = (
                    submitted["city"], submitted["area"], submitted["landmark"] or None,
                    submitted["address"], latitude, longitude, session["user_id"],
                )
                if location_row:
                    cursor.execute(
                        """UPDATE user_locations SET label = 'Default', city = %s, area = %s,
                                  landmark = %s, address = %s, latitude = %s, longitude = %s,
                                  is_default = TRUE WHERE location_id = %s""",
                        (*location_values[:-1], location_row[0]),
                    )
                else:
                    cursor.execute(
                        """INSERT INTO user_locations
                           (user_id, label, city, area, landmark, address, latitude, longitude, is_default)
                           VALUES (%s, 'Default', %s, %s, %s, %s, %s, %s, TRUE)""",
                        (session["user_id"], *location_values[:-1]),
                    )
                connection.commit()
                session["full_name"] = submitted["full_name"]
                flash("Profile and default location saved.", "success")
                if latitude is None:
                    flash("Add your current coordinates to enable 25 km matching.", "info")
                return redirect(url_for("acceptor_profile"))
            except MySQLError:
                if connection is not None and connection.is_connected():
                    connection.rollback()
                errors.append("Profile could not be saved to MySQL. Nothing was changed; please try again.")
            finally:
                if cursor is not None:
                    cursor.close()
                if connection is not None and connection.is_connected():
                    connection.close()
    return render_template("acceptor/profile.html", active_page="profile", profile=values, errors=errors)


@app.get("/acceptor/notifications")
@acceptor_required
def acceptor_notifications():
    notifications = []
    nearby_listings = []
    notification_error = False
    location_error = False
    profile = None
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """SELECT notification_id, event_code, message, created_at, read_at
               FROM notifications
               WHERE recipient_user_id = %s AND event_code <> 'nearby_donation'
               ORDER BY created_at DESC LIMIT 100""",
            (session["user_id"],),
        )
        notifications = cursor.fetchall()
    except MySQLError:
        notification_error = True
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    try:
        profile = _load_acceptor_profile(session["user_id"])
        if profile["latitude"] is not None and profile["longitude"] is not None:
            nearby_listings = _load_nearby_donations(
                profile["latitude"], profile["longitude"], acceptor_id=session["user_id"]
            )
    except MySQLError:
        location_error = True
    return render_template(
        "acceptor/notifications.html", active_page="notifications",
        notifications=notifications, nearby_listings=nearby_listings,
        has_saved_location=bool(
            profile and profile["latitude"] is not None and profile["longitude"] is not None
        ),
        notification_error=notification_error, location_error=location_error,
    )


@app.post("/acceptor/notifications/<int:notification_id>/read")
@acceptor_required
def acceptor_notification_read(notification_id):
    try:
        changed = _mark_user_notifications_read(session["user_id"], notification_id)
    except MySQLError:
        if _wants_ajax_json():
            return jsonify(ok=False, message="Notification status could not be updated."), 503
        flash("Notification status could not be updated.", "error")
        return redirect(url_for("acceptor_notifications"))
    message = "Notification marked as read." if changed else "This notification was already read or is unavailable."
    if _wants_ajax_json():
        return jsonify(ok=True, changed=bool(changed), message=message)
    flash(message, "success" if changed else "info")
    return redirect(url_for("acceptor_notifications"))


@app.post("/acceptor/notifications/read-all")
@acceptor_required
def acceptor_notifications_read_all():
    try:
        count = _mark_user_notifications_read(session["user_id"])
    except MySQLError:
        if _wants_ajax_json():
            return jsonify(ok=False, message="Notifications could not be marked as read."), 503
        flash("Notifications could not be marked as read.", "error")
        return redirect(url_for("acceptor_notifications"))
    message = f"Marked {count} notification{'s' if count != 1 else ''} as read."
    if _wants_ajax_json():
        return jsonify(ok=True, changed_count=count, message=message)
    flash(message, "success")
    return redirect(url_for("acceptor_notifications"))


@app.get("/donor/reports")
@donor_required
def donor_reports():
    if _sample_mode():
        return redirect(url_for("donor_dashboard", demo="1"))
    reports = _load_participant_reports(session["user_id"])
    return render_template(
        "shared/my_reports.html", base_template="donor/base.html",
        active_page="reports", page_title="My Reports", reports=reports,
    )


@app.get("/acceptor/reports")
@acceptor_required
def acceptor_reports():
    reports = _load_participant_reports(session["user_id"])
    return render_template(
        "shared/my_reports.html", base_template="acceptor/base.html",
        active_page="reports", page_title="My Reports", reports=reports,
    )


@app.post("/donor/requests/<int:request_id>/report")
@donor_required
def donor_report_request(request_id):
    return _submit_request_report(request_id, "donor")


@app.post("/acceptor/requests/<int:request_id>/report")
@acceptor_required
def acceptor_report_request(request_id):
    return _submit_request_report(request_id, "acceptor")


@app.post("/donor/requests/<int:request_id>/<decision>")
@donor_required
def donor_decide_request(request_id, decision):
    if decision not in {"approve", "reject"}:
        abort(404)
    rejection_reason = request.form.get("reason", "").strip()
    if decision == "approve" and not _pickup_pin_schema_ready():
        flash("Pickup PIN verification is not enabled yet. Apply database_migrations/009_pickup_verification.sql in MySQL Workbench, then restart the app before approving requests.", "error")
        return redirect(url_for("donor_requests"))
    if decision == "approve" and not _collection_confirmation_schema_ready():
        flash("Two-party collection confirmation is not enabled yet. Apply database_migrations/010_two_party_collection_confirmation.sql in MySQL Workbench, then restart the app before approving requests.", "error")
        return redirect(url_for("donor_requests"))
    if decision == "reject" and len(rejection_reason) > 500:
        flash("The rejection reason must be 500 characters or fewer.", "error")
        return redirect(url_for("donor_requests"))

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT item_id, acceptor_user_id, quantity_requested, status_code FROM donation_requests WHERE request_id = %s FOR UPDATE",
            (request_id,),
        )
        row = cursor.fetchone()
        if row is None or row[3] != "pending":
            connection.rollback()
            flash("This request is no longer pending.", "error")
            return redirect(url_for("donor_requests"))
        item_id, acceptor_id, requested_quantity, request_status = row
        cursor.execute(
            "SELECT d.donor_user_id, d.donation_id, d.status_code, d.pickup_ends_at, i.approximate_expiry_at FROM donations AS d JOIN donation_items AS i ON i.donation_id = d.donation_id WHERE i.item_id = %s FOR UPDATE",
            (item_id,),
        )
        donation = cursor.fetchone()
        # A stale fully_allocated status can remain even when the item's
        # available quantity is positive. The quantity row below is the source
        # of truth for whether another request can be approved.
        if donation is None or donation[0] != session["user_id"] or donation[2] not in {"active", "fully_allocated"}:
            connection.rollback()
            flash("You cannot change this request, or the donation is no longer active.", "error")
            return redirect(url_for("donor_requests"))
        effective_deadline = min(
            [value for value in (donation[3], donation[4]) if value is not None],
            default=None,
        )
        if decision == "approve" and (effective_deadline is None or effective_deadline <= datetime.now()):
            connection.rollback()
            flash("This request has expired because its safe pickup window ended. It cannot be approved.", "error")
            return redirect(url_for("donor_requests", status="expired"))
        if decision == "approve":
            cursor.execute("SELECT available_quantity FROM donation_items WHERE item_id = %s FOR UPDATE", (item_id,))
            available = Decimal(cursor.fetchone()[0])
            requested = Decimal(requested_quantity)
            if requested > available:
                connection.rollback()
                flash("There is no longer enough unreserved food to approve this request.", "error")
                return redirect(url_for("donor_requests"))
            remaining = available - requested
            cursor.execute("UPDATE donation_items SET available_quantity = %s WHERE item_id = %s", (remaining, item_id))
            cursor.execute(
                "UPDATE donation_requests SET status_code = 'approved', decided_by_user_id = %s, decided_at = NOW() WHERE request_id = %s AND status_code = 'pending'",
                (session["user_id"], request_id),
            )
            pickup_pin_nonce = _new_pickup_pin_nonce()
            cursor.execute(
                "INSERT INTO collections (request_id, status_code, pickup_pin_nonce) VALUES (%s, 'scheduled', %s)",
                (request_id, pickup_pin_nonce),
            )
            message = "Your food request was approved. Check My Collections for your pickup verification PIN and pickup details."
            flash("Request approved and quantity reserved.", "success")
        else:
            cursor.execute(
                "UPDATE donation_requests SET status_code = 'rejected', decided_by_user_id = %s, decided_at = NOW(), rejection_reason = %s WHERE request_id = %s AND status_code = 'pending'",
                (session["user_id"], rejection_reason or None, request_id),
            )
            message = "The donor could not approve your food request." + (f" Reason: {rejection_reason}" if rejection_reason else "")
            flash("Request rejected.", "success")
        cursor.execute(
            "SELECT COALESCE(SUM(available_quantity), 0) FROM donation_items WHERE donation_id = %s",
            (donation[1],),
        )
        donation_available = Decimal(cursor.fetchone()[0])
        cursor.execute(
            "UPDATE donations SET status_code = %s WHERE donation_id = %s",
            ("fully_allocated" if donation_available <= 0 else "active", donation[1]),
        )
        event_code = "request_approved" if decision == "approve" else "request_rejected"
        cursor.execute(
            "INSERT INTO notifications (recipient_user_id, event_code, message, donation_id, request_id) VALUES (%s, %s, %s, %s, %s)",
            (acceptor_id, event_code, message, donation[1], request_id),
        )
        connection.commit()
        _send_browser_push(
            [acceptor_id],
            "Food request updated",
            message,
            url_for("acceptor_requests"),
        )
    except MySQLError:
        if connection is not None and connection.is_connected():
            connection.rollback()
        flash("The request could not be updated. No quantity was changed; try again.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    return redirect(url_for("donor_requests"))


if __name__ == "__main__":
    app.run(debug=False, port=5001)
