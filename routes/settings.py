"""
routes/settings.py — App settings (AI, risk, bot, password).

Covers:
  GET  /api/settings
  POST /api/settings
  POST /api/settings/test-ai
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from data.settings_store import get_setting, set_setting, get_all_settings

logger = logging.getLogger(__name__)


def create_router(bot, login_required) -> APIRouter:
    router = APIRouter()

    @router.get("/api/settings")
    async def api_get_settings(request: Request, _=Depends(login_required)):
        """Return all app settings (decrypted, password masked)."""
        settings = get_all_settings()
        settings["dashboard_password"] = "••••••••" if settings.get("dashboard_password") else ""
        return settings

    @router.post("/api/settings")
    async def api_save_settings(request: Request, _=Depends(login_required)):
        """Save app settings. Password is only updated if a non-placeholder value is sent."""
        try:
            data = await request.json()

            if "ai_base_url" in data:
                set_setting("ai_base_url", data["ai_base_url"].strip())
            if "ai_api_key" in data:
                set_setting("ai_api_key", data["ai_api_key"].strip())
            if "ai_model" in data:
                set_setting("ai_model", data["ai_model"].strip())

            if "max_positions" in data:
                val = int(data["max_positions"])
                set_setting("max_positions", str(val))
                bot.config.MAX_POSITIONS = val
                bot.max_positions        = val
            if "risk_percentage" in data:
                val = float(data["risk_percentage"])
                set_setting("risk_percentage", str(val))
                bot.config.RISK_PERCENTAGE = val
                bot.risk_percentage        = val

            # Bot autonomous settings — live reload (no restart required)
            if "bot_autonomous" in data:
                val = str(data["bot_autonomous"]).lower()
                set_setting("bot_autonomous", val)
                bot.config.BOT_AUTONOMOUS = val == "true"
            if "bot_scan_watchlist" in data:
                val = str(data["bot_scan_watchlist"]).strip()
                set_setting("bot_scan_watchlist", val)
                bot.config.BOT_SCAN_WATCHLIST = val
            if "bot_max_daily_loss_pct" in data:
                val = float(data["bot_max_daily_loss_pct"])
                set_setting("bot_max_daily_loss_pct", str(val))
                bot.config.BOT_MAX_DAILY_LOSS_PCT = val
            if "bot_entry_cooldown_hours" in data:
                val = int(data["bot_entry_cooldown_hours"])
                set_setting("bot_entry_cooldown_hours", str(val))
                bot.config.BOT_ENTRY_COOLDOWN_HOURS = val
            if "bot_review_timeframes" in data:
                val = str(data["bot_review_timeframes"]).strip()
                set_setting("bot_review_timeframes", val)
                bot.config.BOT_REVIEW_TIMEFRAMES = [t.strip() for t in val.split(",") if t.strip()]
            if "bot_max_portfolio_heat_pct" in data:
                val = float(data["bot_max_portfolio_heat_pct"])
                set_setting("bot_max_portfolio_heat_pct", str(val))
                bot.config.BOT_MAX_PORTFOLIO_HEAT_PCT = val
            if "bot_max_risk_per_trade_pct" in data:
                val = float(data["bot_max_risk_per_trade_pct"])
                set_setting("bot_max_risk_per_trade_pct", str(val))
                bot.config.BOT_MAX_RISK_PER_TRADE_PCT = val
            if "bot_min_risk_pct" in data:
                val = float(data["bot_min_risk_pct"])
                set_setting("bot_min_risk_pct", str(val))
                bot.config.BOT_MIN_RISK_PCT = val

            # Password — only update if a real value was sent (not the placeholder)
            if "dashboard_password" in data:
                new_pw = data["dashboard_password"]
                if new_pw and new_pw != "••••••••":
                    set_setting("dashboard_password", new_pw)
                    logger.info("Dashboard password updated")
                elif new_pw == "":
                    set_setting("dashboard_password", "")
                    logger.info("Dashboard password removed — app is now passwordless")

            # Reload AI client with new settings
            new_base_url = get_setting("ai_base_url")
            new_api_key  = get_setting("ai_api_key")
            new_model    = get_setting("ai_model")
            bot.ai.client = bot.ai.client.__class__(base_url=new_base_url, api_key=new_api_key)
            bot.ai.model  = new_model
            bot.config.OPENAI_BASE_URL = new_base_url
            bot.config.OPENAI_API_KEY  = new_api_key
            bot.config.OLLAMA_MODEL    = new_model

            return {"status": "ok"}

        except Exception as e:
            logger.error(f"Error saving settings: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.post("/api/settings/test-ai")
    async def api_test_ai(request: Request, _=Depends(login_required)):
        """Test connectivity to the configured AI endpoint."""
        try:
            data     = await request.json()
            base_url = data.get("ai_base_url", get_setting("ai_base_url"))
            api_key  = data.get("ai_api_key", get_setting("ai_api_key"))

            from openai import OpenAI
            client = OpenAI(base_url=base_url, api_key=api_key)
            models = [m.id for m in client.models.list().data]
            return {"status": "ok", "models": models}
        except Exception as e:
            return JSONResponse({"status": "error", "message": str(e)}, status_code=200)

    return router
