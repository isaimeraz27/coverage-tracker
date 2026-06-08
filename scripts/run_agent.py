"""Windows agent entry point. Enrolls on first run, then captures + ships.

Reads config from (in priority): environment, then config.json written by the
installer next to the install dir. Persists the issued token so the logon task
runs unattended. Real capture only runs on Windows; on other OSes use the
synthetic generator for development.
"""
import os
import sys
import json
import getpass

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALL = os.path.dirname(HERE)          # the install dir ($Dir); config.json lives here
sys.path.insert(0, INSTALL)
from agent import agent, shipper          # noqa: E402

CONFIG = os.path.join(INSTALL, "config.json")
TOKEN_FILE = os.path.join(INSTALL, "agent_token.txt")


def _load_config() -> dict:
    if os.path.exists(CONFIG):
        try:
            return json.load(open(CONFIG))
        except (ValueError, OSError):
            pass
    return {}


def _hostname() -> str:
    return os.environ.get("COMPUTERNAME") or (os.uname().nodename if hasattr(os, "uname") else "host")


def _selftest():
    """Capture a few real samples and print them — the 5-second 'is capture working?'
    check to run on a fresh Windows VM. Exits cleanly on non-Windows (no real backend)."""
    import time
    from agent import capture as cap
    print("=== agent self-test ===")
    try:
        from agent import browser_url
        avail = browser_url.UrlReader().available
    except Exception as e:
        avail = False
        print("browser_url import issue:", e)
    print(f"platform        : {sys.platform}")
    print(f"full-URL reader : {'available (comtypes OK)' if avail else 'NOT available — title-based URLs only'}")
    try:
        capture = cap.make_capture(allow_full_url=True)
    except RuntimeError as e:
        print(f"capture backend : {e}")
        print("\nNo real capture backend on this OS. Run this on Windows to see live capture.")
        return 0
    print("capture backend : WindowsCapture (real ctypes)\n")
    for i in range(3):
        s = capture.sample()
        if s is None:
            print(f"sample {i+1}: (none)")
        else:
            print(f"sample {i+1}: app={s.process!r} title={(s.window_title or '')[:50]!r}")
            print(f"          domain={s.domain!r} url={s.url!r}")
            print(f"          idle_ms={s.idle_ms} keys={s.key_count} clicks={s.mouse_count} px={s.mouse_distance_px}")
        time.sleep(2)
    print("\nIf app/idle look right you're good. If url is None on a browser, launch it with\n"
          "--force-renderer-accessibility (see docs/WINDOWS_TEST.md).")
    return 0


def main():
    if "--selftest" in sys.argv:
        sys.exit(_selftest())

    cfg = _load_config()
    server = os.environ.get("TRACKER_SERVER") or cfg.get("server") or "http://127.0.0.1:8765"
    username = os.environ.get("TRACKER_USER") or getpass.getuser()

    token = None
    if os.path.exists(TOKEN_FILE):
        token = open(TOKEN_FILE).read().strip()
    if not token:
        code = os.environ.get("TRACKER_ENROLL_CODE") or cfg.get("code", "")
        token = shipper.enroll(server, code, _hostname())
        if not token:
            print("enrollment failed — check the setup code / server URL")
            sys.exit(1)
        open(TOKEN_FILE, "w").write(token)
        print("enrolled.")
    agent.run_agent(server, token, username)


if __name__ == "__main__":
    main()
