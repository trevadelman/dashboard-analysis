"""
routes/account.py — Account, orders, trades, and Alpaca credential/profile management.

Covers:
  GET  /api/account
  GET  /api/orders
  GET  /api/trades
  GET  /api/trades/log
  GET  /api/profiles
  POST /api/profiles
  POST /api/profiles/{profile_id}/activate
  DEL  /api/profiles/{profile_id}
  POST /api/alpaca/credentials
  GET  /api/market_data
"""

import logging
import os

import pandas as pd
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from data.profile_store import (
    list_profiles, save_profile,
    activate_profile, delete_profile,
)

logger = logging.getLogger(__name__)


def create_router(bot, login_required) -> APIRouter:
    router = APIRouter()

    # ── Account ───────────────────────────────────────────────────────────────

    @router.get("/api/account")
    async def get_account(request: Request, _=Depends(login_required)):
        if not bot._require_api():
            return {}
        try:
            return bot.get_account()
        except Exception as e:
            logger.error(f"Error getting account: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── Orders ────────────────────────────────────────────────────────────────

    @router.get("/api/orders")
    async def get_orders(request: Request, status: str = "all", _=Depends(login_required)):
        if not bot._require_api():
            return []
        try:
            return bot.get_orders(status)
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── Trades ────────────────────────────────────────────────────────────────

    @router.get("/api/trades")
    async def get_trades(request: Request, limit: int = 50, _=Depends(login_required)):
        if not bot._require_api():
            return []
        try:
            orders = bot.get_orders(status="all")
            return [
                {
                    "timestamp": o.get("created_at", ""),
                    "symbol":    o.get("symbol", ""),
                    "side":      o.get("side", ""),
                    "quantity":  o.get("qty", 0),
                    "status":    o.get("status", ""),
                    "order_id":  o.get("id", ""),
                    "type":      o.get("type", ""),
                }
                for o in orders[:limit]
            ]
        except Exception as e:
            logger.error(f"Error getting trades: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.get("/api/trades/log")
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

    # ── Market data ───────────────────────────────────────────────────────────

    @router.get("/api/market_data")
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
            records    = data_reset.to_dict("records")

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

    # ── Profile management ────────────────────────────────────────────────────

    @router.get("/api/profiles")
    async def api_list_profiles(request: Request, _=Depends(login_required)):
        """List all saved profiles (no credentials returned)."""
        return list_profiles()

    @router.post("/api/profiles")
    async def api_save_profile(request: Request, _=Depends(login_required)):
        """Create or update a named profile. Optionally activate it immediately."""
        try:
            data       = await request.json()
            name       = data.get("name", "").strip()
            api_key    = data.get("api_key", "").strip()
            secret_key = data.get("secret_key", "").strip()
            paper      = data.get("paper_trading", True)
            do_activate = data.get("activate", False)

            if not name or not api_key or not secret_key:
                return JSONResponse({"error": "name, api_key, and secret_key are required"}, status_code=400)

            profile = save_profile(name, api_key, secret_key, paper)

            if do_activate:
                return await _activate_and_connect(profile["id"])

            return {"status": "saved", "profile": profile}

        except Exception as e:
            logger.error(f"Error saving profile: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.post("/api/profiles/{profile_id}/activate")
    async def api_activate_profile(profile_id: int, request: Request, _=Depends(login_required)):
        """Activate a saved profile and reconnect the bot."""
        return await _activate_and_connect(profile_id)

    @router.delete("/api/profiles/{profile_id}")
    async def api_delete_profile(profile_id: int, request: Request, _=Depends(login_required)):
        """Delete a saved profile."""
        deleted = delete_profile(profile_id)
        if not deleted:
            return JSONResponse({"error": "Profile not found"}, status_code=404)
        return {"status": "deleted"}

    async def _activate_and_connect(profile_id: int):
        """Activate a profile and reconnect the bot's Alpaca API clients."""
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient

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
            new_crypto = CryptoHistoricalDataClient(
                api_key=profile["api_key"],
                secret_key=profile["secret_key"],
            )
            account = new_trading.get_account()

            bot.trading_client           = new_trading
            bot.data_client              = new_data
            bot.crypto_client            = new_crypto
            bot.config.ALPACA_API_KEY    = profile["api_key"]
            bot.config.ALPACA_SECRET_KEY = profile["secret_key"]
            bot.config.PAPER_TRADING     = paper

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

    @router.post("/api/alpaca/credentials")
    async def update_alpaca_credentials(request: Request, _=Depends(login_required)):
        """Quick-connect without saving a profile. Optionally save as a named profile."""
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

    return router
