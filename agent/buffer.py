"""Local SQLite outbox so data survives offline/network gaps (§7 agent).

Events are enqueued locally, shipped in batches, and removed only on server ack.
"""
from __future__ import annotations

import json
import sqlite3
import time


class Outbox:
    def __init__(self, path: str = "agent_outbox.db"):
        # timeout: if a stray second instance ever touches the file, wait rather than
        # fail the write (a swallowed "database is locked" would silently drop events).
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS outbox("
            "id INTEGER PRIMARY KEY, client_event_id TEXT UNIQUE, payload TEXT, queued_ts REAL)"
        )
        self.conn.commit()

    def enqueue(self, event: dict) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO outbox(client_event_id, payload, queued_ts) VALUES (?,?,?)",
            (event.get("client_event_id"), json.dumps(event), time.time()),
        )
        self.conn.commit()

    def pull(self, limit: int = 200) -> list[tuple[int, dict]]:
        rows = self.conn.execute(
            "SELECT id, payload FROM outbox ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
        return [(r[0], json.loads(r[1])) for r in rows]

    def ack(self, ids: list[int]) -> None:
        if not ids:
            return
        self.conn.executemany("DELETE FROM outbox WHERE id=?", [(i,) for i in ids])
        self.conn.commit()

    def depth(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
