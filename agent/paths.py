"""Locate the writable install/data dir consistently in BOTH source and frozen modes.

In source mode the agent runs as `python scripts/run_agent.py`; in production it runs as a
PyInstaller onefile `coverage-agent.exe`. The two locate their config/token/outbox files
differently, so this is the single source of truth.

CRITICAL: in a frozen onefile exe, `__file__` / `sys._MEIPASS` point at a temp unpack dir
that is WIPED when the process exits — never write config/token/outbox there. The exe's own
directory (`sys.executable`) is the durable, writable location the installer drops it into
(`%LOCALAPPDATA%\CoverageAgent`).
"""
from __future__ import annotations

import os
import sys

# Bump on every meaningful agent change so a deployed exe can be identified (printed by
# `coverage-agent.exe --selftest`). The exe is built out-of-band on Windows, so this is how
# you confirm which build is actually running on a machine.
AGENT_VERSION = "0.4.0"


def install_dir() -> str:
    if getattr(sys, "frozen", False):
        # onefile exe: sys.executable is .../CoverageAgent/coverage-agent.exe
        return os.path.dirname(os.path.abspath(sys.executable))
    # source mode: parent of agent/ (= repo root), matching today's INSTALL
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_path(name: str) -> str:
    """Absolute path to a config/data file next to the install dir."""
    return os.path.join(install_dir(), name)
