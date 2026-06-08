"""Per-user rolling baselines from daily_agg (coaching mode's comparison point).

Coaching mode compares a person to THEIR OWN recent history, not a fixed target. This
computes a simple trailing-window mean for a metric from the already-persisted daily_agg
rows, plus today's delta vs that mean. Cheap (<=10 users, <=14 rows each) — no extra table.
"""
from __future__ import annotations

METRICS = {"score", "adherence", "distract_ratio", "engagement", "focus_quality"}


def baseline(conn, user_fk: int, day: str, metric: str = "score", window: int = 14) -> dict:
    """Mean of `metric` over the `window` days BEFORE `day` (today excluded), and the
    trend = today's value minus that mean. Returns {mean, n, today, trend} with mean/trend
    None when there's no history yet (a brand-new person has no baseline to compare to).
    """
    if metric not in METRICS:  # metric is whitelisted, so safe to inline into SQL
        raise ValueError(f"unknown metric {metric!r}")
    prior = conn.execute(
        f"SELECT {metric} AS v FROM daily_agg "
        f"WHERE user_fk=? AND day<? AND {metric} IS NOT NULL "
        "ORDER BY day DESC LIMIT ?",
        (user_fk, day, window),
    ).fetchall()
    vals = [r["v"] for r in prior if r["v"] is not None]
    today_row = conn.execute(
        f"SELECT {metric} AS v FROM daily_agg WHERE user_fk=? AND day=?",
        (user_fk, day),
    ).fetchone()
    today = today_row["v"] if today_row else None
    if not vals:
        return {"mean": None, "n": 0, "today": today, "trend": None}
    mean = sum(vals) / len(vals)
    trend = (today - mean) if today is not None else None
    return {
        "mean": round(mean, 4),
        "n": len(vals),
        "today": round(today, 4) if today is not None else None,
        "trend": round(trend, 4) if trend is not None else None,
    }
