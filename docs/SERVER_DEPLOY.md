# Production server deploy — small Linux VPS + Caddy (HTTPS)

Goal: run the Coverage dashboard+ingest server on a cheap always-on Linux VM, reachable
over the public internet at `https://coverage.yourco.com`, so agents on employee PCs in any
office can enroll and ship data. Cost: ~$6/mo VM + ~$12/yr domain. One-time setup: ~30–40 min.

The server is pure-Python **stdlib** (zero `pip` deps) and stores one **SQLite file**. So the
host needs almost nothing: Python 3, a public HTTPS address, a persistent disk, and to stay up.

Architecture:

```
employee PC agents  ─┐
(both offices)       │   HTTPS :443
your admin browser  ─┴──────────────▶  Caddy (auto-TLS)  ──▶  127.0.0.1:8765
                                        on the VM             python3 server/run.py (systemd)
                                                              └─ data/tracker.db (SQLite)
```

Caddy terminates TLS and forwards to the Python server on localhost. It sends
`X-Forwarded-Proto: https` and passes the real `Host` header — which is exactly what the
server's `_base_url()` reads, so the `/install.ps1` one-liners, the `/api/v1/disclosure`
URL, and the Secure session cookie all come out correct for a public HTTPS host.

---

## PART A — Prerequisites (10 min)

1. **A domain.** Buy a cheap one (Namecheap/Cloudflare/Porkbun, ~$12/yr) or use a subdomain
   of one you own. You'll point a record at the VM's IP in Part C. Example used below:
   `coverage.yourco.com`.
2. **A VPS account.** Hetzner Cloud (cheapest, ~€4/mo), DigitalOcean, or AWS Lightsail all work.
   Create the smallest instance: 1 vCPU / 1–2 GB RAM / Ubuntu 24.04 LTS. (≤10 agents is a
   trivial load; the smallest tier is plenty.)
3. **An SSH key** so you can log into the VM. Most providers let you paste your public key at
   create time (`cat ~/.ssh/id_ed25519.pub` on your Mac; generate one with `ssh-keygen -t ed25519`
   if you don't have it).

---

## PART B — Create the VM (5 min)

1. In the provider console, create the Ubuntu 24.04 instance with your SSH key attached.
2. **Firewall / security group:** allow inbound **22 (SSH)**, **80 (HTTP)**, **443 (HTTPS)**.
   - Port 80 is needed only so Caddy can obtain the Let's Encrypt cert (HTTP-01 challenge); all
     real traffic is 443.
   - Do NOT expose 8765 — the Python server stays bound to localhost, never the public interface.
3. Note the VM's **public IP** (e.g. `203.0.113.10`).
4. SSH in from your Mac: `ssh root@203.0.113.10` (or `ubuntu@…` depending on provider).

> Optional but recommended: create a non-root user and run the service as it. The runbook below
> uses a dedicated `coverage` system user for the service regardless of how you log in.

---

## PART C — Point the domain at the VM (5 min, do this early so DNS has time to propagate)

In your domain's DNS settings, add an **A record**:

```
coverage   A   203.0.113.10
```

(`coverage` → host, the VM's public IP.) Wait a few minutes; verify from your Mac:
`dig +short coverage.yourco.com` should print the VM IP. Caddy can't get a cert until this resolves.

---

## PART D — Put the code on the VM (5 min)

The server is the `employee-tracker/` tree. Get it onto the VM by whichever is easiest:

- **Clone the private repo** (if the VM can auth to GitHub — deploy key or PAT):
  ```
  git clone https://github.com/isaimeraz27/coverage-tracker.git /opt/coverage
  ```
- **Or copy from your Mac** with rsync/scp:
  ```
  rsync -av --exclude data/ --exclude '*.db' --exclude node_modules \
        /Users/isaimeraz/Downloads/coverage-tracker-share/employee-tracker/ \
        root@203.0.113.10:/opt/coverage/
  ```

You need the **built React dashboard** (`web/dist/`) present on the VM — the server serves it
statically. Build it once on your Mac (`cd web && npm install && npm run build`) and include
`web/dist/` in the copy, OR build it on the VM if Node is installed there. (`web/dist` is
gitignored, so a `git clone` alone will NOT include it — build it or copy it.)

You also need the agent **`.exe` built and placed at `dist/coverage-agent.exe`** for the
`/download/agent.exe` route to work — see `docs/AGENT_EXE_BUILD.md` (built on Windows, then
copied here). Until it's there, enrollment downloads will 404. The dashboard/consent flow
otherwise works without it.

Install Python (Ubuntu 24.04 ships 3.12; the server is 3.9+ safe) — usually already present:
```
apt update && apt install -y python3
```
No `pip install` needed — the server imports only the standard library.

---

## PART E — Run the server as a service (10 min)

1. Create a service user and data dir:
   ```
   useradd --system --home /opt/coverage --shell /usr/sbin/nologin coverage  || true
   mkdir -p /opt/coverage/data
   chown -R coverage:coverage /opt/coverage
   ```

2. Create the systemd unit `/etc/systemd/system/coverage.service`:
   ```ini
   [Unit]
   Description=Coverage activity dashboard + ingest server
   After=network.target

   [Service]
   Type=simple
   User=coverage
   WorkingDirectory=/opt/coverage
   # Binds to 127.0.0.1:8765 (see server/api.py); Caddy fronts it on 443.
   Environment=PORT=8765
   Environment=TRACKER_DB=/opt/coverage/data/tracker.db
   ExecStart=/usr/bin/python3 /opt/coverage/server/run.py
   Restart=always
   RestartSec=3
   # Light hardening for an internet-facing box:
   NoNewPrivileges=true
   PrivateTmp=true
   ProtectSystem=full
   ReadWritePaths=/opt/coverage/data

   [Install]
   WantedBy=multi-user.target
   ```

3. Start it and enable on boot:
   ```
   systemctl daemon-reload
   systemctl enable --now coverage
   systemctl status coverage          # should be active (running)
   curl -s localhost:8765/healthz     # -> {"ok": true}
   ```

The server now runs on `127.0.0.1:8765`, survives reboots, and restarts if it crashes. The
retention-purge daemon (§3.6) starts with it automatically.

---

## PART F — Caddy for automatic HTTPS (10 min)

1. Install Caddy (official apt repo):
   ```
   apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
   apt update && apt install -y caddy
   ```

2. Replace `/etc/caddy/Caddyfile` with **exactly this** (substitute your domain):
   ```
   coverage.yourco.com {
       reverse_proxy 127.0.0.1:8765 {
           header_up X-Forwarded-Proto https
       }
   }
   ```
   That's the entire TLS config. Caddy will automatically obtain and renew a Let's Encrypt
   certificate for the domain. The `header_up X-Forwarded-Proto https` line is the important
   one: it's what makes the server emit `https://` install URLs and set the Secure cookie flag
   (§3c). (Caddy already forwards the original `Host` header, so `_base_url()` resolves to your
   real domain.)

3. Reload and verify:
   ```
   systemctl reload caddy
   # from your Mac:
   curl -s https://coverage.yourco.com/healthz     # -> {"ok": true}, valid cert, no warning
   ```

Open `https://coverage.yourco.com` in a browser → you should see the **Setup** page (no admin
exists yet). That's your cue to do the first-run admin setup (see the office-day walkthrough).

---

## PART G — Backups (5 min, do not skip — this is the consent + activity record)

The whole system state is the one SQLite file. Back it up nightly with a cron job that uses
SQLite's safe online-backup (never just `cp` a live WAL DB):

`/etc/cron.daily/coverage-backup` (make it executable, `chmod +x`):
```bash
#!/bin/sh
set -e
mkdir -p /opt/coverage/backups
STAMP=$(date +%Y%m%d)
sqlite3 /opt/coverage/data/tracker.db ".backup '/opt/coverage/backups/tracker-$STAMP.db'"
# keep 30 days
find /opt/coverage/backups -name 'tracker-*.db' -mtime +30 -delete
```
(`apt install -y sqlite3` for the CLI.) For real durability, also sync `/opt/coverage/backups`
off-box (provider snapshots, or `rclone` to object storage). The `ack_record` consent trail
lives in this DB — losing it loses your Ley 1581 paper trail.

---

## PART H — Operate it

- **Logs:** `journalctl -u coverage -f` (server), `journalctl -u caddy -f` (TLS/proxy).
- **Update the code:** pull/rsync the new tree to `/opt/coverage`, then `systemctl restart coverage`.
  Schema migrations run automatically on startup (idempotent).
- **Rebuild the agent .exe** whenever `agent/`, `shared/contracts.py`, or `scripts/run_agent.py`
  change (per `docs/AGENT_EXE_BUILD.md`), and copy the new exe to `/opt/coverage/dist/`.
- **Never set `TRACKER_NO_AUTH`** on this box — it disables the login gate (it logs a loud
  stderr warning if it's ever on). It's a local-dev-only switch.
- **Security posture:** only 80/443 are public; 8765 is localhost-only; the agent token is
  stored hashed; sessions expire (12h idle / 7d absolute). Keep the VM patched (`unattended-upgrades`).

---

## What this gives you vs. what's still on you

**Done by this runbook:** a public, always-on, HTTPS server that agents and your browser can
reach; auto-renewing TLS; the server running hardened and surviving reboots; nightly DB backups.

**Still required before the office rollout (not host problems):**
1. Build + place `dist/coverage-agent.exe` (Windows build, `docs/AGENT_EXE_BUILD.md`).
2. Code-sign that exe before the *remote* office self-enrolls (in-person office can click through).
3. Get the disclosure wording legally approved (edit it in Settings; it auto-versions).
