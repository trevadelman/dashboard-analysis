"""
data/watchlist.py — Flat watchlist backed by data/watchlist.json.

Each entry is a snapshot of a setup at the moment the user added it:
  id, symbol, added_at, timeframe, price_at_add,
  score_at_add, grade_at_add, signal_at_add,
  tier1_at_add, tier2_at_add, notes

The file is a JSON array.  All mutations go through this module so the
on-disk format stays consistent.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PATH = Path(__file__).parent / "watchlist.json"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _load_raw() -> list[dict]:
    if not _PATH.exists():
        return []
    try:
        with open(_PATH, "r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"watchlist: could not read {_PATH}: {e}")
        return []


def _save(entries: list[dict]) -> None:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_PATH, "w") as f:
            json.dump(entries, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"watchlist: could not write {_PATH}: {e}")


# ── Public API ────────────────────────────────────────────────────────────────

def load() -> list[dict]:
    """Return all watchlist entries, newest first."""
    entries = _load_raw()
    return sorted(entries, key=lambda e: e.get("added_at", ""), reverse=True)


def add(
    symbol: str,
    timeframe: str,
    price_at_add: Optional[float] = None,
    score_at_add: Optional[str] = None,
    grade_at_add: Optional[str] = None,
    signal_at_add: Optional[str] = None,
    tier1_at_add: Optional[str] = None,
    tier2_at_add: Optional[str] = None,
    notes: str = "",
) -> dict:
    """
    Append a new entry and persist.  Returns the saved entry dict.
    Raises ValueError if the symbol is already on the watchlist.
    """
    entries = _load_raw()
    symbol  = symbol.strip().upper()

    # Prevent duplicates — one entry per symbol (regardless of timeframe)
    if any(e.get("symbol") == symbol for e in entries):
        raise ValueError(f"{symbol} is already on the watchlist")

    entry = {
        "id":           str(uuid.uuid4()),
        "symbol":       symbol,
        "added_at":     datetime.now(timezone.utc).isoformat(),
        "timeframe":    timeframe,
        "price_at_add": price_at_add,
        "score_at_add": score_at_add,
        "grade_at_add": grade_at_add,
        "signal_at_add": signal_at_add,
        "tier1_at_add": tier1_at_add,
        "tier2_at_add": tier2_at_add,
        "notes":        notes,
    }
    entries.append(entry)
    _save(entries)
    return entry


def remove(entry_id: str) -> bool:
    """Remove entry by id.  Returns True if found and removed."""
    entries = _load_raw()
    before  = len(entries)
    entries = [e for e in entries if e.get("id") != entry_id]
    if len(entries) == before:
        return False
    _save(entries)
    return True


def update_notes(entry_id: str, notes: str) -> Optional[dict]:
    """Update the notes field for an entry.  Returns the updated entry or None."""
    entries = _load_raw()
    for e in entries:
        if e.get("id") == entry_id:
            e["notes"] = notes
            _save(entries)
            return e
    return None
