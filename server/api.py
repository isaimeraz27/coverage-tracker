"""HTTP API + dashboard (stdlib http.server).

Agent surface (§3.7): POST /api/v1/enroll, /api/v1/ingest, /api/v1/screenshots.
Manager surface: /login, /, /person, JSON reads — all audited (§3.6/§3.10).

NOTE: a single-threaded stdlib server is fine for <=10 agents + a few managers.
For production, front with TLS (reverse proxy) — the spec requires HTTPS-only.
"""
from __future__ import annotations

import os
import sys
import io
import json
import time
import zipfile
import secrets
import threading
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import contracts as C  # noqa: E402
from server import db, auth, rollup, baselines, taxonomy  # noqa: E402
from insights import engine  # noqa: E402

CFG = C.CONFIG_DEFAULTS
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOT_DIR = os.path.join(_BASE, "data", "screenshots")
BRAND_DIR = os.path.join(_BASE, "dashboard", "brand")
WEB_DIST = os.path.join(_BASE, "web", "dist")   # built React SPA (npm run build)

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".ico": "image/x-icon", ".woff": "font/woff",
    ".woff2": "font/woff2", ".webmanifest": "application/manifest+json", ".map": "application/json",
}

# light hardening (internal tool; TLS handled at the domain/proxy)
MAX_INGEST_BODY = 5_000_000        # 5 MB per batch
MAX_BATCH_EVENTS = 1000
_RL: dict = {}                     # (bucket, ip) -> [timestamps]
RATE_LIMITS = {"ingest": (300, 60), "enroll": (20, 60), "setup": (20, 60), "download": (30, 60)}
# The prebuilt standalone agent — built on the Windows VM (scripts/build_agent.ps1) and
# placed here; served at /download/agent.exe. This is the production delivery path.
AGENT_EXE = os.path.join(_BASE, "dist", "coverage-agent.exe")

# LEGACY/DEV fallback: the Python-source zip at /download/agent.zip. The installer no longer
# points at this (the .exe replaced it); kept for dev + a couple of tests. Remove once the
# exe flow is proven in production.
AGENT_BUNDLE = ["shared/contracts.py", "agent/agent.py", "agent/capture.py",
                "agent/redaction.py", "agent/buffer.py", "agent/shipper.py",
                "agent/browser_url.py", "agent/paths.py", "scripts/run_agent.py"]

# PowerShell self-serve installer: downloads ONE standalone .exe — no Python, no pip, no admin.
# Registers a per-user logon task and starts it hidden. Used by BOTH delivery paths (the
# admin Machines page and employee self-enroll) since both build the one-liner from /install.ps1.
_PS_INSTALL = r"""# Coverage activity agent - self-serve install (no admin, no Python required)
$ErrorActionPreference = 'Stop'
$Server = '__SERVER__'
$Code   = '__CODE__'
$Dir = Join-Path $env:LOCALAPPDATA 'CoverageAgent'
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$Exe = Join-Path $Dir 'coverage-agent.exe'
Write-Host 'Downloading Coverage agent...'
Invoke-WebRequest -Uri "$Server/download/agent.exe" -OutFile $Exe
# config.json (next to the exe) carries the server URL + enrollment code the agent reads.
(@{ server = $Server; code = $Code } | ConvertTo-Json) | Set-Content -Path (Join-Path $Dir 'config.json')
# run at every logon as the current user (no admin needed)
schtasks /Create /TN 'CoverageAgent' /TR ('"' + $Exe + '"') /SC ONLOGON /F | Out-Null
# start now, hidden (a console exe started hidden shows no window)
Start-Process -FilePath $Exe -WindowStyle Hidden
Write-Host 'Coverage agent installed. It runs at each login and tracks only during business hours.'
"""
SESSIONS: dict[str, dict] = {}   # sid -> manager row dict
# Local viewing convenience: TRACKER_NO_AUTH=1 skips the login gate and treats the
# viewer as a read-all admin. For local/demo only — never set in production.
NO_AUTH = bool(os.environ.get("TRACKER_NO_AUTH"))
DEV_ADMIN = {"id": None, "username": "local (no-auth)", "role": "admin"}
AUTO_PROVISION_USERS = True      # machines must enroll; their users auto-provision
AUTO_PROVISION_MACHINES = False  # §3.1 unknown machine rejected by default
# DORMANT capability — single source of truth is the documented config key (off by default).
# Re-enable per-role only with legal sign-off.
SCREENSHOTS_ENABLED = bool(C.CONFIG_DEFAULTS.get("screenshots.enabled", False))


def insight_dict(ins) -> dict:
    """Raw, mode-agnostic serialization of a DayInsight. NOTE: never expose this directly
    as an accountability read — go through present_insight() so coaching mode can strip the
    verdict/target. The raw role_target_score here is the engine's fallback, not a UI target.
    """
    return {
        "score": ins.score, "adherence": ins.adherence, "distract_ratio": ins.distract_ratio,
        "focus_quality": ins.focus_quality, "present_s": ins.present_s, "active_s": ins.active_s,
        "on_task_s": ins.on_task_s, "meeting_s": ins.meeting_s, "idle_long_s": ins.idle_long_s,
        "data_completeness": ins.data_completeness, "confidence": ins.confidence,
        "needs_context": ins.needs_context,
        "engagement": ins.engagement, "attention": ins.attention,
        "low_confidence": ins.data_completeness < CFG["confidence.min_completeness"],
        "flags": [{"code": f.code, "severity": f.severity, "positive": f.positive,
                   "message": f.message} for f in ins.flags],
    }


def _mode(conn) -> str:
    """Org presentation mode: 'coaching' (default) | 'evaluative'."""
    m = db.get_setting(conn, "mode", "coaching")
    return m if m in ("coaching", "evaluative") else "coaching"


def present_insight(conn, ins, extra, user_fk=None, day=None) -> dict:
    """The ONLY place allowed to emit a pass/fail verdict or a target.

    Coaching mode — or ANY uncalibrated role — yields target=None, verdict=None and a
    `coaching` block (trend vs the person's own baseline + positive signals + check-in
    framing). Evaluative mode on a calibrated role yields target + verdict. The numeric
    score is always present (it is a trend value, not a judgment, until paired with a
    target). This is what makes "coaching until calibration" a real, leak-proof feature.
    """
    d = insight_dict(ins)
    mode = _mode(conn)
    calibrated = bool(extra.get("calibrated"))
    target = extra.get("target_score")
    show_verdict = mode == "evaluative" and calibrated and target is not None

    d["mode"] = mode
    d["calibrated"] = calibrated
    if show_verdict:
        d["target"] = target
        d["verdict"] = "pass" if ins.score >= target else "fail"
        d["coaching"] = None
    else:
        d["target"] = None
        d["verdict"] = None
        base = (baselines.baseline(conn, user_fk, day, "score")
                if user_fk is not None and day is not None
                else {"mean": None, "n": 0, "today": ins.score, "trend": None})
        d["coaching"] = {
            "trend": base["trend"],
            "baseline": base["mean"],
            "baseline_days": base["n"],
            "positive_signals": [f for f in d["flags"] if f["positive"]],
            "attention_framing": "worth a check-in" if (ins.attention or ins.needs_context) else None,
        }
    return d


class Handler(BaseHTTPRequestHandler):
    server_version = "EmployeeTracker/0.1"

    # -- helpers ----------------------------------------------------------- #
    @property
    def conn(self):
        return self.server.conn  # type: ignore[attr-defined]

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        if raw and self.headers.get("Content-Encoding", "").lower() == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        return raw

    def _json(self, code: int, obj) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self, code: int, body: str, headers: dict | None = None) -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to: str, headers: dict | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", to)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _session(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            if part.strip().startswith("sid="):
                s = SESSIONS.get(part.strip()[4:])
                if s:
                    return s
        return DEV_ADMIN if NO_AUTH else None

    def _json_body(self) -> dict:
        try:
            return json.loads(self._body() or b"{}")
        except json.JSONDecodeError:
            return {}

    def _require_admin(self):
        """Return the admin session row, or None after writing a 403 (caller returns)."""
        mgr = self._session()
        if not mgr or mgr.get("role") != "admin":
            self._json(403, {"error": "admin only"})
            return None
        return mgr

    def _rl(self, bucket: str) -> bool:
        """In-memory per-IP rate limit. Returns False when over the cap."""
        limit, window = RATE_LIMITS.get(bucket, (120, 60))
        key = (bucket, self.client_address[0])
        now = time.time()
        q = _RL.setdefault(key, [])
        while q and q[0] < now - window:
            q.pop(0)
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    def _base_url(self) -> str:
        host = self.headers.get("Host", "127.0.0.1:8765")
        proto = self.headers.get("X-Forwarded-Proto", "http")  # set by the TLS proxy in prod
        return f"{proto}://{host}"

    def log_message(self, *a):  # quieter
        pass

    # -- GET --------------------------------------------------------------- #
    def do_GET(self):
        with self.server.lock:  # type: ignore[attr-defined]
            return self._get()

    def do_POST(self):
        with self.server.lock:  # type: ignore[attr-defined]
            return self._post()

    def do_PUT(self):
        with self.server.lock:  # type: ignore[attr-defined]
            return self._put()

    def do_DELETE(self):
        with self.server.lock:  # type: ignore[attr-defined]
            return self._delete()

    def _put(self):
        u = urlparse(self.path)
        if u.path == "/api/v1/settings":
            return self._settings_put()
        if u.path.startswith("/api/v1/admin/"):
            return self._admin_put(u)
        return self._json(404, {"error": "not found"})

    def _delete(self):
        u = urlparse(self.path)
        if u.path.startswith("/api/v1/admin/"):
            return self._admin_delete(u)
        return self._json(404, {"error": "not found"})

    def _admin_delete(self, u):
        mgr = self._require_admin()
        if not mgr:
            return
        rid = self._path_id(u.path, "/api/v1/admin/taxonomy-rules/")
        if rid is not None and isinstance(rid, int):
            self.conn.execute("DELETE FROM taxonomy_rule WHERE id=?", (rid,))
            self.conn.commit()
            db.audit(self.conn, mgr.get("id"), "config.taxonomy", None, f"delete rule {rid}")
            return self._json(200, {"ok": True})
        tid = self._path_id(u.path, "/api/v1/admin/workflow-templates/")
        if tid is not None and isinstance(tid, int):
            self.conn.execute("DELETE FROM workflow_template_step WHERE template_fk=?", (tid,))
            self.conn.execute("DELETE FROM workflow_template WHERE id=?", (tid,))
            self.conn.commit()
            db.audit(self.conn, mgr.get("id"), "config.workflow", None, f"delete template {tid}")
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "not found"})

    def _get(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/healthz":
            return self._json(200, {"ok": True})
        if u.path == "/api/v1/bootstrap-status":
            return self._json(200, {
                "needs_admin": auth.count_managers(self.conn) == 0,
                "first_run_complete": db.get_setting(self.conn, "first_run_complete", "0") == "1",
                "org_name": db.get_setting(self.conn, "org_name", "Coverage"),
            })
        if u.path == "/api/v1/me":
            mgr = self._session()
            if not mgr:
                return self._json(401, {"error": "not authenticated"})
            return self._json(200, {"username": mgr["username"],
                                    "display_name": mgr.get("display_name") or mgr["username"],
                                    "role": mgr["role"]})
        if u.path.startswith("/brand/"):
            return self._serve_brand(os.path.basename(u.path))
        # ---- agent-facing public endpoints (token / code gated) ----
        if u.path == "/api/v1/agent-config":
            if auth.verify_agent_token(self.conn, q.get("token", [""])[0]) is None:
                return self._json(401, {"error": "invalid token"})
            return self._json(200, db.work_hours(self.conn))
        # GET /setup is now a React route (served by the SPA fallback); the POST handler stays.
        if u.path == "/install.ps1":
            return self._serve_install(q)
        if u.path == "/download/agent.exe":
            if not self._rl("download"):
                return self._json(429, {"error": "rate limited"})
            return self._serve_agent_exe()
        if u.path == "/download/agent.zip":   # legacy/dev fallback
            if not self._rl("download"):
                return self._json(429, {"error": "rate limited"})
            return self._serve_agent_zip()

        # ---- manager JSON data API (session-gated) ----
        if u.path == "/api/v1/overview":
            mgr = self._session()
            if not mgr:
                return self._json(401, {"error": "not authenticated"})
            day = q.get("day", [dt.date.today().isoformat()])[0]
            team, lanes = self._overview_data(mgr, day)
            return self._json(200, {"day": day, "team": team, "lanes": lanes})

        if u.path == "/api/v1/person":
            mgr = self._session()
            if not mgr:
                return self._json(401, {"error": "not authenticated"})
            day = q.get("day", [dt.date.today().isoformat()])[0]
            uid = int(q.get("uid", ["0"])[0])
            if not self._can_see(mgr, uid):
                return self._json(403, {"error": "forbidden"})
            person, ins_d, top, timeline, on_task, breakdown = self._person(uid, day)
            if person is None:
                return self._json(404, {"error": "no such user"})
            db.audit(self.conn, mgr.get("id"), "view_person", uid, f"day={day}")
            return self._json(200, {"person": person, "insight": ins_d, "top": top,
                                    "timeline": timeline, "on_task_set": sorted(on_task),
                                    "breakdown": breakdown})

        if u.path == "/api/v1/settings":
            if not self._require_admin():
                return
            return self._json(200, db.all_settings(self.conn))

        if u.path.startswith("/api/v1/admin/"):
            return self._admin_get(u, q, self._session() or {})

        # Unknown API path → JSON 404 (never the SPA shell).
        if u.path.startswith("/api/"):
            return self._json(404, {"error": "not found"})

        # Everything else → static file or the React SPA shell (client-side routing).
        return self._serve_spa(u.path)

    # -- admin GET reads ---------------------------------------------------- #
    def _admin_get(self, u, q, mgr):
        if not self._require_admin():
            return
        path = u.path
        if path == "/api/v1/admin/roles":
            rows = self.conn.execute(
                "SELECT id, name, target_score, calibrated_ts FROM role ORDER BY name").fetchall()
            out = []
            for r in rows:
                out.append({"id": r["id"], "name": r["name"],
                            "target_score": r["target_score"],
                            "calibrated": r["target_score"] is not None,
                            "calibrated_ts": r["calibrated_ts"],
                            "on_task_set": sorted(db.role_on_task_set(self.conn, r["id"]))})
            return self._json(200, {"roles": out})
        if path == "/api/v1/admin/categories":
            cats = self.conn.execute(
                "SELECT sub_category, coarse_class FROM category ORDER BY coarse_class, sub_category"
            ).fetchall()
            return self._json(200, {"categories": [
                {"sub_category": c["sub_category"], "coarse_class": c["coarse_class"]} for c in cats]})
        if path == "/api/v1/admin/users":
            rows = self.conn.execute(
                "SELECT au.id, au.username, au.display_name, au.tz, au.role_fk, "
                "r.name AS role, m.machine_id, m.hostname "
                "FROM app_user au LEFT JOIN role r ON r.id=au.role_fk "
                "LEFT JOIN machine m ON m.id=au.machine_fk ORDER BY au.username").fetchall()
            return self._json(200, {"users": [dict(r) for r in rows]})
        if path == "/api/v1/admin/managers":
            rows = self.conn.execute(
                "SELECT id, username, display_name, role FROM manager ORDER BY username").fetchall()
            out = []
            for r in rows:
                scope = [s["user_fk"] for s in self.conn.execute(
                    "SELECT user_fk FROM manager_scope WHERE manager_fk=?", (r["id"],)).fetchall()]
                out.append({**dict(r), "scope_user_ids": scope})
            return self._json(200, {"managers": out})
        if path == "/api/v1/admin/machines":
            rows = self.conn.execute(
                "SELECT machine_id, hostname, revoked, enrolled_ts, last_seen_ts FROM machine "
                "ORDER BY machine_id").fetchall()
            return self._json(200, {"machines": [dict(r) for r in rows]})
        if path == "/api/v1/admin/taxonomy-rules":
            rows = self.conn.execute(
                "SELECT id, match_type, pattern, sub_category, is_meeting, priority, enabled, notes "
                "FROM taxonomy_rule ORDER BY priority, match_type, pattern").fetchall()
            return self._json(200, {"rules": [dict(r) for r in rows]})
        if path == "/api/v1/admin/workflow-templates":
            return self._json(200, {"templates": self._workflow_templates_full()})
        return self._json(404, {"error": "not found"})

    def _workflow_templates_full(self):
        out = []
        for t in self.conn.execute(
            "SELECT id, name, match_mode, window_s, expected_duration_s, enabled, notes "
            "FROM workflow_template ORDER BY name").fetchall():
            steps = self.conn.execute(
                "SELECT sub_category, required, step_order FROM workflow_template_step "
                "WHERE template_fk=? ORDER BY step_order, sub_category", (t["id"],)).fetchall()
            out.append({**dict(t), "steps": [dict(s) for s in steps]})
        return out

    # -- POST -------------------------------------------------------------- #
    def _post(self):
        u = urlparse(self.path)
        if u.path == "/api/v1/enroll":
            return self._enroll()
        if u.path == "/api/v1/ingest":
            return self._ingest()
        if u.path == "/api/v1/screenshots":
            return self._screenshot()
        if u.path == "/api/v1/setup-admin":
            return self._setup_admin()
        if u.path == "/api/v1/self-enroll":
            return self._self_enroll()
        if u.path == "/api/v1/login":
            return self._login_json()
        if u.path == "/api/v1/logout":
            return self._logout()
        if u.path == "/api/v1/settings":
            return self._settings_put()
        if u.path.startswith("/api/v1/admin/"):
            return self._admin_post(u)
        return self._json(404, {"error": "not found"})

    # -- auth / bootstrap (JSON, for the React SPA) ------------------------- #
    def _new_session(self, row) -> str:
        sid = secrets.token_hex(16)
        SESSIONS[sid] = dict(row)
        return sid

    def _setup_admin(self):
        """First-run: create the first admin + write org config. 403 once any manager exists."""
        if auth.count_managers(self.conn) != 0:
            return self._json(403, {"error": "already set up"})
        d = self._json_body()
        username = (d.get("username") or "").strip()
        password = d.get("password") or ""
        if not username or not password:
            return self._json(400, {"error": "username and password required"})
        row = auth.create_first_admin(self.conn, username, password, d.get("display_name", ""))
        if row is None:
            return self._json(403, {"error": "already set up"})
        # org config from the same form
        if d.get("org_name"):
            db.set_setting(self.conn, "org_name", d["org_name"])
        if d.get("enroll_password"):
            db.set_setting(self.conn, "enroll_password", d["enroll_password"])
        mode = d.get("mode", "coaching")
        db.set_setting(self.conn, "mode", mode if mode in ("coaching", "evaluative") else "coaching")
        db.set_setting(self.conn, "first_run_complete", "1")
        db.audit(self.conn, row["id"], "setup_admin", None, f"org={d.get('org_name','')}")
        return self._json_with_session(200, {"ok": True, "role": "admin"}, row)

    def _json_with_session(self, code, obj, row):
        """Emit a JSON response that also opens a new manager session via Set-Cookie."""
        sid = self._new_session(row)
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Set-Cookie", f"sid={sid}; HttpOnly; Path=/; SameSite=Lax")
        self.end_headers()
        self.wfile.write(data)

    def _login_json(self):
        d = self._json_body()
        row = auth.verify_login(self.conn, d.get("username", ""), d.get("password", ""))
        if not row:
            return self._json(401, {"error": "invalid credentials"})
        return self._json_with_session(200, {"ok": True, "role": row["role"]}, row)

    def _self_enroll(self):
        """Employee self-serve: setup password -> one-time enrollment code + install one-liner."""
        if not self._rl("setup"):
            return self._json(429, {"error": "rate limited"})
        d = self._json_body()
        if not auth.check_enroll_password(self.conn, d.get("password", "")):
            return self._json(401, {"error": "incorrect setup password"})
        code = auth.issue_enrollment_code(self.conn, machine_id=None,
                                          label=d.get("name", "self-serve"))
        install = f"{self._base_url()}/install.ps1?server={self._base_url()}&code={code}"
        return self._json(200, {"code": code, "install_url": install,
                                "one_liner": f"irm '{install}' | iex"})

    def _logout(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            if part.strip().startswith("sid="):
                SESSIONS.pop(part.strip()[4:], None)
        self._json(200, {"ok": True})

    def _settings_put(self):
        if not self._require_admin():
            return
        d = self._json_body()
        allowed = {"work_start", "work_end", "work_days", "poll_ms", "org_name",
                   "enroll_password", "mode"}
        for k, v in d.items():
            if k not in allowed:
                continue
            if k == "mode" and v not in ("coaching", "evaluative"):
                return self._json(400, {"error": "mode must be coaching|evaluative"})
            if k == "work_days" and isinstance(v, list):
                v = ",".join(str(x) for x in v)
            db.set_setting(self.conn, k, str(v))
        db.audit(self.conn, self._session().get("id"), "change_config", None, "settings")
        return self._json(200, db.all_settings(self.conn))

    # -- admin writes ------------------------------------------------------- #
    @staticmethod
    def _path_id(path: str, prefix: str):
        """'/api/v1/admin/roles/3' with prefix '/api/v1/admin/roles/' -> 3 (or None)."""
        if not path.startswith(prefix):
            return None
        tail = path[len(prefix):].split("/", 1)[0]
        try:
            return int(tail)
        except ValueError:
            return tail or None

    def _admin_post(self, u):
        mgr = self._require_admin()
        if not mgr:
            return
        d = self._json_body()
        path = u.path
        if path == "/api/v1/admin/roles":
            name = (d.get("name") or "").strip()
            if not name:
                return self._json(400, {"error": "role name required"})
            rid = db.ensure_role(self.conn, name, list(d.get("on_task_set", [])))
            if d.get("target_score") is not None:
                self.conn.execute("UPDATE role SET target_score=?, calibrated_ts=? WHERE id=?",
                                  (float(d["target_score"]), db.now_iso(), rid))
                self.conn.commit()
            db.audit(self.conn, mgr.get("id"), "config.role", None, f"create {name}")
            return self._json(200, {"ok": True, "id": rid})
        if path == "/api/v1/admin/users":
            # pre-create a user against a known machine (usually they auto-provision on ingest)
            machine_id = C.normalize_machine_id(d.get("machine_id", ""))
            mfk = db.resolve_machine(self.conn, machine_id) if machine_id else None
            if mfk is None:
                return self._json(400, {"error": "unknown machine_id"})
            ufk = db.resolve_user(self.conn, mfk, C.normalize_username(d.get("username", "")),
                                  auto_provision=True)
            return self._json(200, {"ok": True, "id": ufk})
        if path == "/api/v1/admin/managers":
            uname = (d.get("username") or "").strip()
            if not uname or not d.get("password"):
                return self._json(400, {"error": "username and password required"})
            if self.conn.execute("SELECT 1 FROM manager WHERE username=?", (uname,)).fetchone():
                return self._json(409, {"error": "username taken"})
            mid = auth.create_manager(self.conn, uname, d["password"],
                                      role=d.get("role", "manager"),
                                      display_name=d.get("display_name", ""))
            if d.get("scope_user_ids"):
                auth.set_manager_scope(self.conn, mid, d["scope_user_ids"])
            db.audit(self.conn, mgr.get("id"), "rbac.create_manager", None, uname)
            return self._json(200, {"ok": True, "id": mid})
        if path == "/api/v1/admin/enroll-code":
            machine_id = (C.normalize_machine_id(d["machine_id"])
                          if d.get("machine_id") else None)
            code = auth.issue_enrollment_code(self.conn, machine_id=machine_id,
                                              label=d.get("label", ""))
            db.audit(self.conn, mgr.get("id"), "machine.enroll_code", None, d.get("label", ""))
            install = f"{self._base_url()}/install.ps1?server={self._base_url()}&code={code}"
            return self._json(200, {"code": code, "install_url": install,
                                    "one_liner": f"irm '{install}' | iex"})
        rev = self._path_id(path, "/api/v1/admin/machines/")
        if rev is not None and path.endswith("/revoke"):
            machine_id = path[len("/api/v1/admin/machines/"):-len("/revoke")]
            auth.revoke_machine(self.conn, machine_id)
            db.audit(self.conn, mgr.get("id"), "machine.revoke", None, machine_id)
            return self._json(200, {"ok": True})
        if path == "/api/v1/admin/taxonomy-rules":
            err = self._save_taxonomy_rule(d, mgr)
            return err if err else self._json(200, {"ok": True})
        if path == "/api/v1/admin/taxonomy-test":
            rules = db.taxonomy_rules(self.conn)
            sub, coarse, mtg = taxonomy.categorize_server(
                self.conn, rules, d.get("app"), d.get("domain"), d.get("url"))
            matched = None
            for r in rules:
                if taxonomy.match_rule(r, d.get("app"), d.get("domain"), d.get("url")):
                    matched = r
                    break
            return self._json(200, {
                "sub_category": sub, "coarse_class": coarse, "is_meeting": mtg,
                "matched_rule_id": matched["id"] if matched else None,
                "matched_pattern": matched["pattern"] if matched else None,
                "fallback": sub is None})
        if path == "/api/v1/admin/workflow-templates":
            return self._save_workflow_template(d, mgr)
        return self._json(404, {"error": "not found"})

    def _save_taxonomy_rule(self, d, mgr, rule_id=None):
        mt = d.get("match_type")
        pattern = (d.get("pattern") or "").strip()
        sub = (d.get("sub_category") or "").strip()
        if mt not in ("app", "domain", "url_path", "title") or not pattern or not sub:
            return self._json(400, {"error": "match_type, pattern, sub_category required"})
        # auto-register a new sub_category into the registry (default neutral)
        self.conn.execute("INSERT OR IGNORE INTO category(sub_category, coarse_class) VALUES (?,?)",
                          (sub, db.coarse_for(self.conn, sub)))
        ismtg = 1 if d.get("is_meeting") else 0
        pri = int(d.get("priority", 100))
        en = 0 if d.get("enabled") is False else 1
        notes = d.get("notes")
        if rule_id is None:
            self.conn.execute(
                "INSERT INTO taxonomy_rule(match_type,pattern,sub_category,is_meeting,priority,enabled,notes,created_ts,updated_ts) "
                "VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(match_type,pattern) DO UPDATE SET sub_category=excluded.sub_category, "
                "is_meeting=excluded.is_meeting, priority=excluded.priority, enabled=excluded.enabled, "
                "notes=excluded.notes, updated_ts=excluded.updated_ts",
                (mt, pattern, sub, ismtg, pri, en, notes, db.now_iso(), db.now_iso()))
        else:
            self.conn.execute(
                "UPDATE taxonomy_rule SET match_type=?,pattern=?,sub_category=?,is_meeting=?,"
                "priority=?,enabled=?,notes=?,updated_ts=? WHERE id=?",
                (mt, pattern, sub, ismtg, pri, en, notes, db.now_iso(), rule_id))
        self.conn.commit()
        db.audit(self.conn, mgr.get("id"), "config.taxonomy", None, f"{mt}:{pattern}->{sub}")
        return None

    def _save_workflow_template(self, d, mgr):
        name = (d.get("name") or "").strip()
        if not name:
            return self._json(400, {"error": "template name required"})
        steps = [(s["sub_category"], s.get("required", True), s.get("step_order", i))
                 for i, s in enumerate(d.get("steps", []))]
        db.ensure_workflow_template(
            self.conn, name, d.get("match_mode", "set_within_window"),
            int(d.get("window_s", 1800)), steps,
            expected_duration_s=d.get("expected_duration_s"), notes=d.get("notes"))
        db.audit(self.conn, mgr.get("id"), "config.workflow", None, f"template {name}")
        return self._json(200, {"ok": True})

    def _admin_put(self, u):
        mgr = self._require_admin()
        if not mgr:
            return
        d = self._json_body()
        path = u.path
        rid = self._path_id(path, "/api/v1/admin/roles/")
        if rid is not None and isinstance(rid, int):
            if "on_task_set" in d:
                self.conn.execute("DELETE FROM role_on_task WHERE role_fk=?", (rid,))
                for sub in d["on_task_set"]:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO role_on_task(role_fk, sub_category) VALUES (?,?)",
                        (rid, sub))
            if "target_score" in d:
                tgt = d["target_score"]
                if tgt is None or tgt == "":
                    self.conn.execute("UPDATE role SET target_score=NULL, calibrated_ts=NULL WHERE id=?", (rid,))
                else:
                    self.conn.execute("UPDATE role SET target_score=?, calibrated_ts=? WHERE id=?",
                                      (float(tgt), db.now_iso(), rid))
            self.conn.commit()
            db.audit(self.conn, mgr.get("id"), "config.role", None, f"edit role {rid}")
            return self._json(200, {"ok": True})
        uid = self._path_id(path, "/api/v1/admin/users/")
        if uid is not None and isinstance(uid, int):
            sets, params = [], []
            if "display_name" in d:
                sets.append("display_name=?"); params.append(d["display_name"])
            if "role_fk" in d:
                sets.append("role_fk=?"); params.append(d["role_fk"])
            if "tz" in d:
                sets.append("tz=?"); params.append(d["tz"])
            if sets:
                params.append(uid)
                self.conn.execute(f"UPDATE app_user SET {', '.join(sets)} WHERE id=?", params)
                self.conn.commit()
            db.audit(self.conn, mgr.get("id"), "config.user", uid, "edit user")
            return self._json(200, {"ok": True})
        mid = self._path_id(path, "/api/v1/admin/managers/")
        if mid is not None and isinstance(mid, int) and path.endswith("/scope"):
            auth.set_manager_scope(self.conn, mid, d.get("user_ids", []))
            db.audit(self.conn, mgr.get("id"), "rbac.scope", None, f"manager {mid}")
            return self._json(200, {"ok": True})
        trid = self._path_id(path, "/api/v1/admin/taxonomy-rules/")
        if trid is not None and isinstance(trid, int):
            err = self._save_taxonomy_rule(d, mgr, rule_id=trid)
            return err if err else self._json(200, {"ok": True})
        return self._json(404, {"error": "not found"})

    # -- agent endpoints --------------------------------------------------- #
    def _enroll(self):
        if not self._rl("enroll"):
            return self._json(429, {"error": "rate limited"})
        try:
            data = json.loads(self._body() or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        token = auth.enroll(self.conn, data.get("code", ""), data.get("hostname", ""))
        if not token:
            return self._json(403, {"error": "invalid or used enrollment code"})
        return self._json(200, {"token": token})

    def _ingest(self):
        if not self._rl("ingest"):
            return self._json(429, {"error": "rate limited"})
        if int(self.headers.get("Content-Length", 0) or 0) > MAX_INGEST_BODY:
            return self._json(413, {"error": "payload too large"})
        try:
            data = json.loads(self._body() or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        machine_fk = auth.verify_agent_token(self.conn, data.get("agent_token", ""))
        if machine_fk is None:
            return self._json(401, {"error": "invalid agent token"})
        username = C.normalize_username(data.get("username", ""))
        if not username:
            return self._json(400, {"error": "username required"})
        user_fk = db.resolve_user(self.conn, machine_fk, username, auto_provision=AUTO_PROVISION_USERS)
        if user_fk is None:
            return self._json(403, {"error": "unknown user"})

        skew = self.conn.execute("SELECT clock_skew_s FROM machine WHERE id=?", (machine_fk,)).fetchone()["clock_skew_s"]
        suspect = 1 if abs(skew or 0) > 120 else 0
        accepted = deduped = 0
        for ev in data.get("events", [])[:MAX_BATCH_EVENTS]:
            try:
                if self._store_event(machine_fk, user_fk, ev, suspect):
                    accepted += 1
                else:
                    deduped += 1
            except Exception:  # one bad event never fails the batch
                continue
        self.conn.execute("UPDATE machine SET last_seen_ts=? WHERE id=?", (db.now_iso(), machine_fk))
        self.conn.commit()
        return self._json(200, {"accepted": accepted, "deduped": deduped})

    def _store_event(self, machine_fk, user_fk, ev, suspect) -> bool:
        kind = ev.get("kind")
        cid = ev.get("client_event_id") or C.new_client_event_id()
        ts = ev.get("ts") or db.now_iso()
        ts_norm = ts  # skew-correction hook (§3.1); identity for now, suspect flag set above
        cur = self.conn.cursor()
        if kind == C.EventKind.ACTIVITY.value:
            sub = ev.get("sub_category") or C.UNCATEGORIZED
            r = cur.execute(
                """INSERT OR IGNORE INTO activity_event
                   (machine_fk,user_fk,client_event_id,ts,ts_norm,app,window_title,domain,url,
                    sub_category,category_code,state,active_ms,idle_ms,is_meeting,key_count,mouse_count,
                    mouse_distance_px,suspect_time)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (machine_fk, user_fk, cid, ts, ts_norm, ev.get("app"), ev.get("window_title"),
                 ev.get("domain"), ev.get("url"), sub, C.coarse_of(sub).value,
                 ev.get("state", "active"), ev.get("active_ms", 0), ev.get("idle_ms", 0),
                 1 if ev.get("is_meeting") else 0, ev.get("key_count", 0), ev.get("mouse_count", 0),
                 ev.get("mouse_distance_px", 0), suspect))
            return r.rowcount > 0
        if kind == C.EventKind.ATTENDANCE.value:
            r = cur.execute(
                "INSERT OR IGNORE INTO attendance_event(machine_fk,user_fk,client_event_id,ts,ts_norm,subtype) VALUES (?,?,?,?,?,?)",
                (machine_fk, user_fk, cid, ts, ts_norm, ev.get("subtype")))
            return r.rowcount > 0
        if kind == C.EventKind.AGENT_HEALTH.value:
            cur.execute("INSERT INTO agent_health(machine_fk,ts,agent_version,cpu_pct,buffer_depth,clock_skew_s) VALUES (?,?,?,?,?,?)",
                        (machine_fk, ts, ev.get("agent_version"), ev.get("cpu_pct"), ev.get("buffer_depth"), ev.get("clock_skew_s")))
            if ev.get("clock_skew_s") is not None:
                cur.execute("UPDATE machine SET clock_skew_s=? WHERE id=?", (ev["clock_skew_s"], machine_fk))
            return True
        if kind == C.EventKind.SCREENSHOT.value:
            if not SCREENSHOTS_ENABLED:
                return False                      # dormant capability — drop silently
            r = cur.execute(
                "INSERT OR IGNORE INTO screenshot(machine_fk,user_fk,image_id,taken_ts,monitor,width,height,phash,redacted) VALUES (?,?,?,?,?,?,?,?,?)",
                (machine_fk, user_fk, ev.get("image_id"), ts, ev.get("monitor"), ev.get("width"),
                 ev.get("height"), ev.get("phash"), 1 if ev.get("redacted", True) else 0))
            return r.rowcount > 0
        return False

    def _screenshot(self):
        # DORMANT capability — disabled by default. Re-enable per-role w/ legal sign-off.
        if not SCREENSHOTS_ENABLED:
            return self._json(403, {"error": "screenshots disabled"})
        q = parse_qs(urlparse(self.path).query)
        token = q.get("token", [""])[0]
        image_id = q.get("image_id", [""])[0]
        if auth.verify_agent_token(self.conn, token) is None or not image_id:
            return self._json(401, {"error": "unauthorized or missing image_id"})
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        path = os.path.join(SCREENSHOT_DIR, secrets.token_hex(8) + ".bin")
        with open(path, "wb") as fh:
            fh.write(self._body())  # TODO prod: AES-GCM at rest via OS KEK/DPAPI (stdlib has no AES)
        self.conn.execute("UPDATE screenshot SET stored_path=?, encrypted=0 WHERE image_id=?", (path, image_id))
        self.conn.commit()
        return self._json(200, {"stored": image_id})

    # -- read helpers ------------------------------------------------------ #
    def _can_see(self, mgr, uid) -> bool:
        vis = auth.visible_user_ids(self.conn, mgr)
        return vis is None or uid in vis

    def _overview_data(self, mgr, day):
        vis = auth.visible_user_ids(self.conn, mgr)
        users = self.conn.execute(
            "SELECT au.id, au.display_name, au.username, au.machine_fk, r.name role "
            "FROM app_user au LEFT JOIN role r ON r.id=au.role_fk").fetchall()
        needs, ontrack, lowconf = [], [], []
        adh_sum = adh_n = conf_sum = conf_n = 0
        for usr in users:
            if vis is not None and usr["id"] not in vis:
                continue
            L, ins, extra = rollup.compute_day_full(self.conn, usr["id"], day)
            rollup.persist_day(self.conn, usr["id"], day, L, ins, extra)
            card = self._build_card(usr, day, L, ins, extra)
            card["top"] = extra.get("top", [])
            conf_sum += ins.confidence; conf_n += 1
            if card["low_conf"]:
                lowconf.append(card)
            else:
                adh_sum += ins.adherence; adh_n += 1
                (needs if (ins.attention or ins.needs_context) else ontrack).append(card)
        for lane in (needs, ontrack, lowconf):
            lane.sort(key=lambda c: c["name"])   # unordered by name — never by score
        allc = needs + ontrack + lowconf
        mode = _mode(self.conn)
        # In coaching mode there is no team target to compare against — the KPI is a
        # descriptive average, not a bar. Only evaluative mode (with calibrated roles)
        # surfaces a target. Average the calibrated role targets when present.
        team_target = None
        if mode == "evaluative":
            trow = self.conn.execute(
                "SELECT AVG(target_score) AS t FROM role WHERE target_score IS NOT NULL"
            ).fetchone()
            team_target = trow["t"] if trow and trow["t"] is not None else None
        team = {
            "mode": mode,
            "on_task_pct": (adh_sum / adh_n * 100) if adh_n else 0.0,
            "target": team_target,
            "conf": (conf_sum / conf_n) if conf_n else 0.0,
            "n_needs": len(needs), "n_ontrack": len(ontrack), "n_total": len(allc),
            "n_active": sum(1 for c in allc if c["active"]),
            "as_of": dt.datetime.now().strftime("%H:%M"), "delta": None,
        }
        return team, {"needs": needs, "ontrack": ontrack, "lowconf": lowconf}

    def _build_card(self, usr, day, L, ins, extra):
        d = present_insight(self.conn, ins, extra, user_fk=usr["id"], day=day)
        last = self.conn.execute("SELECT last_seen_ts FROM machine WHERE id=?", (usr["machine_fk"],)).fetchone()
        active = False
        if last and last["last_seen_ts"]:
            try:
                ts = dt.datetime.fromisoformat(last["last_seen_ts"])
                active = (dt.datetime.now(dt.timezone.utc) - ts).total_seconds() < 900
            except ValueError:
                pass
        return {
            "uid": usr["id"], "day": day, "name": usr["display_name"] or usr["username"],
            "role": usr["role"], "score": ins.score,
            # verdict-bearing fields come ONLY from present_insight (None in coaching mode)
            "mode": d["mode"], "calibrated": d["calibrated"],
            "target": d["target"], "verdict": d["verdict"], "coaching": d["coaching"],
            "adherence": ins.adherence, "confidence": ins.confidence, "engagement": ins.engagement,
            "needs_context": ins.needs_context, "attention": ins.attention,
            "data_completeness": ins.data_completeness, "low_conf": d["low_confidence"],
            "persist": self._persistence(usr["id"], day) if ins.attention else False,
            "active": active, "flags": d["flags"],
            "buckets": {"on_task": L.on_task_active_s, "meeting": L.meeting_s,
                        "neutral": L.other_active_s, "distract": L.distract_active_s,
                        "idle": L.idle_short_s + L.idle_long_s},
        }

    def _persistence(self, uid, day):
        """Red rim only if attention persists >=3 of the last 5 days (§9 gate)."""
        base = dt.date.fromisoformat(day)
        hist = []
        for i in range(4, -1, -1):
            _, ins, _ = rollup.compute_day_full(self.conn, uid, (base - dt.timedelta(days=i)).isoformat())
            hist.append(ins)
        return engine.attention_with_persistence(hist)

    # UNUSED: kept for potential revival (workflow nudge unwired from person page, 2026-06-09).
    def _incomplete_workflow_nudge(self, uid, day):
        """Coaching-only: a producer who STARTS quote workflows but doesn't complete them
        (e.g. never saves to the AMS) on >=3 of the last 5 days. A single incomplete day is
        normal ('finishing it after lunch'), so we only surface the PERSISTENT pattern. This
        is an informational nudge, never a verdict — it never trips the attention gate.
        Returns None or a dict {days, recent_templates}."""
        base = dt.date.fromisoformat(day)
        days_with_incomplete = 0
        templates_seen = set()
        for i in range(5):
            d = (base - dt.timedelta(days=i)).isoformat()
            _, _, extra = rollup.compute_day_full(self.conn, uid, d)
            incompletes = [t for t in extra.get("tasks", []) if not t.get("matched")]
            if incompletes:
                days_with_incomplete += 1
                templates_seen.update(t["template"] for t in incompletes)
        if days_with_incomplete >= 3:
            return {"days": days_with_incomplete, "templates": sorted(templates_seen)}
        return None

    def _person(self, uid, day):
        usr = self.conn.execute(
            "SELECT au.id, au.display_name, au.username, au.role_fk, r.name role "
            "FROM app_user au LEFT JOIN role r ON r.id=au.role_fk WHERE au.id=?", (uid,)
        ).fetchone()
        if not usr:
            return None, None, [], [], set(), []
        L, ins, extra = rollup.compute_day_full(self.conn, uid, day)
        rollup.persist_day(self.conn, uid, day, L, ins, extra)
        person = {"name": usr["display_name"] or usr["username"], "role": usr["role"]}
        on_task = db.role_on_task_set(self.conn, usr["role_fk"])
        ins_d = present_insight(self.conn, ins, extra, user_fk=uid, day=day)
        return (person, ins_d, extra.get("top", []), self._person_timeline(uid, day),
                on_task, extra.get("breakdown", []))

    def _person_timeline(self, uid, day):
        rows = self.conn.execute(
            "SELECT ts, sub_category, state, active_ms, idle_ms, is_meeting "
            "FROM activity_event WHERE user_fk=? AND substr(ts_norm,1,10)=? ORDER BY ts_norm",
            (uid, day)).fetchall()
        return rollup.hourly_buckets(rows, db.work_hours(self.conn),
                                     coarse_lookup=lambda sub: db.coarse_for(self.conn, sub))

    # -- self-serve install --------------------------------------------------- #
    def _serve_install(self, q):
        if not self._rl("setup"):
            return self._json(429, {"error": "rate limited"})
        script = (_PS_INSTALL
                  .replace("__SERVER__", q.get("server", [self._base_url()])[0])
                  .replace("__CODE__", q.get("code", [""])[0]))
        data = script.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_agent_exe(self):
        """Stream the prebuilt standalone agent. Built out-of-band on the Windows VM
        (scripts/build_agent.ps1) and copied to AGENT_EXE; 404 with a hint if it's not there."""
        if not os.path.isfile(AGENT_EXE):
            return self._json(404, {"error": "agent.exe not built yet — run "
                                    "scripts/build_agent.ps1 on the Windows VM and place it at "
                                    "dist/coverage-agent.exe"})
        with open(AGENT_EXE, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", "attachment; filename=coverage-agent.exe")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_agent_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for rel in AGENT_BUNDLE:
                p = os.path.join(_BASE, rel)
                if os.path.isfile(p):
                    z.write(p, rel)
            for pkg in ("shared", "agent", "scripts"):
                z.writestr(pkg + "/__init__.py", "")
        data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", "attachment; filename=coverage-agent.zip")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_spa(self, path):
        """Serve a built static asset from web/dist, else the SPA index for client routing.

        If web/dist doesn't exist yet (before `npm run build`), return a friendly hint so a
        developer isn't met with a blank 404.
        """
        if not os.path.isdir(WEB_DIST):
            hint = ("<h1>UI not built yet</h1><p>Run <code>cd web &amp;&amp; npm install &amp;&amp; "
                    "npm run build</code> to build the dashboard, then reload. For live dev, run "
                    "<code>npm run dev</code> (port 5173, proxies the API here).</p>")
            return self._html(200, hint)
        # try to serve a real file under web/dist (assets, favicon, etc.)
        rel = path.lstrip("/")
        if rel:
            candidate = os.path.normpath(os.path.join(WEB_DIST, rel))
            if candidate.startswith(WEB_DIST) and os.path.isfile(candidate):
                return self._serve_static(candidate)
        # otherwise the SPA shell (so /login, /person/3, /admin/roles resolve client-side)
        index = os.path.join(WEB_DIST, "index.html")
        if os.path.isfile(index):
            return self._serve_static(index, no_cache=True)
        return self._json(404, {"error": "not found"})

    def _serve_static(self, abspath, no_cache=False):
        ext = os.path.splitext(abspath)[1].lower()
        ct = _CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(abspath, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        # Vite emits content-hashed asset filenames → safe to cache long; index.html must not.
        if no_cache or os.path.basename(abspath) == "index.html":
            self.send_header("Cache-Control", "no-cache")
        elif "/assets/" in abspath.replace("\\", "/"):
            self.send_header("Cache-Control", "max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)

    def _serve_brand(self, name):
        path = os.path.join(BRAND_DIR, os.path.basename(name))
        if not os.path.isfile(path):
            return self._json(404, {"error": "not found"})
        with open(path, "rb") as fh:
            data = fh.read()
        ct = "image/png" if name.lower().endswith(".png") else "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(data)


def make_server(port: int, db_path: str) -> ThreadingHTTPServer:
    conn = db.connect(db_path, check_same_thread=False)
    db.init_db(conn)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.conn = conn          # type: ignore[attr-defined]
    srv.lock = threading.Lock()  # serialize the shared sqlite conn (§ low volume, <=10 agents)
    return srv


def run(port: int = 8765, db_path: str = db.DEFAULT_DB):
    srv = make_server(port, db_path)
    print(f"dashboard + ingest on http://127.0.0.1:{port}  (db={db_path})")
    srv.serve_forever()


if __name__ == "__main__":
    run(int(os.environ.get("PORT", "8765")))
