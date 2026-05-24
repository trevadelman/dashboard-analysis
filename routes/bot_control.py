"""
routes/bot_control.py — Autonomous bot status, control, and alerts.

Covers:
  GET  /api/bot/status
  POST /api/bot/pause
  GET  /api/bot/actions
  GET  /api/bot/blacklist
  POST /api/bot/blacklist/{symbol}
  GET  /api/alerts
"""

import json as _json
import logging
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

_ACTIONS_PATH = os.path.join("data", "bot_actions.json")


def create_router(bot, login_required) -> APIRouter:
    router = APIRouter()

    @router.get("/api/bot/status")
    async def bot_status(request: Request, _=Depends(login_required)):
        """Return the current state of the autonomous scheduler."""
        try:
            import scheduler as _sched
            status = _sched.get_status()
            status["autonomous"] = bot.config.BOT_AUTONOMOUS
            return status
        except Exception as e:
            logger.error(f"Bot status error: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.post("/api/bot/pause")
    async def bot_pause(request: Request, _=Depends(login_required)):
        """
        Pause or resume the autonomous bot without restarting the app.

        Body: { "halted": true | false }
        Sets bot_state.halted which is checked by all circuit breaker gates.

        When halting manually, halt_source is set to "manual" so that the
        daily equity snapshot at market open does NOT auto-reset this halt.
        Manual halts require an explicit resume via this endpoint.
        """
        try:
            from strategies.auto_manager import _load_state, _save_state, _log_action
            data   = await request.json()
            halted = bool(data.get("halted", True))
            state  = _load_state()
            state["halted"]      = halted
            state["halt_source"] = "manual" if halted else None
            _save_state(state)
            action = "MANUAL_HALT" if halted else "MANUAL_RESUME"
            _log_action(action, None, {}, "Bot halted by user" if halted else "Bot resumed by user")
            return {"status": "ok", "halted": halted}
        except Exception as e:
            logger.error(f"Bot pause error: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.get("/api/bot/actions")
    async def bot_actions(
        request: Request,
        limit: int = 50,
        _=Depends(login_required),
    ):
        """Return the last N entries from bot_actions.json."""
        try:
            with open(_ACTIONS_PATH) as f:
                actions = _json.load(f)
            return list(reversed(actions[-limit:]))
        except FileNotFoundError:
            return []
        except Exception as e:
            logger.error(f"Bot actions error: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.get("/api/bot/blacklist")
    async def bot_blacklist_get(_=Depends(login_required)):
        """Return the current set of symbols blacklisted from bot activity."""
        from data.settings_store import get_blacklist
        return {"blacklisted": sorted(get_blacklist())}

    @router.post("/api/bot/blacklist/{symbol}")
    async def bot_blacklist_toggle(symbol: str, _=Depends(login_required)):
        """
        Toggle a symbol in/out of the bot blacklist.
        Returns the new state: {"symbol": "AAPL", "blacklisted": true/false}
        """
        from data.settings_store import toggle_blacklist
        symbol = symbol.strip().upper()
        blacklisted = toggle_blacklist(symbol)
        return {"symbol": symbol, "blacklisted": blacklisted}

    @router.get("/api/alerts")
    async def api_alerts(
        request: Request,
        limit: int = 100,
        _=Depends(login_required),
    ):
        """
        Return the most recent high-quality setup log entries (score ≥ 85,
        swing/short timeframe, no signal fired yet).

        Entries are returned newest-first, capped at `limit` (default 100).
        """
        from data.high_quality_setups import load as hqs_load
        return hqs_load(limit=limit)

    return router
