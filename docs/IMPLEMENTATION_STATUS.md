# Plentiq implementation status

Updated: 2026-10-26

## Current build

- The active project is the root Flask application; `V1/` remains a read-only reference.
- The configured MySQL database for this build is `plentiq_dev`. Older notes that call it `plentiq_v2_db` are stale.
- Demo accounts authenticate through Flask and MySQL. Passwords are stored as hashes.
- Donors can create listings, review acceptor requests, and manage approved pickups. Acceptors can post food needs, request nearby donations, and track collection status.
- Nearby matching uses saved coordinates and a 25 km radius. Exact acceptor coordinates are not shown to donors.
- In-app notifications are stored in MySQL. The notification page supports unread/read state and the top bell shows the unread count.
- Participant reports use the existing `reports` and `report_reasons` tables and are reviewed in the admin workspace.
- The admin workspace includes account, listing, request, collection, and report summaries. Collected quantities are reported by unit; they do not claim a number of people fed.

## Database changes in the current milestone

- No table or column changes are required for notifications, pickup scheduling/status, participant reports, or the corrected pending-request count.
- `database_migrations/007_report_reasons.sql` seeds three lookup values: missed collection, unsafe or misrepresented food, and other conduct. It is idempotent and does not alter existing reports.
- Pickup times are stored in the existing `collections.scheduled_pickup_at` field. Collected, missed, and cancelled states use the existing status field.

## Browser push

In-app notifications are the supported notification path. Browser push is optional and is not configured for this local environment: VAPID keys are absent and the current MinGW-based Python environment could not install the declared `pywebpush` package because its cryptography dependency requires a Rust build. Do not claim browser push is enabled until both the dependency and VAPID keys are configured.

## Local review

Run `./.venv/bin/python.exe app.py` from the project root and open `http://127.0.0.1:5001`. Restart an older process so it loads the current routes and templates.

Review one complete workflow: donor listing → acceptor request → donor approval → agreed pickup time → collection marked collected → participant report → admin review. Confirm notification read state and database counts along the way.

## Deferred ideas

See [DEFERRED_FEATURES.md](DEFERRED_FEATURES.md). The pickup PIN, urgent-expiry prioritization, and printable donation summary are future work and are not implemented in this milestone.
