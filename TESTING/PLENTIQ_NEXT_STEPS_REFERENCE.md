# PLENTIQ — Next Steps & Implementation Reference

**Prepared:** 23 October 2026  
**Purpose:** A single working reference for improving the existing Plentiq Flask + MySQL project in small, testable steps while preserving the presentation demo.  
**Source basis:** Static inspection of the uploaded `PLENTIQ.zip`, the audit produced in this conversation, and the user's later confirmation that the local app starts after environment setup. The ZIP predates subsequent local edits, so verify current files in VS Code before changing them.

---

## 1. Current state — what is already set up

- Plentiq is an existing Flask + MySQL application. **Do not rebuild it from scratch.**
- Core workflow in the codebase includes user login, demo-account switching, donor listings, NGO donation requests, request approval/rejection, collection scheduling/status and distribution records. Static inspection is not proof that every workflow is runtime-tested.
- The local Flask app now requires `PLENTIQ_SECRET_KEY` from the process environment. The user saved it in the Windows user environment and has reported that the app starts.
- `PLENTIQ_DEMO_MODE` has been saved as `1` for the presentation, and demo switching is intentionally being kept available for local use. Do not casually change these settings during UI work.
- The shared demo password was removed from the login page by the first small Codex change. Existing seeded demo accounts were not intended to be deleted.
- Root `AGENTS.md` now exists in the local project and points to `AGENTS/PLENTIQ_AGENTS.md`.
- The current user wants to work one page/feature at a time, reduce Codex usage, improve the UI, address the account flow and later make MySQL query materials easy to run and explain.

### Important limitation

The initial audit was static. It did not connect to the user's running MySQL instance or execute the full application workflow. The uploaded ZIP may not include the most recent local changes. Do not assume a current local file is identical to its archived version.

---

## 2. Product decisions to keep consistent

### Keep Plentiq food-focused

This project is a **surplus-food redistribution system**, not a general marketplace for wires, clothes or arbitrary goods. Do not expand the data model beyond food unless the project guide explicitly changes the requirements.

Keep these concepts separate:

- **Food category:** what the food is, e.g. prepared meals, rice dishes, curries, bakery, packaged food, sweets/desserts.
- **Donation source:** where it came from, e.g. restaurant, catering service, wedding/function, college/office event or community event.
- **Quantity unit:** portions, kilograms, packets, boxes, etc. Do not sum unlike units into one total.

### Admin needs a real purpose

An admin is the platform-governance role, not just a decorative dashboard. Useful responsibilities include reviewing donor/NGO accounts, suspending/reinstating accounts, moderating suspicious listings, managing food categories and viewing system-wide operational reports. Admin-only operations must be enforced by the backend, not merely hidden in the UI. Account status changes should have a reason and timestamp; preserve an audit history where feasible.

### Email and password: what is and is not “real”

A registration page can validate an email's **format** and enforce a password policy without sending email verification. These are different capabilities:

1. **Format validation:** catches values such as `abc` or `person@`.
2. **Uniqueness validation:** prevents two accounts using the same email.
3. **Password policy:** e.g. at least 8 characters with a letter, a number and a symbol; validate in both browser and server.
4. **Password storage:** store a strong password hash, never the raw password.
5. **Email ownership verification:** requires a working mailbox verification link/OTP. Without an email service, the app cannot prove a mailbox belongs to the person or that it can receive email.

For the academic demo, a reasonable first version is **email-format validation + unique email + password policy + hashed password + controlled account status**, without claiming that email ownership is verified. The user is not asking to implement email OTP now. Do not invent a pretend verification state.

The registration form must not let a user self-select the administrator role. Prefer donor/NGO registration with a review/pending state if the project is to support onboarding. Check the current schema before deciding whether an additional account status/verification field is needed. Do not casually alter existing demo users or reset the database.

### Food expiry

- Packaged food: collect the actual date printed on the package; do not generate a fictional manufacturer expiry date.
- Prepared/perishable food: collect preparation time and handling/storage information, then enforce a conservative pickup/donation deadline approved by the project guide or relevant food-safety policy.
- Server-side validation is required. HTML date limits alone are not enough.
- Reject absurd/far-future expiry values. Recheck expiry when showing available food, approving a request and scheduling pickup.
- An app-suggested deadline is a prompt, not a guarantee that food is safe.

### Scope control

Do not add payment gateways, AI safety certification, maps, real email OTP, notifications, or unrelated features unless the project requirements demand them and the core flows are working. Food donation does not inherently require payments.

---

## 3. Findings from the ZIP audit — keep as a backlog, not a reason to rewrite

The following were identified by static source inspection and should be rechecked against the latest local code before acting:

### Login/accounts/admin

- No registration route or password-reset workflow was found in the uploaded archive.
- The login flow supported pre-seeded/demo accounts; the shared demo password was displayed in the archived login template and has since been removed in the local edit reported by the user.
- The admin users table showed account status but did not offer complete verification/suspension/reinstatement actions in the archived code.
- The archived role guards relied heavily on session role values and did not consistently re-check current account activity.
- There was no login rate-limiting or CSRF token coverage found during the static review. These remain deferred security backlog items, not part of the first visual redesign task.

### Food/business rules

- Listing creation rejected past expiry, but the archived code did not enforce a suitable maximum expiry horizon; edit validation was incomplete.
- Listing editing exposed a status field, which could permit inconsistent business states if not constrained.
- A donor could potentially edit available quantity in a way that conflicts with already-approved requests.
- Request cancellation had a potential race with concurrent approval because the update was not safely conditional/locked in the reviewed path.
- Pickup scheduling did not robustly reject a past time or a time after expiry in the reviewed helper.
- The public listings query could include non-available statuses if no status filter was selected.
- Public listing display used `users.full_name` as an organisation name; the data model may need a separate organisation/public display name and a privacy decision.
- Dashboard summaries added unlike units and labelled totals as portions, which is misleading.

### SQL, tests and portability

- Six core tables were identified: `users`, `food_categories`, `food_listings`, `donation_requests`, `collection_schedules`, `distribution_records`.
- `plentiq_workflow.sql` contained approval/rejection procedures. Static review found branches/authorization/not-found behavior that need deliberate testing.
- `test.sql` had plain-text headings instead of SQL comments; `report.sql` was empty in the archive.
- `scratch/test_stage3.py` wrote directly through the configured connection and had a hard-coded Windows path. Do not run it against the presentation database.
- The archived project lacked a proper dependency manifest, project README, `.env.example` and `.gitignore`.
- The dated SQL backup included synthetic test records and should not be treated as a clean demo seed.
- No setup/cleanup SQL should be executed against the existing demo database merely to test a feature.

---

## 4. Recommended implementation order

### Phase 0 — Preserve the demo (current operational priority)

- Keep `PLENTIQ_SECRET_KEY` stable; do not rotate it unless it is exposed.
- Keep `PLENTIQ_DEMO_MODE=1` for the local presentation, as the user requested.
- Start the app in the same working way that has already succeeded (`python app.py` or the project's virtual-environment Python).
- Before substantive changes, copy the project folder or make a version-control checkpoint. The earlier audit noted that the archive had no Git metadata, so verify before assuming Git is available.
- Do not reset, drop, clean or recreate `plentiq_db`.

### Phase 1 — Define the visual system, then improve page by page

1. **Current login page audit/redesign:** preserve demo switching, improve hierarchy and form spacing, add clear inline errors, accessible labels, show/hide password if useful, loading/submission state, and a responsive layout. Do not remove working routes or demo behavior.
2. **Registration page and flow:** after login design is stable, implement email/password form validation if approved. Validate in both JavaScript and Flask. Distinguish format validation from email ownership verification. Prevent admin self-registration and decide how new donor/NGO accounts become active.
3. **Public landing and food discovery:** clear search/filter/sort, only available/unexpired/positive-stock donations by default, useful empty states, visible units and expiry/pickup information.
4. **Food detail/request form:** make the amount/unit and expiry/pickup conditions obvious; validate requested quantity against current availability on the server.
5. **Donor dashboard/listing create/edit:** clean overview, unambiguous statuses, readable cards/tables, create/edit validation, and no direct arbitrary status manipulation.
6. **NGO dashboard/request management:** show only relevant requests; separate pending/approved/rejected/collected/distributed states; clear next actions.
7. **Collection and distribution:** readable schedule and status controls, correct order of operations, prevent distribution before collection.
8. **Admin dashboard:** actionable account review/suspension, listing moderation, category management, and metrics with meaningful units.
9. **Shared components:** navigation, buttons, forms, tables, alerts/toasts, confirmation dialogs, loading/error/empty states, and responsive mobile behavior.
10. **Final consistency pass:** keyboard navigation, form labels, sensible contrast, spacing, no horizontal overflow, consistent copy.

Do not redesign all pages in a single Codex task. Establish the direction on one page, verify it, then reuse the patterns.

### Phase 2 — Account lifecycle (after first visual page)

- Add donor/NGO registration if required for the demo.
- Email format validation; normalise and enforce unique email in the backend/database.
- Password policy: minimum 8 characters, at least one letter, one number and one symbol (client-side hints and server-side enforcement). Avoid forcing a complex policy that breaks existing seeded demo accounts unless they are deliberately migrated.
- Store hashes using the password-hashing functions already used by Flask; never store plaintext.
- Do not claim mailbox ownership is verified unless a real verification link/OTP flow is implemented and tested.
- Decide on account activation/review: pending new accounts and admin approval are a reasonable academic option. Check schema before adding fields or changing existing accounts.
- Add admin actions for approve/suspend/reinstate, prevent self-escalation to admin, and ensure server-side role checks match UI visibility.

### Phase 3 — Food and workflow consistency

- Add approved category/source/unit choices and clear labels.
- Enforce safe expiry/pickup input constraints on create/edit/request/approval/scheduling.
- Hide expired, unavailable, cancelled and fully allocated records from active browse results by default.
- Keep quantity and status changes atomic; prevent cancellations/edits from undoing approved allocations.
- Prevent over-requests and duplicate active requests.
- Group totals by unit or show counts rather than adding unlike units.
- Make public organisation display different from a user's personal name if required.
- Treat all safety suggestions as non-certifying assistance.

### Phase 4 — SQL learning/reporting files (later, as requested)

Separate SQL by purpose so each part can be executed and explained independently:

- Setup/schema and constraints.
- Stored procedures/workflow logic.
- A deliberately small demo-seed script.
- Read-only report queries, each with a `--` comment title and one understandable query block.
- Viva queries for `SELECT`, `WHERE`, `JOIN`, aggregates, subqueries, keys/constraints and transactions.

Example reporting topics: table counts; `SHOW CREATE TABLE`; relationships/foreign keys; available listings; requests joined to NGOs and food listings; collection/distribution lifecycle; category counts; totals grouped by unit; procedure existence. Read-only query documents must not insert/update/delete data. Run any state-changing SQL only after reviewing it and confirming the target database.

### Phase 5 — Portable setup, test and submission

- Add `requirements.txt`, `.env.example` placeholders only, `.gitignore`, README, and reproducible first-run instructions.
- Ensure no `.venv`, cache folders, private `.env`, credentials or noisy backup is included in a shareable ZIP.
- Create a clean demo seed distinct from personal recovery backups.
- Run integration tests only against an isolated disposable test database, not the presentation DB.
- Verify three donors, three NGOs and one admin if that is still the agreed demo; use a small coherent set of listings with pending, approved, rejected, collected and distributed examples.
- Prepare screenshots and viva explanations only for features actually verified.

---

## 5. What is intentionally deferred for now

The user explicitly prefers not to spend the next iteration on broader security repairs. Keep these visible in the checklist, but do not start them during visual iteration unless a defect blocks the demo:

- CSRF protection on all state-changing forms and POST-only logout.
- Login/register rate limiting and abuse monitoring.
- Full password reset and real mailbox verification service.
- Comprehensive session revalidation/invalidation on suspension.
- Production hosting, HTTPS, WSGI deployment, monitoring, backups and operational hardening.
- Any destructive database cleanup/migration.

Demo account switching must remain a **local presentation convenience only**. Never deploy the current demo switch publicly.

---

## 6. Small-prompt workflow for Codex / Antigravity

Use the coding model only for a defined, verifiable task. Suggested template:

> Read `AGENTS.md` and `PLENTIQ_NEXT_STEPS_REFERENCE.md`. Work only on **[one page or feature]**. First inspect the relevant route/template/CSS and identify the minimum files needed. Then implement only the agreed change. Preserve existing route names, database schema and demo-switching behavior unless the task explicitly says otherwise. Do not run SQL, reset data, install packages, or modify unrelated pages. Check responsive layout and form/error/empty states. Review the diff and report changed files, checks actually run, and anything not verified. Stop after this task.

Rules:

1. One task per prompt; do not ask for “complete the whole project.”
2. Prefer edits to specific files over repository-wide rewrites.
3. Ask for a plan only when a task is risky; avoid repeated whole-project audits.
4. Test manually where appropriate; static syntax checks do not prove MySQL workflows work.
5. Keep the current presentation state recoverable before each substantial change.
6. If switching to Antigravity, stop the other agent first, preserve the current files, and have only one agent editing a given set of files at a time.

---

## 7. First task recommended from here

**Start with the login page only.** The user's current visible concern is that the login screen now looks like a basic email/password form and the earlier demo area changed. Preserve the working demo feature while improving the page's appearance. Do not implement registration at the same time as the login redesign; do not change authentication/security behavior in that visual task.

Acceptance criteria for the first page task:

- Existing login route and field names remain compatible with Flask.
- Demo-switching panel continues to appear when local demo mode is enabled.
- The shared password is not displayed on the page.
- Clear title, short value statement, labelled email/password inputs and clear primary button.
- Helpful form validation/error display without leaking sensitive information.
- Responsive desktop/mobile layout using the project's existing CSS approach.
- No new packages, schema changes, or changes to other pages.
- Codex explains which files changed; user verifies normal login and demo switching manually.

After the login design is verified, decide the signup policy and then go page by page.
