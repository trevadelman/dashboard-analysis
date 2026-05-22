"""
routes/positions.py — Open position management: review, adjust, close, execute trade.

Covers:
  GET  /api/positions
  GET  /api/positions/{symbol}/orders
  GET  /api/positions/{symbol}/review
  POST /api/positions/{symbol}/adjust
  POST /api/positions/{symbol}/close
  POST /api/execute_trade
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def create_router(bot, login_required) -> APIRouter:
    router = APIRouter()

    @router.get("/api/positions")
    async def get_positions(request: Request, _=Depends(login_required)):
        if not bot._require_api():
            return []
        try:
            return bot.get_positions()
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.get("/api/positions/{symbol}/orders")
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

    @router.get("/api/positions/{symbol}/review")
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

        Loads per-position state from bot_state.json so the UI review runs in
        the same phase (validation vs participation) as the bot's automated review.
        """
        if not bot._require_api():
            return JSONResponse({"error": "No Alpaca credentials"}, status_code=400)
        try:
            from strategies.position_manager import PositionReviewer
            from dataclasses import asdict
            from strategies.auto_manager import (
                _load_state, _load_position_state,
                _save_position_state, _save_state,
            )

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

            tf_cfg = {'long': ('1y', '1d'), 'swing': ('3mo', '1h'), 'short': ('1mo', '15m')}
            period, interval = tf_cfg.get(timeframe, ('1y', '1d'))
            bars = bot.get_market_data(symbol, period=period, interval=interval)

            # Load per-position state so the reviewer runs in the correct phase.
            # Without this the UI always starts cold in validation phase and
            # diverges from what the bot sees.
            bot_state = _load_state()
            pos_state = _load_position_state(bot_state, symbol)

            reviewer = PositionReviewer(timeframe=timeframe)
            review   = reviewer.review(position, orders, bars, position_state=pos_state)

            # Persist updated state so the UI review and bot review stay in sync.
            if review.updated_position_state:
                _save_position_state(bot_state, symbol, review.updated_position_state)
                _save_state(bot_state)

            result = asdict(review)
            result['timeframe_used'] = timeframe
            return result

        except Exception as e:
            logger.error(f"Position review error for {symbol}: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.post("/api/positions/{symbol}/adjust")
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

    @router.post("/api/positions/{symbol}/close")
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

    @router.post("/api/execute_trade")
    async def execute_trade(request: Request, _=Depends(login_required)):
        try:
            data         = await request.json()
            symbol       = data.get("symbol")
            side         = data.get("side")
            entry_price  = float(data.get("entry_price"))
            stop_price   = float(data.get("stop_price"))
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
            # breakout_level is the actual breakout price before any ATR buffer.
            # The UI sends it when available; fall back to entry_price if not provided.
            breakout_level = float(data.get("breakout_level", entry_price))
            trade_info = {
                "timestamp":      datetime.now().isoformat(),
                "symbol":         symbol,
                "side":           side,
                "entry_type":     entry_type,
                "timeframe":      timeframe,
                "quantity":       quantity,
                "entry_price":    entry_price,
                "stop_price":     stop_price,
                "target_price":   target_price,
                "breakout_level": breakout_level,
                "order_id":       str(order.id),
                "status":         order.status.value,
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

    return router
