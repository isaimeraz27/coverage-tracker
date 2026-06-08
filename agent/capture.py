"""Platform capture abstraction (§7 Windows Agent).

WindowsCapture uses stdlib ctypes (GetForegroundWindow / GetWindowText /
GetLastInputInfo / QueryFullProcessImageName) — no pywin32 dependency. It is only
*invoked* on Windows; the module imports fine everywhere so the rest of the agent
(segmentation, buffering, shipping) is testable on any OS via MockCapture.
"""
from __future__ import annotations

import sys
import time
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("agent.capture")


@dataclass
class Sample:
    ts: float                       # epoch seconds
    process: str                    # lowercased image name, no extension
    window_title: Optional[str]
    domain: Optional[str]           # registrable domain if a browser/known
    idle_ms: int                    # ms since last system input (GetLastInputInfo)
    key_count: int = 0              # COUNTS ONLY since last sample — never content
    mouse_count: int = 0            # click count since last sample
    mouse_distance_px: int = 0      # cursor travel (px) since last sample — engagement signal
    url: Optional[str] = None       # full URL — only when full-URL capture is enabled for the role
                                    # (and never for sensitive domains; see redaction)


class Capture:
    def sample(self) -> Optional[Sample]:
        raise NotImplementedError

    def drain_attendance(self) -> list[str]:
        return []


class MockCapture(Capture):
    """Replays a scripted list of Samples — used by tests and for dashboard dev
    on non-Windows hosts (no real surveillance)."""

    def __init__(self, samples: list[Sample]):
        self._samples = list(samples)
        self._i = 0

    def sample(self) -> Optional[Sample]:
        if self._i >= len(self._samples):
            return None
        s = self._samples[self._i]
        self._i += 1
        return s


_BROWSERS = {"chrome", "msedge", "firefox", "brave", "opera", "vivaldi"}


class WindowsCapture(Capture):  # pragma: no cover - Windows-only runtime path
    """Real capture via ctypes. Active window, title, process, idle time. Browser URL via
    UI Automation (agent/browser_url, optional comtypes) when full-URL capture is enabled.

    Hardened for 64-bit: every Win32 call has explicit restype/argtypes so HWND/HANDLE/
    LRESULT are pointer-sized (not truncated to 32-bit). Defensive: any single Win32 failure
    logs once and returns a safe default rather than raising, so capture survives glitches.
    """

    def __init__(self, allow_full_url: bool = False):
        if not sys.platform.startswith("win"):
            raise RuntimeError("WindowsCapture is only valid on Windows")
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes
        self.wt = wintypes
        self.allow_full_url = allow_full_url
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        u, k, w = self.user32, self.kernel32, wintypes
        # --- explicit signatures (the 64-bit-correctness fix) ---
        u.GetForegroundWindow.restype = w.HWND
        u.GetForegroundWindow.argtypes = []
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetWindowTextLengthW.argtypes = [w.HWND]
        u.GetWindowTextW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        u.GetWindowThreadProcessId.restype = w.DWORD
        u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        u.GetLastInputInfo.restype = w.BOOL
        k.GetTickCount.restype = w.DWORD
        k.GetTickCount.argtypes = []
        k.OpenProcess.restype = w.HANDLE
        k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        k.QueryFullProcessImageNameW.restype = w.BOOL
        k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
        k.CloseHandle.restype = w.BOOL
        k.CloseHandle.argtypes = [w.HANDLE]

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
        u.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
        self._LII = LASTINPUTINFO

        self._mon = _InputMonitor()    # low-level hooks: clicks, key counts, cursor travel
        self._urls = None              # lazily-created browser_url reader (optional)

    def _idle_ms(self) -> int:
        try:
            lii = self._LII()
            lii.cbSize = self.ct.sizeof(self._LII)
            self.user32.GetLastInputInfo(self.ct.byref(lii))
            return int(self.kernel32.GetTickCount() - lii.dwTime)
        except Exception as e:
            log.warning("idle read failed: %s", e)
            return 0

    def _foreground(self):
        try:
            hwnd = self.user32.GetForegroundWindow()
            if not hwnd:
                return None, None, None
            length = self.user32.GetWindowTextLengthW(hwnd)
            buf = self.ct.create_unicode_buffer(length + 1)
            self.user32.GetWindowTextW(hwnd, buf, length + 1)
            pid = self.wt.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, self.ct.byref(pid))
            return hwnd, buf.value, pid.value
        except Exception as e:
            log.warning("foreground read failed: %s", e)
            return None, None, None

    def _process_name(self, pid: int) -> str:
        PROCESS_QUERY_LIMITED = 0x1000
        try:
            h = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
            if not h:
                return ""
            try:
                size = self.wt.DWORD(260)
                buf = self.ct.create_unicode_buffer(260)
                if self.kernel32.QueryFullProcessImageNameW(h, 0, buf, self.ct.byref(size)):
                    name = buf.value.replace("\\", "/").split("/")[-1]
                    return name.lower().removesuffix(".exe")
                return ""
            finally:
                self.kernel32.CloseHandle(h)
        except Exception as e:
            log.warning("process-name read failed: %s", e)
            return ""

    def _read_url(self, hwnd, proc):
        """Full URL for a browser foreground window, or None. Optional + fail-safe."""
        if not (self.allow_full_url and hwnd and proc in _BROWSERS):
            return None
        try:
            if self._urls is None:
                from agent import browser_url
                self._urls = browser_url.UrlReader()
            return self._urls.read(hwnd)
        except Exception as e:
            log.warning("url read failed: %s", e)
            return None

    def sample(self) -> Optional[Sample]:
        try:
            hwnd, title, pid = self._foreground()
            proc = self._process_name(pid) if pid else ""
            url = self._read_url(hwnd, proc)
            domain = url or _domain_from_title(title)   # url preferred; redaction normalizes
            keys, clicks, dist = self._mon.drain()
            return Sample(ts=time.time(), process=proc, window_title=title,
                          domain=domain, idle_ms=self._idle_ms(),
                          key_count=keys, mouse_count=clicks, mouse_distance_px=dist, url=url)
        except Exception as e:
            log.warning("sample failed: %s", e)
            return None


class _InputMonitor:  # pragma: no cover - Windows-only runtime path
    """Background thread running WH_KEYBOARD_LL + WH_MOUSE_LL low-level hooks.
    Accumulates key COUNTS (never the key value), click counts, and cursor travel
    distance. drain() returns and resets the accumulators each poll."""

    def __init__(self):
        import threading
        self.keys = 0
        self.clicks = 0
        self.dist = 0.0
        self._last = None
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def drain(self):
        with self._lock:
            k, c, d = self.keys, self.clicks, int(self.dist)
            self.keys = self.clicks = 0
            self.dist = 0.0
        return k, c, d

    def _run(self):
        import ctypes
        from ctypes import wintypes
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
            WM_KEYDOWN, WM_SYSKEYDOWN = 0x100, 0x104
            WM_MOUSEMOVE = 0x200
            CLICKS = {0x201, 0x204, 0x207}  # L/R/M button down

            # LRESULT is pointer-sized; on 64-bit it must NOT be c_long (32-bit) or the hook
            # chain return is truncated and the hook can be dropped or crash.
            LRESULT = ctypes.c_ssize_t
            HHOOK = wintypes.HHOOK if hasattr(wintypes, "HHOOK") else wintypes.HANDLE
            HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

            user32.SetWindowsHookExW.restype = HHOOK
            user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
            user32.CallNextHookEx.restype = LRESULT
            user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
            user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]

            class POINT(ctypes.Structure):
                _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

            class MSLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [("pt", POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                            ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

            def on_key(nCode, wparam, lparam):
                if nCode == 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    with self._lock:
                        self.keys += 1           # COUNT only; the keycode is never read/stored
                return user32.CallNextHookEx(None, nCode, wparam, lparam)

            def on_mouse(nCode, wparam, lparam):
                if nCode == 0:
                    if wparam in CLICKS:
                        with self._lock:
                            self.clicks += 1
                    elif wparam == WM_MOUSEMOVE:
                        pt = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents.pt
                        p = (pt.x, pt.y)
                        if self._last is not None:
                            dx, dy = p[0] - self._last[0], p[1] - self._last[1]
                            with self._lock:
                                self.dist += (dx * dx + dy * dy) ** 0.5
                        self._last = p
                return user32.CallNextHookEx(None, nCode, wparam, lparam)

            self._kb, self._ms = HOOKPROC(on_key), HOOKPROC(on_mouse)
            user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb, None, 0)
            user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms, None, 0)
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:
            # the input monitor is best-effort; its failure must not kill the agent —
            # counts simply stay at zero.
            log.warning("input monitor stopped: %s", e)


def _domain_from_title(title: Optional[str]):
    if not title:
        return None
    import re
    m = re.search(r"([a-z0-9.-]+\.(?:com|org|net|io|us|co|dev|gov))", title.lower())
    return m.group(1) if m else None


def make_capture(allow_full_url: bool = False) -> Capture:
    if sys.platform.startswith("win"):
        return WindowsCapture(allow_full_url=allow_full_url)
    raise RuntimeError("No real capture backend on this OS — use MockCapture for dev/tests")
