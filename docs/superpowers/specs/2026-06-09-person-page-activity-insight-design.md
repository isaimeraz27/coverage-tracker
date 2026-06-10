# Person-page activity insight redesign

**Date:** 2026-06-09
**Status:** approved-pending-review

## Goal

Make the person page answer "who's doing what" from **trustworthy, captured data only** —
no intent inference. Three changes, plus removal of the unreliable workflow/tasks feature.

The product is a disclosed employee-monitoring tool for an insurance agency, governed by
Colombia's Ley 1581 + US monitoring law. Its credibility depends on every number being
defensible. Inference-based workflow detection ("started but didn't finish a new-business
quote") is too unreliable for clean producer sequences and is being **unwired from the UI**.

## Scope

1. **Hourly activity timeline** — replace the broken proportional strip with a per-hour bar
   chart. Fixes a real hardcoded-8am bug.
2. **"Where the hours went" → clickable taxonomy categories** — two-level, click a category
   to expand to the apps/domains under it.
3. **Factual activity summary** — a compact, scannable strip of already-computed numbers.
4. **Remove workflow/tasks UI** — drop `TasksCard` and the `tasks` payload from the person
   page. The detection engine (`insights/workflows.py`, `detect_tasks`) stays in the codebase
   but is unwired from the dashboard, so it can be revived later.

Out of scope: per-person/role workflow assignment (explicitly dropped); any LLM narrative;
changes to scoring/engine math; agent changes.

---

## 1. Hourly activity timeline

### Current bug
`server/api.py::_person_timeline` hardcodes the work window to 8:00–18:00:
- `WIN = 10 * 3600.0  # 8:00–18:00 work window`
- `sec = (t.hour - 8) * 3600 + ...` — anchors every segment to an 8am origin.

And `web/src/pages/Person.tsx` hardcodes the axis labels `8a 10a 12p 2p 4p 6p`. Activity
outside 8–18 (e.g. a midnight test) renders off-axis or clamped, so the timeline lies.

### New behavior
A **bar chart, one bar per hour**, height = total **active minutes** in that hour (0–60),
stacked/colored by class: productive (gold `#D4AF37`), distracting (red `rgba(176,0,32,.55)`),
meeting (`#F4D77A`), idle (grey `#cdcdcd`). The hour axis is **driven by the configured work
window**, read from settings — not hardcoded.

### Server change — `_person_timeline(uid, day)` returns hourly buckets
Replace the per-segment proportional output with an aggregation keyed by clock hour:

```
buckets = { hour: {productive_s, distracting_s, meeting_s, idle_s} }  for each event row
```
- Hour = `dt.datetime.fromisoformat(ts).hour` (local clock hour of the stored event).
- Classify each row exactly as the timeline does today (meeting → idle → coarse class of
  `sub_category`), but **sum seconds into the hour bucket** instead of emitting a strip span.
- Return shape:
  ```json
  {
    "work_start": 8, "work_end": 18,
    "hours": [
      {"hour": 8, "productive_s": 1800, "distracting_s": 0, "meeting_s": 600, "idle_s": 120},
      ...
    ]
  }
  ```
- `work_start`/`work_end` come from `db.work_hours(conn)` (already exists). The frontend renders
  one bar per hour from `work_start` to `work_end - 1` inclusive. If any activity falls **outside**
  that window (e.g. the midnight test), include those hours too so nothing is hidden — i.e. the
  rendered hour range is `min(work_start, earliest_active_hour) .. max(work_end-1, latest_active_hour)`.
- Bar height normalization: 3600s = full height. Stack the four classes within each bar.

The endpoint payload field stays named `timeline` but its value becomes this object (was an
array). `Person.tsx` is the only consumer.

### Frontend change — `Person.tsx`
Replace the strip (lines ~74–91) with an `<HourlyTimeline>` component: a fl/CSS bar chart,
one column per hour, four stacked segments per column, hour labels under each (or every other
to avoid crowding). Tooltip per bar: "9a — 45m productive, 10m meeting".

---

## 2. "Where the hours went" → clickable taxonomy categories

### Current
`build_ledger` returns `extra["top"]` = flat top-5 `[{sub, secs}]`. `Person.tsx` renders a flat
bar list keyed by `sub`, marking each "expected" if in `on_task_set`.

### New behavior
Two-level, clickable:
- **Top level** = taxonomy **category** (the coarse/category grouping), with total seconds, a
  bar, and the expected/not-in-role marker.
- **Click** → expand to **children grouped by app name (desktop) or domain (web)** — whichever
  the event carries — each with its own seconds. Full URL shown on hover where available.

### Server change — `build_ledger` returns a nested breakdown
Alongside the existing flat `top` (kept for the daily-card `top` consumer at `api.py:836` and
`persist_day`'s `top_json` to avoid breaking other call sites), add `extra["breakdown"]`:

```json
"breakdown": [
  {
    "category": "Quoting", "secs": 5400, "expected": true,
    "children": [
      {"label": "ezlynx.com", "kind": "domain", "secs": 3000},
      {"label": "applied.com", "kind": "domain", "secs": 2400}
    ]
  },
  ...
]
```
- **Category = the resolved `sub_category`** (the fine category — `dev_tools`, `work_comms`,
  `social`, etc.). Confirmed by reading `shared/contracts.py`: the two-level model is
  `sub_category` (fine, human-readable) → `CoarseClass` (coarse: productive/neutral/distracting/
  idle, used only for color). There is no separate category-name table; `sub_category` IS the
  display category. The UI may prettify the label (e.g. `work_comms` → "Work comms"). `coarse`
  is carried per category to pick the bar color.
- Child key = `app` if it's a desktop app row, else `domain`. `kind` records which.
- Accumulate seconds per (category, child) during the **existing** `build_ledger` row loop —
  no second query. Sort categories by secs desc, children by secs desc.
- `expected` = category's `sub` ∈ `on_task_set` (carry the on_task check up to the category).
- Idle and meeting handled as today (meeting is its own category; idle excluded from breakdown).

`compute_day_full` already returns `extra`; the person endpoint passes `extra["breakdown"]`
through in the `person` payload (new field `breakdown`).

### Frontend change — `Person.tsx`
Replace the flat "Where the hours went" list with a `<CategoryBreakdown>` component:
collapsed categories with bars; clicking a row toggles its children (apps/domains) indented
below with their own mini-bars. Pure client-side expand/collapse (`useState` set of open ids).

---

## 3. Factual activity summary

A compact strip at the top of the breakdown card (or its own small card), all from
already-computed `insight`/`ledger` numbers — no new computation, no inference:
- Productive vs distracting split (from `adherence` / `distract_ratio` already shown).
- Meeting time, idle (long) time (already in the metrics card).
- Top 3 children (apps/domains) by time, pulled from the new `breakdown`.

This is presentation-only; it reuses fields already in the payload plus the new `breakdown`.
Implementation may fold this into the breakdown card header rather than a separate component.

---

## 4. Remove workflow/tasks UI

- `Person.tsx`: delete `<TasksCard>` usage (line 151) and the `TasksCard` function (156–196).
- `server/api.py::_person`: stop returning `tasks` in the payload (line 345/942–943). Leave
  `compute_day_full`/`detect_tasks` intact (engine stays; just not surfaced).
- `_incomplete_workflow_nudge` and the `incomplete_workflow_streak` flag (api.py ~934–941):
  **remove from the person flags** too — it's the same unreliable signal. Keep the function
  defined but unused, or delete; decide during implementation (prefer delete the call, keep the
  helper for potential revival).
- The Overview's incomplete-count (api.py:914) — review and remove if it surfaces the same
  signal on the floor view.

---

## Data flow

```
activity_event rows (per day, per user)
        │  build_ledger() single loop
        ├──→ ledger L (scoring inputs, unchanged)
        ├──→ extra["top"]        (flat top-5, kept for daily card + persistence)
        └──→ extra["breakdown"]  (NEW: nested category → app/domain → secs)

api._person()
        ├──→ insight (present_insight gate, unchanged — coaching guarantee intact)
        ├──→ breakdown  (NEW payload field)
        └──→ timeline   (CHANGED: now hourly-bucket object, not strip array)

Person.tsx
        ├──→ <HourlyTimeline timeline={...}/>      (NEW bar chart)
        ├──→ <CategoryBreakdown breakdown={...}/>  (NEW clickable two-level)
        └──→ factual summary strip                 (NEW, reuses existing numbers)
```

## Testing

- **Server:** unit-test the new `_person_timeline` hourly aggregation (event at 9:15 with
  30m active → 1800s in hour 9; event at 00:18 → hour 0 present even though outside 8–18) and
  the `build_ledger` `breakdown` nesting (two domains under one category sum + sort correctly;
  desktop app row keyed by app; idle excluded; meeting is its own category). Assert the existing
  `top` flat shape is unchanged (no regression for the daily card / `top_json`).
- **Coaching guarantee:** assert the person payload still never emits a verdict in coaching/
  uncalibrated mode (existing guard untouched; add a regression assertion that `breakdown`/
  `timeline` carry no target/verdict fields).
- **Removal:** assert `tasks` is no longer in the person payload and the
  `incomplete_workflow_streak` flag no longer appears.
- All **70 existing tests must stay green**; update any that asserted the old `timeline` array
  shape or the `tasks`/nudge presence.

## Risks / decisions to resolve in implementation

1. **Category name source** — RESOLVED: `sub_category` is the display category (no separate
   category-name table; coarse class is color-only). UI prettifies the label.
2. **`timeline` field shape change** is breaking for any other consumer — confirmed only
   `Person.tsx` reads it. Verify no test asserts the old array shape (update if so).
3. **Local vs UTC hour** — bucket by the hour of the stored `ts` (local clock the agent
   recorded). Keep consistent with how `ts` is written; verify against an event's stored value.
4. **Outside-window activity** — must still render (don't clamp), so test data captured at odd
   hours isn't invisible. This is what surfaced the original bug.
