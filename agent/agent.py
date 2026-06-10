"""Agent core: the Segmenter (pure, testable) + the runtime loop.

Segmenter folds periodic samples into the canonical event stream (§3.2):
focus segments (state=active) and idle spans (state=idle with explicit idle_ms,
§3.4). is_meeting is set from the conferencing match (§3.5). It is pure — no I/O —
so it is unit-tested on any OS.
"""
from __future__ import annotations

import os
import sys
import time
import datetime as dt
from typing import Optional

import logging  # noqa: E402

if not getattr(sys, "frozen", False):   # frozen exe already has shared/agent bundled
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import contracts as C  # noqa: E402
from agent import capture as cap, redaction, buffer  # noqa: E402
from agent.paths import data_path  # noqa: E402

CFG = C.CONFIG_DEFAULTS
log = logging.getLogger("agent")


def _iso(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat()


class Segmenter:
    def __init__(self, idle_gap_s: int | None = None, allow_full_url: bool = False):
        self.gap_ms = (idle_gap_s if idle_gap_s is not None else CFG["idle.input_gap_s"]) * 1000
        self.allow_full_url = allow_full_url
        self.mode: Optional[str] = None
        self._reset_acc()

    def _reset_acc(self):
        self.app = None
        self.sub = C.UNCATEGORIZED
        self.domain = None
        self.is_meeting = False
        self.start_ts = None
        self.active_ms = 0
        self.idle_ms = 0
        self.key = 0
        self.mouse = 0
        self.dist = 0

    # -- public ------------------------------------------------------------ #
    def feed(self, sample: cap.Sample, interval_s: float) -> list[dict]:
        out: list[dict] = []
        inc = int(interval_s * 1000)
        idle = sample.idle_ms >= self.gap_ms
        sub, is_meeting = C.categorize(sample.process, sample.domain)

        if idle:
            if self.mode == "active":
                out.append(self._close())
                self.mode = None
            if self.mode is None:
                self._open(sample, sub, is_meeting, "idle")
            self.idle_ms += inc
        else:
            # Segment by (process, URL-or-domain, sub, meeting): different sites/URLs must
            # stay separate segments even when the agent can't categorize them (the server's
            # taxonomy resolves them later from domain/url). The full URL distinguishes
            # subdomain tools (dialer.x.com vs ams.x.com) that share a registrable domain —
            # without it, consecutive visits to different tools merge into one blob.
            site = getattr(sample, "url", None) or sample.domain
            key = (sample.process, site, sub, is_meeting)
            cur_site = getattr(self, "url", None) or getattr(self, "domain", None)
            cur = (self.app, cur_site, self.sub, self.is_meeting)
            if self.mode == "idle":
                out.append(self._close())
                self.mode = None
            if self.mode == "active" and key != cur:
                out.append(self._close())
                self.mode = None
            if self.mode is None:
                self._open(sample, sub, is_meeting, "active")
            self.active_ms += inc
            self.key += sample.key_count
            self.mouse += sample.mouse_count
            self.dist += sample.mouse_distance_px
        return out

    def flush(self) -> list[dict]:
        """Close the open segment (if any) and return it, leaving the segmenter in a
        clean state so it can be called repeatedly mid-stream (periodic checkpoint),
        not only once at exit."""
        if not self.mode:
            return []
        ev = self._close()
        self.mode = None
        self.url = None
        return [ev]

    # -- internal ---------------------------------------------------------- #
    def _open(self, sample, sub, is_meeting, mode):
        self.mode = mode
        self.app = sample.process or self.app
        self.sub = sub
        self.domain = sample.domain
        self.url = getattr(sample, "url", None)
        self.is_meeting = is_meeting
        self.start_ts = _iso(sample.ts)
        self.active_ms = self.idle_ms = self.key = self.mouse = self.dist = 0

    def _close(self) -> dict:
        if self.mode == "idle":
            ev = C.ActivityEvent(
                ts=self.start_ts, app=self.app or "", window_title=None, domain=None,
                sub_category=C.IDLE_CATEGORY, category_code=C.CoarseClass.IDLE.value,
                state=C.ActivityState.IDLE.value, active_ms=0, idle_ms=self.idle_ms,
                is_meeting=False)
        else:
            ev = C.ActivityEvent(
                ts=self.start_ts, app=self.app or "", domain=self.domain,
                url=getattr(self, "url", None),
                sub_category=self.sub, category_code=C.coarse_of(self.sub).value,
                state=C.ActivityState.ACTIVE.value, active_ms=self.active_ms, idle_ms=0,
                is_meeting=self.is_meeting, key_count=self.key, mouse_count=self.mouse,
                mouse_distance_px=self.dist)
        self._reset_acc()
        return C.to_wire(ev)


# --------------------------------------------------------------------------- #
# Runtime loop (best-effort; not part of the unit-tested surface)
# --------------------------------------------------------------------------- #


DEFAULT_WH = {"work_start": 8, "work_end": 18, "work_days": [0, 1, 2, 3, 4], "poll_ms": CFG["poll_ms"]}

# Force-close the open segment into the outbox at least this often. Without it, a long
# single-app focus never emits (a segment only closes on an app/URL change or idle flip),
# and an abrupt process kill (logoff/shutdown send an UNCATCHABLE terminate on Windows —
# no graceful flush) loses the whole open segment. Checkpointing bounds that loss and makes
# data land during steady work. The server's rollup sums the chunks, so granularity is fine.
SEGMENT_CHECKPOINT_S = 60


def within_work_hours(now: dt.datetime, wh: dict) -> bool:
    """Honor the dashboard-set tracking window — day-of-week + start/end hour."""
    return now.weekday() in wh["work_days"] and wh["work_start"] <= now.hour < wh["work_end"]


def run_agent(server_url: str, token: str, username: str,
              poll_s: float | None = None, ship_every: int = 12):  # pragma: no cover
    from agent.shipper import Shipper, get_config
    wh = get_config(server_url, token) or dict(DEFAULT_WH)
    poll_s = poll_s or wh.get("poll_ms", CFG["poll_ms"]) / 1000.0
    full_url = bool(wh.get("full_url", False))
    capture = cap.make_capture(allow_full_url=full_url)
    outbox = buffer.Outbox(data_path("agent_outbox.db"))  # absolute — logon task CWD is unwritable
    shipper = Shipper(server_url, token, username)
    seg = Segmenter(allow_full_url=full_url)
    outbox.enqueue(C.to_wire(C.AttendanceEvent(ts=_iso(time.time()),
                                               subtype=C.AttendanceSubtype.LOGON.value)))
    last = time.time()
    last_cfg = time.time()
    last_checkpoint = time.time()
    ticks = 0
    print(f"agent running for {username} -> {server_url}  (hours {wh['work_start']}:00–{wh['work_end']}:00)")
    try:
        while True:
            now = dt.datetime.now()
            if time.time() - last_cfg > 600:           # refresh work-hours every 10 min
                try:
                    nc = get_config(server_url, token)
                    if nc:
                        wh = nc
                        full_url = bool(wh.get("full_url", False))
                except Exception as e:
                    log.warning("config refresh failed: %s", e)
                last_cfg = time.time()
            if not within_work_hours(now, wh):          # outside business hours -> don't track
                time.sleep(30)
                continue
            try:
                s = capture.sample()
                if s is not None:
                    s = redaction.redact_sample(s, allow_full_url=full_url)
                if s is not None:
                    interval = time.time() - last
                    last = time.time()
                    for ev in seg.feed(s, interval):
                        outbox.enqueue(ev)
            except Exception as e:           # one bad tick must never kill the agent
                log.warning("capture tick failed: %s", e)
            ticks += 1
            # Periodically checkpoint the open segment so its data lands even during a
            # long single-app focus and survives an abrupt kill (see SEGMENT_CHECKPOINT_S).
            if time.time() - last_checkpoint > SEGMENT_CHECKPOINT_S:
                for ev in seg.flush():
                    outbox.enqueue(ev)
                last_checkpoint = time.time()
            if ticks % ship_every == 0:
                try:
                    _flush(outbox, shipper)
                except Exception as e:
                    log.warning("flush failed (will retry): %s", e)
            time.sleep(poll_s)
    except KeyboardInterrupt:
        for ev in seg.flush():
            outbox.enqueue(ev)
        _flush(outbox, shipper)


def _flush(outbox, shipper):  # pragma: no cover
    batch = outbox.pull(200)
    if not batch:
        return
    try:
        shipper.post([ev for _, ev in batch])
        outbox.ack([i for i, _ in batch])
    except Exception as e:
        print("ship failed, will retry:", e)  # buffered, survives offline
