"""Batched, gzipped, token-authenticated uploader to the §3.7 ingest surface.
Stdlib urllib only; TLS is provided by the server URL (https in production)."""
from __future__ import annotations

import gzip
import json
import secrets
import urllib.request
import urllib.error


def get_config(server_url: str, token: str) -> dict | None:
    """Fetch the dashboard-controlled work-hours window + poll interval."""
    url = server_url.rstrip("/") + "/api/v1/agent-config?token=" + token
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, ValueError):
        return None


def enroll(server_url: str, code: str, hostname: str, disclosure_version=None) -> str | None:
    payload = {"code": code, "hostname": hostname}
    if disclosure_version is not None:
        payload["disclosure_version"] = disclosure_version
    body = json.dumps(payload).encode()
    req = urllib.request.Request(server_url.rstrip("/") + "/api/v1/enroll",
                                 data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("token")
    except urllib.error.HTTPError:
        return None


class Shipper:
    def __init__(self, server_url: str, token: str, username: str):
        self.base = server_url.rstrip("/")
        self.token = token
        self.username = username

    def post(self, events: list[dict]) -> dict:
        envelope = {
            "agent_token": self.token,
            "username": self.username,
            "batch_id": secrets.token_hex(8),
            "events": events,
        }
        raw = gzip.compress(json.dumps(envelope).encode())
        req = urllib.request.Request(
            self.base + "/api/v1/ingest", data=raw,
            headers={"Content-Type": "application/json",
                     "Content-Encoding": "gzip",
                     "Idempotency-Key": envelope["batch_id"]},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def post_screenshot(self, image_id: str, blob: bytes) -> dict:
        url = f"{self.base}/api/v1/screenshots?token={self.token}&image_id={image_id}"
        req = urllib.request.Request(url, data=blob,
                                     headers={"Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
