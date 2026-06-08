"""Read the active browser tab's URL on Windows via UI Automation.

This is the one piece that needs COM. We use `comtypes` (a small, pure-Python, dependency-
free COM wrapper) rather than ~300 lines of hand-rolled ctypes vtables, because the latter
is brittle across Windows/browser versions and untestable off-Windows.

It is **optional and fail-safe**: comtypes is imported lazily inside the reader; if it is
not installed, or any COM/UI-Automation call fails, `read()` returns None and the agent
falls back to title-based domain parsing. A URL-read failure must never affect the rest of
capture.

Chromium-family browsers expose their accessibility tree (and thus the omnibox) on demand;
if the URL never resolves, launching the browser with `--force-renderer-accessibility`
forces it on. See docs/WINDOWS_TEST.md.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("agent.browser_url")

# UI Automation control type id for Edit (the omnibox is an Edit control)
_UIA_EDIT = 50004
# property id for ControlType
_UIA_CONTROLTYPE_PROPERTY = 30003
# ValueValue property id — reading this directly is far more robust than fetching the
# ValuePattern and casting it (verified on real Windows: the omnibox Edit exposes the
# full URL on this property).
_UIA_VALUEVALUE_PROPERTY = 30045
# TreeScope_Descendants
_TREESCOPE_DESCENDANTS = 4

# Name hints for the address bar across browsers (Edge/Chrome = "Address and search bar";
# Firefox = "Search with ... or enter address"). We also fall back to the first Edit whose
# value looks like a URL.
_OMNIBOX_HINTS = ("address and search bar", "address field", "search or enter address",
                  "enter address", "url")


class UrlReader:
    """Holds the IUIAutomation instance (COM init is expensive — create it once)."""

    def __init__(self):
        self._uia = None
        self._ok = True   # flips False permanently if COM is unavailable, so we stop trying
        self._init_uia()

    def _init_uia(self):
        try:
            # Lazy + Windows-only. The CORRECT comtypes pattern: generate the typed module
            # from the type library first, THEN create the coclass — otherwise CreateObject
            # returns an untyped IUnknown and ElementFromHandle/etc. don't exist on it.
            from comtypes.client import CreateObject, GetModule
            GetModule("UIAutomationCore.dll")              # one-time codegen (cached)
            from comtypes.gen.UIAutomationClient import CUIAutomation
            self._uia = CreateObject(CUIAutomation)
        except Exception as e:
            self._ok = False
            log.info("UI Automation unavailable (urls will fall back to title): %s", e)

    @property
    def available(self) -> bool:
        return self._ok and self._uia is not None

    def read(self, hwnd) -> Optional[str]:
        """Return the active tab's URL for a browser window, or None on any failure."""
        if not self.available or not hwnd:
            return None
        try:
            element = self._uia.ElementFromHandle(hwnd)
            if not element:
                return None
            url = self._find_omnibox_value(element)
            return self._normalize(url)
        except Exception as e:
            log.debug("url read failed: %s", e)
            return None

    def _find_omnibox_value(self, root) -> Optional[str]:
        """Find the address-bar Edit control and read its value (the URL)."""
        try:
            cond = self._uia.CreatePropertyCondition(_UIA_CONTROLTYPE_PROPERTY, _UIA_EDIT)
            edits = root.FindAll(_TREESCOPE_DESCENDANTS, cond)
            n = edits.Length if edits else 0
            best = None
            for i in range(n):
                el = edits.GetElement(i)
                try:
                    name = (el.CurrentName or "").lower()
                except Exception:
                    name = ""
                val = self._value_of(el)
                if val and any(h in name for h in _OMNIBOX_HINTS):
                    return val                       # confident match by name hint
                if val and ("." in val or "/" in val) and best is None:
                    best = val                       # first URL-ish Edit as fallback
            return best
        except Exception as e:
            log.debug("omnibox search failed: %s", e)
            return None

    def _value_of(self, element) -> Optional[str]:
        # Read the ValueValue property directly — robust and avoids fragile pattern casting.
        try:
            v = element.GetCurrentPropertyValue(_UIA_VALUEVALUE_PROPERTY)
            return v or None
        except Exception:
            return None

    @staticmethod
    def _normalize(url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        u = url.strip()
        if not u or " " in u and "://" not in u:
            return None
        if "://" not in u:
            u = "https://" + u   # omnibox often omits the scheme
        return u


def read_active_url(hwnd) -> Optional[str]:
    """One-shot convenience (creates a reader each call — prefer UrlReader for the loop)."""
    return UrlReader().read(hwnd)
