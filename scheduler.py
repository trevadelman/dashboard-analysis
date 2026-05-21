"""
scheduler.py — APScheduler wrapper for the autonomous trading bot.

Registers all background jobs and starts the scheduler alongside the Flask app.
All jobs run in a thread pool so they never block the request thread.

Equity watchlist jobs
─────────────────────
  market_open_snapshot  — 9:30 ET daily (Mon–Fri): snapshot equity, reset halt
  long_review           — 9:35 ET daily (Mon–Fri): review long (daily) positions
  long_scan             — 9:40 ET daily (Mon–Fri): scan for long signals
  exit_poller           — every 5 min: detect closed positions
  swing_review          — every 60 min: review swing positions
  swing_scan            — every 60 min: scan for swing signals
  short_scan            — every 15 min: scan for short signals

Crypto watchlist jobs (24/7 — no market-hours gates)
─────────────────────────────────────────────────────
  market_open_snapshot  — midnight ET daily: snapshot equity, reset halt
  long_review           — 00:05 ET daily: review long (daily) positions
  long_scan             — 00:10 ET daily: scan for long signals
  exit_poller           — every 5 min: detect closed positions (unchanged)
  swing_review          — every 60 min: review swing positions (unchanged)
  swing_scan            — every 60 min: scan for swing signals (unchanged)
  short_scan            — every 15 min: scan for short signals (unchanged)
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# Module-level scheduler instance — started once, shared across the app
_scheduler: BackgroundScheduler | None = None


# ── Job wrappers ──────────────────────────────────────────────────────────────
# Each wrapper loads fresh state, calls the appropriate auto_manager function,
# and handles exceptions so a single job failure never kills the scheduler.

def _job_market_open_snapshot(bot, config) -> None:
    try:
        from strategies.auto_manager import _load_state, snapshot_daily_equity
        state = _load_state()
        snapshot_daily_equity(bot, state)
    except Exception as e:
        logger.error(f"[scheduler] market_open_snapshot failed: {e}")


def _job_exit_poller(bot, config) -> None:
    try:
        from strategies.auto_manager import _load_state, detect_exits
        # Exit detection runs whenever any position could have been closed.
        # Crypto positions can close 24/7; equity positions only during market hours.
        # We run the poller unconditionally and let detect_exits handle the routing.
        state = _load_state()
        detect_exits(bot, state)
    except Exception as e:
        logger.error(f"[scheduler] exit_poller failed: {e}")


def _job_long_review(bot, config) -> None:
    try:
        from strategies.auto_manager import _load_state, run_position_review
        state = _load_state()
        run_position_review(bot, config, state, timeframe="long")
    except Exception as e:
        logger.error(f"[scheduler] long_review failed: {e}")


def _job_swing_review(bot, config) -> None:
    try:
        from strategies.auto_manager import _load_state, run_position_review
        state = _load_state()
        run_position_review(bot, config, state, timeframe="swing")
    except Exception as e:
        logger.error(f"[scheduler] swing_review failed: {e}")


def _job_long_scan(bot, config) -> None:
    try:
        from strategies.auto_manager import _load_state, run_entry_scan
        state = _load_state()
        run_entry_scan(bot, config, state, timeframe="long")
    except Exception as e:
        logger.error(f"[scheduler] long_scan failed: {e}")


def _job_swing_scan(bot, config) -> None:
    try:
        from strategies.auto_manager import _load_state, run_entry_scan
        state = _load_state()
        run_entry_scan(bot, config, state, timeframe="swing")
    except Exception as e:
        logger.error(f"[scheduler] swing_scan failed: {e}")


def _job_short_scan(bot, config) -> None:
    try:
        from strategies.auto_manager import _load_state, run_entry_scan
        state = _load_state()
        run_entry_scan(bot, config, state, timeframe="short")
    except Exception as e:
        logger.error(f"[scheduler] short_scan failed: {e}")


# ── Scheduler lifecycle ───────────────────────────────────────────────────────

def start(bot, config) -> BackgroundScheduler:
    """
    Start the background scheduler and register all jobs.

    The trigger schedule for the three daily jobs (snapshot, long_review, long_scan)
    depends on whether the configured watchlist is a crypto list:

      Equity  — 9:30 / 9:35 / 9:40 ET Mon–Fri (NYSE market open)
      Crypto  — 00:00 / 00:05 / 00:10 ET daily (midnight, 7 days a week)

    The interval jobs (exit_poller, swing_review, swing_scan, short_scan) run on
    the same cadence regardless of asset class — run_position_review and
    run_entry_scan gate themselves internally via is_tradeable_now().

    Safe to call multiple times — returns the existing scheduler if already running.
    """
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        logger.info("[scheduler] Already running — skipping start")
        return _scheduler

    from strategies.auto_manager import is_crypto_watchlist
    crypto_mode = is_crypto_watchlist(config)

    _scheduler = BackgroundScheduler(timezone=_ET)

    # ── Daily jobs — trigger depends on asset class ───────────────────────────

    if crypto_mode:
        # Crypto: midnight ET, 7 days a week
        snapshot_trigger    = CronTrigger(hour=0, minute=0,  timezone=_ET)
        long_review_trigger = CronTrigger(hour=0, minute=5,  timezone=_ET)
        long_scan_trigger   = CronTrigger(hour=0, minute=10, timezone=_ET)
        snapshot_name    = "Crypto midnight equity snapshot"
        long_review_name = "Long position review (crypto)"
        long_scan_name   = "Long timeframe entry scan (crypto)"
        logger.info("[scheduler] Crypto watchlist detected — daily jobs scheduled at midnight ET")
    else:
        # Equity: 9:30 / 9:35 / 9:40 ET Mon–Fri
        snapshot_trigger    = CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=_ET)
        long_review_trigger = CronTrigger(day_of_week="mon-fri", hour=9, minute=35, timezone=_ET)
        long_scan_trigger   = CronTrigger(day_of_week="mon-fri", hour=9, minute=40, timezone=_ET)
        snapshot_name    = "Market open equity snapshot"
        long_review_name = "Long position review"
        long_scan_name   = "Long timeframe entry scan"
        logger.info("[scheduler] Equity watchlist detected — daily jobs scheduled at 9:30 ET Mon–Fri")

    _scheduler.add_job(
        _job_market_open_snapshot,
        snapshot_trigger,
        args=[bot, config],
        id="market_open_snapshot",
        name=snapshot_name,
        replace_existing=True,
        misfire_grace_time=300,
    )

    _scheduler.add_job(
        _job_long_review,
        long_review_trigger,
        args=[bot, config],
        id="long_review",
        name=long_review_name,
        replace_existing=True,
        misfire_grace_time=300,
    )

    _scheduler.add_job(
        _job_long_scan,
        long_scan_trigger,
        args=[bot, config],
        id="long_scan",
        name=long_scan_name,
        replace_existing=True,
        misfire_grace_time=300,
    )

    # ── Interval jobs — same cadence for both equity and crypto ───────────────

    # Every 5 min — exit detection poller
    _scheduler.add_job(
        _job_exit_poller,
        IntervalTrigger(minutes=5),
        args=[bot, config],
        id="exit_poller",
        name="Exit detection poller",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Every 60 min — swing position review
    _scheduler.add_job(
        _job_swing_review,
        IntervalTrigger(minutes=60),
        args=[bot, config],
        id="swing_review",
        name="Swing position review",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Every 60 min — swing entry scan
    _scheduler.add_job(
        _job_swing_scan,
        IntervalTrigger(minutes=60),
        args=[bot, config],
        id="swing_scan",
        name="Swing timeframe entry scan",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Every 15 min — short entry scan
    _scheduler.add_job(
        _job_short_scan,
        IntervalTrigger(minutes=15),
        args=[bot, config],
        id="short_scan",
        name="Short timeframe entry scan",
        replace_existing=True,
        misfire_grace_time=60,
    )

    _scheduler.start()
    mode_label = "crypto (24/7)" if crypto_mode else "equity (market hours)"
    logger.info(f"[scheduler] Started — all jobs registered ({mode_label} mode)")
    return _scheduler


def stop() -> None:
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[scheduler] Stopped")
    _scheduler = None


def get_status() -> dict:
    """
    Return a status dict for the /api/bot/status endpoint.

    Returns:
        {
          "running": bool,
          "autonomous": bool,
          "halted": bool,
          "daily_open_equity": float | None,
          "jobs": [{ "id", "name", "next_run" }, ...],
          "today_actions": int,
          "last_actions": [...]   # last 10 entries from bot_actions.json
        }
    """
    import json, os
    from strategies.auto_manager import _load_state, _ACTIONS_PATH

    state = _load_state()

    jobs = []
    if _scheduler and _scheduler.running:
        for job in _scheduler.get_jobs():
            next_run = job.next_run_time
            jobs.append({
                "id":       job.id,
                "name":     job.name,
                "next_run": next_run.isoformat() if next_run else None,
            })

    # Count today's actions and return the last 10
    today_str = datetime.now(_ET).strftime("%Y-%m-%d")
    today_actions = 0
    last_actions  = []
    try:
        with open(_ACTIONS_PATH) as f:
            actions = json.load(f)
        for a in actions:
            if a.get("timestamp", "").startswith(today_str):
                today_actions += 1
        last_actions = actions[-10:]
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"Could not read bot_actions.json: {e}")

    return {
        "running":            _scheduler is not None and _scheduler.running,
        "halted":             state.get("halted", False),
        "daily_open_equity":  state.get("daily_open_equity"),
        "jobs":               jobs,
        "today_actions":      today_actions,
        "last_actions":       last_actions,
    }
