"""Raw events -> per-user-per-day ledger -> daily_agg + insights.

Builds the §3.9 second-ledger from stored activity events, applying:
  - the role on_task_set (§3.3) to split on-task vs off-task active time,
  - the two-tier idle model (§3.4) via the explicit idle_ms,
  - the meeting bucket (§3.5) as present-and-excused.
"""
from __future__ import annotations

import os
import sys
import json
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import contracts as C  # noqa: E402
from insights import engine, workflows  # noqa: E402
from server import db, taxonomy  # noqa: E402


def build_ledger(conn, user_fk: int, day: str):
    """Build the §3.9 day-ledger AND the resolved ordered event stream.

    Returns (DayLedger, extra, resolved_stream). Categorization is applied SERVER-SIDE
    here from the editable taxonomy (taxonomy.categorize_server) — so editing a rule
    reclassifies history on the next rollup. When no rule matches, we fall back to the
    event's stored sub_category (keeps the simulator/agent + integration tests valid).
    `resolved_stream` is the per-row resolution used by Phase B workflow detection.
    """
    urow = conn.execute("SELECT role_fk FROM app_user WHERE id=?", (user_fk,)).fetchone()
    on_task = db.role_on_task_set(conn, urow["role_fk"] if urow else None)
    short_max = C.CONFIG_DEFAULTS["idle.short_max_s"]
    rules = db.taxonomy_rules(conn)  # pre-sorted; first match wins

    rows = conn.execute(
        "SELECT * FROM activity_event WHERE user_fk=? AND substr(ts_norm,1,10)=? ORDER BY ts_norm, id",
        (user_fk, day),
    ).fetchall()

    L = engine.DayLedger()
    top: dict[str, float] = {}
    # nested breakdown: category(sub) -> {"secs", "coarse", "children": {label -> {"secs","kind"}}}
    bd: dict[str, dict] = {}
    stream: list[dict] = []
    for r in rows:
        a = (r["active_ms"] or 0) / 1000.0
        i = (r["idle_ms"] or 0) / 1000.0
        if r["suspect_time"]:
            L.suspect_time_s += a + i

        is_idle = r["state"] == C.ActivityState.IDLE.value
        # resolve category server-side (active rows only; idle rows are their own bucket)
        sub = r["sub_category"] or C.UNCATEGORIZED
        coarse = db.coarse_for(conn, sub)
        is_meeting = bool(r["is_meeting"])
        if not is_idle:
            rsub, rcoarse, rmeet = taxonomy.categorize_server(
                conn, rules, r["app"], r["domain"], r["url"])
            if rsub is not None:                 # a rule matched → use it
                sub, coarse = rsub, rcoarse
            is_meeting = rmeet or is_meeting     # resolver/safety-net can promote to meeting

        # record the resolved row for workflow detection (Phase B)
        stream.append({"ts": r["ts_norm"] or r["ts"], "sub": sub, "coarse": coarse,
                       "is_meeting": is_meeting, "is_idle": is_idle,
                       "active_s": a, "idle_s": i, "app": r["app"], "domain": r["domain"]})

        if is_meeting and not is_idle:
            L.meeting_s += a + i
            top["meeting"] = top.get("meeting", 0) + a + i
            # meeting time is intentionally NOT app/domain-attributed: shows in top, not breakdown
            continue
        if is_idle:
            if i <= short_max:
                L.idle_short_s += i
            else:
                L.idle_long_s += i
            continue
        # active, non-meeting segment
        if sub in on_task:
            L.on_task_active_s += a
        elif coarse == C.CoarseClass.DISTRACTING.value:
            L.distract_active_s += a
        else:
            L.other_active_s += a
        # engagement inputs (active segments only)
        L.key_count += r["key_count"] or 0
        L.mouse_count += r["mouse_count"] or 0
        L.mouse_px += r["mouse_distance_px"] or 0
        if a > 0:
            L.focus_segments_s.append(a)
        # tiny in-segment gaps count as forgiven idle
        L.idle_short_s += i
        top[sub] = top.get(sub, 0) + a
        child_label = r["domain"] or r["app"] or "unknown"
        child_kind = "domain" if r["domain"] else "app"
        cat = bd.setdefault(sub, {"secs": 0.0, "coarse": coarse, "children": {}})
        cat["secs"] += a
        ch = cat["children"].setdefault(child_label, {"secs": 0.0, "kind": child_kind})
        ch["secs"] += a

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


def role_target_score(conn, user_fk: int):
    """The user's role target, or None when the role is uncalibrated (§10 #8).

    None is the signal "no validated bar yet" → forces coaching presentation
    (present_insight in server/api.py). A concrete number is set by an admin
    calibrating the role in /admin/roles.
    """
    row = conn.execute(
        "SELECT r.target_score FROM app_user au "
        "LEFT JOIN role r ON r.id = au.role_fk WHERE au.id=?", (user_fk,),
    ).fetchone()
    return row["target_score"] if row and row["target_score"] is not None else None


# Engine fallback when a role is uncalibrated: the engine signature wants a float, but the
# fact that we're uncalibrated is carried separately (extra["calibrated"]) so presentation,
# not the math, decides whether a verdict is ever shown.
_UNCALIBRATED_FALLBACK = 70.0


def compute_day_full(conn, user_fk: int, day: str):
    """Compute without persisting. Returns (ledger, insight, extra).

    extra carries calibration state so the API can gate verdicts without touching the
    pure engine DayInsight: {top, calibrated, target_score, tasks}.
    """
    L, extra, stream = build_ledger(conn, user_fk, day)
    tgt = role_target_score(conn, user_fk)
    ins = engine.compute_day(
        L, role_target_score=(tgt if tgt is not None else _UNCALIBRATED_FALLBACK))
    # Phase B: detect workflow/task instances from the resolved stream (coaching-only).
    templates = db.workflow_templates(conn)
    tasks = workflows.detect_tasks(stream, templates) if templates else []
    extra = {**extra, "calibrated": tgt is not None, "target_score": tgt, "tasks": tasks}
    return L, ins, extra


def persist_day(conn, user_fk: int, day: str, L, ins, extra) -> None:
    conn.execute(
        """INSERT INTO daily_agg
           (user_fk, day, present_s, active_s, on_task_s, meeting_s, distract_s,
            idle_short_s, idle_long_s, adherence, distract_ratio, focus_quality,
            score, engagement, data_completeness, top_json, computed_ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(user_fk, day) DO UPDATE SET
            present_s=excluded.present_s, active_s=excluded.active_s,
            on_task_s=excluded.on_task_s, meeting_s=excluded.meeting_s,
            distract_s=excluded.distract_s, idle_short_s=excluded.idle_short_s,
            idle_long_s=excluded.idle_long_s, adherence=excluded.adherence,
            distract_ratio=excluded.distract_ratio, focus_quality=excluded.focus_quality,
            score=excluded.score, engagement=excluded.engagement,
            data_completeness=excluded.data_completeness,
            top_json=excluded.top_json, computed_ts=excluded.computed_ts""",
        (user_fk, day, round(L.present_s), round(L.active_s), round(L.on_task_s),
         round(L.meeting_s), round(L.distract_active_s), round(L.idle_short_s),
         round(L.idle_long_s), ins.adherence, ins.distract_ratio, ins.focus_quality,
         ins.score, ins.engagement, ins.data_completeness, json.dumps(extra), db.now_iso()),
    )
    conn.commit()


def rollup_day(conn, user_fk: int, day: str) -> engine.DayInsight:
    L, ins, extra = compute_day_full(conn, user_fk, day)
    persist_day(conn, user_fk, day, L, ins, extra)
    return ins


def rollup_all(conn, day: str | None = None) -> int:
    day = day or dt.date.today().isoformat()
    n = 0
    for u in conn.execute("SELECT id FROM app_user").fetchall():
        rollup_day(conn, u["id"], day)
        n += 1
    return n
