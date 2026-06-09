# Build the Coverage agent into a single coverage-agent.exe — RUN ON WINDOWS (the VM).
# PyInstaller cannot cross-compile from macOS, so this runs on the Windows EC2 VM.
#
#   cd <repo root>
#   powershell -ExecutionPolicy Bypass -File scripts\build_agent.ps1
#
# Output: dist\coverage-agent.exe   (then copy it to where the server serves it — see
# docs\AGENT_EXE_BUILD.md). Re-run whenever anything under agent\, shared\contracts.py, or
# scripts\run_agent.py changes.
$ErrorActionPreference = 'Stop'

Write-Host "Installing build deps (pyinstaller + comtypes)..."
python -m pip install --upgrade pyinstaller comtypes

# Pre-generate the UIAutomation COM bindings so PyInstaller bundles them (otherwise the
# frozen exe would try to generate them at runtime into a read-only dir).
Write-Host "Pre-generating UIAutomation bindings..."
python -c "from comtypes.client import GetModule; GetModule('UIAutomationCore.dll')"

Write-Host "Building coverage-agent.exe..."
pyinstaller --clean --noconfirm build\coverage-agent.spec

if (Test-Path dist\coverage-agent.exe) {
    Write-Host ""
    Write-Host "Built: dist\coverage-agent.exe"
    Write-Host "Next: dist\coverage-agent.exe --selftest   (confirm capture + URL work frozen)"
} else {
    Write-Host "BUILD FAILED — dist\coverage-agent.exe not found."
    exit 1
}
