# Employee Activity Tracker — reference implementation

A working implementation of the build-ready spec in
[`../employee-activity-tracker.md`](../employee-activity-tracker.md). A lightweight
agent runs on each company Windows machine, ships activity events to a central
dashboard host, and an explainable insights engine answers **who is on-task** and
**who is wasting time** — for coaching *and* accountability, with the spec's
privacy/governance guardrails built in.

> Internal tool, company-owned devices, work hours only, disclosed to all staff.
> Insights are decision-support, **never** automated evaluation (spec §3.10).

## Implementation choices (deviations from §5, on purpose)

- **Pure Python standard library — zero third-party deps.** No `pip install`, no
  network needed. This was chosen so the whole product **runs and is tested on this
  machine** (the build host has no .NET). The §3 *canonical contracts* are
  implemented faithfully; only the language/framework differs from §5's suggestion.
- **Agent uses `ctypes`** (stdlib) for the Win32 calls instead of pywin32, behind a
  platform abstraction. Real capture runs only on Windows; a `MockCapture` backend
  plus the synthetic generator make all the non-Windows logic testable anywhere.
- **TLS & at-rest encryption are host-level, not in-process.** The spec requires
  HTTPS-only (front with a TLS reverse proxy) and at-rest encryption (OS DPAPI/KEK).
  stdlib has no AES, so the screenshot at-rest crypto is a documented stub.

## Run it

The dashboard is a React app (`web/`) served by the Python server, which also ingests
agent data. Production starts **empty** and is configured through a first-run admin flow —
no demo logins.

```bash
cd employee-tracker

# 1. build the dashboard UI once (outputs web/dist, which the server serves)
cd web && npm install && npm run build && cd ..

# 2. start the server (serves the SPA + the JSON/ingest API)
python3 server/run.py                 # http://127.0.0.1:8765

# 3. open the URL → first-run setup creates your admin + names the org.
#    Then: Roles → add a role (optionally set a target to calibrate it),
#          Machines → issue an enrollment code,
#          run an agent (or the simulator below) against that code.
```

**Test the pipeline with no Windows machine** — the simulator streams a realistic day
through the *real* agent→server code path (capture → redaction → segmenter → outbox →
shipper → ingest):

```bash
# grab the enrollment code from the Machines page, then:
python3 tools/live_agent.py --user sim_sam --code <CODE>
# sim_sam appears on the floor: zoom→meeting, reddit/youtube→distracting,
# keepass dropped by redaction, a long away gap → idle_long.
```

**Coaching vs evaluative.** A fresh org defaults to **coaching mode** and all roles
uncalibrated → people are compared to their own 14-day baseline, with **no pass/fail**.
Setting a role's target score (Roles page) *calibrates* it; switching the org to
**evaluative** (Settings) then shows pass/fail for calibrated roles. The score never
changes — only what the dashboard concludes from it.

### Insurance taxonomy + workflow tracking (for training)

Built for an agency tracking **producers** for training. Two admin-configurable layers:

- **Taxonomy** (`Taxonomy` admin page): map your agency's tools to categories with
  app / domain / **URL-path** rules — e.g. `app.ezlynx.com/quotes/*/rating` → `rating`
  distinguishes the *quote stage* within one tool. Rules apply **server-side and
  retroactively**: edit a rule and past activity reclassifies on the next view, no
  re-ingest. A live "paste a URL → see the match" tester is built in. Turn on full-URL
  capture in Settings (`full_url`); sensitive domains (banking/health/identity) always
  drop the path on-device regardless.
- **Workflows** (`Workflows` admin page): define a task as a set/sequence of categories
  within a window — e.g. a *new-business quote* = `rating` + `carrier_portal` + `ams`.
  The person page then shows **"Tasks today"**: per-quote duration, tool-switches,
  re-opens, on-task ratio, and time-vs-expected. **Coaching signal only — never a
  verdict** (it's a prototype; calibrate against real producer capture).

Try it: `python3 tools/live_agent.py --user maria --profile producer --code <CODE>`
streams a producer's full-URL day (dialer → CRM → rater → carrier → AMS → e-sign, twice)
so the taxonomy and two detected quote workflows light up on Maria's person page.

**Frontend dev loop** (hot reload): run the server in one terminal and `cd web && npm run
dev` in another (port 5173, proxies the API to 8765).

**Optional demo data** (synthetic personas, local QA only — never in production):

```bash
python3 tools/seed.py --demo   # 5 personas x 3 days; logins admin/admin, manager/manager
```

**Tests:**

```bash
python3 -m unittest discover -s tests   # 41 tests
```

## Layout

```
shared/contracts.py     §3.2/§3.3/§3.4 canonical taxonomy, categories, idle, config, DTOs
server/db.py            §3.1 identity (INTEGER PKs), §3.3 category table, §3.6 retention purge
server/auth.py          §3.7 enroll -> eat_ token, manager login (PBKDF2), RBAC scope
server/api.py           §3.7 ingest surface + dashboard read routes (stdlib http.server, audited)
server/rollup.py        raw events -> §3.9 day-ledger -> daily_agg; per-role target (calibration)
server/migrations.py    numbered schema migrations layered on the db.SCHEMA baseline
server/baselines.py     per-user 14-day baseline from daily_agg (coaching-mode comparison)
insights/engine.py      §3.9 on-task/score/flags + §3.10 confidence & needs-context (pure)
web/                    React + Vite + Tailwind dashboard (login, first-run, floor, person, admin)
agent/capture.py        WindowsCapture (ctypes) | MockCapture
agent/segmenter (agent.py)  polls -> focus segments + idle spans (§3.4/§3.5)  [pure/tested]
agent/redaction.py      capture-time: drop sensitive apps, redact password titles, strip URLs
agent/buffer.py         offline SQLite outbox; shipper.py gzip+token upload
tools/live_agent.py     simulator: real agent pipeline over the wire, no Windows machine
tools/synth.py          synthetic activity generator (test infra; §5)
tools/seed.py           OPT-IN demo DB (--demo): synthetic personas for local QA only
tests/                  insights fixture (§3.9), contracts, segmenter, redaction, wire e2e,
                        migrations, mode/verdict gate, admin API, first-run bootstrap
```

`server/api.py` serves the built `web/dist` SPA and exposes the JSON API:
first-run bootstrap (`/api/v1/bootstrap-status`, `/setup-admin`), session auth
(`/me`, `/login`, `/logout`), the manager reads (`/overview`, `/person`), and the
admin/onboarding surface (`/api/v1/admin/roles|users|managers|machines`, `/settings`).
`present_insight()` is the single gate that decides whether a pass/fail verdict is ever
emitted — coaching mode and uncalibrated roles never produce one.

## Spec milestone status (§5)

| Milestone | Status |
|---|---|
| M1 Ingest API + DB | ✅ |
| M2 Agent capture + send | ✅ logic + ctypes Win32 (real capture untested off-Windows) |
| M3 Dashboard overview | ✅ |
| M4 Categories + role profiles | ✅ |
| M5 Insights engine | ✅ (canonical §3.9 example is a passing fixture) |
| M6 Screenshots | ◐ metadata + blob endpoint, off by default; at-rest AES stubbed (no stdlib AES) |
| M7 Security / retention / audit | ✅ retention purge, audit log, RBAC, token enroll · TLS/at-rest = host-level |
| M8 Packaging / installer | ◐ run scripts present; MSI/NSSM Windows service install not generated here |

## Guardrails implemented (not just documented)

- **One on-task definition** (§3.9) used by score, adherence, and dashboard.
- **Two-tier idle** (§3.4): short idle forgiven, long idle flagged — fed by explicit `idle_ms`.
- **Meeting awareness** (§3.5): conferencing foreground sets `is_meeting`, kept present-and-excused.
- **Capture-time redaction** (§3.8): sensitive apps dropped, password titles redacted, URLs → domain.
- **Confidence / needs-context** (§3.10): thin-data days are "not for evaluation"; no ranking/discipline endpoint exists.
- **Retention purge** (§3.6), **access audit** + **RBAC** (§3.6/§3.10), **idempotent ingest** (§3.7).

## Not production-ready as-is

Before a real pilot: terminate TLS in front (HTTPS-only, LAN/VPN), enable OS-level
at-rest DB/screenshot encryption, build the MSI + NSSM service install, get legal
sign-off on the §3.6 retention table (US + Colombia / Ley 1581), and have managers
set each role's `on_task_set`. The score weights/thresholds are admitted heuristics —
keep insights **coaching-only until calibrated** against pilot data (spec §10 #8).
```