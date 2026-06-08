"""Server-side, DB-driven categorization (the admin-editable taxonomy).

Replaces the agent-side `contracts.categorize()` (now a fallback) for the dashboard's
own bucketing. Rules live in the `taxonomy_rule` table and are applied at ROLLUP time,
so editing a rule retroactively reclassifies all history with no re-ingest.

A rule matches by `match_type`:
  - url_path : fnmatch glob on  host + '/' + path   ('ezlynx.com/quotes/*/rating')
  - domain   : exact host or suffix match           ('github.com', '.github.com')
  - app      : exact lowercased process name (.exe stripped)
  - title    : case-insensitive substring (best-effort; titles null at 14d retention)

Rules are pre-sorted by the caller (db.taxonomy_rules: priority asc, then specificity,
then id), so the FIRST match wins.

3.9-safe, stdlib-only. Pure w.r.t. the rule list passed in; the conn is only used to
read the coarse class for the resolved sub_category.
"""
from __future__ import annotations

import os
import sys
import fnmatch
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import contracts as C  # noqa: E402

# sentinel sub returned when no rule matched → caller falls back to the stored sub_category
NO_MATCH = None


def _host_and_path(domain, url):
    """Return (host, 'host/path') lowercased for matching. Prefers the full url; falls
    back to the domain when no url is present."""
    raw = (url or domain or "").strip()
    if not raw:
        return "", ""
    if "://" not in raw:
        raw = "http://" + raw  # let urlsplit find the host
    parts = urlsplit(raw)
    host = (parts.netloc or "").lower().split("@")[-1].split(":")[0]
    path = (parts.path or "").rstrip("/")
    hp = host + path
    return host, hp


def match_rule(rule, app, domain, url):
    """Does one rule match? Returns True/False. `rule` is a dict with match_type/pattern."""
    mt = rule["match_type"]
    pat = (rule["pattern"] or "").lower()
    if mt == "app":
        proc = (app or "").lower()
        if proc.endswith(".exe"):
            proc = proc[:-4]
        return proc == pat
    host, hp = _host_and_path(domain, url)
    if mt == "domain":
        return bool(host) and (host == pat or host.endswith("." + pat))
    if mt == "url_path":
        # glob over host+path; the pattern carries its own host (e.g. 'ezlynx.com/quotes/*/rating').
        # Subdomain-tolerant: 'ezlynx.com/...' also matches 'app.ezlynx.com/...' so admins don't
        # have to enumerate every subdomain.
        if not hp:
            return False
        if fnmatch.fnmatchcase(hp, pat):
            return True
        if not pat.startswith("*"):
            return fnmatch.fnmatchcase(hp, "*." + pat) or fnmatch.fnmatchcase(hp, "*" + pat)
        return False
    if mt == "title":
        # title isn't passed here (best-effort); handled by a separate hook if ever wired
        return False
    return False


def _conferencing(app, domain):
    proc = (app or "").lower().removesuffix(".exe")
    dom = (domain or "").lower()
    return proc in C.CONFERENCING_PROCESSES or any(
        dom == d or dom.endswith("." + d) for d in C.CONFERENCING_DOMAINS)


def categorize_server(conn, rules, app, domain, url):
    """Resolve (sub_category, coarse_class, is_meeting) from the taxonomy rules.

    `rules` is the pre-sorted list from db.taxonomy_rules(conn) (first match wins).
    Returns (NO_MATCH, None, is_meeting) when no rule matched — the caller then falls
    back to the event's stored sub_category. is_meeting always reflects the conferencing
    safety-net OR a matched rule's flag, even on no-match (so meetings are never lost).
    """
    meeting_net = _conferencing(app, domain)
    for r in rules:
        if match_rule(r, app, domain, url):
            from server import db  # lazy: avoid import cycle at module load
            sub = r["sub_category"]
            coarse = db.coarse_for(conn, sub)
            return sub, coarse, bool(r["is_meeting"]) or meeting_net
    return NO_MATCH, None, meeting_net
