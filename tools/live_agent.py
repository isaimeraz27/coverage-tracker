"""Live agent simulator — exercises the REAL agent pipeline over the wire,
without a Windows machine.

It feeds scripted Samples through the actual code path:
  MockCapture -> redaction.redact_sample -> Segmenter (focus/idle/meeting, §3.4/§3.5)
  -> Outbox (offline buffer) -> Shipper (gzip + eat_ token) -> POST /api/v1/enroll + /ingest
The only thing swapped vs a real Windows agent is the OS capture backend — every
other line of agent + server code runs for real.

  # with the dashboard already running (python3 server/run.py):
  python3 tools/live_agent.py --user sim_sam
  python3 tools/live_agent.py --user sim_sam --stream   # drip events so the dashboard updates live
"""
from __future__ import annotations

import os
import sys
import time
import argparse
import tempfile
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.capture import Sample, MockCapture          # noqa: E402
from agent.agent import Segmenter                       # noqa: E402
from agent import redaction, shipper                    # noqa: E402
from agent.buffer import Outbox                         # noqa: E402
from server import db, auth                             # noqa: E402

# (minutes, process, domain, idle_ms[, url]) — a realistic mixed day for one person.
# The optional 5th element is a full URL, exercised when full-URL capture is enabled.
DEFAULT_PROFILE = [
    (110, "code", None, 0),                 # deep work
    (25,  "chrome", "github.com", 0),       # code review
    (8,   "explorer", None, 300_000),       # short break (forgiven idle)
    (55,  "zoom", None, 0),                 # meeting (is_meeting)
    (35,  "chrome", "reddit.com", 0),       # distraction
    (20,  "chrome", "youtube.com", 0),      # distraction
    (90,  "code", None, 0),                 # deep work
    (15,  "slack", None, 0),                # comms
    (40,  "explorer", None, 1_500_000),     # long idle (away)
    (30,  "keepass", None, 0),              # sensitive app — should be DROPPED by redaction
]

# An insurance PRODUCER's day, with full URLs so url_path taxonomy rules + workflow
# templates fire. Two new-business quotes (dialer→CRM→rater→carrier→AMS→e-sign), a
# meeting, some distraction, a long lunch, and a dropped password-manager block.
PRODUCER_PROFILE = [
    (20, "chrome", "dialer.example.com", 0, "https://dialer.example.com/calls/active"),
    (15, "chrome", "crm.example.com", 0, "https://crm.example.com/leads/823"),
    (25, "chrome", "example-rater.com", 0, "https://example-rater.com/quotes/55/rating"),
    (15, "chrome", "progressive.example.com", 0, "https://progressive.example.com/quote/auto"),
    (10, "chrome", "ams.example.com", 0, "https://ams.example.com/policy/9912"),
    (8,  "docusign", "docusign.example.com", 0, "https://docusign.example.com/sign/abc"),
    (12, "chrome", "youtube.com", 0, "https://youtube.com/watch?v=x"),  # distraction
    (50, "explorer", None, 1_500_000),     # lunch (long idle / away)
    (18, "chrome", "crm.example.com", 0, "https://crm.example.com/leads/904"),
    (22, "chrome", "example-rater.com", 0, "https://example-rater.com/quotes/61/rating"),
    (12, "chrome", "travelers.example.com", 0, "https://travelers.example.com/quote"),
    (10, "chrome", "ams.example.com", 0, "https://ams.example.com/policy/9920"),
    (45, "zoom", None, 0),                  # team meeting
    (30, "keepass", None, 0),               # sensitive — dropped by redaction
]

PROFILES = {"default": DEFAULT_PROFILE, "producer": PRODUCER_PROFILE}

SAMPLE_INTERVAL_S = 60.0  # each scripted sample represents one minute


def build_samples(profile) -> list[Sample]:
    # anchor at today 09:00 UTC so events roll up under today's date
    base = dt.datetime.combine(dt.date.today(), dt.time(9, 0), dt.timezone.utc).timestamp()
    out = []
    idx = 0
    for row in profile:
        minutes, proc, dom, idle_ms = row[0], row[1], row[2], row[3]
        url = row[4] if len(row) > 4 else None
        for _ in range(minutes):
            out.append(Sample(ts=base + idx * SAMPLE_INTERVAL_S, process=proc,
                              window_title=f"{proc} window", domain=dom, idle_ms=idle_ms,
                              key_count=120, mouse_count=40, url=url))
            idx += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8765")
    ap.add_argument("--user", default="sim_sam")
    ap.add_argument("--machine", default="ws-sim-01")
    ap.add_argument("--code", default="", help="enrollment code (else one is minted locally for convenience)")
    ap.add_argument("--db", default=db.DEFAULT_DB, help="db path used only to mint a code if --code omitted")
    ap.add_argument("--profile", default="default", choices=list(PROFILES),
                    help="'producer' = an insurance producer's full-URL day (quotes/rater/carrier/AMS)")
    ap.add_argument("--stream", action="store_true", help="drip batches with pauses so the dashboard updates live")
    args = ap.parse_args()
    full_url = args.profile == "producer"   # the producer profile ships full URLs

    code = args.code
    if not code:
        # Convenience: mint a code the way an admin would. In production this is a
        # separate admin action; the agent only ever sees the code, never the DB.
        conn = db.connect(args.db)
        db.init_db(conn)
        code = auth.issue_enrollment_code(conn, args.machine)
        conn.close()
        print(f"[admin step] issued enrollment code for {args.machine}: {code}")

    token = shipper.enroll(args.server, code, args.machine.upper())
    if not token:
        print("ENROLL FAILED — is the server running at", args.server, "?")
        sys.exit(1)
    print(f"[agent] enrolled, token {token[:18]}…")

    capture = MockCapture(build_samples(PROFILES[args.profile]))
    seg = Segmenter(allow_full_url=full_url)
    outbox = Outbox(tempfile.mktemp(suffix="_outbox.db"))
    sh = shipper.Shipper(args.server, token, args.user)

    dropped = produced = 0
    acc = ded = 0
    batch_n = 0
    while True:
        s = capture.sample()
        if s is None:
            break
        s = redaction.redact_sample(s, allow_full_url=full_url)   # capture-time privacy
        if s is None:
            dropped += 1
            continue
        for ev in seg.feed(s, SAMPLE_INTERVAL_S):
            outbox.enqueue(ev)
            produced += 1
        # ship every ~50 produced events (or in stream mode, periodically)
        if outbox.depth() >= 50:
            acc, ded, batch_n = _ship(sh, outbox, acc, ded, batch_n, args.stream)
    for ev in seg.flush():
        outbox.enqueue(ev)
        produced += 1
    acc, ded, batch_n = _ship(sh, outbox, acc, ded, batch_n, args.stream)

    print(f"[agent] done. focus/idle events produced={produced}  shipped accepted={acc} deduped={ded}")
    print(f"[privacy] samples dropped by redaction (sensitive apps): {dropped}")
    print(f"\nNow refresh the dashboard — user '{args.user}' appears. "
          f"(zoom -> meeting, reddit/youtube -> distracting, keepass dropped, the 25-min away -> long idle)")


def _ship(sh, outbox, acc, ded, batch_n, stream):
    batch = outbox.pull(200)
    if not batch:
        return acc, ded, batch_n
    try:
        r = sh.post([ev for _, ev in batch])
        outbox.ack([i for i, _ in batch])
        acc += r.get("accepted", 0)
        ded += r.get("deduped", 0)
        batch_n += 1
        print(f"  [ship #{batch_n}] accepted={r.get('accepted')} deduped={r.get('deduped')}")
        if stream:
            time.sleep(3)
    except Exception as e:
        print("  [ship] failed (buffered, will retry):", e)
    return acc, ded, batch_n


if __name__ == "__main__":
    main()
