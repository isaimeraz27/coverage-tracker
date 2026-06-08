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
# ValuePattern id
_UIA_VALUE_PATTERN = 10002

# AutomationId/Name hints for the address bar across browsers (best-effort; we also fall
# back to "the first Edit descendant that looks like a URL").
_OMNIBOX_HINTS = ("url_field", "address and search bar", "address field", "search or enter address")


class UrlReader:
    """Holds the IUIAutomation instance (COM init is expensive — create it once)."""

    def __init__(self):
        self._uia = None
        self._ok = True   # flips False permanently if COM is unavailable, so we stop trying
        self._init_uia()

    def _init_uia(self):
        try:
            import comtypes.client  # lazy: only on Windows w/ comtypes installed
            # CUIAutomation CLSID; comtypes generates the interface from the type library
            self._uia = comtypes.client.CreateObject(
                "{ff48dba4-60ef-4201-aa87-54103eef594e}",  # CLSID_CUIAutomation
                interface=None,
            )
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
        """Find the address-bar Edit control and read its ValuePattern value."""
        try:
            import comtypes
            # Condition: ControlType == Edit
            cond = self._uia.CreatePropertyCondition(_UIA_CONTROLTYPE_PROPERTY, _UIA_EDIT)
            # TreeScope_Descendants = 4
            edits = root.FindAll(4, cond)
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
        try:
            pat = element.GetCurrentPattern(_UIA_VALUE_PATTERN)
            if not pat:
                return None
            import comtypes
            vp = pat.QueryInterface(comtypes.gen.UIAutomationClient.IUIAutomationValuePattern) \
                if hasattr(comtypes, "gen") else pat
            return vp.CurrentValue or None
        except Exception:
            try:
                return element.GetCurrentPropertyValue(10003) or None  # ValueValue prop fallback
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
