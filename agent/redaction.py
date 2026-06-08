"""Capture-time privacy safeguards (§3.3/§3.8 + agent §B).

Runs on the employee's machine BEFORE anything is buffered or sent:
  - skip known-sensitive apps entirely (password managers, banking),
  - drop window titles that look like a credential/password context,
  - strip URLs to registrable domain unless full-URL is enabled for the role.
Sensitive data therefore never leaves the device.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import contracts as C  # noqa: E402

SENSITIVE_APPS = {
    "keepass", "keepassxc", "1password", "1password7", "bitwarden",
    "lastpass", "dashlane", "protonpass",
}

SENSITIVE_TITLE_HINTS = (
    "password", "passcode", "sign in", "log in", "login", "2fa",
    "authenticator", "one-time", "otp", "bank", "wallet", "seed phrase",
)

# Registrable domains where the FULL URL must never be captured, even when full-URL
# capture is enabled for the role. The domain (and category) may still be recorded, but
# the path/query — which can carry account numbers, claim IDs, health details — is dropped
# on-device before buffering. Admins should keep this airtight when turning full-URL on.
SENSITIVE_DOMAINS = {
    # banking / payments
    "chase.com", "bankofamerica.com", "wellsfargo.com", "paypal.com", "venmo.com",
    "americanexpress.com", "capitalone.com",
    # health / pharmacy
    "myhealth.com", "mychart.com", "cvs.com", "walgreens.com", "anthem.com", "kaiserpermanente.org",
    # password / identity
    "lastpass.com", "1password.com", "bitwarden.com",
}


def is_sensitive_app(process: str) -> bool:
    return (process or "").lower().removesuffix(".exe") in SENSITIVE_APPS


def looks_sensitive_title(title: str | None) -> bool:
    t = (title or "").lower()
    return any(h in t for h in SENSITIVE_TITLE_HINTS)


def is_sensitive_domain(domain: str | None) -> bool:
    reg = C.registrable_domain(domain or "")
    return bool(reg) and reg in SENSITIVE_DOMAINS


def redact_sample(sample, allow_full_url: bool = False):
    """Return a possibly-modified copy of the sample, or None to drop it entirely.

    When `allow_full_url` is False (default), the full URL is discarded and only the
    registrable domain is kept. When True, the full URL is preserved EXCEPT on sensitive
    domains (banking/health/identity), where the path is always dropped to domain-only.
    """
    if is_sensitive_app(sample.process):
        return None  # do not record sensitive apps at all
    title = sample.window_title
    if looks_sensitive_title(title):
        title = "[redacted]"

    # `domain` may arrive carrying a full URL (the heuristic capture). Resolve both the
    # registrable domain (always kept) and the full URL (kept only when allowed + safe).
    raw = sample.domain
    full = getattr(sample, "url", None) or raw  # url field preferred; else domain may hold it
    reg = C.registrable_domain(raw) if raw else None

    if allow_full_url and not is_sensitive_domain(raw):
        sample.url = full if (full and "/" in str(full)) else None
        sample.domain = reg
    else:
        # domain-only: drop the full URL entirely (sensitive domain or full-URL disabled)
        sample.url = None
        sample.domain = reg

    sample.window_title = title
    return sample
