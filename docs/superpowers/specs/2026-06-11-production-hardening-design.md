# Production hardening — design

**Date:** 2026-06-11
**Status:** approved-pending-review

## Goal

Make the Coverage employee-monitoring tool safe to run on an always-on, internet-facing,
HTTPS server with real employees under Colombia's Ley 1581. Four code workstreams, each
independently testable. Hosting/TLS/legal-wording are handled separately (ops/legal), not here.

The product's spine is fairness + defensibility: metadata only, coaching-not-verdict, and now
**provable consent + enforced retention + hardened auth**. All 80 existing tests must stay green;
the coaching-mode and privacy guarantees must not regress.

---

## 1. `TRACKER_NO_AUTH` strict gate

**Problem:** `server/api.py:82` — `NO_AUTH = bool(os.environ.get("TRACKER_NO_AUTH"))`. `bool()` of any
non-empty string is True, so `TRACKER_NO_AUTH=0` or `=false` *enables* the bypass. When on,
`_session()` returns `DEV_ADMIN` (read-all admin) for every request with no login.

**Fix:**
- `NO_AUTH = os.environ.get("TRACKER_NO_AUTH") == "1"` (strict; only the literal "1").
- On server startup (in `make_server` or `run.py`), if `NO_AUTH` is active, print a loud
  `WARNING: authentication is DISABLED (TRACKER_NO_AUTH=1) — never use this in production`.

**Test:** `TRACKER_NO_AUTH` set to `"0"`, `"false"`, `""` → bypass OFF (protected endpoint 401);
`"1"` → bypass ON. (Set/restore the env var in the test; must not leak to other tests.)

---

## 2. Schedule the retention purge

**Problem:** `db.purge_expired()` (db.py:401) is fully implemented per the §3.6 retention schedule
but is **never called** — data lives forever, itself a Ley 1581 violation.

**Fix:** A daemon thread started by `make_server` that:
- runs `purge_expired()` once ~30s after startup, then every 24h,
- acquires `server.lock` for the DB write (safe alongside requests),
- logs the purged counts,
- wraps each run in try/except so a failed purge never crashes the server,
- is a `daemon=True` thread so it dies with the process.

Expose the interval as a constant (`PURGE_INTERVAL_S = 86400`) and a one-shot helper
`run_purge_once(server)` so it's unit-testable without waiting 24h.

**Test:** call `run_purge_once` against a DB seeded with an old event (ts beyond retention) and a
fresh event → old gone, fresh kept. (Reuses `purge_expired`'s own coverage; this verifies the
scheduler hook calls it correctly under the lock.)

---

## 3. Security hardening batch

### 3a. Hash agent tokens at rest (transparent to deployed agents)

**Problem:** `auth.enroll` (auth.py:39-41) writes the raw `eat_<machine_id>_<uuid>` token into
`machine.token`; `verify_agent_token` (auth.py:52-56) does `WHERE token=?` on the raw value. Anyone
who reads the SQLite file gets every reusable agent credential.

**Fix:**
- Store `sha256(token).hexdigest()` in `machine.token`. `enroll` returns the RAW token (agent keeps
  it in config.json as today); only the hash is persisted.
- `verify_agent_token(conn, token)` hashes the incoming token and matches the hash. To stay
  transparent to already-enrolled agents (Option A), it ALSO accepts a legacy plaintext match and,
  on a legacy hit, upgrades that row to the hash in place (lazy migration) — so old agents keep
  working and self-heal on first contact.
- Backfill of existing plaintext tokens is handled by the **lazy self-upgrade in
  `verify_agent_token`** (above), NOT a SQL migration — SQLite has no SHA-256, and the project's
  migrations are a declarative SQL-statement list (`server/migrations.py:
  MIGRATIONS = [(version, name, steps)]`), not arbitrary Python. No schema change is needed for 3a
  (the `machine.token` column already exists); the only change is WHAT we store in it.
- Add `hash_token(token) -> str` helper in auth.py (sha256 hex).

**Test:** enroll → stored value is a 64-hex hash, not the raw token; `verify_agent_token(raw)`
resolves the machine; a tampered token fails. Legacy path: insert a row with a raw plaintext token,
`verify_agent_token(raw)` still resolves AND the row is upgraded to the hash afterward.

### 3b. Require a non-default setup (enroll) password

**Problem:** `enroll_password` defaults to the public string `"coverage-setup"` (db.py:184) and
`_setup_admin` only sets it `if d.get("enroll_password")` (api.py:471) — optional, so the guessable
default can ship. Anyone who knows it can self-enroll a machine and post forged activity.

**Fix:**
- In `_setup_admin`, REQUIRE `enroll_password`: reject (400) if missing/empty or equal to the seed
  `"coverage-setup"`. Setup cannot complete without a real one.
- Keep the field on the SetupAdmin React page (already there); add inline validation + a helper note.

**Test:** setup-admin with no enroll_password → 400; with `"coverage-setup"` → 400; with a real
password → 200 and the setting is stored.

### 3c. Session expiry (idle + absolute) + Secure cookie

**Problem:** `SESSIONS` (api.py:79) maps `sid -> row` with no timestamps; a session is valid until
the process restarts or explicit logout. No idle/absolute expiry, no `Secure` flag.

**Fix:**
- `SESSIONS[sid] = {"row": row, "created": now, "last_seen": now}`.
- `_session()`: if `now - last_seen > IDLE_TIMEOUT_S` (12h) OR `now - created > ABSOLUTE_TIMEOUT_S`
  (7d) → delete the entry and treat as unauthenticated; else update `last_seen` and return the row.
- Constants: `IDLE_TIMEOUT_S = 12*3600`, `ABSOLUTE_TIMEOUT_S = 7*86400`.
- Cookie: add `Secure` when the effective scheme is https (derive from `_base_url`/
  `X-Forwarded-Proto`), gated so local http dev still works (no Secure on plain localhost http).
- Time source: a module-level `_now()` returning epoch seconds, so tests can monkeypatch it
  (avoids real sleeps).

**Test:** fresh session authenticates; advance `_now` past idle → rejected + pruned; within idle but
past absolute → rejected; activity within idle keeps it alive and resets last_seen. Cookie carries
`Secure` when scheme is https, omits it on http localhost.

---

## 4. Consent / acknowledgment at install (terminal)

**Goal:** a server-side record that each employee was shown the monitoring disclosure and
acknowledged it BEFORE their agent enrolls — the Ley 1581 paper trail. Wording is the client's
(legal); we build the mechanism + storage + an editable disclosure setting.

### Data
- New table `ack_record(id, machine_id, hostname, disclosure_version, acknowledged_ts)`
  via a numbered migration.
- Settings: `disclosure_text` (the monitoring notice the client edits) and `disclosure_version`
  (bumped when the text changes — changing the text auto-bumps the version).

### Flow (terminal, at install)
- The `/install.ps1` script, BEFORE downloading/enrolling, fetches the current disclosure
  (`GET /api/v1/disclosure` → `{version, text}`), prints it, and prompts:
  `Type 'I agree' to consent to monitoring and continue (anything else cancels):`.
- If the response isn't `I agree` → the script prints a cancel notice and exits WITHOUT installing.
- On agreement, install proceeds; the agent's **enroll call includes** `disclosure_version` +
  `hostname`, and the server writes an `ack_record` row at enroll time (consent is bound to the
  same request that mints the token, so no token is ever issued without a recorded ack).
- `disclosure_text`/`version` are server-derived (the script fetches them); never trust a
  client-supplied version other than echoing the one we served.

### Dashboard surface (read-only, minimal)
- The Machines page shows, per enrolled machine: `consented v<N> · <date>` (from `ack_record`).
  No new admin actions required for the pilot — just visibility that the trail exists.
- (Editing `disclosure_text` can reuse the existing Settings save path — a textarea on the
  Settings page; if that's more than a small add, defer the editor to a follow-up and ship with the
  setting editable via the same admin settings API. Decide during implementation; the mechanism +
  record are the must-haves.)

### Tests
- `GET /api/v1/disclosure` returns the current `{version, text}`.
- Enroll with a `disclosure_version` writes an `ack_record` bound to the machine; enroll without one
  still works for backward-compat but records `disclosure_version=null` (so already-scripted installs
  don't break) — flagged so the dashboard can show "not acknowledged".
- The install script body contains the disclosure-fetch + `I agree` gate and the cancel path.

---

## Cross-cutting

- **Migrations:** 3a (token hash) and 4 (ack_record table) are numbered migrations in
  `server/migrations.py`, idempotent, run on startup — consistent with the existing pattern.
- **No agent capture/segmentation changes.** 3a is transparent to the agent; 4 only adds fields to
  the enroll request + the install script preamble.
- **Coaching/privacy guarantees untouched:** none of this touches `present_insight`, the scoring
  engine, or redaction.
- **Order of build:** 1 → 2 → 3a → 3b → 3c → 4 (cheapest/most-isolated first; consent last as the
  biggest piece). Each lands green before the next.

## Out of scope (ops/legal/follow-up)
- TLS/HTTPS termination, the always-on host, and the systemd/service unit (ops — Phase 2).
- The actual legal disclosure wording (client's counsel).
- DB backups (ops runbook).
- `full_url` Settings UI toggle and a rich disclosure-text editor (nice-to-have follow-ups).
- Code-signing the .exe (deferred — in-person installs).
