"""
App settings store — key/value pairs backed by SQLite.
Sensitive values (password, AI API key) are encrypted with the same
Fernet key used by profile_store.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from data.profile_store import _encrypt, _decrypt, _connect as _profile_connect

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent / "profiles.db"


# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULTS: dict[str, Any] = {
    "dashboard_password": "",          # empty = no password required
    "ai_base_url":        "http://localhost:11434/v1",
    "ai_api_key":         "ollama",
    "ai_model":           "gemma3:4b-it-qat",
    "max_positions":        "5",
    "risk_percentage":      "2.0",
    "max_position_pct":     "20.0",   # max % of equity in a single position
    # ── Autonomous bot ────────────────────────────────────────────────────────
    "bot_autonomous":           "false",
    "bot_scan_watchlist":       "sp500_top100",
    "bot_max_daily_loss_pct":   "2.0",
    "bot_entry_cooldown_hours": "24",
    "bot_review_timeframes":    "swing,long",
    # Comma-separated symbols the bot must never touch (entry or management).
    # Set via the positions page toggle; persists across restarts.
    "bot_blacklisted_symbols":  "",
}

# Keys whose values are encrypted at rest
_ENCRYPTED_KEYS = {"dashboard_password", "ai_api_key"}


# ── DB init ───────────────────────────────────────────────────────────────────

def _connect():
    import sqlite3
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL DEFAULT '',
                encrypted  INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT
            )
        """)
        conn.commit()


_init_db()


# ── Public API ────────────────────────────────────────────────────────────────

def get_setting(key: str) -> Optional[str]:
    """Return the setting value for key, or the default if not set."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT value, encrypted FROM settings WHERE key = ?", (key,)
        ).fetchone()

    if row is None:
        return DEFAULTS.get(key)

    value = row["value"]
    if row["encrypted"] and value:
        try:
            value = _decrypt(value)
        except Exception:
            logger.warning(f"Failed to decrypt setting '{key}' — returning empty string")
            return ""
    return value


def set_setting(key: str, value: str) -> None:
    """Persist a setting. Encrypts sensitive keys automatically."""
    encrypted = key in _ENCRYPTED_KEYS
    stored    = _encrypt(value) if (encrypted and value) else value
    now       = datetime.utcnow().isoformat()

    with _connect() as conn:
        conn.execute("""
            INSERT INTO settings (key, value, encrypted, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                                           encrypted=excluded.encrypted,
                                           updated_at=excluded.updated_at
        """, (key, stored, 1 if encrypted else 0, now))
        conn.commit()


def get_all_settings() -> dict[str, str]:
    """Return all settings as a plain dict (decrypted, safe to send to UI)."""
    result = dict(DEFAULTS)
    with _connect() as conn:
        rows = conn.execute("SELECT key, value, encrypted FROM settings").fetchall()
    for row in rows:
        value = row["value"]
        if row["encrypted"] and value:
            try:
                value = _decrypt(value)
            except Exception:
                value = ""
        result[row["key"]] = value
    return result


def get_blacklist() -> set[str]:
    """Return the set of symbols currently blacklisted from bot activity."""
    raw = get_setting("bot_blacklisted_symbols") or ""
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def toggle_blacklist(symbol: str) -> bool:
    """
    Toggle symbol in/out of the bot blacklist.

    Returns True if the symbol is now blacklisted, False if it was removed.
    """
    symbol = symbol.strip().upper()
    current = get_blacklist()
    if symbol in current:
        current.discard(symbol)
        blacklisted = False
    else:
        current.add(symbol)
        blacklisted = True
    set_setting("bot_blacklisted_symbols", ",".join(sorted(current)))
    return blacklisted


def init_from_env() -> None:
    """
    One-time bootstrap: if settings table is empty, seed from environment
    variables / .env so existing users don't lose their config.
    """
    import os
    from dotenv import load_dotenv
    load_dotenv()

    with _connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
    if count > 0:
        return  # Already initialized — don't overwrite

    seeds = {
        "dashboard_password": os.getenv("DASHBOARD_PASSWORD", ""),
        "ai_base_url":        os.getenv("OPENAI_BASE_URL", DEFAULTS["ai_base_url"]),
        "ai_api_key":         os.getenv("OPENAI_API_KEY", DEFAULTS["ai_api_key"]),
        "ai_model":           os.getenv("OLLAMA_MODEL", DEFAULTS["ai_model"]),
        "max_positions":      os.getenv("MAX_POSITIONS", DEFAULTS["max_positions"]),
        "risk_percentage":    os.getenv("RISK_PERCENTAGE", DEFAULTS["risk_percentage"]),
    }
    for key, value in seeds.items():
        set_setting(key, value)
    logger.info("Settings initialized from environment / defaults")
