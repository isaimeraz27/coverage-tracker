# Testing the real agent on a Windows VM

Everything else can be tested on any OS via the simulator. The **real capture** (active
window, idle, input counts, browser URLs) only runs on Windows and has to be tested there.
This walks you through doing it on a cheap cloud Windows VM in ~30 minutes.

> The agent is **defensive**: any single Win32/COM failure logs and is skipped, it never
> crashes the agent. Use `--selftest` (below) to see exactly what it captures.

---

## 0. Prereqs on your side

- Your dashboard server reachable from the VM. Two easy options:
  - **Simplest:** run the whole server *on the VM itself* (`python server\server\run.py`)
    and point the agent at `http://127.0.0.1:8765`. The agent and server on one box — fine
    for a functional test.
  - **Or** run the server on your Mac and expose it to the VM (same VPC, or a tunnel like
    `cloudflared`/`ngrok`). The agent's `TRACKER_SERVER` must be a URL the VM can reach.

## 1. Get an x64 Windows VM

Any works; x64 (not ARM) is what the ctypes fixes target.

- **AWS EC2:** launch a `t3.small` with the *Microsoft Windows Server 2022 Base* AMI.
  ~$0.02–0.04/hr. **Stop (not terminate)** it when done so you stop paying.
- **Azure:** a `B2s` with *Windows 11* or *Windows Server 2022*. Similar price.
- Open RDP (3389) to your IP only. Connect with Microsoft Remote Desktop (free on Mac).

## 2. Install Python on the VM

In the VM, download Python 3.11+ from python.org → **check "Add python.exe to PATH"** during
install. Verify in PowerShell: `python --version`.

## 3. Get the agent onto the VM

**Option A — the dashboard's one-liner (the real path employees use):**
On the dashboard, log in as admin → **Machines** → *Issue enrollment code* → copy the
`irm ... | iex` command → paste it into PowerShell on the VM. It downloads the agent, tries
`pip install comtypes`, registers a logon task, and starts the agent.

**Option B — manual (better for poking around):**
```powershell
# copy the employee-tracker folder to the VM (RDP drive share, git, or scp), then:
cd employee-tracker
python -m pip install --user comtypes      # enables real browser-URL capture
python scripts\run_agent.py --selftest     # <-- DO THIS FIRST
```

## 4. Self-test (the 5-second sanity check)

`python scripts\run_agent.py --selftest` prints what the agent sees *right now*:

```
=== agent self-test ===
platform        : win32
full-URL reader : available (comtypes OK)
capture backend : WindowsCapture (real ctypes)

sample 1: app='chrome' title='Quotes - EZLynx'
          domain='https://app.ezlynx.com/quotes/55/rating' url='https://app.ezlynx.com/quotes/55/rating'
          idle_ms=120 keys=14 clicks=3 px=842
...
```

**What to check:**
- `app` matches the focused window (switch apps between samples to confirm).
- `idle_ms` rises when you stop touching the mouse/keyboard.
- `keys`/`clicks`/`px` are non-zero while you type/move.
- `url` shows the real address when a browser is focused. **If `url` is None on a browser**,
  see Troubleshooting.

## 5. Real run against the dashboard

Set the server + code, then run (Option A already did this):
```powershell
$env:TRACKER_SERVER = "http://<your-server>:8765"
$env:TRACKER_ENROLL_CODE = "<code from the Machines page>"
$env:TRACKER_USER = "your_name"
python scripts\run_agent.py
```
Then on the VM: open your seeded carrier/rater URLs, type in some apps, switch around for a
few minutes. On the dashboard, open that user's **person page** — you should see real apps
in the timeline, and (if your taxonomy rules match the URLs you visited) the categories +
a detected **new_business_quote** task.

> Tracking only happens inside the configured **work hours** (Settings). If you test
> off-hours, temporarily widen the window or the agent will correctly capture nothing.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `full-URL reader: NOT available` | `comtypes` didn't install. `python -m pip install --user comtypes`, retry. Until then URLs are guessed from the window title. |
| Browser `url` is None even with comtypes | Chromium exposes its a11y tree on demand. Launch the browser once with `chrome.exe --force-renderer-accessibility` (or Edge `msedge.exe --force-renderer-accessibility`). Some locked-down builds also need it set via policy. |
| `app` empty / wrong | A protected/elevated window was foreground (UAC, some system dialogs) — expected; the agent skips it. |
| Nothing appears on the dashboard | (1) work-hours window — see above; (2) the VM can't reach `TRACKER_SERVER` — test with `curl $env:TRACKER_SERVER/healthz`; (3) wrong/used enrollment code — issue a fresh one. |
| Categories all `uncategorized` | Your taxonomy rules don't match the URLs you visited. Use the **Taxonomy → tester** to paste a URL and see what matches; add/fix rules (they apply retroactively). |
| Agent seems to do nothing | Run in the foreground (`python scripts\run_agent.py`, not the hidden task) and watch the console; capture errors are logged as warnings, not crashes. |

## Cleanup

- Stop the agent: close the console, or `schtasks /Delete /TN CoverageAgent /F`.
- **Stop the VM** (don't just disconnect) so billing stops.
