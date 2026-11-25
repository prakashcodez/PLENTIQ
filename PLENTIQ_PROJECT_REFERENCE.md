# PLENTIQ — Project Plan and Continuity Notes

> **Purpose:** This is the working reference for rebuilding Plentiq. Use it to keep decisions consistent across future conversations and coding sessions. Explain things in simple language and focus on the current step. Do not repeat this entire document unless asked. Do not invent missing teacher feedback or claim code was tested when it was not.

## 1. What Plentiq is

Plentiq is a college project for sharing surplus food. Donors (restaurants, caterers, events, etc.) list food that is still suitable to collect. Acceptors request a quantity, donors decide, collection is tracked, and distribution is recorded. An admin looks after accounts and platform activity.

**Main workflow:**

1. Donor posts a food listing.
2. Acceptor finds suitable available food.
3. Acceptor requests a quantity.
4. Donor approves or rejects the request.
5. On approval, available quantity is updated safely.
6. A pickup is scheduled and marked collected, missed, or cancelled.
7. After collection, the acceptor records the amount distributed and optionally the number of recipients.

Keep the project focused on food. A food category (what the food is), donation source (where it came from), and quantity unit (portions, packets, kg, boxes, etc.) are different things.

## 2. Current working decisions

- **Project name:** Plentiq.
- **New project:** build in the project root outside `V1`.
- **`V1`:** old build; read-only reference. Do not edit, delete, rename, or overwrite it.
- **Stack:** Python 3.12, Flask, MySQL 8, `mysql-connector-python`, HTML, CSS, JavaScript where needed, and Jinja templates.
- **Database for the current root build:** `plentiq_dev`. This is the database used by the current root SQL files and `.env`; older references below to `plentiq_v2_db` are historical planning notes. Never point the root app at V1's database.
- **User roles:** one Admin, two Donor accounts, and two Acceptor accounts for the demonstration (five accounts total).
- **Entry flow:** the existing Plentiq landing page comes first; its login action opens the email/password login page. Use the old landing/login as reference unless a new design is explicitly requested.
- **Account switching:** a convenient “Switch Demo Account” control can be used for the local college demonstration only. It must be disabled outside the explicit local-demo setting. Normal permissions must still be checked by Flask.
- **Language in the new UI:** use “Acceptor” instead of “NGO” where appropriate, unless the project brief or faculty explicitly requires “NGO”.
- **MySQL is the first source of truth** for accounts and project records. Firebase is optional future work, not part of the first build. Do not introduce both systems before the Flask + MySQL version works.
- **No ORM:** use parameterized MySQL queries, matching the DBMS project requirements.
- **Work one small step at a time:** inspect changes, test, then continue. Save working milestones in Git.

## 3. Folder safety

Expected direction (the exact current tree must be checked in VS Code before changes):

```text
PLENTIQ/
├── V1/                     # old project; reference only
├── app.py                  # new Flask app, when created
├── config.py               # new configuration, when needed
├── templates/              # new HTML/Jinja pages
├── static/                 # new CSS, JavaScript, images
├── database/               # reviewed SQL files
├── tests/                  # tests
├── docs/                   # project notes and progress
├── requirements.txt
├── .env.example
└── .gitignore
```

Before writing new code, confirm the files really are inside `V1` and that the project root is the active VS Code folder. Do not blindly move files again. Do not copy V1's `.env`, `.venv`, secrets, database dumps, or old demo credentials into the new build. Back up V1. Keep secrets out of Git.

## 4. The five demonstration accounts

Create exactly five starting demo accounts in the new database:

- 1 Admin
- 2 Donors
- 2 Acceptors

The account rows belong in MySQL, not in HTML or hardcoded login checks in `app.py`.

**Password rules:**

- Never store plain-text passwords in the database.
- Hash passwords using a suitable Python password-hashing function such as Werkzeug's `generate_password_hash`; check them with `check_password_hash`.
- Never put passwords in HTML, JavaScript, screenshots, Git commits, or general documentation.
- Use temporary demo-only passwords supplied separately. A local seed script may create the five demo accounts and store only password hashes.
- A user must not be able to select the Admin role through public registration.
- If account registration is added, public users may request a Donor or Acceptor account; the Admin role is created separately. Decide whether new accounts require Admin approval before they can post/request food.

## 5. Build order — do the work in this order

### Stage 0 — protect the old project

- [ ] Check that `V1` contains the previous project.
- [ ] Make a backup.
- [ ] Open the parent `PLENTIQ` folder as the new active workspace.
- [ ] Do not modify V1 or use its database.

### Stage 1 — build the UI designs

Build the public landing page first, then the email/password login page. Use V1 for inspiration and suitable logo/images. Then build the main donor, acceptor, and admin screens and the forms needed for the main workflow.

Use temporary sample data at this stage. It is fine to test page layout, links, forms, filters, password visibility, and navigation without MySQL.

**“Ready to connect” means:** fields have clear names, forms are designed for Flask, navigation/actions have a clear destination, client-side interactions work, and no control pretends to save real data. Do not leave decorative buttons that do nothing; if backend work is not connected yet, make the missing connection explicit rather than faking success.

Do not spend days perfecting every tiny detail before integration. Make the core screens consistent and usable, then connect features one at a time.

### Stage 2 — decide the data fields and design the database

After the key screens and actions are clear, list the data each action must save. Then have Codex propose the SQL schema and review it before running it. The database supports the whole app; it is **not one database per screen**.

Likely core tables:

- `users` — the five accounts, role, display name, email, password hash, account status and timestamps.
- `food_categories` — categories such as meals, bakery, packaged food, etc.
- `food_listings` — donor, category, food details, total/available quantity, unit, source, expiry, pickup details and status.
- `donation_requests` — acceptor, listing, requested quantity, status, decision/review notes and timestamps.
- `collection_schedules` — approved request, pickup time and collection status.
- `distribution_records` — collected schedule, quantity distributed, optional recipient count and time.
- `notifications` — in-app notifications and whether each was read.
- `user_flags` or an equivalent review/audit table — repeated suspicious activity, reason, time and Admin review result.
- `audit_logs` — important Admin decisions and sensitive workflow actions, where practical.

These are proposed tables, not a final schema. Review columns and relationships against the actual screen and workflow requirements before running SQL. Add tables only when there is a clear need.

The original plan proposed a new database named `plentiq_v2_db`; the root application now uses `plentiq_dev`. Keep schema changes separate from demo-data/lookup seeding. Never run destructive SQL automatically. Back up and explain the impact before any future migration.

### Stage 3 — connect Flask and MySQL

- Connect the root Flask app to `plentiq_dev`.
- Use a configuration file/environment variables for secrets; do not hardcode DB passwords or the Flask secret key.
- Create the five demo accounts with password hashes.
- Implement real login, logout, sessions and role-based redirects.
- Enforce permissions in Flask, not just by hiding buttons in HTML.
- Test login and account roles before moving to the food workflow.

### Stage 4 — make one complete feature work at a time

1. **Login:** stored account is checked; wrong password fails; correct account reaches the correct dashboard.
2. **Food listing:** donor creates a listing; MySQL saves it; acceptor can see it after reload.
3. **Request food:** acceptor submits a positive quantity; request is saved and appears on both relevant views.
4. **Approve/reject:** donor can decide only on requests for their listings (Admin only if specifically authorised). Approval reduces available quantity exactly once. Rejection does not reduce quantity.
5. **Collection:** pickup can only be scheduled for an approved request; the pickup time must be valid; status can be updated.
6. **Distribution:** record distribution only after collection, and never allow more than the approved/collected quantity.
7. **Admin:** review accounts, flagged activity, listings, requests and system summaries.
8. **Notifications:** create in-app notifications for relevant events, then add nearby-food alerts.

For each feature: implement → run it → check the database/result → fix it → save a Git checkpoint. Do not claim a test passed unless it was actually run.

## 6. Full feature reference

### Public landing and login

- Landing page appears first and explains Plentiq.
- Login page uses email and password.
- Password show/hide, clear errors, logout and role-based dashboard redirect.
- A sign-in attempt must not be accepted just because the UI looks correct; Flask checks the password hash in MySQL.
- Browse pages may show safe public listing details, but only an authorised Acceptor can submit a request.

### Donor

- Dashboard counters and activity.
- Create, view, edit and cancel own food listings when allowed.
- Capture food name, category, donation source, quantity, unit, expiry/safe-consumption deadline, city/location and pickup details.
- See requests for their own listings.
- Approve/reject a request with an optional reason.
- Set/view collection details and status.

### Acceptor

- Dashboard with available food, own requests, collection and distribution history.
- Browse available, non-expired listings.
- Filter by category and area/city; location-distance filter can be added later.
- Request a quantity no greater than the available quantity.
- View request status and review reason; cancel only a pending request if allowed.
- View pickup arrangements and mark/confirm collection only where authorised.
- Record the distributed quantity and optional number of recipients after collection.

### Admin

- Dashboard with overall counts and activity summaries from MySQL.
- Review user accounts; activate, suspend or reinstate with a reason.
- Review suspicious/repeated submissions and decide whether they are legitimate or spam.
- View/manage listings and requests; manage food categories if included.
- See collection/distribution records and basic reports.
- Admin-only actions must be checked by the backend and recorded in an audit log where practical.

## 7. Spam, fake submissions and safe quantity handling

Do not treat every repeat click as fraud. Detect and flag patterns for review.

- Block duplicate active requests from the same acceptor for the same listing, or show a clear message if a request already exists.
- Validate all fields on the server, even if the browser also validates them.
- Reject blank, nonnumeric, zero or negative quantities.
- Reject requests for expired, unavailable or cancelled food.
- Do not allow more quantity to be requested than is available.
- Add a simple configurable rate limit for repeated submissions if practical.
- Record a flag with the account, event, reason and time; show it in the Admin review area. Avoid automatic permanent bans based only on a guess.
- Check account status on each protected request; suspended users must lose access.
- Use parameterized SQL queries, not SQL strings assembled from user input.

**Critical approval rule:** quantity reduction and request approval must happen together in one database transaction with locking or another safe concurrency method. Prevent duplicate approval and prevent two acceptors from allocating more food than exists. If a step fails, roll back related changes.

## 8. Expiry and nearby notifications

### Expiry

- Each listing records its expiry or safe-collection deadline.
- Expired listings should no longer appear as requestable.
- Flask/MySQL must reject requests for expired listings even if someone bypasses the browser UI.
- Show the remaining time or deadline clearly when useful. Do not claim the app independently certifies food safety; it stores donor-provided information and applies the entered deadline.

### Location and nearby alerts

This is an enhancement; build it after the core workflow works.

- Start with city/area (and optionally postcode) so the first version does not depend on a paid API.
- If coordinates or a geocoding/location API are later added, ask for permission where needed and keep only what the feature needs. Do not expose a person's precise location publicly.
- Use a configurable distance limit. The distance is a product setting to agree on, not a value to hardcode forever.
- Notify an interested/opted-in acceptor only when a listing is available, unexpired and within the chosen distance.
- An acceptor in Hyderabad should not receive a “nearby” alert for a Chennai listing just because the listing is new.
- Start with **in-app notifications stored in MySQL**. Browser push, email, maps, and external APIs can be considered later.
- If an external API needs a key, keep it in environment configuration, not in frontend code or Git.

## 9. Testing requirements

At minimum, verify these cases against a dedicated new/test database, not the old project database:

- Correct login, wrong password, logout and each role's access.
- Donor can create a listing; another user sees the saved listing.
- Missing fields and invalid quantity are rejected.
- Expired/unavailable listing cannot be requested.
- Duplicate request is prevented or flagged correctly.
- Request above available quantity is rejected.
- Approve reduces quantity exactly once; reject leaves quantity unchanged.
- A user cannot approve another donor's request unless explicitly authorised.
- Competing requests cannot allocate more food than exists.
- Collection cannot be scheduled before approval.
- Distribution cannot be recorded before collection or above the allowed quantity.
- Suspended users cannot continue using old sessions.
- Nearby alerts respect distance, listing expiry and user notification preference.
- Admin can review flags and record decisions.

Test with disposable data. Do not run destructive test scripts against a live/demo database. Keep screenshots and SQL query results for the college demonstration, but hide passwords and secrets.

## 10. DBMS/college demonstration goals

Keep the implementation simple enough to explain. The project should demonstrate:

- Primary keys and foreign keys.
- `NOT NULL`, `UNIQUE`, `CHECK` and sensible defaults.
- `INSERT`, `SELECT`, `UPDATE`, and appropriate `DELETE`/cancel handling.
- Joins, grouping/aggregate reports, and useful subqueries.
- Transactions, rollback and safe quantity allocation.
- Stored procedures only if they are genuinely implemented, installed, and tested. Do not claim they exist just because a `.sql` file exists.
- A clear end-to-end demo: donor creates food → acceptor requests → donor approves → quantity changes → collection → distribution → Admin summary.

## 11. Coding-agent / Codex rules

- One task per prompt; keep prompts small to reduce credit use.
- Inspect the relevant files before edits.
- Say exactly which page/feature may be changed.
- Do not regenerate the whole app or all CSS to fix one issue.
- Do not touch V1, the old database, old credentials, or unrelated files.
- Check the browser and actual database when appropriate; separate “code inspected” from “test passed”.
- Ask for a concise summary: files changed, tests actually run, remaining problem, next step.
- Commit after each working milestone; never commit `.env` or passwords.

## 12. What to do today

1. Check that V1 is intact and back it up.
2. Open the parent project folder as the new workspace.
3. Build the **landing page first**, then the **email/password login page**, using V1 as visual reference.
4. Check the pages in a browser. Navigation and visible controls should work where possible, but do not fake database saves or a successful login.
5. Build the key donor, acceptor and admin screen layouts with realistic sample data and clearly named forms.
6. The database schema and demo accounts now exist in `plentiq_dev`; review current SQL and migrations before making any schema changes.
7. Connect Flask and MySQL, then implement login and one full food-listing/request workflow.
8. Add remaining collection, distribution, Admin moderation and nearby notifications in stages.

**Do not create a new database for every page. Do not wait until every visual detail is perfect before backend integration. Use one shared database and connect one complete feature at a time.**

## 13. Open decisions / do not invent answers

- The exact wording of the lecturer/ma'am's first-submission feedback is not verified in this reference. Add the exact feedback when the original notes are available; do not invent it.
- Confirm whether public users can browse all listing details or must sign in first. Recommended default: public can see safe, limited listing information; only signed-in, approved Acceptors can request food.
- Confirm if donor/acceptor self-registration is needed in the first version. The five demo accounts must exist regardless.
- Confirm the distance radius for nearby alerts. Keep it configurable.
- Decide later whether an external location API is worth the complexity. Start without one if city/area filtering is enough.
- Keep Firebase as optional future work unless the project supervisor requires it. The first working build uses Flask + MySQL.

---

### Assistant continuity note

Use this file as the project reference whenever relevant without announcing that you are doing so. Give simple, calm, step-by-step guidance. Do not overload the user. Answer the exact question first, say what the next action is, and do not repeat questions already answered here. Be honest about unknowns, especially exact faculty feedback and untested functionality.
