# Person-page Activity Insight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the person page's broken hardcoded-8am timeline with an hourly bar chart, turn the flat "where the hours went" list into a clickable two-level category→app/domain breakdown, add a factual summary, and unwire the unreliable workflow/tasks feature.

**Architecture:** All data is computed server-side in `build_ledger`/`_person_timeline` from `activity_event` rows (no inference). The person endpoint gains a `breakdown` field and changes `timeline` from a strip-array to an hourly-bucket object. `Person.tsx` renders two new presentational components. The `top` flat field and the workflow detection engine stay intact (just unwired from the UI).

**Tech Stack:** Python stdlib (`http.server` + sqlite3), React + Vite + TypeScript + Tailwind. Tests via `python3 -m unittest`.

---

## File structure

- `server/rollup.py` — `build_ledger` gains `extra["breakdown"]` (nested category→app/domain), built in the existing row loop. `top` unchanged.
- `server/api.py` — `_person_timeline` rewritten to return hourly buckets + work-window bounds; `_person` passes `breakdown` through and drops `tasks` + the incomplete-workflow nudge flag; the person JSON response gains `breakdown`, changes `timeline` shape, drops `tasks`.
- `web/src/lib/api.ts` — `Person` interface: new `breakdown`, new `timeline` shape, remove `tasks`.
- `web/src/pages/Person.tsx` — new `<HourlyTimeline>` + `<CategoryBreakdown>` components; remove `<TasksCard>`; add factual summary strip.
- `tests/test_person_insight.py` — NEW: hourly aggregation + breakdown nesting + removal regressions.

Run all tests from repo root `employee-tracker/` with: `python3 -m unittest discover -s tests`

> **Commit note:** the working tree is mirrored to a bare repo at `/tmp/et_repo.git`. Each commit step uses:
> `GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git add <files> && GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git commit -m "..."`
> If a normal `git` repo is present in the working tree, plain `git add/commit` is fine instead.

---

## Task 1: Server — nested category breakdown in `build_ledger`

**Files:**
- Modify: `server/rollup.py:40-95` (the `build_ledger` row loop + return)
- Test: `tests/test_person_insight.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_person_insight.py`:

```python
"""Person-page data: nested category breakdown + hourly timeline buckets."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server import db, rollup  # noqa: E402
from shared import contracts as C  # noqa: E402


def _seed_user(conn):
    conn.execute("INSERT INTO role(name, target_score) VALUES('producer', NULL)")
    rid = conn.execute("SELECT id FROM role WHERE name='producer'").fetchone()["id"]
    conn.execute("INSERT INTO app_user(username, display_name, role_fk) VALUES('u','U',?)", (rid,))
    return conn.execute("SELECT id FROM app_user WHERE username='u'").fetchone()["id"]


def _ev(conn, uid, ts, app, domain, sub, active_ms, idle_ms=0, state="active", meeting=0):
    conn.execute(
        "INSERT INTO activity_event(user_fk,machine_fk,client_event_id,ts,ts_norm,app,domain,"
        "url,sub_category,category_code,state,active_ms,idle_ms,is_meeting) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (uid, 1, ts + app, ts, ts, app, domain, None, sub,
         C.coarse_of(sub).value, state, active_ms, idle_ms, meeting))


class TestBreakdown(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = db.connect(self.path)
        db.init_db(self.conn)
        self.uid = _seed_user(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_breakdown_nests_domains_under_category_sorted(self):
        day = "2026-06-09"
        # two domains under work_comms, one app under dev_tools
        _ev(self.conn, self.uid, day + "T09:00:00+00:00", "msedge", "mail.google.com", "work_comms", 1200_000)
        _ev(self.conn, self.uid, day + "T09:30:00+00:00", "msedge", "outlook.office.com", "work_comms", 600_000)
        _ev(self.conn, self.uid, day + "T10:00:00+00:00", "code", None, "dev_tools", 900_000)
        self.conn.commit()
        _, extra, _ = rollup.build_ledger(self.conn, self.uid, day)
        bd = {c["category"]: c for c in extra["breakdown"]}
        self.assertIn("work_comms", bd)
        self.assertIn("dev_tools", bd)
        # work_comms total = 1800s, two children sorted desc by secs
        self.assertEqual(bd["work_comms"]["secs"], 1800)
        kids = bd["work_comms"]["children"]
        self.assertEqual(kids[0]["label"], "mail.google.com")
        self.assertEqual(kids[0]["secs"], 1200)
        self.assertEqual(kids[0]["kind"], "domain")
        self.assertEqual(kids[1]["label"], "outlook.office.com")
        # dev_tools child keyed by app (no domain)
        self.assertEqual(bd["dev_tools"]["children"][0]["label"], "code")
        self.assertEqual(bd["dev_tools"]["children"][0]["kind"], "app")

    def test_breakdown_excludes_idle_and_keeps_top_flat_shape(self):
        day = "2026-06-09"
        _ev(self.conn, self.uid, day + "T09:00:00+00:00", "code", None, "dev_tools", 600_000)
        _ev(self.conn, self.uid, day + "T09:20:00+00:00", "", None, "idle", 0, idle_ms=600_000, state="idle")
        self.conn.commit()
        _, extra, _ = rollup.build_ledger(self.conn, self.uid, day)
        cats = [c["category"] for c in extra["breakdown"]]
        self.assertNotIn("idle", cats)
        # top flat field unchanged: list of {sub, secs}
        self.assertTrue(all(set(t.keys()) == {"sub", "secs"} for t in extra["top"]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_person_insight -v`
Expected: FAIL — `KeyError: 'breakdown'` (build_ledger doesn't return it yet).

- [ ] **Step 3: Implement the breakdown accumulation in `build_ledger`**

In `server/rollup.py`, inside `build_ledger`, add two accumulators next to `top` (after line 41 `top: dict[str, float] = {}`):

```python
    top: dict[str, float] = {}
    # nested breakdown: category(sub) -> {"secs", "coarse", "children": {label -> [secs, kind]}}
    bd: dict[str, dict] = {}
```

In the **active, non-meeting** branch, right after the existing `top[sub] = top.get(sub, 0) + a` (line 91), add:

```python
        # nested breakdown child key: domain for web rows, else app
        child_label = r["domain"] or r["app"] or "unknown"
        child_kind = "domain" if r["domain"] else "app"
        cat = bd.setdefault(sub, {"secs": 0.0, "coarse": coarse, "children": {}})
        cat["secs"] += a
        ch = cat["children"].setdefault(child_label, {"secs": 0.0, "kind": child_kind})
        ch["secs"] += a
```

Then replace the return block (currently lines 93-95):

```python
    top_sorted = sorted(top.items(), key=lambda kv: kv[1], reverse=True)[:5]
    breakdown = [
        {"category": k, "secs": round(v["secs"]), "coarse": v["coarse"],
         "children": [
             {"label": lbl, "kind": c["kind"], "secs": round(c["secs"])}
             for lbl, c in sorted(v["children"].items(), key=lambda kv: kv[1]["secs"], reverse=True)
         ]}
        for k, v in sorted(bd.items(), key=lambda kv: kv[1]["secs"], reverse=True)
    ]
    extra = {"top": [{"sub": k, "secs": round(v)} for k, v in top_sorted],
             "breakdown": breakdown}
    return L, extra, stream
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_person_insight -v`
Expected: PASS (both tests).

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `python3 -m unittest discover -s tests`
Expected: all previously-green tests still pass (70 + 2 new).

- [ ] **Step 6: Commit**

```bash
GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git add server/rollup.py tests/test_person_insight.py
GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git commit -m "feat(server): nested category->app/domain breakdown in build_ledger"
```

---

## Task 2: Server — hourly timeline buckets

**Files:**
- Modify: `server/api.py:945-972` (`_person_timeline`)
- Test: `tests/test_person_insight.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_person_insight.py` a new class (needs a live-ish setup with the API helper; use the server's `_person_timeline` via a minimal handler is awkward, so test the pure bucketing by importing a new module-level helper). To keep `_person_timeline` testable, the implementation will delegate to a pure function `hourly_buckets(rows, work_hours)` in `server/rollup.py`. Test that:

```python
class TestHourlyTimeline(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = db.connect(self.path)
        db.init_db(self.conn)
        self.uid = _seed_user(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_buckets_by_clock_hour_and_includes_outside_window(self):
        day = "2026-06-09"
        # 30 min productive at 09:15, 10 min meeting at 09:40, and activity at 00:18 (outside 8-18)
        _ev(self.conn, self.uid, day + "T09:15:00+00:00", "code", None, "dev_tools", 1800_000)
        _ev(self.conn, self.uid, day + "T09:40:00+00:00", "zoom", None, "meeting", 600_000, meeting=1)
        _ev(self.conn, self.uid, day + "T00:18:00+00:00", "msedge", "x.com", "social", 300_000)
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT ts, sub_category, state, active_ms, idle_ms, is_meeting, app, domain "
            "FROM activity_event WHERE user_fk=? AND substr(ts_norm,1,10)=? ORDER BY ts_norm",
            (self.uid, day)).fetchall()
        out = rollup.hourly_buckets(rows, {"work_start": 8, "work_end": 18})
        self.assertEqual(out["work_start"], 8)
        self.assertEqual(out["work_end"], 18)
        by_hour = {h["hour"]: h for h in out["hours"]}
        self.assertEqual(by_hour[9]["productive_s"], 1800)
        self.assertEqual(by_hour[9]["meeting_s"], 600)
        # hour 0 is outside 8-18 but must still appear (the original bug)
        self.assertIn(0, by_hour)
        self.assertEqual(by_hour[0]["distracting_s"], 300)

    def test_hours_span_covers_work_window_even_when_empty(self):
        out = rollup.hourly_buckets([], {"work_start": 8, "work_end": 12})
        hours = [h["hour"] for h in out["hours"]]
        self.assertEqual(hours, [8, 9, 10, 11])  # work_end exclusive
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_person_insight.TestHourlyTimeline -v`
Expected: FAIL — `AttributeError: module 'server.rollup' has no attribute 'hourly_buckets'`.

- [ ] **Step 3: Add the pure `hourly_buckets` function to `server/rollup.py`**

Add at module level (after `build_ledger`, before `role_target_score`):

```python
def hourly_buckets(rows, work_hours: dict) -> dict:
    """Aggregate activity rows into per-clock-hour buckets for the person timeline.

    Pure: takes DB rows (with ts, sub_category, state, active_ms, idle_ms, is_meeting)
    and the work-hours window. Buckets by the clock hour of `ts`. Hours OUTSIDE the
    work window that have activity are still included (so odd-hour data is never hidden).
    """
    import datetime as _dt
    ws = work_hours.get("work_start", 8)
    we = work_hours.get("work_end", 18)
    buckets: dict[int, dict] = {}

    def _b(h):
        return buckets.setdefault(h, {"hour": h, "productive_s": 0, "distracting_s": 0,
                                      "meeting_s": 0, "idle_s": 0})

    for r in rows:
        try:
            t = _dt.datetime.fromisoformat(r["ts"])
        except (ValueError, TypeError):
            continue
        h = t.hour
        active = (r["active_ms"] or 0) / 1000.0
        idle = (r["idle_ms"] or 0) / 1000.0
        if r["is_meeting"] and r["state"] != C.ActivityState.IDLE.value:
            _b(h)["meeting_s"] += round(active + idle)
        elif r["state"] == C.ActivityState.IDLE.value:
            _b(h)["idle_s"] += round(idle)
        else:
            coarse = C.coarse_of(r["sub_category"])
            if coarse == C.CoarseClass.DISTRACTING:
                _b(h)["distracting_s"] += round(active)
            else:
                _b(h)["productive_s"] += round(active)
    # ensure the work window is represented even with no activity
    for h in range(ws, we):
        _b(h)
    hours = [buckets[h] for h in sorted(buckets)]
    return {"work_start": ws, "work_end": we, "hours": hours}
```

Add `from shared import contracts as C` if not already imported at the top of `rollup.py` (it is — used by `build_ledger`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_person_insight.TestHourlyTimeline -v`
Expected: PASS.

- [ ] **Step 5: Rewrite `_person_timeline` in `server/api.py` to use it**

Replace the whole body of `_person_timeline` (api.py:945-972) with:

```python
    def _person_timeline(self, uid, day):
        rows = self.conn.execute(
            "SELECT ts, sub_category, state, active_ms, idle_ms, is_meeting "
            "FROM activity_event WHERE user_fk=? AND substr(ts_norm,1,10)=? ORDER BY ts_norm",
            (uid, day)).fetchall()
        return rollup.hourly_buckets(rows, db.work_hours(self.conn))
```

(`rollup` and `db` are already imported in api.py.)

- [ ] **Step 6: Run the full suite**

Run: `python3 -m unittest discover -s tests`
Expected: all green. If any test asserted the OLD `timeline` array shape (`l/w/c/t`), update it to the new object shape — search: `grep -rn "\"l\"\|'l'\|timeline" tests/`.

- [ ] **Step 7: Commit**

```bash
GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git add server/rollup.py server/api.py tests/test_person_insight.py
GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git commit -m "feat(server): hourly timeline buckets, drop hardcoded 8am window"
```

---

## Task 3: Server — wire `breakdown` into person payload, drop tasks + nudge flag

**Files:**
- Modify: `server/api.py:340-346` (person response), `server/api.py:922-943` (`_person`)
- Test: `tests/test_person_insight.py` (extend) + existing `tests/test_incomplete_nudge.py`

- [ ] **Step 1: Write the failing test (payload contains breakdown, not tasks)**

This needs the live server. Add to `tests/test_person_insight.py` a check using the existing live-server pattern from `tests/test_server.py`. Simpler: assert at the `_person` tuple level by constructing a handler is heavy — instead assert the response JSON via a tiny live server. Add:

```python
import json, threading, urllib.request
from server import api, auth


class TestPersonPayload(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        pre = db.connect(self.path); db.init_db(pre)
        self.uid = _seed_user(pre)
        _ev(pre, self.uid, "2026-06-09T09:00:00+00:00", "code", None, "dev_tools", 600_000)
        pre.commit(); pre.close()
        self.srv = api.make_server(0, self.path)
        with self.srv.lock:
            auth.create_first_admin(self.srv.conn, "admin", "pw12345678")
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        # login to get a cookie
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/v1/login",
            data=json.dumps({"username": "admin", "password": "pw12345678"}).encode(),
            headers={"Content-Type": "application/json"})
        r = urllib.request.urlopen(req)
        self.cookie = r.headers.get("Set-Cookie", "").split(";")[0]

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); os.unlink(self.path)

    def test_person_payload_has_breakdown_no_tasks(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/v1/person/{self.uid}?day=2026-06-09",
            headers={"Cookie": self.cookie})
        body = json.loads(urllib.request.urlopen(req).read())
        self.assertIn("breakdown", body)
        self.assertNotIn("tasks", body)
        self.assertIsInstance(body["timeline"], dict)  # new object shape, not array
        self.assertIn("hours", body["timeline"])
```

> Note: verify the exact login route + first-admin helper names against `tests/test_server.py` / `server/auth.py` while implementing; adjust `create_first_admin` / `/api/v1/login` if the real names differ. The intent (logged-in GET of the person endpoint) is what matters.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_person_insight.TestPersonPayload -v`
Expected: FAIL — `breakdown` missing / `tasks` present.

- [ ] **Step 3: Update `_person` to return breakdown, drop tasks + nudge**

In `server/api.py::_person` (922-943): remove the nudge block (934-941) and change the return. New tail of `_person`:

```python
        person = {"name": usr["display_name"] or usr["username"], "role": usr["role"]}
        on_task = db.role_on_task_set(self.conn, usr["role_fk"])
        ins_d = present_insight(self.conn, ins, extra, user_fk=uid, day=day)
        return (person, ins_d, extra.get("top", []), self._person_timeline(uid, day),
                on_task, extra.get("breakdown", []))
```

Update the caller (api.py:340) — rename the 6th tuple element and the response:

```python
            person, ins_d, top, timeline, on_task, breakdown = self._person(uid, day)
            ...
            return self._json(200, {"person": person, "insight": ins_d, "top": top,
                                    "timeline": timeline, "on_task_set": sorted(on_task),
                                    "breakdown": breakdown})
```

(The `_person` signature returns a 6-tuple either way — last element changes from `tasks` to `breakdown`.)

- [ ] **Step 4: Run the failing test + full suite**

Run: `python3 -m unittest tests.test_person_insight.TestPersonPayload -v`
Expected: PASS.
Run: `python3 -m unittest discover -s tests`
Expected: `tests/test_incomplete_nudge.py` may now FAIL (it asserted the nudge flag appears on the person page). Update it: the nudge is intentionally removed from the person flags. Either (a) delete the test if it only covered the person-flag surfacing, or (b) repoint it at `_incomplete_workflow_nudge` as a still-defined helper if it tests that function directly. Inspect the file and choose; document the choice in the commit message.

- [ ] **Step 5: Keep `_incomplete_workflow_nudge` helper but remove its only call**

Confirm no remaining caller: `grep -n "_incomplete_workflow_nudge" server/api.py` — should show only the `def`. Leave the function defined (revival-ready) with a comment `# UNUSED: kept for potential revival (see spec 2026-06-09)`.

- [ ] **Step 6: Commit**

```bash
GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git add server/api.py tests/test_person_insight.py tests/test_incomplete_nudge.py
GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git commit -m "feat(server): person payload exposes breakdown; drop tasks + incomplete-workflow nudge from UI surface"
```

---

## Task 4: Server — drop incomplete-workflow count from Overview (if present)

**Files:**
- Modify: `server/api.py:836,914` (the daily card / overview assembly)
- Test: existing suite

- [ ] **Step 1: Inspect the overview card assembly**

Run: `grep -n "incomplete\|tasks" server/api.py` and read api.py:830-920. Identify whether the Overview/floor card surfaces an incomplete-workflow count (api.py:914 `incompletes = [...]`).

- [ ] **Step 2: Remove the incomplete-count surfacing from the card**

If api.py:914 feeds a `nudge`/`incomplete` field into the floor card response, remove that field and the `incompletes` computation. Leave `extra["tasks"]` produced by `compute_day_full` alone (engine intact); just don't surface it. Keep `card["top"] = extra.get("top", [])` (still used).

- [ ] **Step 3: Run the full suite**

Run: `python3 -m unittest discover -s tests`
Expected: all green. Update any overview test asserting the incomplete field.

- [ ] **Step 4: Commit**

```bash
GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git add server/api.py tests/
GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git commit -m "feat(server): remove incomplete-workflow signal from overview"
```

---

## Task 5: Frontend — types for the new payload

**Files:**
- Modify: `web/src/lib/api.ts:128-135` (`Person` interface)

- [ ] **Step 1: Update the `Person` interface**

Replace lines 128-135:

```typescript
export interface TimelineHour {
  hour: number;
  productive_s: number;
  distracting_s: number;
  meeting_s: number;
  idle_s: number;
}

export interface BreakdownChild {
  label: string;
  kind: "domain" | "app";
  secs: number;
}

export interface BreakdownCategory {
  category: string;
  secs: number;
  coarse: string;
  children: BreakdownChild[];
}

export interface Person {
  person: { name: string; role: string | null };
  insight: PersonInsight;
  top: { sub: string; secs: number }[];
  timeline: { work_start: number; work_end: number; hours: TimelineHour[] };
  on_task_set: string[];
  breakdown: BreakdownCategory[];
}
```

The `Task` interface (api.ts:~115-126) is now unused by `Person` but may be referenced elsewhere — leave it defined; remove only if `grep -rn "Task\b" web/src` shows no other use.

- [ ] **Step 2: Typecheck**

Run: `cd web && npx tsc --noEmit && cd ..`
Expected: errors ONLY in `Person.tsx` (it still references `data.tasks` / old `timeline`/`top`) — those are fixed in Task 6. No errors in `api.ts` itself.

- [ ] **Step 3: Commit**

```bash
GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git add web/src/lib/api.ts
GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git commit -m "feat(web): types for hourly timeline + category breakdown"
```

---

## Task 6: Frontend — HourlyTimeline + CategoryBreakdown + remove TasksCard

**Files:**
- Modify: `web/src/pages/Person.tsx` (timeline render 74-91, "where the hours went" 103-126, TasksCard 151+156-196)

- [ ] **Step 1: Replace the timeline block (lines 74-91) with `<HourlyTimeline>`**

```tsx
      {data.timeline.hours.length > 0 && <HourlyTimeline tl={data.timeline} />}
```

Add the component (near the other helpers at the bottom of the file):

```tsx
function HourlyTimeline({ tl }: { tl: Person["timeline"] }) {
  const ampm = (h: number) =>
    h === 0 ? "12a" : h < 12 ? `${h}a` : h === 12 ? "12p" : `${h - 12}p`;
  const max = 3600; // a full hour
  const seg = (s: number, c: string, key: string) =>
    s > 0 ? <div key={key} style={{ height: `${(s / max) * 100}%`, background: c }} /> : null;
  return (
    <div className="card mt-4">
      <h3 className="font-serif font-semibold mb-2">Activity by hour</h3>
      <div className="flex items-end gap-1 h-28">
        {tl.hours.map((h) => {
          const total = h.productive_s + h.meeting_s + h.distracting_s + h.idle_s;
          return (
            <div key={h.hour} className="flex-1 flex flex-col items-center justify-end h-full">
              <div
                className="w-full flex flex-col-reverse rounded-sm overflow-hidden bg-[#f4f4f4]"
                style={{ height: "100%" }}
                title={`${ampm(h.hour)} — ${Math.round(total / 60)}m active`}
              >
                {seg(h.productive_s, "#D4AF37", "p")}
                {seg(h.meeting_s, "#F4D77A", "m")}
                {seg(h.distracting_s, "rgba(176,0,32,.55)", "d")}
                {seg(h.idle_s, "#cdcdcd", "i")}
              </div>
              <span className="text-[10px] text-muted mt-1">{ampm(h.hour)}</span>
            </div>
          );
        })}
      </div>
      <div className="flex gap-3 mt-2 text-[11px] text-muted">
        <Legend c="#D4AF37" t="productive" /><Legend c="#F4D77A" t="meeting" />
        <Legend c="rgba(176,0,32,.55)" t="distracting" /><Legend c="#cdcdcd" t="idle" />
      </div>
    </div>
  );
}

function Legend({ c, t }: { c: string; t: string }) {
  return (
    <span className="flex items-center gap-1">
      <span className="h-2 w-2 rounded-full" style={{ background: c }} /> {t}
    </span>
  );
}
```

- [ ] **Step 2: Replace the "Where the hours went" card (lines 103-126) with `<CategoryBreakdown>`**

```tsx
        <CategoryBreakdown breakdown={data.breakdown} onTask={data.on_task_set} />
```

Add the component:

```tsx
function CategoryBreakdown({
  breakdown, onTask,
}: { breakdown: Person["breakdown"]; onTask: string[] }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const pretty = (s: string) => s.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
  const max = breakdown[0]?.secs || 1;
  const h = (s: number) => `${(s / 3600).toFixed(1)}h`;
  return (
    <div className="card">
      <h3 className="font-serif font-semibold mb-2">Where the hours went</h3>
      {breakdown.length === 0 && <div className="text-muted text-[13px]">no activity</div>}
      {breakdown.map((c) => {
        const on = onTask.includes(c.category);
        const isOpen = open.has(c.category);
        return (
          <div key={c.category} className="mb-1.5">
            <button
              className="w-full flex items-center gap-2 text-[13px] text-left"
              onClick={() =>
                setOpen((p) => {
                  const n = new Set(p);
                  n.has(c.category) ? n.delete(c.category) : n.add(c.category);
                  return n;
                })
              }
            >
              <span className="text-muted w-3">{isOpen ? "▾" : "▸"}</span>
              <span className="h-2 w-2 rounded-full" style={{ background: on ? "#D4AF37" : "#cfcfcf" }} />
              <span className="flex-1">
                {pretty(c.category)}{" "}
                <span className="text-muted">{on ? "· expected" : "· not in role"}</span>
              </span>
              <span className="w-[120px]">
                <span className="inline-block h-2 rounded"
                  style={{ width: `${Math.min(100, (c.secs / max) * 100)}%`, background: on ? "#D4AF37" : "#cfcfcf" }} />
              </span>
              <b className="w-[46px] text-right">{h(c.secs)}</b>
            </button>
            {isOpen && (
              <div className="ml-7 mt-1 mb-2">
                {c.children.map((ch) => (
                  <div key={ch.label} className="flex items-center gap-2 text-[12px] text-muted py-0.5">
                    <span className="flex-1 truncate" title={ch.label}>{ch.label}</span>
                    <span className="text-[10px] px-1 rounded bg-[#eee]">{ch.kind}</span>
                    <b className="w-[46px] text-right text-ink">{h(ch.secs)}</b>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 3: Remove `<TasksCard>` usage + definition**

Delete line 151 (`<TasksCard tasks={data.tasks} />`) and the entire `TasksCard` function (156-196). Ensure `useState` is imported (it is, line 1).

- [ ] **Step 4: Add factual summary strip (spec §3)**

Inside the breakdown card header, under the `<h3>`, add a one-line summary using existing `ins` fields. In `PersonPage`, the breakdown card is rendered via `<CategoryBreakdown>`; pass the summary as a prop OR render it just above. Simplest: render a summary row above the two-column grid (after the timeline), using values already in `ins`:

```tsx
      <div className="card mt-4 flex flex-wrap gap-x-6 gap-y-1 text-[13px]">
        <span>On-task <b>{pct(ins.adherence)}</b></span>
        <span>Distraction <b>{pct(ins.distract_ratio)}</b></span>
        <span>Meeting <b>{h(ins.meeting_s)}</b></span>
        <span>Idle <b>{h(ins.idle_long_s)}</b></span>
        {data.breakdown[0] && (
          <span className="text-muted">
            Top: {data.breakdown[0].category.replace(/_/g, " ")} ({(data.breakdown[0].secs / 3600).toFixed(1)}h)
          </span>
        )}
      </div>
```

(`h` and `pct` helpers already exist at the top of Person.tsx.)

- [ ] **Step 5: Typecheck + build**

Run: `cd web && npx tsc --noEmit && npm run build && cd ..`
Expected: no type errors; Vite build succeeds, writes `web/dist`.

- [ ] **Step 6: Commit**

```bash
GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git add web/src/pages/Person.tsx web/dist
GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git commit -m "feat(web): hourly bar-chart timeline + clickable category breakdown + factual summary; remove tasks card"
```

---

## Task 7: Full verification + manual smoke

**Files:** none (verification)

- [ ] **Step 1: Full backend suite**

Run: `python3 -m unittest discover -s tests`
Expected: ALL green (72+ tests). Zero failures.

- [ ] **Step 2: Grep for stragglers**

Run: `grep -rn "data.tasks\|TasksCard\|incomplete_workflow" web/src server/api.py`
Expected: no `data.tasks`/`TasksCard`; `_incomplete_workflow_nudge` appears ONLY as its (uncalled) definition.

- [ ] **Step 3: Manual smoke (local)**

```bash
python3 server/run.py
```
Open `http://127.0.0.1:8765`, log in, open a person with activity. Verify: (a) hourly bar chart renders with correct hour labels (no forced 8a start), (b) categories expand to apps/domains on click, (c) summary strip shows, (d) no "Tasks today" card.

- [ ] **Step 4: Final commit (if any smoke fixes)**

```bash
GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git add -A
GIT_DIR=/tmp/et_repo.git GIT_WORK_TREE=$(pwd) git commit -m "chore: person-page insight redesign verified"
```

---

## Self-review notes

- **Spec coverage:** §1 hourly timeline → Tasks 2,6. §2 clickable categories → Tasks 1,5,6. §3 factual summary → Task 6 step 4. §4 remove workflows → Tasks 3,4,6. All covered.
- **`top` flat field preserved** (Task 1 keeps it; daily card api.py:836 + `top_json` persistence untouched) — verified consumers.
- **`timeline` shape change** is breaking only for `Person.tsx` (sole consumer) — Task 2 step 6 + Task 5 handle it; Task 2 step 6 explicitly greps tests for the old shape.
- **Coaching guarantee** untouched: `present_insight` gate not modified; breakdown/timeline carry no verdict/target fields (Task 1/2 output shapes contain only secs).
- **Type consistency:** `breakdown` category objects use `{category, secs, coarse, children:[{label,kind,secs}]}` consistently across server (Task 1), types (Task 5), UI (Task 6). `timeline` uses `{work_start,work_end,hours:[{hour,productive_s,distracting_s,meeting_s,idle_s}]}` consistently across Tasks 2,5,6.
- **Open verification during impl:** exact login route/first-admin helper names in Task 3 step 1 (adjust to match `tests/test_server.py`); whether Overview surfaces an incomplete count (Task 4 step 1 inspects before editing).
