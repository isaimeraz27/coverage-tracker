"""Build a browsable DEMO DB: roles, an admin login, an enrolled machine, the five
synthetic personas across several days, then roll up. No real data involved.

OPT-IN demo/QA scaffolding ONLY — production starts EMPTY and is configured via the
first-run admin flow in the dashboard. The demo logins (admin/admin, manager/manager)
and personas (Ana/Beto/…) must never ship in a real deployment, so this refuses to run
without the explicit --demo flag.

    python tools/seed.py --demo     # seeds data/tracker.db with synthetic personas
    python server/run.py            # then browse http://127.0.0.1:8765 (admin/admin)
"""
from __future__ import annotations

import os
import sys
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import db, auth, rollup  # noqa: E402
from shared import contracts as C  # noqa: E402
from tools import synth  # noqa: E402


def seed(db_path: str = db.DEFAULT_DB, days: int = 3) -> None:
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = db.connect(db_path)
    db.init_db(conn)

    # roles + on_task sets
    role_ids = {name: db.ensure_role(conn, name, on_task) for name, on_task in synth.ROLES.items()}

    # one enrolled machine
    mfk = db.resolve_machine(conn, "ws-demo-01", auto_provision=True, hostname="WS-DEMO-01")
    conn.execute("UPDATE machine SET token=?, enrolled_ts=? WHERE id=?",
                 (C.make_agent_token("ws-demo-01"), db.now_iso(), mfk))

    # admin + one scoped manager
    auth.create_manager(conn, "admin", "admin", role="admin")
    mgr_id = auth.create_manager(conn, "manager", "manager", role="manager")

    # users
    uids = {}
    for p in synth.PERSONAS:
        ufk = db.resolve_user(conn, mfk, p["username"], auto_provision=True)
        conn.execute("UPDATE app_user SET display_name=?, role_fk=? WHERE id=?",
                     (p["display"], role_ids[p["role"]], ufk))
        uids[p["username"]] = ufk
    # manager sees ana + beto only (demo RBAC)
    for uname in ("ana", "beto"):
        conn.execute("INSERT OR IGNORE INTO manager_scope(manager_fk,user_fk) VALUES (?,?)",
                     (mgr_id, uids[uname]))
    conn.commit()

    today = dt.date.today()
    for d in range(days):
        day = (today - dt.timedelta(days=d)).isoformat()
        for p in synth.PERSONAS:
            events = synth.build_events(day, p["buckets"], p.get("focus_block", 1500),
                                        p.get("intensity", 1.0))
            synth.insert_direct(conn, mfk, uids[p["username"]], events)
        rollup.rollup_all(conn, day)

    print(f"seeded {db_path}")
    print(f"  machine: ws-demo-01   users: {', '.join(uids)}   days: {days}")
    print(f"  logins:  admin/admin (sees all)   manager/manager (sees ana, beto)")


if __name__ == "__main__":
    if "--demo" not in sys.argv:
        print("Refusing to seed synthetic demo data without --demo.\n"
              "Production starts empty: run the server and complete the first-run admin setup\n"
              "in the dashboard instead. To seed the demo personas for local QA:\n"
              "    python tools/seed.py --demo")
        sys.exit(1)
    seed()
