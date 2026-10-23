import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import unittest
from datetime import datetime, timedelta
from decimal import Decimal
import mysql.connector

# Ensure app can be imported
sys.path.insert(0, r"c:\CODING FILES\PLENTIQ")
from app import app, get_db_connection
from db_config import DB_CONFIG

class Stage3CollectionDistributionTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()

    def _create_test_listing_and_approved_request(self, quantity=50, requested=20):
        """Helper to create a fresh listing and approved request for testing."""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            future_expiry = datetime.now() + timedelta(days=5)
            cursor.execute("""
                INSERT INTO food_listings (
                    donor_id, category_id, food_name, description,
                    quantity_total, quantity_available, unit,
                    expiry_at, pickup_address, city, status
                ) VALUES (1, 1, 'Stage 3 Test Meal', 'Fresh test food', %s, %s, 'portions', %s, '10 Main Rd', 'Chennai', 'available')
            """, (quantity, quantity - requested, future_expiry))
            listing_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO donation_requests (
                    listing_id, ngo_id, requested_quantity, request_status, requested_at, reviewed_by, reviewed_at
                ) VALUES (%s, 2, %s, 'approved', NOW(), 1, NOW())
            """, (listing_id, requested))
            request_id = cursor.lastrowid
            conn.commit()
            return listing_id, request_id
        finally:
            cursor.close()
            conn.close()

    def test_01_schedule_approved_request(self):
        """1. Scheduling collection for an approved request succeeds and records in MySQL."""
        listing_id, request_id = self._create_test_listing_and_approved_request()

        with self.client as c:
            # Login as Donor (user 1)
            c.get("/demo/switch/donor")
            pickup_time = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
            res = c.post(f"/requests/{request_id}/schedule", data={
                "scheduled_at": pickup_time,
                "notes": "Please pick up at rear loading bay"
            }, follow_redirects=True)
            self.assertEqual(res.status_code, 200)
            self.assertIn("Collection pickup successfully scheduled", res.get_data(as_text=True))

        # Direct MySQL verification
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM collection_schedules WHERE request_id = %s", (request_id,))
        sched = cur.fetchone()
        cur.close()
        conn.close()

        self.assertIsNotNone(sched)
        self.assertEqual(sched["status"], "scheduled")
        self.assertIsNone(sched["collected_at"])
        self.assertEqual(sched["notes"], "Please pick up at rear loading bay")
        print(f"[PASS] test_01_schedule_approved_request (Schedule #{sched['schedule_id']})")

    def test_02_reject_scheduling_unapproved_request(self):
        """2. Reject scheduling collection for pending, rejected, or cancelled requests."""
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        future_expiry = datetime.now() + timedelta(days=5)
        cur.execute("""
            INSERT INTO food_listings (donor_id, category_id, food_name, quantity_total, quantity_available, unit, expiry_at, pickup_address, city, status)
            VALUES (1, 1, 'Pending Test Meal', 40, 40, 'portions', %s, 'Address', 'City', 'available')
        """, (future_expiry,))
        lid = cur.lastrowid

        # Insert a pending request
        cur.execute("INSERT INTO donation_requests (listing_id, ngo_id, requested_quantity, request_status) VALUES (%s, 2, 10, 'pending')", (lid,))
        pending_req_id = cur.lastrowid

        # Insert a rejected request
        cur.execute("INSERT INTO donation_requests (listing_id, ngo_id, requested_quantity, request_status) VALUES (%s, 2, 10, 'rejected')", (lid,))
        rejected_req_id = cur.lastrowid
        conn.commit()
        cur.close()
        conn.close()

        with self.client as c:
            c.get("/demo/switch/donor")
            pickup_time = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")

            # Try to schedule pending request
            res_pending = c.post(f"/requests/{pending_req_id}/schedule", data={"scheduled_at": pickup_time}, follow_redirects=True)
            self.assertIn("Only approved requests can be scheduled", res_pending.get_data(as_text=True))

            # Try to schedule rejected request
            res_rejected = c.post(f"/requests/{rejected_req_id}/schedule", data={"scheduled_at": pickup_time}, follow_redirects=True)
            self.assertIn("Only approved requests can be scheduled", res_rejected.get_data(as_text=True))

        # Verify no schedules were inserted in MySQL
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) AS cnt FROM collection_schedules WHERE request_id IN (%s, %s)", (pending_req_id, rejected_req_id))
        count = cur.fetchone()["cnt"]
        cur.close()
        conn.close()
        self.assertEqual(count, 0)
        print("[PASS] test_02_reject_scheduling_unapproved_request")

    def test_03_prevent_duplicate_schedules(self):
        """3. Prevent scheduling duplicate collections for the same request."""
        listing_id, request_id = self._create_test_listing_and_approved_request()

        with self.client as c:
            c.get("/demo/switch/donor")
            pickup_time = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
            # First schedule -> Success
            res1 = c.post(f"/requests/{request_id}/schedule", data={"scheduled_at": pickup_time}, follow_redirects=True)
            self.assertIn("Collection pickup successfully scheduled", res1.get_data(as_text=True))

            # Second schedule -> Duplicate rejected
            res2 = c.post(f"/requests/{request_id}/schedule", data={"scheduled_at": pickup_time}, follow_redirects=True)
            self.assertIn("already exists", res2.get_data(as_text=True))

        print("[PASS] test_03_prevent_duplicate_schedules")

    def test_04_record_completed_collection(self):
        """4. Authorized participant records completed collection, setting status and collected_at."""
        listing_id, request_id = self._create_test_listing_and_approved_request()

        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO collection_schedules (request_id, scheduled_at, status) VALUES (%s, NOW(), 'scheduled')", (request_id,))
        schedule_id = cur.lastrowid
        conn.commit()
        cur.close()
        conn.close()

        # NGO marks collection as completed
        with self.client as c:
            c.get("/demo/switch/ngo")
            res = c.post(f"/schedules/{schedule_id}/status", data={"status": "collected", "notes": "Picked up on time"}, follow_redirects=True)
            self.assertEqual(res.status_code, 200)
            self.assertIn("marked as collected", res.get_data(as_text=True))

        # Check in MySQL
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM collection_schedules WHERE schedule_id = %s", (schedule_id,))
        sched = cur.fetchone()
        cur.close()
        conn.close()

        self.assertEqual(sched["status"], "collected")
        self.assertIsNotNone(sched["collected_at"])
        self.assertIn("Picked up on time", sched["notes"] or "")
        print(f"[PASS] test_04_record_completed_collection (Collected at {sched['collected_at']})")

    def test_05_record_missed_collection(self):
        """5. Recording a missed collection sets status to 'missed' and collected_at to NULL."""
        listing_id, request_id = self._create_test_listing_and_approved_request()

        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO collection_schedules (request_id, scheduled_at, status) VALUES (%s, NOW(), 'scheduled')", (request_id,))
        schedule_id = cur.lastrowid
        conn.commit()
        cur.close()
        conn.close()

        with self.client as c:
            c.get("/demo/switch/donor")
            res = c.post(f"/schedules/{schedule_id}/status", data={"status": "missed", "notes": "NGO van broke down"}, follow_redirects=True)
            self.assertEqual(res.status_code, 200)
            self.assertIn("marked as missed", res.get_data(as_text=True))

        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM collection_schedules WHERE schedule_id = %s", (schedule_id,))
        sched = cur.fetchone()
        cur.close()
        conn.close()

        self.assertEqual(sched["status"], "missed")
        self.assertIsNone(sched["collected_at"])
        print("[PASS] test_05_record_missed_collection")

    def test_06_prevent_distribution_before_collection(self):
        """6. Prevent recording distribution before collection is completed."""
        listing_id, request_id = self._create_test_listing_and_approved_request()

        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO collection_schedules (request_id, scheduled_at, status) VALUES (%s, NOW(), 'scheduled')", (request_id,))
        schedule_id = cur.lastrowid
        conn.commit()
        cur.close()
        conn.close()

        with self.client as c:
            c.get("/demo/switch/ngo")
            # Try to GET distribution form while still 'scheduled'
            res_get = c.get(f"/schedules/{schedule_id}/distribute", follow_redirects=True)
            self.assertIn("Food must be marked as collected first", res_get.get_data(as_text=True))

            # Try to POST distribution while still 'scheduled'
            res_post = c.post(f"/schedules/{schedule_id}/distribute", data={
                "quantity_distributed": "15",
                "recipient_count": "20"
            }, follow_redirects=True)
            self.assertIn("Food must be marked as collected first", res_post.get_data(as_text=True))

        print("[PASS] test_06_prevent_distribution_before_collection")

    def test_07_record_valid_distribution(self):
        """7. Recording a valid distribution succeeds, stores recipient count, and links to schedule."""
        listing_id, request_id = self._create_test_listing_and_approved_request(quantity=50, requested=30)

        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO collection_schedules (request_id, scheduled_at, status, collected_at) VALUES (%s, NOW(), 'collected', NOW())", (request_id,))
        schedule_id = cur.lastrowid
        conn.commit()
        cur.close()
        conn.close()

        with self.client as c:
            c.get("/demo/switch/ngo")
            dist_time = datetime.now().strftime("%Y-%m-%dT%H:%M")
            res = c.post(f"/schedules/{schedule_id}/distribute", data={
                "quantity_distributed": "30",
                "recipient_count": "60",
                "distributed_at": dist_time,
                "notes": "Served lunch at Hope Shelter"
            }, follow_redirects=True)
            self.assertEqual(res.status_code, 200)
            self.assertIn("Successfully recorded distribution", res.get_data(as_text=True))

        # Check in MySQL
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM distribution_records WHERE schedule_id = %s", (schedule_id,))
        dist = cur.fetchone()
        cur.close()
        conn.close()

        self.assertIsNotNone(dist)
        self.assertEqual(dist["quantity_distributed"], Decimal("30.00"))
        self.assertEqual(dist["recipient_count"], 60)
        self.assertEqual(dist["notes"], "Served lunch at Hope Shelter")
        self.assertEqual(dist["recorded_by"], 2) # NGO user ID
        print(f"[PASS] test_07_record_valid_distribution (Distribution #{dist['distribution_id']})")

    def test_08_reject_distribution_quantity_exceeding_approved(self):
        """8. Reject distribution quantity greater than the approved collected quantity."""
        listing_id, request_id = self._create_test_listing_and_approved_request(quantity=50, requested=25)

        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO collection_schedules (request_id, scheduled_at, status, collected_at) VALUES (%s, NOW(), 'collected', NOW())", (request_id,))
        schedule_id = cur.lastrowid
        conn.commit()
        cur.close()
        conn.close()

        with self.client as c:
            c.get("/demo/switch/ngo")
            # Approved was 25, trying to distribute 30
            res = c.post(f"/schedules/{schedule_id}/distribute", data={
                "quantity_distributed": "30.00",
                "recipient_count": "50"
            }, follow_redirects=True)
            self.assertIn("cannot exceed approved collected quantity", res.get_data(as_text=True))

        # Check no distribution record inserted
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) AS cnt FROM distribution_records WHERE schedule_id = %s", (schedule_id,))
        count = cur.fetchone()["cnt"]
        cur.close()
        conn.close()
        self.assertEqual(count, 0)
        print("[PASS] test_08_reject_distribution_quantity_exceeding_approved")

    def test_09_prevent_duplicate_distribution_records(self):
        """9. Prevent duplicate distribution records for the same collection schedule."""
        listing_id, request_id = self._create_test_listing_and_approved_request(quantity=50, requested=20)

        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO collection_schedules (request_id, scheduled_at, status, collected_at) VALUES (%s, NOW(), 'collected', NOW())", (request_id,))
        schedule_id = cur.lastrowid
        conn.commit()
        cur.close()
        conn.close()

        with self.client as c:
            c.get("/demo/switch/ngo")
            # First distribution -> Success
            res1 = c.post(f"/schedules/{schedule_id}/distribute", data={
                "quantity_distributed": "20.00",
                "recipient_count": "40"
            }, follow_redirects=True)
            self.assertIn("Successfully recorded distribution", res1.get_data(as_text=True))

            # Second distribution on same schedule -> Rejected
            res2 = c.post(f"/schedules/{schedule_id}/distribute", data={
                "quantity_distributed": "20.00",
                "recipient_count": "40"
            }, follow_redirects=True)
            self.assertIn("Distribution has already been recorded", res2.get_data(as_text=True))

        print("[PASS] test_09_prevent_duplicate_distribution_records")

    def test_10_reject_unauthorized_operations(self):
        """10. Reject unauthorized operations across roles (donors distributing, wrong users scheduling)."""
        listing_id, request_id = self._create_test_listing_and_approved_request(quantity=50, requested=15)

        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO collection_schedules (request_id, scheduled_at, status, collected_at) VALUES (%s, NOW(), 'collected', NOW())", (request_id,))
        schedule_id = cur.lastrowid
        conn.commit()
        cur.close()
        conn.close()

        with self.client as c:
            # 1. Donor trying to record distribution -> 403 Forbidden
            c.get("/demo/switch/donor")
            res_donor_dist = c.get(f"/schedules/{schedule_id}/distribute")
            self.assertEqual(res_donor_dist.status_code, 403)

            # 2. NGO trying to schedule collection -> 403 Forbidden
            c.get("/demo/switch/ngo")
            res_ngo_sched = c.post(f"/requests/{request_id}/schedule", data={"scheduled_at": "2026-10-25T10:00"})
            self.assertEqual(res_ngo_sched.status_code, 403)

        print("[PASS] test_10_reject_unauthorized_operations")

    def test_11_dashboard_and_database_verification(self):
        """11. Verify that scheduled collections and distributions appear correctly on donor, NGO and admin dashboards."""
        listing_id, request_id = self._create_test_listing_and_approved_request(quantity=50, requested=25)

        # Schedule and complete collection
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO collection_schedules (request_id, scheduled_at, status, collected_at) VALUES (%s, NOW(), 'collected', NOW())", (request_id,))
        schedule_id = cur.lastrowid
        # Record distribution
        cur.execute("""
            INSERT INTO distribution_records (schedule_id, recorded_by, quantity_distributed, recipient_count, distributed_at, notes)
            VALUES (%s, 2, 25.00, 55, NOW(), 'Verified community distribution')
        """, (schedule_id,))
        dist_id = cur.lastrowid
        conn.commit()
        cur.close()
        conn.close()

        # 1. Verify NGO dashboard shows the distribution history
        with self.client as c:
            c.get("/demo/switch/ngo")
            res_ngo = c.get("/dashboard/ngo")
            html_ngo = res_ngo.get_data(as_text=True)
            self.assertIn("Community Distribution History", html_ngo)
            self.assertIn("Verified community distribution", html_ngo)
            self.assertIn("55", html_ngo)

        # 2. Verify Donor dashboard shows the completed collection and distribution summary
        with self.client as c:
            c.get("/demo/switch/donor")
            res_donor = c.get("/dashboard/donor")
            html_donor = res_donor.get_data(as_text=True)
            self.assertIn("Collected", html_donor)
            self.assertIn("Distributed:", html_donor)

        # 3. Verify Admin dashboard shows platform log
        with self.client as c:
            c.get("/demo/switch/admin")
            res_admin = c.get("/dashboard/admin")
            html_admin = res_admin.get_data(as_text=True)
            self.assertIn("Platform Food Distribution &amp; Community Impact Log", html_admin)
            self.assertIn("Verified community distribution", html_admin)

        print("[PASS] test_11_dashboard_and_database_verification")

if __name__ == "__main__":
    unittest.main()
