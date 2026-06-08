"""Workflow / task detection — group a resolved activity stream into task instances.

A producer's day is a stream of focus segments (sub-categories). An admin defines
"workflow templates" — e.g. a *new-business quote* = touches {rating, carrier_portal,
ams} within a 30-minute window. This walks the ordered stream and emits task instances
with duration, tool-switches, re-opens, and an on-task ratio.

PURE: rows in, task dicts out. No DB, no engine coupling. Lives outside the scoring
engine on purpose — it must never feed a pass/fail verdict.

PROTOTYPE QUALITY — and deliberately so. The open/close heuristic and windows are tuned
against the simulator, so detection is somewhat self-fulfilling until it sees real
producer capture. One tool serving two stages (a rater used for both new-business and
endorsements) will confuse set-membership. Treat all output as COACHING / exploratory,
never as an evaluation. Calibrate thresholds against real data later.
"""
from __future__ import annotations

# An idle gap longer than this (seconds) breaks a task in two (someone walked away).
_BREAK_IDLE_S = 600


def _instance(template, members):
    """Summarize a run of stream rows that belong to one task instance."""
    subs = [m["sub"] for m in members]
    apps = [m["app"] for m in members if m.get("app")]
    duration_s = sum(m["active_s"] + m["idle_s"] for m in members)
    # on-task ratio within the instance = active time in this template's step set / active time
    step_set = set(template["steps"])
    active = sum(m["active_s"] for m in members)
    on_step = sum(m["active_s"] for m in members if m["sub"] in step_set)
    # tool switches = adjacent app changes
    switches = sum(1 for x, y in zip(apps, apps[1:]) if x != y)
    hit = set(s for s in subs if s in step_set)
    required = set(s for s, req in template["required"].items() if req)
    if template["match_mode"] == "sequence":
        matched = _sequence_ok(subs, template["order"])
    else:
        matched = required.issubset(hit)
    exp = template.get("expected_duration_s")
    return {
        "template": template["name"],
        "start": members[0]["ts"],
        "end": members[-1]["ts"],
        "duration_s": round(duration_s),
        "tool_switches": switches,
        "on_task_ratio": round(on_step / active, 3) if active > 0 else 0.0,
        "matched": bool(matched),
        "steps_hit": sorted(hit),
        "steps_missing": sorted(required - hit),
        "vs_expected": (round(duration_s / exp, 2) if exp else None),
    }


def _sequence_ok(subs, order):
    """True if `order` appears as a subsequence of the seen subs."""
    it = iter(subs)
    return all(any(s == step for s in it) for step in order)


def detect_tasks(stream, templates):
    """Group the ordered resolved stream into task instances.

    stream: list of {ts, sub, coarse, is_meeting, is_idle, active_s, idle_s, app, domain}
            (as produced by rollup.build_ledger, ordered by time).
    templates: list of dicts with keys: name, match_mode, window_s, expected_duration_s,
               steps (list[str]), required (dict[str,bool]), order (list[str]).
    Returns a list of task-instance dicts. Adds reopen_count per template name.
    """
    if not templates or not stream:
        return []
    instances = []
    for tmpl in templates:
        step_set = set(tmpl["steps"])
        open_members = []
        last_hit_idx = None
        for row in stream:
            if row["is_idle"]:
                # a long idle breaks an open task
                if open_members and row["idle_s"] > _BREAK_IDLE_S:
                    instances.append(_instance(tmpl, open_members))
                    open_members = []
                    last_hit_idx = None
                elif open_members:
                    open_members.append(row)  # short idle counts toward the task
                continue
            in_template = row["sub"] in step_set
            if in_template:
                # If the required set is already complete and a required step reappears,
                # treat it as a NEW task instance (the producer started another quote).
                required = set(s for s, req in tmpl["required"].items() if req)
                hit = set(m["sub"] for m in open_members if m["sub"] in step_set)
                if open_members and required and required.issubset(hit) and row["sub"] in required:
                    instances.append(_instance(tmpl, open_members[: last_hit_idx + 1]))
                    open_members = []
                    last_hit_idx = None
                open_members.append(row)
                last_hit_idx = len(open_members) - 1
            elif open_members:
                # a non-template active segment: keep the task open but watch the window.
                # close if we've drifted past window_s since the last template hit.
                span = sum(m["active_s"] + m["idle_s"] for m in open_members[last_hit_idx + 1:]) \
                    if last_hit_idx is not None else 0
                if span > tmpl["window_s"]:
                    instances.append(_instance(tmpl, open_members[: last_hit_idx + 1]))
                    open_members = []
                    last_hit_idx = None
                else:
                    open_members.append(row)
        if open_members and last_hit_idx is not None:
            instances.append(_instance(tmpl, open_members[: last_hit_idx + 1]))

    # reopen_count: how many instances exist per template (a producer who quotes twice)
    counts: dict = {}
    for inst in instances:
        counts[inst["template"]] = counts.get(inst["template"], 0) + 1
    for inst in instances:
        inst["reopen_count"] = counts[inst["template"]] - 1
    return instances
