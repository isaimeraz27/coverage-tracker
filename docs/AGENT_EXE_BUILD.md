# Building the agent as a single .exe (no Python on employee machines)

The agent ships as one standalone **`coverage-agent.exe`** (PyInstaller) — employees need no
Python, no pip, nothing. PyInstaller **cannot cross-compile from macOS**, so the binary is
built on the Windows EC2 VM. The Mac authors the setup; the VM produces + verifies the exe.

> Rebuild the exe whenever anything under `agent/`, `shared/contracts.py`, or
> `scripts/run_agent.py` changes. Server/dashboard/source-only changes do NOT need a rebuild.
> `coverage-agent.exe --selftest` prints a version (e.g. `v0.3.0, frozen exe`) so you can
> confirm which build is deployed.

---

## ON THE MAC (author + pre-flight)

1. Make the code changes (already done): `agent/paths.py`, frozen-aware `scripts/run_agent.py`,
   `agent/agent.py` (absolute outbox path), `agent/browser_url.py` (comtypes gen_dir redirect),
   server `/download/agent.exe` route + exe installer, `build/coverage-agent.spec`,
   `scripts/build_agent.ps1`.
2. Run the suite — all green:
   ```bash
   python3 -m unittest discover -s tests
   ```
3. Commit + push so the VM can pull.

---

## ON THE WINDOWS EC2 VM (build + verify)

Start/RDP into the VM per `docs/AWS_WINDOWS_TEST.md` (region **us-east-1**; note the public IP
changes after a Stop/Start, so re-download the RDP file).

1. **Get the latest code** (repo private → make it briefly public to ZIP-download, or `git pull`
   if a clone is set up). Land in the repo root (`coverage-tracker-main` / `employee-tracker`).
2. **Build:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\build_agent.ps1
   ```
   This installs pyinstaller+comtypes, pre-generates the UIAutomation bindings, and produces
   `dist\coverage-agent.exe` (~10–20 MB).
3. **Self-test the frozen exe — the moment of truth:**
   ```powershell
   dist\coverage-agent.exe --selftest
   ```
   With a browser focused (within the 3-second window) you should see:
   - `=== agent self-test (vX.Y.Z, frozen exe) ===`
   - `full-URL reader : available (comtypes OK)`
   - 3 samples with app / title / **url** / idle / key+click counts.
   If the URL reader says NOT available: confirm `%LOCALAPPDATA%\CoverageAgent\gen` got created
   and that `comtypes.gen.UIAutomationClient` was bundled (it's in the spec's hiddenimports).
4. **Serve it:** the server serves the exe from `dist\coverage-agent.exe`. The build already put
   it there, so no copy is needed unless you build elsewhere.
5. **Build the dashboard UI + start the server** (per `docs/AWS_WINDOWS_TEST.md` Part D):
   ```powershell
   cd web; npm install; npm run build; cd ..
   python server\run.py
   ```
6. **Full self-serve install test** (the real deployment path): open `http://127.0.0.1:8765`
   → first-run admin → Settings: enable `full_url` → Machines → **Issue enrollment code** →
   copy the `irm '…/install.ps1?…' | iex` one-liner → run it in a **fresh** PowerShell window.
   Verify:
   - downloads `agent.exe` (no Python prompt, no pip, no zip),
   - writes `config.json` to `%LOCALAPPDATA%\CoverageAgent`,
   - registers the logon task: `schtasks /Query /TN CoverageAgent`,
   - the hidden process starts (Task Manager), enrolls (a row appears under **Machines**), and
     within ~2 minutes the `tester` person page shows real activity.
7. **Reboot** (or log off/on) → confirm the ONLOGON task auto-starts the exe and **no console
   window flashes**. If a flash appears, change the installer's `/TR` to wrap the start in
   `powershell -WindowStyle Hidden -Command "Start-Process -WindowStyle Hidden '<exe>'"` (server
   change only — no exe rebuild needed), or split a `console=False` build for the task.
8. **STOP the EC2 instance** when done (Part E) so it stops billing.

---

## Known limitations / risks

- **Unsigned exe → SmartScreen / antivirus.** Expected. In person: **"More info → Run anyway"**;
  AV may quarantine the unsigned PyInstaller bootloader (a known false-positive). **Code-signing
  must land before the remote (Office 2) rollout** — that removes the warnings entirely.
- **comtypes codegen:** handled (gen_dir redirect to `%LOCALAPPDATA%` + bundled bindings). Worst
  case it falls back to title-based domains — never a crash. Confirm via `--selftest`.
- **Rebuild trigger:** any change to `agent/`, `shared/contracts.py`, or `scripts/run_agent.py`
  → rebuild on the VM. The served exe can otherwise lag the source.
- **No admin needed:** the installer uses a per-user logon scheduled task, not a Windows service.
