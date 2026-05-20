"""
Alpaca profile store — encrypted credential storage backed by SQLite.

Profiles are named sets of Alpaca API credentials (key + secret + paper flag).
Keys are encrypted at rest using Fernet symmetric encryption.  The encryption
key is derived from SECRET_KEY in the environment; if SECRET_KEY is absent a
random key is generated and stored in data/profiles.key so it survives restarts.
"""

import os
import sqlite3
import base64
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

_DB_PATH  = Path(__file__).parent / "profiles.db"
_KEY_PATH = Path(__file__).parent / "profiles.key"


# ── Encryption helpers ────────────────────────────────────────────────────────

def _load_fernet() -> Fernet:
    """
    Return a Fernet instance.  Key priority:
      1. SECRET_KEY env var (padded/hashed to 32 bytes → URL-safe base64)
      2. Persisted random key in data/profiles.key
      3. Generate a new random key and persist it
    """
    secret = os.environ.get("SECRET_KEY", "")
    if secret:
        # Derive a 32-byte key from the secret so any string works
        raw = hashlib.sha256(secret.encode()).digest()
        key = base64.urlsafe_b64encode(raw)
        return Fernet(key)

    if _KEY_PATH.exists():
        key = _KEY_PATH.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        _KEY_PATH.write_bytes(key)
        logger.info(f"Generated new profile encryption key at {_KEY_PATH}")

    return Fernet(key)


_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = _load_fernet()
    return _fernet


def _encrypt(value: str) -> str:
    return _get_fernet().encrypt(value.encode()).decode()


def _decrypt(token: str) -> str:
    return _get_fernet().decrypt(token.encode()).decode()


# ── Database ──────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT    NOT NULL UNIQUE,
                api_key_enc   TEXT    NOT NULL,
                secret_key_enc TEXT   NOT NULL,
                paper_trading INTEGER NOT NULL DEFAULT 1,
                is_active     INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT    NOT NULL,
                last_used_at  TEXT
            )
        """)
        conn.commit()


_init_db()


# ── Public API ────────────────────────────────────────────────────────────────

def list_profiles() -> list[dict]:
    """Return all profiles without decrypting credentials."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, paper_trading, is_active, created_at, last_used_at "
            "FROM profiles ORDER BY last_used_at DESC, created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_active_profile() -> Optional[dict]:
    """Return the active profile with decrypted credentials, or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM profiles WHERE is_active = 1 LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return _decrypt_row(dict(row))


def get_profile(profile_id: int) -> Optional[dict]:
    """Return a single profile with decrypted credentials."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
    if not row:
        return None
    return _decrypt_row(dict(row))


def save_profile(name: str, api_key: str, secret_key: str, paper: bool) -> dict:
    """
    Create or update a profile by name.
    Returns the saved profile (without credentials).
    """
    now         = datetime.utcnow().isoformat()
    api_enc     = _encrypt(api_key)
    secret_enc  = _encrypt(secret_key)
    paper_int   = 1 if paper else 0

    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM profiles WHERE name = ?", (name,)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE profiles SET api_key_enc=?, secret_key_enc=?, paper_trading=? WHERE name=?",
                (api_enc, secret_enc, paper_int, name)
            )
            profile_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO profiles (name, api_key_enc, secret_key_enc, paper_trading, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, api_enc, secret_enc, paper_int, now)
            )
            profile_id = cur.lastrowid
        conn.commit()

    return {"id": profile_id, "name": name, "paper_trading": paper_int}


def activate_profile(profile_id: int) -> Optional[dict]:
    """
    Set a profile as active (deactivates all others).
    Returns the profile with decrypted credentials, or None if not found.
    """
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE profiles SET is_active = 0")
        conn.execute(
            "UPDATE profiles SET is_active = 1, last_used_at = ? WHERE id = ?",
            (now, profile_id)
        )
        conn.commit()
    return _decrypt_row(dict(row))


def delete_profile(profile_id: int) -> bool:
    """Delete a profile. Returns True if a row was deleted."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        conn.commit()
    return cur.rowcount > 0


def seed_from_env(api_key: str, secret_key: str, paper: bool) -> dict:
    """
    Seed a 'Default (.env)' profile from environment credentials if no profiles
    exist yet.  Activates it automatically.
    """
    profiles = list_profiles()
    if not profiles:
        profile = save_profile("Default (.env)", api_key, secret_key, paper)
        activate_profile(profile["id"])
        logger.info("Seeded default profile from .env credentials")
        return profile
    return {}


# ── Internal ──────────────────────────────────────────────────────────────────

def _decrypt_row(row: dict) -> dict:
    row["api_key"]    = _decrypt(row.pop("api_key_enc"))
    row["secret_key"] = _decrypt(row.pop("secret_key_enc"))
    return row
