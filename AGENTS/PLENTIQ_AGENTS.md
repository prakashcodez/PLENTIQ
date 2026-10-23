# Codex Instructions — Plentiq

## Project purpose
Plentiq is a college DBMS project for managing surplus food donations from food donors to NGOs. Keep the scope food-focused unless the project owner explicitly changes it.

## Current stack
- Python 3.12
- Flask
- MySQL 8 / InnoDB
- mysql-connector-python
- Jinja HTML templates, CSS, and small inline JavaScript
- Existing database tables: `users`, `food_categories`, `food_listings`, `donation_requests`, `collection_schedules`, `distribution_records`

## Non-negotiable safeguards
1. Inspect before editing. Keep changes small and limited to the named task.
2. Never drop, reset, recreate, or bulk-delete the database. Never run setup or cleanup SQL automatically.
3. Never run `scratch/test_stage3.py` against `plentiq_db`; it inserts test rows. Use a dedicated test database and explain the test target before any write test.
4. Do not copy credentials or secrets into source, documentation, prompts, logs, screenshots, or outputs. Do not print secret values. Plan to move secrets to environment configuration.
5. Preserve existing table/column names unless a reviewed migration is explicitly needed. Never silently alter schema or seed data.
6. Do not replace the whole app, regenerate all CSS, or rewrite `app.py` wholesale to solve a local issue.
7. Do not claim a feature or test works unless you actually verified it. Clearly label static inspection, unit tests, integration tests, and manual-browser checks separately.
8. Before schema/data changes, explain the migration, backup needed, rollback plan, and exact affected tables. Wait for approval before destructive or irreversible actions.
9. Never change a database backup or demo data unless explicitly asked. Do not treat the dated `plentiq_db_backup_20261021_123941.sql` as a clean demo seed; it contains synthetic test rows.
10. Keep a diff summary and list commands/tests run at the end of each task.

## Desired product behaviour
- Multiple distinct donor accounts and NGO accounts.
- Public listing shows the donor organisation's public display name, not a private person's name by accident.
- NGOs can browse currently available, non-expired food and request a quantity.
- Donor (or explicitly authorised admin) approves/rejects; approval allocates quantity exactly once under transaction/locking.
- Pickup is scheduled only for approved requests, and a pickup time in the past is rejected server-side.
- Distribution can be recorded only after collection and cannot exceed the approved quantity.
- A user cannot request food from their own donor account or create spam duplicate active requests.
- Admin has a real governance use case: review/verify donor and NGO accounts, suspend/reinstate abusive accounts with reason/audit record, moderate listings, manage categories, and view system-wide statistics.
- Registration must never let users choose the `admin` role. New non-demo accounts must not gain donation/request permissions before the configured review/verification process completes.
- Suspended/inactive users must lose access even if an old session cookie remains valid.
- Demo account switching is for local assessment only and must be disabled by default outside explicit local-demo mode.

## Food/expiry rules
- Keep category, donation source, and quantity unit as distinct concepts.
- Do not sum unlike units (e.g. kilograms plus portions) into a single “portions” metric. Group totals by unit or report listing counts.
- Reject invalid/negative quantities, invalid categories/source types, expired listings, impossible date ranges, and inconsistent listing states on the server—not only with HTML attributes.
- Do not let a user set expiry a century ahead. For packaged food, use the actual manufacturer label date; do not use AI to invent/extend it. For prepared food, use an approved configurable policy and capture preparation/storage details where in scope. A suggestion is not a food-safety guarantee.
- The app records information; it does not certify that food is safe.

## Security expectations
- Random secret key and DB settings from environment.
- A dedicated database user with least privileges, not a privileged root account.
- Passwords hashed with a suitable password-hashing function; never stored or logged in plaintext.
- Rate-limit login/registration; CSRF-protect every state-changing form; use POST for logout, moderation actions, and demo account switching.
- Server-side authentication/authorization for every sensitive action; check the current account status, object ownership, and allowed state transition.
- Keep an audit trail for account verification/suspension and admin moderation actions.

## Working style for every change
1. State the precise bug/feature and the acceptance criteria.
2. Inspect only the necessary routes, templates, CSS, schema and tests first.
3. Make the smallest coherent change; avoid unrelated styling and refactors.
4. Test negative and positive cases against a dedicated test database.
5. Show the changed files, a concise explanation, tests actually run and any unverified risks.
6. Do not mark the task complete if acceptance criteria failed.

## Project documentation
Use `PLENTIQ_Audit_and_Implementation_Plan.md` as the current audit and roadmap. This is a static archive review, not proof of live MySQL/runtime correctness. Reconcile it with the actual working tree before implementing fixes.
