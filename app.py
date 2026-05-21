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

    @app.get("/positions", response_class=HTMLResponse)
    async def positions_page(request: Request, _=Depends(login_required)):
        return templates.TemplateResponse(request, "positions.html", {"active_page": "positions"})

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

    @app.get("/api/positions/{symbol}/orders")
    async def get_position_orders(symbol: str, request: Request, _=Depends(login_required)):
        """Return all open/held orders related to a specific symbol (includes bracket legs)."""
        if not bot._require_api():
            return []
        try:
            # Use status=all so we capture 'held' stop orders (bracket legs that are
            # pending activation) in addition to 'new' limit orders.
            all_orders = bot.get_orders(status="all")
            active_statuses = {"new", "held", "accepted", "pending_new", "partially_filled"}
            return [
                o for o in all_orders
                if o["symbol"].upper() == symbol.upper()
                and o["status"] in active_statuses
            ]
        except Exception as e:
            logger.error(f"Error getting orders for {symbol}: {e}")
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
        Activate a profile and reconnect the bot's Alpaca API clients.
        Verifies the credentials work before committing.
        """
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient

        profile = activate_profile(profile_id)
        if not profile:
            return JSONResponse({"error": "Profile not found"}, status_code=404)

        try:
            paper = bool(profile["paper_trading"])
            new_trading = TradingClient(
                api_key=profile["api_key"],
                secret_key=profile["secret_key"],
                paper=paper,
            )
            new_data = StockHistoricalDataClient(
                api_key=profile["api_key"],
                secret_key=profile["secret_key"],
            )
            account = new_trading.get_account()

            bot.trading_client            = new_trading
            bot.data_client               = new_data
            bot.config.ALPACA_API_KEY     = profile["api_key"]
            bot.config.ALPACA_SECRET_KEY  = profile["secret_key"]
            bot.config.PAPER_TRADING      = paper
            bot._data_cache.clear()

            logger.info(f"Activated profile '{profile['name']}' (paper={paper})")
            return {
                "status":       "ok",
                "profile_id":   profile_id,
                "profile_name": profile["name"],
                "paper":        paper,
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

            from alpaca.trading.client import TradingClient
            from alpaca.data.historical import StockHistoricalDataClient

            new_trading = TradingClient(api_key=api_key, secret_key=secret_key, paper=paper)
            new_data    = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
            account     = new_trading.get_account()

            bot.trading_client           = new_trading
            bot.data_client              = new_data
            bot.config.ALPACA_API_KEY    = api_key
            bot.config.ALPACA_SECRET_KEY = secret_key
            bot.config.PAPER_TRADING     = paper
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

    @app.get("/api/trades/log")
    async def get_trade_log(request: Request, _=Depends(login_required)):
        """
        Return the local trade log (data/trades.json).

        Each entry includes the timeframe the trade was entered on, which the
        positions page uses to pre-select the correct review timeframe.
        """
        try:
            return bot.get_trade_history(limit=500)
        except Exception as e:
            logger.error(f"Error reading trade log: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/positions/{symbol}/review")
    async def review_position(
        symbol: str,
        request: Request,
        timeframe: str = "long",
        _=Depends(login_required),
    ):
        """
        Run a position review for a single symbol.

        Fetches current bars, re-runs Tier 1 + momentum health checks,
        and returns a verdict with suggested stop/target adjustments.

        The timeframe parameter should match the timeframe the trade was entered
        on (read from the trade log by the UI). Defaults to 'long'.
        """
        if not bot._require_api():
            return JSONResponse({"error": "No Alpaca credentials"}, status_code=400)
        try:
            from strategies.position_manager import PositionReviewer
            from dataclasses import asdict

            # Fetch position and orders
            positions = bot.get_positions()
            position  = next((p for p in positions if p['symbol'].upper() == symbol.upper()), None)
            if position is None:
                return JSONResponse({"error": f"No open position for {symbol}"}, status_code=404)

            all_orders = bot.get_orders(status='all')
            active_statuses = {'new', 'held', 'accepted', 'pending_new', 'partially_filled'}
            orders = [
                o for o in all_orders
                if o['symbol'].upper() == symbol.upper()
                and o['status'] in active_statuses
            ]

            # Fetch bars with indicators — use the timeframe the trade was entered on
            tf_cfg = {'long': ('1y', '1d'), 'swing': ('3mo', '1h'), 'short': ('1mo', '15m')}
            period, interval = tf_cfg.get(timeframe, ('1y', '1d'))
            bars = bot.get_market_data(symbol, period=period, interval=interval)

            reviewer = PositionReviewer(timeframe=timeframe)
            review   = reviewer.review(position, orders, bars)

            # Serialize the dataclass to a plain dict and include the timeframe used
            result = asdict(review)
            result['timeframe_used'] = timeframe
            return result

        except Exception as e:
            logger.error(f"Position review error for {symbol}: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/positions/{symbol}/adjust")
    async def adjust_position_orders(
        symbol: str,
        request: Request,
        _=Depends(login_required),
    ):
        """
        Modify the stop-loss and/or take-profit orders for an open position.

        Body: { "stop_price": 145.50, "target_price": 162.00 }
        Either field is optional — only the provided fields are updated.
        """
        if not bot._require_api():
            return JSONResponse({"error": "No Alpaca credentials"}, status_code=400)
        try:
            data       = await request.json()
            new_stop   = float(data['stop_price'])   if 'stop_price'   in data else None
            new_target = float(data['target_price']) if 'target_price' in data else None

            if new_stop is None and new_target is None:
                return JSONResponse({"error": "Provide stop_price and/or target_price"}, status_code=400)

            result = bot.adjust_orders(symbol, new_stop=new_stop, new_target=new_target)
            if result['status'] == 'error':
                return JSONResponse(result, status_code=500)
            return result

        except Exception as e:
            logger.error(f"Order adjustment error for {symbol}: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/positions/{symbol}/close")
    async def close_position_endpoint(
        symbol: str,
        request: Request,
        _=Depends(login_required),
    ):
        """Close an open position at market price."""
        if not bot._require_api():
            return JSONResponse({"error": "No Alpaca credentials"}, status_code=400)
        try:
            result = bot.close_position(symbol)
            if result['status'] == 'error':
                return JSONResponse(result, status_code=500)
            return result
        except Exception as e:
            logger.error(f"Close position error for {symbol}: {e}")
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

            from alpaca.trading.requests import (
                LimitOrderRequest, MarketOrderRequest,
                TakeProfitRequest, StopLossRequest,
            )
            from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

            order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
            tif        = TimeInForce.GTC if time_in_force == "gtc" else TimeInForce.DAY
            entry_type = data.get("entry_type", "limit")

            if entry_type == "limit":
                req = LimitOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=order_side,
                    time_in_force=tif,
                    limit_price=round(entry_price, 2),
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=round(target_price, 2)),
                    stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
                )
            else:
                req = MarketOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=order_side,
                    time_in_force=tif,
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=round(target_price, 2)),
                    stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
                )
            order = bot.trading_client.submit_order(req)

            timeframe = data.get("timeframe", "long")
            trade_info = {
                "timestamp":  datetime.now().isoformat(),
                "symbol":     symbol,
                "side":       side,
                "entry_type": entry_type,
                "timeframe":  timeframe,
                "quantity":   quantity,
                "entry_price":  entry_price,
                "stop_price":   stop_price,
                "target_price": target_price,
                "order_id": str(order.id),
                "status":   order.status.value,
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

    @app.post("/api/backtest")
    async def run_backtest(request: Request, _=Depends(login_required)):
        """
        Run a walk-forward backtest for a single symbol.

        Body: { symbol, timeframe, period }
        Returns the full result dict from BacktestEngine.run().
        """
        if not bot._require_api():
            return JSONResponse({"error": "No Alpaca credentials — add a profile first"}, status_code=400)
        try:
            from backtester.engine import BacktestEngine
            data = await request.json()
            symbol    = data.get("symbol", "SPY").upper().strip()
            timeframe = data.get("timeframe", "long")
            period    = data.get("period", "1y")
            engine = BacktestEngine(bot.data_client)
            result = engine.run(symbol=symbol, timeframe=timeframe, period=period)
            return result
        except Exception as e:
            logger.error(f"Backtest error: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/backtest", response_class=HTMLResponse)
    async def backtest_page(request: Request, _=Depends(login_required)):
        return templates.TemplateResponse(request, "backtest.html", {"active_page": "backtest"})

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

        scanner = MarketScanner(bot.data_client)

        def event_generator():
            import json as _json
            collected = []
            try:
                for chunk in scanner.scan_stream(list_name=list_name, custom=custom, timeframe=timeframe):
                    # Collect result events so we can write the cache after streaming
                    try:
                        payload = _json.loads(chunk.removeprefix("data: ").strip())
                        if payload.get("type") == "result":
                            collected.append(payload)
                        elif payload.get("type") == "done":
                            # Write cache once the scan is complete
                            scanner.write_cache(collected, list_name, timeframe)
                    except Exception:
                        pass
                    yield chunk
            except Exception as e:
                logger.error(f"Scanner error: {e}")
                yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/scan/cache")
    async def get_scan_cache(
        request: Request,
        timeframe: str = "long",
        _=Depends(login_required),
    ):
        """
        Return cached scan results for a specific timeframe.

        The cache file is structured as:
          { "long": { "AAPL": {...}, ... }, "swing": {...}, "short": {...}, "last_updated": "..." }

        Returns:
          { "timeframe": tf, "results": [...], "total": N, "signals": N, "last_updated": "..." }
        """
        import json as _json
        cache_path = os.path.join("data", "scan_cache.json")
        if not os.path.exists(cache_path):
            return JSONResponse({"error": "No scan cache found — run a scan first"}, status_code=404)
        try:
            with open(cache_path, "r") as f:
                cache = _json.load(f)

            tf_data = cache.get(timeframe, {})
            if not isinstance(tf_data, dict):
                tf_data = {}

            results = list(tf_data.values())
            return {
                "timeframe":    timeframe,
                "results":      results,
                "total":        len(results),
                "signals":      sum(1 for r in results if r.get("signal") in ("BUY", "SELL")),
                "last_updated": cache.get("last_updated", ""),
            }
        except Exception as e:
            logger.error(f"Error reading scan cache: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/scan/cache/info")
    async def get_scan_cache_info(request: Request, _=Depends(login_required)):
        """
        Return metadata about the scan cache — counts per timeframe, last updated.
        Used by the UI to populate the cache badge without loading all results.
        """
        import json as _json
        cache_path = os.path.join("data", "scan_cache.json")
        if not os.path.exists(cache_path):
            return {"cached": False}
        try:
            with open(cache_path, "r") as f:
                cache = _json.load(f)
            info = {"cached": True, "last_updated": cache.get("last_updated", ""), "timeframes": {}}
            for tf in ("long", "swing", "short"):
                tf_data = cache.get(tf, {})
                if isinstance(tf_data, dict) and tf_data:
                    results = list(tf_data.values())
                    info["timeframes"][tf] = {
                        "total":   len(results),
                        "signals": sum(1 for r in results if r.get("signal") in ("BUY", "SELL")),
                    }
            return info
        except Exception as e:
            logger.error(f"Error reading scan cache info: {e}")
            return {"cached": False}

    @app.get("/market", response_class=HTMLResponse)
    async def market_pulse_page(request: Request, _=Depends(login_required)):
        return templates.TemplateResponse(request, "market.html", {"active_page": "market"})

    @app.get("/api/market/pulse")
    async def get_market_pulse(
        request: Request,
        timeframe: str = "all",
        _=Depends(login_required),
    ):
        """
        Compute market pulse statistics from the scan cache.

        When timeframe="all" (default), returns stats for all three timeframes
        plus cross-timeframe signals and top setups.
        When timeframe is "long", "swing", or "short", returns stats for that
        timeframe only (backward-compatible).

        No Alpaca API calls — reads only from data/scan_cache.json.
        """
        import json as _json
        from screeners.symbol_lists import SECTORS

        cache_path = os.path.join("data", "scan_cache.json")
        if not os.path.exists(cache_path):
            return JSONResponse({"error": "No scan cache — run a scan first"}, status_code=404)

        try:
            with open(cache_path, "r") as f:
                cache = _json.load(f)

            last_updated = cache.get("last_updated", "")

            # If a specific timeframe is requested, use the old single-TF path
            if timeframe in ("long", "swing", "short"):
                tf_data = cache.get(timeframe, {})
                if not isinstance(tf_data, dict) or not tf_data:
                    return JSONResponse(
                        {"error": f"No cached results for timeframe '{timeframe}' — run a scan first"},
                        status_code=404,
                    )
                results = list(tf_data.values())
            else:
                # "all" — aggregate across all available timeframes
                results = []
                for tf in ("long", "swing", "short"):
                    tf_data = cache.get(tf, {})
                    if isinstance(tf_data, dict):
                        results.extend(tf_data.values())
                if not results:
                    return JSONResponse({"error": "No scan cache — run a scan first"}, status_code=404)

            def _compute_stats(rows):
                if not rows:
                    return None
                total = len(rows)
                signals = [r for r in rows if r.get("signal") in ("BUY", "SELL")]
                bullish = sum(1 for r in rows if r.get("regime") == "BULLISH")
                bearish = sum(1 for r in rows if r.get("regime") == "BEARISH")
                no_trade = total - bullish - bearish

                scores = [r["score"] for r in rows if r.get("score") is not None]
                avg_score = round(sum(scores) / len(scores), 1) if scores else 0

                # Grade distribution — assign grades to ALL symbols based on score,
                # not just signal rows. This makes the distribution meaningful even
                # when signals are rare.
                grade_dist = {"A": 0, "B": 0, "C": 0, "D": 0}
                for r in rows:
                    sc = r.get("score")
                    if sc is None:
                        continue
                    if sc >= 80:
                        grade_dist["A"] += 1
                    elif sc >= 60:
                        grade_dist["B"] += 1
                    elif sc >= 40:
                        grade_dist["C"] += 1
                    else:
                        grade_dist["D"] += 1

                # Score distribution buckets (0-20, 20-40, 40-60, 60-80, 80-100)
                score_buckets = {"0-20": 0, "20-40": 0, "40-60": 0, "60-80": 0, "80-100": 0}
                for r in rows:
                    sc = r.get("score")
                    if sc is None:
                        continue
                    if sc < 20:
                        score_buckets["0-20"] += 1
                    elif sc < 40:
                        score_buckets["20-40"] += 1
                    elif sc < 60:
                        score_buckets["40-60"] += 1
                    elif sc < 80:
                        score_buckets["60-80"] += 1
                    else:
                        score_buckets["80-100"] += 1

                # "Setups forming" — passed BB compression + RS but no trigger yet.
                # These are coiled + outperforming, waiting for the breakout.
                setups_forming = sum(
                    1 for r in rows
                    if r.get("signal") == "NONE"
                    and r.get("tier_reached", 1) >= 2
                    and (r.get("bb_width_pct") or 100) <= 50
                    and (r.get("rs_vs_spy") or -999) >= 0
                )

                # Top failure gate — infer from raw indicator values for accuracy.
                # For no-signal rows that reached Tier 2, determine which gate blocked them.
                failure_counts = {}
                for r in rows:
                    if r.get("signal") != "NONE":
                        continue
                    if r.get("tier_reached", 1) < 2:
                        # Blocked at Tier 1 (regime)
                        failure_counts["Regime"] = failure_counts.get("Regime", 0) + 1
                        continue
                    # Tier 2 reached — determine which gate failed first
                    bb = r.get("bb_width_pct")
                    rs = r.get("rs_vs_spy")
                    rvol = r.get("rvol")
                    rsi = r.get("rsi")
                    reason_text = (r.get("tier2_reason") or "").lower()

                    if bb is not None and bb > 50:
                        failure_counts["BB Compression"] = failure_counts.get("BB Compression", 0) + 1
                    elif rs is not None and rs < 0:
                        failure_counts["Relative Strength"] = failure_counts.get("Relative Strength", 0) + 1
                    elif "breakout" in reason_text or "broke" in reason_text or "range" in reason_text:
                        failure_counts["Breakout"] = failure_counts.get("Breakout", 0) + 1
                    elif rvol is not None and rvol < 1.1:
                        failure_counts["RVOL"] = failure_counts.get("RVOL", 0) + 1
                    elif "rsi" in reason_text:
                        failure_counts["RSI"] = failure_counts.get("RSI", 0) + 1
                    elif "ema9" in reason_text or "ema 9" in reason_text:
                        failure_counts["EMA9 Trigger"] = failure_counts.get("EMA9 Trigger", 0) + 1
                    else:
                        failure_counts["Other"] = failure_counts.get("Other", 0) + 1

                top_failure = max(failure_counts, key=failure_counts.get) if failure_counts else "N/A"

                # Market stance — use score distribution + regime + signal rate
                signal_rate = len(signals) / total if total else 0
                bullish_rate = bullish / total if total else 0
                high_score_pct = (grade_dist["A"] + grade_dist["B"]) / total if total else 0

                if signal_rate >= 0.15 and bullish_rate >= 0.6:
                    stance = "RISK ON"
                elif signal_rate >= 0.05 and bullish_rate >= 0.5:
                    stance = "SELECTIVE"
                elif bullish_rate < 0.3:
                    stance = "RISK OFF"
                elif high_score_pct >= 0.20 and bullish_rate >= 0.5:
                    # Many high-quality setups forming but not yet triggered
                    stance = "COILING"
                else:
                    stance = "WAIT"

                return {
                    "total":           total,
                    "signals":         len(signals),
                    "signal_rate":     round(signal_rate * 100, 1),
                    "setups_forming":  setups_forming,
                    "setup_rate":      round(setups_forming / total * 100, 1) if total else 0,
                    "bullish":         bullish,
                    "bearish":         bearish,
                    "no_trade":        no_trade,
                    "bullish_pct":     round(bullish / total * 100, 1) if total else 0,
                    "bearish_pct":     round(bearish / total * 100, 1) if total else 0,
                    "avg_score":       avg_score,
                    "grade_dist":      grade_dist,
                    "score_buckets":   score_buckets,
                    "top_failure":     top_failure,
                    "failure_counts":  failure_counts,
                    "stance":          stance,
                    "high_score_pct":  round(high_score_pct * 100, 1),
                }

            overall = _compute_stats(results)

            # Build sector lookup: symbol → sector name
            sym_to_sector = {}
            for sector_name, syms in SECTORS.items():
                for s in syms:
                    if s not in sym_to_sector:
                        sym_to_sector[s] = sector_name

            # Group results by sector
            sector_buckets = {}
            for r in results:
                sector = sym_to_sector.get(r.get("symbol", ""), "Other")
                sector_buckets.setdefault(sector, []).append(r)

            sectors = []
            for sector_name, rows in sector_buckets.items():
                stats = _compute_stats(rows)
                if stats:
                    stats["sector"] = sector_name
                    sectors.append(stats)

            sectors.sort(key=lambda s: s["avg_score"], reverse=True)

            return {
                "timeframe":    timeframe,
                "last_updated": last_updated,
                "overall":      overall,
                "sectors":      sectors,
            }

        except Exception as e:
            logger.error(f"Market pulse error: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/market/pulse/stream")
    async def market_pulse_stream(
        request: Request,
        timeframe: str = "long",
        _=Depends(login_required),
    ):
        """
        Stream AI commentary on the current market pulse via SSE.
        Reads the pulse stats and feeds them to the AI for a plain-English read.
        """
        from fastapi.responses import StreamingResponse
        import json as _json

        # Fetch the pulse stats first
        pulse_res = await get_market_pulse(request, timeframe=timeframe, _=None)
        if isinstance(pulse_res, JSONResponse):
            async def err():
                yield f"data: {_json.dumps({'type': 'error', 'message': 'No cache data available'})}\n\n"
            return StreamingResponse(err(), media_type="text/event-stream")

        pulse = pulse_res if isinstance(pulse_res, dict) else {}
        overall = pulse.get("overall", {})
        sectors = pulse.get("sectors", [])

        top_sectors    = sectors[:5]
        bottom_sectors = sectors[-3:] if len(sectors) > 5 else []
        hot_sectors    = [s for s in sectors if s.get("signal_rate", 0) >= 10]
        coiling_sectors = [s for s in sectors if s.get("setup_rate", 0) >= 10]

        # Build full sector table for the AI
        sector_table = "\n".join(
            f"  {s['sector']}: score={s['avg_score']}, signals={s['signals']} ({s['signal_rate']}%), "
            f"setups={s.get('setups_forming', 0)} ({s.get('setup_rate', 0)}%), "
            f"bullish={s['bullish_pct']}%, top_failure={s.get('top_failure', 'N/A')}"
            for s in sectors
        )

        # Grade and score distribution
        gd = overall.get("grade_dist", {})
        sb = overall.get("score_buckets", {})
        fc = overall.get("failure_counts", {})
        failure_breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(fc.items(), key=lambda x: -x[1]))

        prompt = f"""You are a quantitative market analyst reviewing a batch scan of {overall.get('total', 0)} stocks.

## SCAN PARAMETERS
- Timeframe: {timeframe.upper()} ({'daily bars' if timeframe == 'long' else 'hourly bars' if timeframe == 'swing' else '15-min bars'})
- Strategy: Compression breakout with relative strength filter (BB squeeze + RS vs SPY + RVOL + RSI momentum)
- Scoring: 0-100 composite (BB compression 25pts, RS vs SPY 25pts, RVOL 20pts, RSI momentum 15pts, ATR contraction 15pts)

## OVERALL MARKET STATS
- Stance: {overall.get('stance', 'N/A')}
- Regime: {overall.get('bullish_pct', 0)}% BULLISH / {overall.get('bearish_pct', 0)}% BEARISH / {round((overall.get('no_trade', 0) / overall.get('total', 1)) * 100, 1)}% NO_TRADE
- Avg Score: {overall.get('avg_score', 0)}/100
- High-quality setups (A+B grade): {overall.get('high_score_pct', 0)}% of universe
- Triggered signals (BUY/SELL): {overall.get('signals', 0)} ({overall.get('signal_rate', 0)}%)
- Setups forming (compressed + outperforming, no trigger yet): {overall.get('setups_forming', 0)} ({overall.get('setup_rate', 0)}%)

## GRADE DISTRIBUTION (all symbols, score-based)
- A (80-100): {gd.get('A', 0)} symbols
- B (60-79):  {gd.get('B', 0)} symbols
- C (40-59):  {gd.get('C', 0)} symbols
- D (0-39):   {gd.get('D', 0)} symbols

## SCORE DISTRIBUTION
- 80-100: {sb.get('80-100', 0)} | 60-80: {sb.get('60-80', 0)} | 40-60: {sb.get('40-60', 0)} | 20-40: {sb.get('20-40', 0)} | 0-20: {sb.get('0-20', 0)}

## SIGNAL FAILURE BREAKDOWN (why signals aren't triggering)
{failure_breakdown if failure_breakdown else 'N/A'}

## ALL SECTORS (sorted by avg score, highest first)
{sector_table}

{"## HOT SECTORS (>10% signal rate): " + ", ".join(s['sector'] for s in hot_sectors) if hot_sectors else "## No sectors with >10% signal rate"}
{"## COILING SECTORS (>10% setups forming): " + ", ".join(s['sector'] for s in coiling_sectors) if coiling_sectors else ""}

## YOUR TASK
Provide a structured market commentary with these sections:

### Market Environment
2-3 sentences on what the data tells you about the current market environment. Reference specific numbers.

### Positioning Stance
What should a momentum trader do right now? Be specific: sit on hands, hold SPY, look for selective entries, etc. Explain why based on the data.

### Sector Rotation
Which sectors are leading, which are lagging, and what does the rotation pattern suggest? Reference the failure breakdown to explain why sectors are or aren't triggering.

### Setups to Watch
If there are setups forming (compressed + outperforming but not yet triggered), what does that mean? Are we close to a broad breakout or is this a false coil?

### Actionable Takeaway
One specific, concrete action a trader should take today based on this data.

Rules: Use markdown headers. Be direct. Use actual numbers from the data. No disclaimers. Write like a seasoned trader reviewing a morning scan, not a compliance officer."""

        def event_generator():
            try:
                for chunk in bot.ai.stream_analysis(prompt):
                    yield f"data: {_json.dumps({'type': 'chunk', 'text': chunk})}\n\n"
                yield f"data: {_json.dumps({'type': 'done'})}\n\n"
            except Exception as e:
                logger.error(f"Market pulse AI stream error: {e}")
                yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/market/overview")
    async def get_market_overview(request: Request, _=Depends(login_required)):
        """
        Return a cross-timeframe overview for the Market Pulse page.

        Reads the scan cache and returns:
          - Per-timeframe stats (stance, regime split, avg score, signal count, setup count)
          - Active signals across all timeframes (BUY/SELL, sorted by score desc)
          - Top 10 setups per timeframe (highest score, regardless of signal)
        """
        import json as _json

        cache_path = os.path.join("data", "scan_cache.json")
        if not os.path.exists(cache_path):
            return JSONResponse({"error": "No scan cache — run a scan first"}, status_code=404)

        try:
            with open(cache_path, "r") as f:
                cache = _json.load(f)

            last_updated = cache.get("last_updated", "")
            tf_summaries = {}
            all_signals  = []
            top_setups   = {}

            for tf in ("long", "swing", "short"):
                tf_data = cache.get(tf, {})
                if not isinstance(tf_data, dict) or not tf_data:
                    continue

                rows = list(tf_data.values())
                total    = len(rows)
                bullish  = sum(1 for r in rows if r.get("regime") == "BULLISH")
                bearish  = sum(1 for r in rows if r.get("regime") == "BEARISH")
                signals  = [r for r in rows if r.get("signal") in ("BUY", "SELL")]
                setups   = [
                    r for r in rows
                    if r.get("signal") == "NONE"
                    and r.get("tier_reached", 1) >= 2
                    and (r.get("bb_width_pct") or 100) <= 50
                    and (r.get("rs_vs_spy") or -999) >= 0
                ]
                scores   = [r["score"] for r in rows if r.get("score") is not None]
                avg_score = round(sum(scores) / len(scores), 1) if scores else 0

                signal_rate  = len(signals) / total if total else 0
                bullish_rate = bullish / total if total else 0
                high_score_pct = sum(1 for s in scores if s >= 60) / total if total else 0

                if signal_rate >= 0.15 and bullish_rate >= 0.6:
                    stance = "RISK ON"
                elif signal_rate >= 0.05 and bullish_rate >= 0.5:
                    stance = "SELECTIVE"
                elif bullish_rate < 0.3:
                    stance = "RISK OFF"
                elif high_score_pct >= 0.20 and bullish_rate >= 0.5:
                    stance = "COILING"
                else:
                    stance = "WAIT"

                # Grade distribution
                grade_dist = {"A": 0, "B": 0, "C": 0, "D": 0}
                for r in rows:
                    sc = r.get("score")
                    if sc is None:
                        continue
                    if sc >= 80:
                        grade_dist["A"] += 1
                    elif sc >= 60:
                        grade_dist["B"] += 1
                    elif sc >= 40:
                        grade_dist["C"] += 1
                    else:
                        grade_dist["D"] += 1

                # Failure gate counts
                failure_counts = {}
                for r in rows:
                    if r.get("signal") != "NONE":
                        continue
                    if r.get("tier_reached", 1) < 2:
                        failure_counts["Regime"] = failure_counts.get("Regime", 0) + 1
                        continue
                    bb   = r.get("bb_width_pct")
                    rs   = r.get("rs_vs_spy")
                    rvol = r.get("rvol")
                    reason_text = (r.get("tier2_reason") or "").lower()
                    if bb is not None and bb > 50:
                        failure_counts["BB Compression"] = failure_counts.get("BB Compression", 0) + 1
                    elif rs is not None and rs < 0:
                        failure_counts["Relative Strength"] = failure_counts.get("Relative Strength", 0) + 1
                    elif "breakout" in reason_text or "broke" in reason_text or "range" in reason_text:
                        failure_counts["Breakout"] = failure_counts.get("Breakout", 0) + 1
                    elif rvol is not None and rvol < 1.1:
                        failure_counts["RVOL"] = failure_counts.get("RVOL", 0) + 1
                    elif "rsi" in reason_text:
                        failure_counts["RSI"] = failure_counts.get("RSI", 0) + 1
                    elif "ema9" in reason_text or "ema 9" in reason_text:
                        failure_counts["EMA9 Trigger"] = failure_counts.get("EMA9 Trigger", 0) + 1
                    else:
                        failure_counts["Other"] = failure_counts.get("Other", 0) + 1

                tf_summaries[tf] = {
                    "total":          total,
                    "bullish":        bullish,
                    "bearish":        bearish,
                    "bullish_pct":    round(bullish / total * 100, 1) if total else 0,
                    "bearish_pct":    round(bearish / total * 100, 1) if total else 0,
                    "signals":        len(signals),
                    "signal_rate":    round(signal_rate * 100, 1),
                    "setups_forming": len(setups),
                    "setup_rate":     round(len(setups) / total * 100, 1) if total else 0,
                    "avg_score":      avg_score,
                    "stance":         stance,
                    "grade_dist":     grade_dist,
                    "failure_counts": failure_counts,
                }

                # Collect signals for the cross-timeframe signals table
                for r in signals:
                    all_signals.append({**r, "timeframe": tf})

                # Top 10 setups by score for this timeframe
                sorted_rows = sorted(rows, key=lambda r: r.get("score", 0), reverse=True)
                top_setups[tf] = sorted_rows[:10]

            # Sort all signals by score descending
            all_signals.sort(key=lambda r: r.get("score", 0), reverse=True)

            return {
                "last_updated": last_updated,
                "timeframes":   tf_summaries,
                "signals":      all_signals,
                "top_setups":   top_setups,
            }

        except Exception as e:
            logger.error(f"Market overview error: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/scan/sectors")
    async def get_sectors(request: Request, _=Depends(login_required)):
        """Return the list of available GICS sector names."""
        from screeners.symbol_lists import SECTORS
        return {"sectors": list(SECTORS.keys())}

    @app.get("/api/scan/universe/info")
    async def get_universe_info(request: Request, _=Depends(login_required)):
        """Return metadata about the cached asset universe."""
        from screeners.symbol_lists import get_universe_cache_info
        return get_universe_cache_info()

    @app.post("/api/scan/universe/refresh")
    async def refresh_universe(request: Request, _=Depends(login_required)):
        """
        Trigger a fresh fetch of the Alpaca asset universe.
        Deletes the existing cache so the next scan will re-fetch.
        """
        import json as _json
        if not bot._require_api():
            return JSONResponse({"error": "No Alpaca credentials"}, status_code=400)
        try:
            from screeners.symbol_lists import fetch_alpaca_universe, _UNIVERSE_CACHE_PATH
            import os as _os
            cache_path = _os.path.normpath(_UNIVERSE_CACHE_PATH)
            if _os.path.exists(cache_path):
                _os.remove(cache_path)
            symbols = fetch_alpaca_universe(bot.trading_client, min_price=1.0)
            return {"status": "ok", "count": len(symbols)}
        except Exception as e:
            logger.error(f"Universe refresh error: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

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

    # ── Autonomous bot status & control ───────────────────────────────────────

    @app.get("/api/bot/status")
    async def bot_status(request: Request, _=Depends(login_required)):
        """
        Return the current state of the autonomous scheduler.

        Response:
          {
            "running": bool,
            "halted": bool,
            "autonomous": bool,
            "daily_open_equity": float | null,
            "jobs": [{ "id", "name", "next_run" }, ...],
            "today_actions": int,
            "last_actions": [...]
          }
        """
        try:
            import scheduler as _sched
            status = _sched.get_status()
            status["autonomous"] = bot.config.BOT_AUTONOMOUS
            return status
        except Exception as e:
            logger.error(f"Bot status error: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/bot/pause")
    async def bot_pause(request: Request, _=Depends(login_required)):
        """
        Pause or resume the autonomous bot without restarting the app.

        Body: { "halted": true | false }

        Sets bot_state.halted which is checked by all circuit breaker gates.
        """
        try:
            from strategies.auto_manager import _load_state, _save_state, _log_action
            data   = await request.json()
            halted = bool(data.get("halted", True))
            state  = _load_state()
            state["halted"] = halted
            _save_state(state)
            action = "MANUAL_HALT" if halted else "MANUAL_RESUME"
            _log_action(action, None, {}, "Bot halted by user" if halted else "Bot resumed by user")
            return {"status": "ok", "halted": halted}
        except Exception as e:
            logger.error(f"Bot pause error: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/bot/actions")
    async def bot_actions(
        request: Request,
        limit: int = 50,
        _=Depends(login_required),
    ):
        """Return the last N entries from bot_actions.json."""
        import json as _json
        actions_path = os.path.join("data", "bot_actions.json")
        try:
            with open(actions_path) as f:
                actions = _json.load(f)
            return list(reversed(actions[-limit:]))
        except FileNotFoundError:
            return []
        except Exception as e:
            logger.error(f"Bot actions error: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

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
    from alpaca.trading.client import TradingClient
    from alpaca.data.historical import StockHistoricalDataClient

    active = get_active_profile()
    if active and bot.trading_client is None:
        # Reconnect using the persisted active profile
        try:
            paper = bool(active["paper_trading"])
            bot.trading_client = TradingClient(
                api_key=active["api_key"],
                secret_key=active["secret_key"],
                paper=paper,
            )
            bot.data_client = StockHistoricalDataClient(
                api_key=active["api_key"],
                secret_key=active["secret_key"],
            )
            logger.info(f"Restored active profile '{active['name']}' on startup")
        except Exception as e:
            logger.warning(f"Could not restore active profile on startup: {e}")

    dashboard = create_dashboard(bot)

    # ── Start autonomous scheduler ────────────────────────────────────────────
    # The scheduler runs in a background thread and never blocks requests.
    # It starts regardless of BOT_AUTONOMOUS — the entry scan jobs check the
    # flag themselves, so position review and exit detection always run.
    try:
        import scheduler as _scheduler_mod
        _scheduler_mod.start(bot, config)
        logger.info("Autonomous scheduler started")
    except Exception as e:
        logger.warning(f"Scheduler could not start: {e}")

    return dashboard


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=5001, reload=False)
