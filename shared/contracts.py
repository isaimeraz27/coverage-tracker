"""Shared canonical contracts — the single source of truth for agent and server.

Implements the NORMATIVE §3 model from the spec:
  §3.2 canonical event taxonomy (4 kinds)
  §3.3 two-level category model (fine sub_category -> coarse class)
  §3.4 two-tier idle model (input gap + short/long thresholds)
  §3.6 retention schedule
  §3.7 ingest/enrollment/token surface
  §3.9 on-task definition

Pure stdlib so it imports identically on the Windows agent and the dashboard host.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

SCHEMA_VERSION = "1.0.0"

# --------------------------------------------------------------------------- #
# §3.2  Canonical event taxonomy
# --------------------------------------------------------------------------- #


class EventKind(str, Enum):
    ACTIVITY = "activity"        # one focus segment (active app span OR pure-idle span)
    ATTENDANCE = "attendance"    # session lifecycle
    SCREENSHOT = "screenshot"    # metadata for one image (bytes uploaded separately, §3.7)
    AGENT_HEALTH = "agent_health"  # heartbeat


class ActivityState(str, Enum):
    ACTIVE = "active"
    IDLE = "idle"


class AttendanceSubtype(str, Enum):
    LOGON = "logon"
    LOGOFF = "logoff"
    LOCK = "lock"
    UNLOCK = "unlock"
    SUSPEND = "suspend"
    RESUME = "resume"


# --------------------------------------------------------------------------- #
# §3.3  Two-level category model
# --------------------------------------------------------------------------- #


class CoarseClass(str, Enum):
    PRODUCTIVE = "productive"
    NEUTRAL = "neutral"
    DISTRACTING = "distracting"
    IDLE = "idle"


# Fine category -> coarse class. Admin-editable at runtime via the `category`
# table; this is the seed. `meeting` is coarse-neutral but the time ledger
# treats it as present-and-excused (§3.5/§3.9).
CATEGORY_SEED: dict[str, CoarseClass] = {
    # productive
    "dev_tools": CoarseClass.PRODUCTIVE,
    "code_review": CoarseClass.PRODUCTIVE,
    "technical_docs": CoarseClass.PRODUCTIVE,
    "work_comms": CoarseClass.PRODUCTIVE,
    "design": CoarseClass.PRODUCTIVE,
    "research": CoarseClass.PRODUCTIVE,
    "office_docs": CoarseClass.PRODUCTIVE,
    # neutral
    "meeting": CoarseClass.NEUTRAL,
    "file_management": CoarseClass.NEUTRAL,
    "system": CoarseClass.NEUTRAL,
    "uncategorized": CoarseClass.NEUTRAL,
    # distracting
    "social": CoarseClass.DISTRACTING,
    "streaming": CoarseClass.DISTRACTING,
    "shopping": CoarseClass.DISTRACTING,
    "news": CoarseClass.DISTRACTING,
    "gaming": CoarseClass.DISTRACTING,
    # idle
    "idle": CoarseClass.IDLE,
}

MEETING_CATEGORY = "meeting"
IDLE_CATEGORY = "idle"
UNCATEGORIZED = "uncategorized"


def coarse_of(sub_category: str) -> CoarseClass:
    return CATEGORY_SEED.get(sub_category, CoarseClass.NEUTRAL)


# Default app/process/domain -> fine category mapping (seed; admin-editable).
# Process names are matched lowercased without extension; domains by suffix.
APP_CATEGORY_SEED: dict[str, str] = {
    # dev tools
    "code": "dev_tools", "devenv": "dev_tools", "idea64": "dev_tools",
    "pycharm64": "dev_tools", "sublime_text": "dev_tools", "rider64": "dev_tools",
    "windowsterminal": "dev_tools", "powershell": "dev_tools", "cmd": "dev_tools",
    "cursor": "dev_tools",
    # comms
    "outlook": "work_comms", "slack": "work_comms", "teams": "work_comms",
    # meetings (also flagged is_meeting via CONFERENCING_MATCH)
    "zoom": "meeting", "webex": "meeting",
    # office / design
    "winword": "office_docs", "excel": "office_docs", "powerpnt": "office_docs",
    "figma": "design", "blender": "design", "photoshop": "design",
    # explorer / system
    "explorer": "file_management",
}

DOMAIN_CATEGORY_SEED: dict[str, str] = {
    "github.com": "code_review", "gitlab.com": "code_review",
    "stackoverflow.com": "research", "docs.python.org": "research",
    "confluence": "technical_docs", "notion.so": "technical_docs",
    "mail.google.com": "work_comms", "outlook.office.com": "work_comms",
    "youtube.com": "streaming", "netflix.com": "streaming", "twitch.tv": "streaming",
    "twitter.com": "social", "x.com": "social", "facebook.com": "social",
    "instagram.com": "social", "reddit.com": "social", "tiktok.com": "social",
    "amazon.com": "shopping", "ebay.com": "shopping",
    "cnn.com": "news", "espn.com": "news",
    "meet.google.com": "meeting", "teams.microsoft.com": "meeting",
}

# §3.5 conferencing match -> processes/domains that set is_meeting=true
CONFERENCING_PROCESSES = {"zoom", "teams", "webex", "bluejeans", "gotomeeting"}
CONFERENCING_DOMAINS = {"meet.google.com", "teams.microsoft.com", "zoom.us"}


def categorize(process: Optional[str], domain: Optional[str]) -> tuple[str, bool]:
    """Return (fine_sub_category, is_meeting). Domain wins over process for browsers."""
    proc = (process or "").lower().removesuffix(".exe")
    dom = (domain or "").lower()
    is_meeting = proc in CONFERENCING_PROCESSES or any(
        dom == d or dom.endswith("." + d) for d in CONFERENCING_DOMAINS
    )
    sub = None
    if dom:
        # exact, then suffix match
        sub = DOMAIN_CATEGORY_SEED.get(dom)
        if sub is None:
            for d, c in DOMAIN_CATEGORY_SEED.items():
                if dom == d or dom.endswith("." + d):
                    sub = c
                    break
    if sub is None and proc:
        sub = APP_CATEGORY_SEED.get(proc)
    if is_meeting:
        sub = MEETING_CATEGORY
    return (sub or UNCATEGORIZED, is_meeting)


# --------------------------------------------------------------------------- #
# §3.4 / §3.6 / §3.10  Canonical config defaults (single key set)
# --------------------------------------------------------------------------- #

CONFIG_DEFAULTS = {
    # capture / idle (§3.4)
    "poll_ms": 5000,
    "idle.input_gap_s": 180,      # active -> idle after this much no-input
    "idle.short_max_s": 600,      # idle run <= this is forgiven (idle_short)
    "focus_target_s": 1500,       # 25 min target focus block (FocusQuality)
    # privacy (§3.3/§3.8)
    "strip_urls": True,           # domain-only by default
    # screenshots: DORMANT capability — removed from the default capture/ship path.
    # This key IS the switch (server/api.py reads it); flip to True only per-role
    # with legal sign-off to re-enable later.
    "screenshots.enabled": False,
    "screenshots.interval_s": 600,
    # engagement (cursor + clicks) — secondary intensity signal, NOT part of the score
    "engagement.ref_input_per_min": 45,   # active-input rate treated as 100% engaged (heuristic, calibrate)
    "flag.low_engagement": 0.20,          # below this engagement_index -> informational note only
    # insights score weights (§3.9 illustrative formula — NORMATIVE here)
    "score.w_adherence": 0.5,
    "score.w_nondistract": 0.3,
    "score.w_focus": 0.2,
    # flag thresholds (§9, admitted heuristics — calibrate in pilot)
    "flag.distract_ratio": 0.25,
    "flag.distract_secs": 7200,    # 2h
    "flag.idle_long_secs": 7200,   # 2h of long-idle
    "flag.low_adherence": 0.50,
    # confidence / human-in-the-loop (§3.10)
    "confidence.min_completeness": 0.60,
    "work_window_s": 8 * 3600,
}

# §3.6 retention schedule (days) — binding governance table
RETENTION_DAYS = {
    "raw_events": 45,
    "titles_urls": 14,     # title/url columns nulled at 14d even though event survives to 45d
    "screenshots": 7,      # hard cap 14
    "screenshots_cap": 14,
    "aggregates": 395,     # ~13 months
    "audit": 365,          # minimum
}

# §3.7 token format
TOKEN_PREFIX = "eat_"


def make_agent_token(machine_id: str) -> str:
    return f"{TOKEN_PREFIX}{machine_id}_{uuid.uuid4().hex}"


def token_machine_id(token: str) -> Optional[str]:
    if not token.startswith(TOKEN_PREFIX):
        return None
    rest = token[len(TOKEN_PREFIX):]
    parts = rest.rsplit("_", 1)
    return parts[0] if len(parts) == 2 else None


def new_client_event_id() -> str:
    return uuid.uuid4().hex


_HOSTNAME_RE = re.compile(r"[^a-z0-9._-]")


def normalize_machine_id(hostname: str) -> str:
    return _HOSTNAME_RE.sub("", hostname.strip().lower())


def normalize_username(username: str) -> str:
    return username.strip().lower()


def registrable_domain(url_or_host: str) -> Optional[str]:
    """Reduce a URL/host to its registrable domain (§3 strip_urls)."""
    if not url_or_host:
        return None
    s = url_or_host.strip()
    s = re.sub(r"^[a-z]+://", "", s, flags=re.I)
    s = s.split("/")[0].split("?")[0].split("#")[0]
    s = s.split("@")[-1].split(":")[0].lower()
    parts = [p for p in s.split(".") if p]
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return s or None


# --------------------------------------------------------------------------- #
# Wire DTOs (what the agent ships; §3.2). Dataclasses keep agent+server in sync.
# --------------------------------------------------------------------------- #


@dataclass
class ActivityEvent:
    kind: str = EventKind.ACTIVITY.value
    client_event_id: str = field(default_factory=new_client_event_id)
    ts: str = ""                       # ISO8601, agent local time
    app: str = ""                      # process image name (lowercased, no ext)
    window_title: Optional[str] = None
    domain: Optional[str] = None       # registrable domain (default) ...
    url: Optional[str] = None          # ... full URL only if role-enabled (§3.3)
    sub_category: str = UNCATEGORIZED
    category_code: str = CoarseClass.NEUTRAL.value
    state: str = ActivityState.ACTIVE.value
    active_ms: int = 0
    idle_ms: int = 0                   # §3.4 — explicit, never collapsed to a flag
    is_meeting: bool = False
    key_count: int = 0                 # COUNTS ONLY — never content (§ scope)
    mouse_count: int = 0               # click count
    mouse_distance_px: int = 0         # cursor travel during the segment (engagement signal)


@dataclass
class AttendanceEvent:
    kind: str = EventKind.ATTENDANCE.value
    client_event_id: str = field(default_factory=new_client_event_id)
    ts: str = ""
    subtype: str = AttendanceSubtype.LOGON.value


@dataclass
class ScreenshotEvent:  # DORMANT — not produced by the default agent (see api.SCREENSHOTS_ENABLED)
    kind: str = EventKind.SCREENSHOT.value
    client_event_id: str = field(default_factory=new_client_event_id)
    ts: str = ""
    image_id: str = ""
    monitor: int = 0
    width: int = 0
    height: int = 0
    phash: str = ""
    redacted: bool = True


@dataclass
class AgentHealthEvent:
    kind: str = EventKind.AGENT_HEALTH.value
    client_event_id: str = field(default_factory=new_client_event_id)
    ts: str = ""
    agent_version: str = "0.1.0"
    cpu_pct: float = 0.0
    buffer_depth: int = 0
    clock_skew_s: float = 0.0


def to_wire(ev) -> dict:
    return asdict(ev)
