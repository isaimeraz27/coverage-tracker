"""Insights & Analytics Engine — the core that answers "who's on-task / who's
wasting time", implemented per the NORMATIVE §3.9 definitions.

Pure functions over a per-user-per-day second-ledger. No ML; every number traces
to a config-driven formula (§9 design intent). The §3.9 worked example is the
canonical unit-test fixture (see tests/test_insights.py).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import contracts as C  # noqa: E402

CFG = C.CONFIG_DEFAULTS


@dataclass
class DayLedger:
    """Mutually-exclusive second buckets for one user-day (§3.9 time ledger).

    on_task_active_s + distract_active_s + other_active_s = active_s.
    The on/off-task split is decided upstream (rollup) using the role on_task_set.
    """
    on_task_active_s: float = 0.0     # active in role on_task_set (excl meeting)
    distract_active_s: float = 0.0    # active, coarse=distracting
    other_active_s: float = 0.0       # active, neither on-task nor distracting
    meeting_s: float = 0.0            # conferencing foreground (present & excused)
    idle_short_s: float = 0.0         # idle runs <= idle.short_max_s (forgiven)
    idle_long_s: float = 0.0          # idle runs >  idle.short_max_s (away)
    focus_segments_s: list[float] = field(default_factory=list)  # active-segment durations
    work_window_s: float = float(CFG["work_window_s"])
    suspect_time_s: float = 0.0       # clock-skew flagged time
    # engagement inputs (active segments only) — secondary signal, NOT in the score
    key_count: int = 0
    mouse_count: int = 0              # clicks
    mouse_px: float = 0.0             # cursor travel

    @property
    def active_s(self) -> float:
        return self.on_task_active_s + self.distract_active_s + self.other_active_s

    @property
    def present_s(self) -> float:
        # §3.9: engaged/excused time; idle_long & away excluded from denominator
        return self.active_s + self.meeting_s + self.idle_short_s

    @property
    def on_task_s(self) -> float:
        # §3.9 canonical: role on_task categories + meeting
        return self.on_task_active_s + self.meeting_s


@dataclass
class Flag:
    code: str
    severity: str          # 'low' | 'med' | 'high'
    positive: bool
    message: str


@dataclass
class DayInsight:
    score: float
    adherence: float           # == dashboard on_task_pct (§3.9)
    distract_ratio: float
    focus_quality: float
    present_s: float
    active_s: float
    on_task_s: float
    meeting_s: float
    idle_long_s: float
    data_completeness: float
    confidence: float
    needs_context: bool        # §3.10 — refuse to conclude on thin data
    role_target_score: float   # absolute target shown beside any baseline delta
    engagement: float          # 0..1 input intensity during active time (secondary; not in score)
    flags: list[Flag]
    attention: bool            # compound gate; never an automatic discipline signal


def focus_quality(segments_s: list[float], target_s: float) -> float:
    if not segments_s:
        return 0.0
    avg = sum(segments_s) / len(segments_s)
    return max(0.0, min(1.0, avg / target_s))


def _div(n: float, d: float) -> float:
    return (n / d) if d > 0 else 0.0


def engagement_index(ledger: "DayLedger", cfg: dict) -> float:
    """Input intensity during ACTIVE time, normalized to 0..1. Cursor travel is
    folded in (~300px ≈ one input unit). Heuristic ref rate — calibrate (§10 #8).
    Secondary signal only: it is NOT a term in the productivity score."""
    active_min = ledger.active_s / 60.0
    if active_min <= 0:
        return 0.0
    units = ledger.key_count + ledger.mouse_count + ledger.mouse_px / 300.0
    rate = units / active_min
    return max(0.0, min(1.0, rate / cfg["engagement.ref_input_per_min"]))


def compute_day(
    ledger: DayLedger,
    cfg: dict = CFG,
    role_target_score: float = 70.0,
    focus_quality_override: Optional[float] = None,
) -> DayInsight:
    present = ledger.present_s
    adherence = _div(ledger.on_task_s, present)
    distract_ratio = _div(ledger.distract_active_s, present)
    fq = focus_quality_override if focus_quality_override is not None else \
        focus_quality(ledger.focus_segments_s, cfg["focus_target_s"])

    # §3.9 normative score
    score = 100.0 * (
        cfg["score.w_adherence"] * adherence
        + cfg["score.w_nondistract"] * (1.0 - distract_ratio)
        + cfg["score.w_focus"] * fq
    )
    score = max(0.0, min(100.0, score))

    # §3.10 confidence / needs-context
    data_completeness = min(1.0, _div(present, ledger.work_window_s))
    confidence = data_completeness
    if ledger.suspect_time_s > 0.1 * max(present, 1):
        confidence *= 0.5
    needs_context = (
        data_completeness < cfg["confidence.min_completeness"]
        # meeting-heavy with no calendar integration -> always wants a human look
        or ledger.meeting_s > 0.4 * max(present, 1)
        # lots of long-idle but we can't see why (phone/offsite meeting blindness, §3.5)
        or ledger.idle_long_s > 0.5 * max(ledger.work_window_s, 1)
    )

    eng = engagement_index(ledger, cfg)
    flags = _flags(ledger, adherence, distract_ratio, fq, eng, cfg)
    # 'info' severity (e.g. low_engagement) never trips the gate — secondary signal
    neg = [f for f in flags if not f.positive and f.severity in ("med", "high")]
    attention = (len(neg) >= 2) or any(f.severity == "high" for f in neg)

    return DayInsight(
        score=round(score, 1),
        adherence=round(adherence, 4),
        distract_ratio=round(distract_ratio, 4),
        focus_quality=round(fq, 4),
        present_s=present,
        active_s=ledger.active_s,
        on_task_s=ledger.on_task_s,
        meeting_s=ledger.meeting_s,
        idle_long_s=ledger.idle_long_s,
        data_completeness=round(data_completeness, 4),
        confidence=round(confidence, 4),
        needs_context=needs_context,
        role_target_score=role_target_score,
        engagement=round(eng, 4),
        flags=flags,
        attention=attention,
    )


def _flags(ledger, adherence, distract_ratio, fq, eng, cfg) -> list[Flag]:
    out: list[Flag] = []
    hrs = lambda s: f"{s/3600:.1f}h"
    if ledger.distract_active_s > cfg["flag.distract_secs"] or distract_ratio > cfg["flag.distract_ratio"]:
        out.append(Flag(
            "distracting_excess", "high", False,
            f"{hrs(ledger.distract_active_s)} in distracting apps "
            f"({distract_ratio*100:.0f}% of present time)."))
    if ledger.idle_long_s > cfg["flag.idle_long_secs"]:
        out.append(Flag(
            "high_idle", "high", False,
            f"{hrs(ledger.idle_long_s)} of long idle (>10-min gaps) — needs context."))
    if adherence < cfg["flag.low_adherence"] and ledger.present_s > 0:
        out.append(Flag(
            "low_adherence", "med", False,
            f"On-task {adherence*100:.0f}% — below the role expectation."))
    if ledger.active_s > 3600 and eng < cfg["flag.low_engagement"]:
        out.append(Flag(
            "low_engagement", "info", False,
            f"Low input intensity ({eng*100:.0f}%) despite {hrs(ledger.active_s)} active — "
            f"screen open but little keyboard/mouse (context, not a verdict)."))
    if adherence >= 0.80 and distract_ratio < 0.10 and fq >= 0.70:
        out.append(Flag(
            "strong_focus", "low", True,
            f"Strong day: on-task {adherence*100:.0f}%, low distraction, focused blocks."))
    return out


def attention_with_persistence(history: list[DayInsight]) -> bool:
    """§9 compound gate including persistence: attention if any day trips the
    single-day gate AND it persists >=3 of the last 5 days (resists one bad day)."""
    if not history:
        return False
    last5 = history[-5:]
    tripped = sum(1 for d in last5 if d.attention)
    return tripped >= 3
