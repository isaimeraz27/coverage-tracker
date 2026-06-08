# Azure Windows VM — step-by-step test guide

Goal: stand up a cheap Windows VM, run the **whole tool on it** (server + agent together),
and watch your own real activity appear on the dashboard. Cost: ~$0 on Azure's free credit
if you **stop the VM when done**. Time: ~30–40 min, mostly downloads.

> Everything runs on the VM, so the agent talks to `http://127.0.0.1:8765` — no network or
> firewall setup. RDP from your Mac just gives you the VM's screen.

---

## PART A — Create the Azure account (one-time, ~10 min)

1. Go to **azure.microsoft.com** → **Start free** (or **Try Azure for free**).
2. Sign in with a Microsoft account (or make one). You'll enter a **credit card for identity** —
   the **~$200 free credit (30 days)** covers this test, so you won't actually be charged.
3. Finish signup → you land in the **Azure Portal** (portal.azure.com).

### Set a spending cap so you CANNOT be surprised
4. In the portal search bar, type **"Cost Management"** → open it → **Budgets** → **+ Add**.
   Set a budget of e.g. **$5** with an alert at 80%. (Belt-and-suspenders; the credit
   already protects you, but this guarantees an email if anything ever runs long.)

---

## PART B — Launch the Windows VM (~10 min + ~5 min provisioning)

1. Portal search bar → **"Virtual machines"** → **+ Create** → **Azure virtual machine**.
2. Fill the **Basics** tab:
   - **Subscription / Resource group:** create a new resource group, name it `coverage-test`
     (deleting this group later deletes everything = guaranteed cleanup).
   - **Virtual machine name:** `coverage-win`
   - **Region:** pick the one nearest you (lower latency over RDP).
   - **Image:** **Windows 11 Pro** (or *Windows Server 2022 Datacenter* — either works).
   - **Size:** click **See all sizes** → pick **B2s** (2 vCPU, 4 GB) — cheap and enough.
   - **Username:** `coverageadmin`  **Password:** a strong one you'll remember (you log in with this — no key pair needed, unlike AWS).
   - **Public inbound ports:** **Allow selected ports** → check **RDP (3389)**.
3. Leave the rest default → **Review + create** → **Create**. Wait ~3–5 min for
   "deployment complete" → **Go to resource**.

---

## PART C — Connect from your Mac (~5 min)

1. On the Mac App Store, install **Windows App** (Microsoft's RDP client; formerly
   "Microsoft Remote Desktop") — free.
2. In the Azure portal, on your VM page → **Connect** → **Download RDP file**.
3. Open that `.rdp` file → it launches Windows App → enter the username/password from Part B.
   (Accept the certificate warning — it's your own VM.) You now see the Windows desktop.

---

## PART D — Run the tool on the VM (~10 min)

Do all of this **inside the Windows VM** (in the RDP window).

### 1. Install Python
- Open **Microsoft Edge** → go to **python.org/downloads** → download Python 3.12+.
- Run the installer → **CHECK "Add python.exe to PATH"** → Install.
- Open **PowerShell** (Start → type "PowerShell") → verify: `python --version`

### 2. Get the code onto the VM
Easiest: install git and clone, OR copy via the RDP clipboard / a cloud drive. With git:
```powershell
winget install --id Git.Git -e --silent      # if winget is available
# then clone your repo, OR copy the employee-tracker folder over any way you like
```
If you don't have the repo in git yet, the simplest copy method: zip `employee-tracker`
on your Mac, upload to your own OneDrive/Google Drive, download it inside the VM, unzip.

### 3. SELF-TEST FIRST (the 5-second "does capture work?" check)
```powershell
cd employee-tracker
python -m pip install --user comtypes        # enables real browser-URL capture
python scripts\run_agent.py --selftest
```
Expected: it prints 3 samples showing the focused app, window title, **url**, idle ms, and
key/click counts. Switch apps / move the mouse between samples to see the numbers change.
**This is the moment of truth — if this looks right, the agent works on Windows.**
👉 Copy the output and send it to me; I'll confirm or fix anything odd.

### 4. Build the dashboard UI + start the server (same VM)
```powershell
# Node is needed once to build the UI. Install it, then:
cd web
npm install
npm run build
cd ..
python server\run.py            # serves dashboard + ingest on http://127.0.0.1:8765
```
(If you'd rather skip Node on the VM, build `web/dist` on your Mac and copy it over — the
server just serves the prebuilt folder.)

### 5. Open the dashboard + enroll this machine
- In the VM's Edge: go to **http://127.0.0.1:8765** → first-run setup → create an admin.
- Admin → **Settings** → turn **full_url** on (so URLs are captured).
- Admin → **Machines** → **Issue enrollment code** → copy it.

### 6. Run the REAL agent (new PowerShell window, server still running)
```powershell
cd employee-tracker
$env:TRACKER_SERVER = "http://127.0.0.1:8765"
$env:TRACKER_ENROLL_CODE = "<the code you copied>"
$env:TRACKER_USER = "tester"
python scripts\run_agent.py
```
Now **use the VM for a few minutes** — open apps, browse to a couple of sites, type. Then
refresh the dashboard → open the **tester** person page → you should see your real apps in
the timeline. Add Taxonomy rules matching the sites you visited (Taxonomy → tester) and
they'll categorize retroactively.

> Tracking only runs inside **work hours** (Settings). If you're testing at an odd hour,
> widen the work-hours window first or the agent will correctly capture nothing.

---

## PART E — STOP THE VM (do not skip — this is what keeps it ~free)

- Azure portal → your VM → **Stop**. A *stopped (deallocated)* VM costs almost nothing.
- When fully done testing: delete the **`coverage-test` resource group** → removes the VM,
  disk, and everything in one click, so nothing lingers and bills.

---

## If something breaks
See `docs/WINDOWS_TEST.md` troubleshooting table (AV quarantine, no-admin, empty URLs,
`--force-renderer-accessibility`, network). The agent is defensive — a single failure logs
and is skipped, it won't crash — so `--selftest` output + any console warnings tell us what
to fix. Send me both and I'll diagnose from here.
