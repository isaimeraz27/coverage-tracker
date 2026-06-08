"""Start the dashboard + ingest server.

    python server/run.py            # http://127.0.0.1:8765
Env: PORT, TRACKER_DB. Front with a TLS-terminating reverse proxy in production
(the spec requires HTTPS-only, LAN/VPN-only).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import api, db  # noqa: E402

if __name__ == "__main__":
    api.run(int(os.environ.get("PORT", "8765")), os.environ.get("TRACKER_DB", db.DEFAULT_DB))
