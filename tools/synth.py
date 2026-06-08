"""Synthetic activity generator — populate the dashboard and tests WITHOUT any
real surveillance (spec §5 testing strategy).

Personas produce known second-ledgers so insights are predictable and the demo
shows a realistic spread: a strong day, a clearly-distracted day, a low-data day
that must read as 'not for evaluation', etc.
"""
from __future__ import annotations

import os
import sys
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import contracts as C  # noqa: E402

# role -> on_task fine categories (§3.3)
ROLES = {
    "developer": ["dev_tools", "code_review", "technical_docs", "work_comms"],
    "designer": ["design", "research", "technical_docs", "work_comms"],
    "support": ["work_comms", "research", "technical_docs"],
}

# Each persona's day as {sub_category: hours}. Special keys: meeting, idle_short, idle_long.
PERSONAS = [
    {"username": "ana", "display": "Ana Reyes", "role": "developer",
     "buckets": {"dev_tools": 4.0, "code_review": 0.5, "technical_docs": 0.4,
                 "work_comms": 0.6, "meeting": 1.0, "social": 0.3,
                 "idle_short": 0.3, "idle_long": 0.4}},
    {"username": "beto", "display": "Beto Cruz", "role": "developer", "focus_block": 300,
     "buckets": {"dev_tools": 1.5, "work_comms": 0.3, "meeting": 0.5,
                 "social": 2.5, "streaming": 1.0, "idle_long": 2.0}},
    {"username": "carla", "display": "Carla Diaz", "role": "designer",
     "buckets": {"design": 5.0, "research": 0.8, "meeting": 0.7, "work_comms": 0.3,
                 "social": 0.2, "idle_short": 0.3}},
    {"username": "diego", "display": "Diego Mora", "role": "support", "intensity": 0.15,
     "buckets": {"work_comms": 5.0, "research": 0.5, "meeting": 1.0, "news": 0.4,
                 "idle_short": 0.4}},  # looks busy but barely typing -> low_engagement note
    {"username": "eva", "display": "Eva Luna", "role": "developer",
     "buckets": {"dev_tools": 1.0, "meeting": 0.3, "idle_short": 0.1}},  # partial day -> low confidence
]

_SUB_APP = {  # a representative app per category, just for realism in titles
    "dev_tools": "code", "code_review": "chrome", "technical_docs": "chrome",
    "work_comms": "slack", "design": "figma", "research": "chrome",
    "social": "chrome", "streaming": "chrome", "news": "chrome", "meeting": "zoom",
}
_SUB_DOMAIN = {"code_review": "github.com", "technical_docs": "notion.so",
               "research": "stackoverflow.com", "social": "reddit.com",
               "streaming": "youtube.com", "news": "cnn.com"}


def _chunks(total: float, size: float) -> list[float]:
    out, left = [], total
    while left > 1:
        out.append(min(size, left))
        left -= size
    return out


def build_events(day: str, buckets: dict, focus_block_s: float = 1500.0,
                 intensity: float = 1.0) -> list[dict]:
    """Turn an {sub: hours} profile into canonical ActivityEvent dicts for `day`.

    Active time is split into realistic focus blocks (focus_block_s drives the
    FocusQuality signal — small blocks = fragmented = lower score). idle_short is
    split into <=5-min runs so it is correctly classified as forgiven idle (§3.4);
    idle_long stays one continuous away-run.
    """
    base = dt.datetime.fromisoformat(day + "T09:00:00+00:00")
    events: list[dict] = []
    cursor = 0.0

    def emit(sub, secs, state, is_meeting=False):
        nonlocal cursor
        ts = (base + dt.timedelta(seconds=cursor)).isoformat()
        cursor += secs
        if state == C.ActivityState.IDLE.value:
            events.append(C.to_wire(C.ActivityEvent(
                ts=ts, app="explorer", sub_category=C.IDLE_CATEGORY,
                category_code=C.CoarseClass.IDLE.value, state=state,
                active_ms=0, idle_ms=int(secs * 1000))))
        else:
            prod = C.coarse_of(sub) == C.CoarseClass.PRODUCTIVE
            mins = secs / 60.0
            events.append(C.to_wire(C.ActivityEvent(
                ts=ts, app=_SUB_APP.get(sub, "chrome"), domain=_SUB_DOMAIN.get(sub),
                sub_category=sub, category_code=C.coarse_of(sub).value,
                state=state, active_ms=int(secs * 1000), idle_ms=0, is_meeting=is_meeting,
                # intensity scales engagement: meetings are low-input, work is high-input
                key_count=int(mins * (0 if is_meeting else (45 if prod else 12) * intensity)),
                mouse_count=int(mins * (0 if is_meeting else (8 if prod else 10) * intensity)),
                mouse_distance_px=int(mins * (0 if is_meeting else 1800) * intensity))))

    for sub, hours in buckets.items():
        secs = hours * 3600.0
        if sub == "idle_long":
            emit(sub, secs, C.ActivityState.IDLE.value)
        elif sub == "idle_short":
            for run in _chunks(secs, 300.0):       # <=5 min => forgiven
                emit(sub, run, C.ActivityState.IDLE.value)
        else:
            for blk in _chunks(secs, focus_block_s):
                emit(sub, blk, C.ActivityState.ACTIVE.value, is_meeting=(sub == "meeting"))
    return events


def insert_direct(conn, machine_fk: int, user_fk: int, events: list[dict]) -> None:
    """Insert events straight into the server DB (demo seeding / tests)."""
    for ev in events:
        conn.execute(
            """INSERT OR IGNORE INTO activity_event
               (machine_fk,user_fk,client_event_id,ts,ts_norm,app,window_title,domain,url,
                sub_category,category_code,state,active_ms,idle_ms,is_meeting,key_count,mouse_count,
                mouse_distance_px,suspect_time)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (machine_fk, user_fk, ev["client_event_id"], ev["ts"], ev["ts"], ev["app"],
             ev.get("window_title"), ev.get("domain"), ev.get("url"), ev["sub_category"],
             ev["category_code"], ev["state"], ev["active_ms"], ev["idle_ms"],
             1 if ev["is_meeting"] else 0, ev["key_count"], ev["mouse_count"],
             ev.get("mouse_distance_px", 0)))
    conn.commit()
