"""
scheduler.py — APScheduler wrapper for the autonomous trading bot.

Registers all background jobs and starts the scheduler alongside the Flask app.
All jobs run in a thread pool so they never block the request thread.

Jobs
────
  market_open_snapshot  — 9:30 ET daily (Mon–Fri): snapshot equity, reset halt
  long_review           — 9:35 ET daily (Mon–Fri): review long (daily) positions
  exit_poller           — every 5 min during market hours: detect closed positions
  swing_review          — every hour during market hours: review swing positions
  swing_scan            — every hour during market hours: scan for swing signals
  short_scan            — every 15 min during market hours: scan for short signals
  long_scan             — 9:40 ET daily (Mon–Fri): scan for long signals
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
        from strategies.auto_manager import _load_state, detect_exits, is_market_hours
        if not is_market_hours():
            return
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
    Safe to call multiple times — returns the existing scheduler if already running.
    """
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        logger.info("[scheduler] Already running — skipping start")
        return _scheduler

    _scheduler = BackgroundScheduler(timezone=_ET)

    # ── Daily jobs (ET cron) ──────────────────────────────────────────────────

    # 9:30 ET — snapshot opening equity, reset daily halt
    _scheduler.add_job(
        _job_market_open_snapshot,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=_ET),
        args=[bot, config],
        id="market_open_snapshot",
        name="Market open equity snapshot",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # 9:35 ET — review long (daily) positions
    _scheduler.add_job(
        _job_long_review,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=35, timezone=_ET),
        args=[bot, config],
        id="long_review",
        name="Long position review",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # 9:40 ET — scan for long signals
    _scheduler.add_job(
        _job_long_scan,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=40, timezone=_ET),
        args=[bot, config],
        id="long_scan",
        name="Long timeframe entry scan",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # ── Interval jobs ─────────────────────────────────────────────────────────

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
    logger.info("[scheduler] Started — all jobs registered")
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
