# PyInstaller spec for the Coverage Windows agent.
# Build ON WINDOWS (PyInstaller can't cross-compile from macOS):
#     pyinstaller --clean --noconfirm build\coverage-agent.spec
# Output: dist\coverage-agent.exe  (onefile, ~10-20 MB)
#
# console=True so `coverage-agent.exe --selftest` prints output. Production runs windowless
# via `Start-Process -WindowStyle Hidden` in the installer (a console exe started hidden
# shows no window). Only split into a console=False build if a window flash is observed.
import os

# the repo root is the spec file's parent's parent (build/ is under the repo root)
ROOT = os.path.abspath(os.path.join(os.path.dirname(SPECPATH), "."))
REPO = os.path.dirname(ROOT) if os.path.basename(ROOT) == "build" else ROOT

block_cipher = None

a = Analysis(
    [os.path.join(REPO, "scripts", "run_agent.py")],
    pathex=[REPO],
    binaries=[],
    datas=[],
    # The agent uses LAZY imports the analyzer can miss (inside run_agent / capture.sample /
    # _selftest), plus comtypes' runtime-generated UIAutomation bindings. List them explicitly.
    hiddenimports=[
        "agent", "agent.agent", "agent.capture", "agent.browser_url", "agent.buffer",
        "agent.redaction", "agent.shipper", "agent.paths", "shared", "shared.contracts",
        "comtypes", "comtypes.client", "comtypes.server", "comtypes.stream",
        "comtypes.automation", "comtypes.typeinfo", "comtypes.persist", "comtypes.GUID",
        "comtypes.gen", "comtypes.gen.UIAutomationClient",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "numpy", "pytest", "unittest", "test", "pydoc"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="coverage-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
