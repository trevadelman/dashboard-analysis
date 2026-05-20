"""
Trading Bot Dashboard
FastAPI web interface with password auth, Alpaca data, and AI analysis.
"""

import logging
import os
from datetime import datetime
from functools import wraps

import pandas as pd
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from bot import TradingBot
from config import Config
from data.profile_store import (
    list_profiles, get_profile, save_profile,
    activate_profile, delete_profile, seed_from_env,
)
from data.settings_store import get_setting, set_setting, get_all_settings

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")


def create_dashboard(bot: TradingBot) -> FastAPI:
    """
    Create FastAPI dashboard for the trading bot.

    Args:
        bot (TradingBot): Trading bot instance

    Returns:
        FastAPI: FastAPI application
    """
    app = FastAPI(title="Alpaca Trading Dashboard", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory="static"), name="static")
    app.add_middleware(
        SessionMiddleware,
        secret_key=os.environ.get("SECRET_KEY", "dev-key-change-in-production"),
        max_age=86400,  # 24 hours
    )

    # ===== AUTH =====

    def _password_required() -> bool:
        """Return True if a dashboard password has been set."""
        pw = get_setting("dashboard_password")
        return bool(pw and pw.strip())

    def login_required(request: Request):
        if _password_required() and not request.session.get("authenticated"):
            raise HTTPException(status_code=307, headers={"Location": "/login"})

    # ===== PAGES =====

    @app.get("/login", response_class=HTMLResponse)
    async def login_get(request: Request):
        if not _password_required():
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login", response_class=HTMLResponse)
    async def login_post(request: Request, password: str = Form(...)):
        stored_pw = get_setting("dashboard_password") or ""
        if password == stored_pw:
            request.session["authenticated"] = True
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse(request, "login.html", {"error": "Incorrect password."})

    @app.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        if _password_required():
            return RedirectResponse(url="/login", status_code=303)
        return RedirectResponse(url="/", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, _=Depends(login_required)):
        return templates.TemplateResponse(request, "dashboard.html", {"active_page": "dashboard"})

    @app.get("/scanner", response_class=HTMLResponse)
    async def scanner_page(request: Request, _=Depends(login_required)):
        return templates.TemplateResponse(request, "scanner.html", {"active_page": "scanner"})

    # ===== API ROUTES =====

    @app.get("/api/account")
    async def get_account(request: Request, _=Depends(login_required)):
        if not bot._require_api():
            return {}   # No credentials — return empty dict, not an error
        try:
            return bot.get_account()
        except Exception as e:
            logger.error(f"Error getting account: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/positions")
    async def get_positions(request: Request, _=Depends(login_required)):
        if not bot._require_api():
            return []   # No credentials — return empty list
        try:
            return bot.get_positions()
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/orders")
    async def get_orders(request: Request, status: str = "all", _=Depends(login_required)):
        if not bot._require_api():
            return []
        try:
            return bot.get_orders(status)
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/trades")
    async def get_trades(request: Request, limit: int = 50, _=Depends(login_required)):
        if not bot._require_api():
            return []
        try:
            orders = bot.get_orders(status="all")
            trades = [
                {
                    "timestamp": o.get("created_at", ""),
                    "symbol": o.get("symbol", ""),
                    "side": o.get("side", ""),
                    "quantity": o.get("qty", 0),
                    "status": o.get("status", ""),
                    "order_id": o.get("id", ""),
                    "type": o.get("type", ""),
                }
                for o in orders[:limit]
            ]
            return trades
        except Exception as e:
            logger.error(f"Error getting trades: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/analyze")
    async def analyze(request: Request, _=Depends(login_required)):
        try:
            data = await request.json()
            symbol = data.get("symbol", "SPY")
            use_ai = data.get("use_ai", True)
            params = data.get("params")
            return bot.analyze_symbol(symbol, use_ai_confirmation=use_ai, params=params)
        except Exception as e:
            logger.error(f"Error analyzing symbol: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/analyze/stream")
    async def analyze_stream(
        request: Request,
        symbol: str = "SPY",
        use_ai: bool = True,
        _=Depends(login_required),
    ):
        from fastapi.responses import StreamingResponse

        def event_generator():
            try:
                for chunk in bot.analyze_symbol_stream(symbol, use_ai_confirmation=use_ai):
                    yield chunk
            except Exception as e:
                import json as _json
                logger.error(f"Error in analysis stream for {symbol}: {e}")
                yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Profile management ────────────────────────────────────────────────────

    @app.get("/api/profiles")
    async def api_list_profiles(request: Request, _=Depends(login_required)):
        """List all saved profiles (no credentials returned)."""
        return list_profiles()

    @app.post("/api/profiles")
    async def api_save_profile(request: Request, _=Depends(login_required)):
        """
        Create or update a named profile.
        Optionally activate it immediately (activate=true).
        """
        try:
            data       = await request.json()
            name       = data.get("name", "").strip()
            api_key    = data.get("api_key", "").strip()
            secret_key = data.get("secret_key", "").strip()
            paper      = data.get("paper_trading", True)
            activate   = data.get("activate", False)

            if not name or not api_key or not secret_key:
                return JSONResponse({"error": "name, api_key, and secret_key are required"}, status_code=400)

            profile = save_profile(name, api_key, secret_key, paper)

            if activate:
                return await _activate_and_connect(profile["id"])

            return {"status": "saved", "profile": profile}

        except Exception as e:
            logger.error(f"Error saving profile: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/profiles/{profile_id}/activate")
    async def api_activate_profile(profile_id: int, request: Request, _=Depends(login_required)):
        """Activate a saved profile and reconnect the bot."""
        return await _activate_and_connect(profile_id)

    @app.delete("/api/profiles/{profile_id}")
    async def api_delete_profile(profile_id: int, request: Request, _=Depends(login_required)):
        """Delete a saved profile."""
        deleted = delete_profile(profile_id)
        if not deleted:
            return JSONResponse({"error": "Profile not found"}, status_code=404)
        return {"status": "deleted"}

    async def _activate_and_connect(profile_id: int):
        """
        Activate a profile and reconnect the bot's Alpaca API client.
        Verifies the credentials work before committing.
        """
        from alpaca_trade_api.rest import REST

        profile = activate_profile(profile_id)
        if not profile:
            return JSONResponse({"error": "Profile not found"}, status_code=404)

        try:
            base_url = "https://paper-api.alpaca.markets" if profile["paper_trading"] else "https://api.alpaca.markets"
            new_api  = REST(key_id=profile["api_key"], secret_key=profile["secret_key"], base_url=base_url)
            account  = new_api.get_account()

            bot.api = new_api
            bot.config.ALPACA_API_KEY    = profile["api_key"]
            bot.config.ALPACA_SECRET_KEY = profile["secret_key"]
            bot.config.PAPER_TRADING     = bool(profile["paper_trading"])
            bot.config.ALPACA_BASE_URL   = base_url
            bot._data_cache.clear()

            logger.info(f"Activated profile '{profile['name']}' (paper={profile['paper_trading']})")
            return {
                "status": "ok",
                "profile_id":   profile_id,
                "profile_name": profile["name"],
                "paper":        bool(profile["paper_trading"]),
                "equity":       float(account.equity),
            }

        except Exception as e:
            logger.warning(f"Profile activation failed for id={profile_id}: {e}")
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/api/alpaca/credentials")
    async def update_alpaca_credentials(request: Request, _=Depends(login_required)):
        """
        Quick-connect without saving a profile.
        Optionally save as a named profile (provide 'name' in body).
        """
        try:
            data       = await request.json()
            api_key    = data.get("api_key", "").strip()
            secret_key = data.get("secret_key", "").strip()
            paper      = data.get("paper_trading", True)
            name       = data.get("name", "").strip()

            if not api_key or not secret_key:
                return JSONResponse({"error": "api_key and secret_key are required"}, status_code=400)

            from alpaca_trade_api.rest import REST

            base_url = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
            new_api  = REST(key_id=api_key, secret_key=secret_key, base_url=base_url)
            account  = new_api.get_account()

            bot.api = new_api
            bot.config.ALPACA_API_KEY    = api_key
            bot.config.ALPACA_SECRET_KEY = secret_key
            bot.config.PAPER_TRADING     = paper
            bot.config.ALPACA_BASE_URL   = base_url
            bot._data_cache.clear()

            saved_profile = None
            if name:
                saved_profile = save_profile(name, api_key, secret_key, paper)
                activate_profile(saved_profile["id"])

            logger.info(f"Alpaca credentials updated via UI (paper={paper}, saved={bool(name)})")
            return {
                "status":  "ok",
                "equity":  float(account.equity),
                "paper":   paper,
                "profile": saved_profile,
            }

        except Exception as e:
            logger.warning(f"Alpaca credential update failed: {e}")
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/analyze/stream/multi")
    async def analyze_stream_multi(
        request: Request,
        symbol: str = "SPY",
        use_ai: bool = True,
        _=Depends(login_required),
    ):
        from fastapi.responses import StreamingResponse

        def event_generator():
            try:
                for chunk in bot.analyze_symbol_multi_timeframe_stream(
                    symbol, use_ai_confirmation=use_ai
                ):
                    yield chunk
            except Exception as e:
                import json as _json
                logger.error(f"Error in multi-timeframe stream for {symbol}: {e}")
                yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/config")
    async def get_config(request: Request, _=Depends(login_required)):
        return bot.config.STRATEGY_PARAMS

    @app.post("/api/config")
    async def update_config(request: Request, _=Depends(login_required)):
        try:
            new_params = await request.json()
            for key in bot.config.STRATEGY_PARAMS.keys():
                if key in new_params:
                    expected_type = type(bot.config.STRATEGY_PARAMS[key])
                    bot.config.STRATEGY_PARAMS[key] = expected_type(new_params[key])
            return {"status": "success", "config": bot.config.STRATEGY_PARAMS}
        except Exception as e:
            logger.error(f"Error updating config: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/execute_trade")
    async def execute_trade(request: Request, _=Depends(login_required)):
        try:
            data = await request.json()
            symbol = data.get("symbol")
            side = data.get("side")
            entry_price = float(data.get("entry_price"))
            stop_price = float(data.get("stop_price"))
            target_price = float(data.get("target_price"))

            if not all([symbol, side, entry_price, stop_price, target_price]):
                return JSONResponse({"error": "Missing required trade parameters"}, status_code=400)

            quantity = bot.calculate_position_size(entry_price, stop_price)
            if quantity < 1:
                quantity = 1

            can_trade, reason = bot.can_trade(symbol, side)
            if not can_trade:
                return {"status": "skipped", "reason": reason}

            time_in_force = data.get("time_in_force", "gtc")

            order = bot.api.submit_order(
                symbol=symbol,
                qty=quantity,
                side=side,
                type="market",
                time_in_force=time_in_force,
                order_class="bracket",
                stop_loss={"stop_price": round(stop_price, 2)},
                take_profit={"limit_price": round(target_price, 2)},
            )

            trade_info = {
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "entry_price": entry_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "order_id": order.id,
                "status": order.status,
            }
            bot._log_trade(trade_info)
            logger.info(
                f"Bracket order submitted: {side} {quantity} {symbol} @ {entry_price} "
                f"| stop={stop_price} | target={target_price}"
            )
            return {"status": "executed", "trade": trade_info}

        except Exception as e:
            logger.error(f"Error executing trade: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/scan/stream")
    async def scan_stream(
        request: Request,
        list_name: str = "sp500_top100",
        custom: str = "",
        timeframe: str = "long",
        _=Depends(login_required),
    ):
        """Stream market scanner results via SSE."""
        from fastapi.responses import StreamingResponse
        from screeners.market_scanner import MarketScanner

        if not bot._require_api():
            import json as _json
            async def no_api():
                yield f"data: {_json.dumps({'type': 'error', 'message': 'No Alpaca credentials — add a profile first'})}\n\n"
            return StreamingResponse(no_api(), media_type="text/event-stream")

        scanner = MarketScanner(bot.api)

        def event_generator():
            try:
                for chunk in scanner.scan_stream(list_name=list_name, custom=custom, timeframe=timeframe):
                    yield chunk
            except Exception as e:
                import json as _json
                logger.error(f"Scanner error: {e}")
                yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/market_data")
    async def get_market_data(
        request: Request,
        symbol: str = "SPY",
        period: str = "3mo",
        interval: str = "1d",
        _=Depends(login_required),
    ):
        try:
            data = bot.get_market_data(symbol, period=period, interval=interval)

            if data.empty:
                return []

            data_reset = data.reset_index()
            records = data_reset.to_dict("records")

            for record in records:
                ts_value = None
                for key, value in list(record.items()):
                    if isinstance(value, pd.Timestamp):
                        try:
                            ms = int(value.tz_convert("UTC").timestamp() * 1000)
                        except TypeError:
                            ms = int(value.timestamp() * 1000)
                        record[key] = ms
                        ts_value = ms
                    elif pd.isna(value):
                        record[key] = None

                # Alpaca's DataFrame index is named "timestamp"; yfinance uses "Date"/"Datetime".
                # Ensure the JS always finds the key "timestamp".
                if ts_value is not None and "timestamp" not in record:
                    record["timestamp"] = ts_value

            return records
        except Exception as e:
            logger.error(f"Error getting market data: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── Settings API ──────────────────────────────────────────────────────────

    @app.get("/api/settings")
    async def api_get_settings(request: Request, _=Depends(login_required)):
        """Return all app settings (decrypted, password masked)."""
        settings = get_all_settings()
        # Never send the actual password to the client
        settings["dashboard_password"] = "••••••••" if settings.get("dashboard_password") else ""
        return settings

    @app.post("/api/settings")
    async def api_save_settings(request: Request, _=Depends(login_required)):
        """Save app settings. Password is only updated if a non-placeholder value is sent."""
        try:
            data = await request.json()

            # AI settings
            if "ai_base_url" in data:
                set_setting("ai_base_url", data["ai_base_url"].strip())
            if "ai_api_key" in data:
                set_setting("ai_api_key", data["ai_api_key"].strip())
            if "ai_model" in data:
                set_setting("ai_model", data["ai_model"].strip())

            # Risk settings
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

            # Password — only update if a real value was sent (not the placeholder)
            if "dashboard_password" in data:
                new_pw = data["dashboard_password"]
                if new_pw and new_pw != "••••••••":
                    set_setting("dashboard_password", new_pw)
                    logger.info("Dashboard password updated")
                elif new_pw == "":
                    # Explicitly clearing the password
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

    @app.post("/api/settings/test-ai")
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

    return app


def _clean_for_json(obj):
    """Recursively replace NaN/Inf with None for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _clean_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_for_json(item) for item in obj]
    if isinstance(obj, float) and (pd.isna(obj) or obj == float("inf") or obj == float("-inf")):
        return None
    return obj


# ===== ENTRY POINTS =====

def create_app() -> FastAPI:
    """Application factory — creates the bot and wires up the dashboard."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    config = Config()
    bot    = TradingBot(config)

    # Seed a default profile from .env credentials if none exist yet,
    # and restore the last active profile on restart.
    if config.ALPACA_API_KEY and config.ALPACA_SECRET_KEY:
        seed_from_env(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, config.PAPER_TRADING)

    from data.profile_store import get_active_profile
    active = get_active_profile()
    if active and bot.api is None:
        # Reconnect using the persisted active profile
        from alpaca_trade_api.rest import REST
        try:
            base_url = "https://paper-api.alpaca.markets" if active["paper_trading"] else "https://api.alpaca.markets"
            bot.api  = REST(key_id=active["api_key"], secret_key=active["secret_key"], base_url=base_url)
            logger.info(f"Restored active profile '{active['name']}' on startup")
        except Exception as e:
            logger.warning(f"Could not restore active profile on startup: {e}")

    return create_dashboard(bot)


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=5001, reload=False)
