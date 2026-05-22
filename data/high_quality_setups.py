"""
data/high_quality_setups.py — Append-only log of high-quality setups.

A setup is logged when the scanner scores a swing or short timeframe symbol
at 85+ but no signal has fired yet.  This lets us track how long a symbol
stays in a compressed, high-quality state before (or without) triggering.

File: data/high_quality_setups.json
Format: JSON array, newest entries last.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_PATH = os.path.join(os.path.dirname(__file__), "high_quality_setups.json")


def append_setup(entry: dict) -> None:
    """Append a single setup entry to the log file."""
    entries = _load_raw()
    if "timestamp" not in entry:
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    entries.append(entry)
    _write(entries)


def load(limit: int = 100) -> list[dict]:
    """Return the most recent `limit` entries, newest first."""
    entries = _load_raw()
    return list(reversed(entries[-limit:]))


def clear() -> None:
    """Wipe the log file."""
    _write([])


# ── Internal ──────────────────────────────────────────────────────────────────

def _load_raw() -> list:
    if not os.path.exists(_PATH):
        return []
    try:
        with open(_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"[high_quality_setups] Could not read log: {e}")
        return []


def _write(entries: list) -> None:
    try:
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        with open(_PATH, "w") as f:
            json.dump(entries, f)
    except Exception as e:
        logger.warning(f"[high_quality_setups] Could not write log: {e}")
