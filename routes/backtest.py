"""
routes/backtest.py — Walk-forward backtesting.

Covers:
  POST /api/backtest
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def create_router(bot, login_required) -> APIRouter:
    router = APIRouter()

    @router.post("/api/backtest")
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
            symbol              = data.get("symbol", "SPY").upper().strip()
            timeframe           = data.get("timeframe", "long")
            period              = data.get("period", "1y")
            use_position_review = bool(data.get("use_position_review", False))
            engine = BacktestEngine(bot.data_client)
            result = engine.run(
                symbol=symbol,
                timeframe=timeframe,
                period=period,
                use_position_review=use_position_review,
            )
            return result
        except Exception as e:
            logger.error(f"Backtest error: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    return router
