# AWS Windows VM (EC2) — step-by-step test guide

Goal: stand up a cheap Windows VM, run the **whole tool on it** (server + agent together),
and watch your own real activity appear on the dashboard. Cost: a few **cents/hour** (under
$1 for a test) — **stop the instance when done**. Time: ~30–40 min.

> Everything runs on the VM, so the agent talks to `http://127.0.0.1:8765` — no network/
> firewall setup. RDP from your Mac just shows the VM's screen.
>
> AWS difference vs Azure: you log in to Windows with an auto-generated admin password that
> you **decrypt using a key pair** (Part C). It's one extra step — covered below.

---

## PART A — AWS account (one-time, ~10 min)
1. Go to **aws.amazon.com** → **Create an AWS Account**.
2. Email + password, account name (anything), enter a **real credit/debit card** (Windows
   isn't in the free tier, but the charge is pennies). Verify phone.
3. Support plan: choose **Basic support — Free**.
4. Sign in to the **AWS Management Console**.

### Set a billing alert (so you can't be surprised)
5. Console search bar → **"Billing and Cost Management"** → **Budgets** → **Create budget**
   → simple **$5** monthly budget with an email alert. (Tiny test cost, but this guarantees
   a heads-up if an instance is ever left running.)

---

## PART B — Launch the Windows instance (~10 min)
1. **Pick your region first** (top-right corner) — choose the one nearest you.
2. Console search → **EC2** → **Instances** → **Launch instances**.
3. Configure:
   - **Name:** `coverage-win`
   - **Application and OS Image (AMI):** search **"Windows Server 2022"** → select
     *Microsoft Windows Server 2022 Base* (Free-tier-eligible label may show, but Windows
     license still bills hourly — that's fine).
   - **Instance type:** **t3.small** (2 vCPU, 2 GB). If it feels sluggish, **t3.medium**
     (4 GB) is still only ~$0.05/hr.
   - **Key pair:** click **Create new key pair** → name `coverage-key`, type **RSA**, format
     **.pem** → **Download** it (saves `coverage-key.pem` to your Mac — you NEED this for the
     password in Part C; don't lose it).
   - **Network settings:** **Allow RDP (3389)** from **My IP** (safer than Anywhere).
   - **Storage:** bump to **60 GB** (Windows + our tools need room).
4. **Launch instance** → **View all instances** → wait until **Instance state = Running** and
   **Status checks = 2/2** (~2–4 min).

---

## PART C — Get the Windows password + connect (~5 min)
1. Select your instance → **Connect** (top) → **RDP client** tab.
2. Click **Get password** → **Upload private key file** → choose the `coverage-key.pem` you
   downloaded → **Decrypt password**. Copy the password it shows (and the **Public DNS**
   address + username `Administrator`).
3. Click **Download remote desktop file** (an `.rdp` file).
4. On your Mac: install **Windows App** from the Mac App Store (free RDP client) → open the
   `.rdp` file → log in as **Administrator** with the decrypted password. Accept the cert
   warning (it's your own VM). You now see the Windows desktop.

---

## PART D — Run the tool on the VM (~10 min)
Do all of this **inside the Windows VM** (the RDP window).

> Server 2022 ships with **IE Enhanced Security** which blocks downloads. If Edge nags,
> Server Manager → **Local Server** → turn **IE Enhanced Security Configuration = Off**,
> or just use the Edge that's preinstalled and click through the prompts.

### 1. Install Python
- Edge → **python.org/downloads** → Python 3.12+ → run installer →
  **CHECK "Add python.exe to PATH"** → Install.
- PowerShell → `python --version` to confirm.

### 2. Get the code onto the VM
Easiest is git; otherwise zip on the Mac → upload to your OneDrive/Drive → download in the VM → unzip.
```powershell
winget install --id Git.Git -e --silent     # if available; then git clone your repo
```

### 3. SELF-TEST FIRST — the moment of truth
```powershell
cd employee-tracker
python -m pip install --user comtypes        # enables real browser-URL capture
python scripts\run_agent.py --selftest
```
Expected: 3 samples printing the focused app, window title, **url**, idle ms, key/click
counts. Switch apps / move the mouse between samples to watch them change.
👉 **Copy this output and send it to me** — it confirms the agent works on real Windows
(or shows exactly what to fix).

### 4. Build the UI + start the server (same VM)
```powershell
cd web
npm install         # needs Node — install from nodejs.org if absent, OR prebuild web/dist on the Mac and copy it
npm run build
cd ..
python server\run.py            # http://127.0.0.1:8765
```

### 5. Enroll + run the real agent
- VM's Edge → **http://127.0.0.1:8765** → first-run admin setup.
- **Settings** → turn **full_url** on. **Machines** → **Issue enrollment code** → copy it.
- New PowerShell window (leave the server running):
```powershell
cd employee-tracker
$env:TRACKER_SERVER = "http://127.0.0.1:8765"
$env:TRACKER_ENROLL_CODE = "<code>"
$env:TRACKER_USER = "tester"
python scripts\run_agent.py
```
- Use the VM a few minutes (apps, browsing, typing) → refresh the dashboard → open the
  `tester` person page → your real activity shows up. Add Taxonomy rules for sites you
  visited and they categorize retroactively.

> Tracking runs only inside **work hours** (Settings) — widen the window if testing off-hours.

---

## PART E — STOP THE INSTANCE (do not skip)
- EC2 → Instances → select it → **Instance state → Stop instance** (stopped = ~no compute
  charge; you still pay a few cents/month for the disk until you terminate).
- Fully done? **Instance state → Terminate** to delete it and stop all charges.

## If something breaks
See `docs/WINDOWS_TEST.md` troubleshooting (AV quarantine, no-admin, empty URLs,
`--force-renderer-accessibility`, network). The agent is defensive — failures log and are
skipped, not fatal — so send me the `--selftest` output + any console warnings.
