"""
Trading Bot Dashboard
FastAPI web interface with password auth, Alpaca data, and AI analysis.
"""

import logging
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from bot import TradingBot
from config import Config
from data.profile_store import seed_from_env
from data.settings_store import get_setting

logger = logging.getLogger(__name__)


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

    # ── Auth dependency ───────────────────────────────────────────────────────

    def _password_required() -> bool:
        pw = get_setting("dashboard_password")
        return bool(pw and pw.strip())

    def login_required(request: Request):
        if _password_required() and not request.session.get("authenticated"):
            raise HTTPException(status_code=307, headers={"Location": "/login"})

    # ── Register routers ──────────────────────────────────────────────────────

    from routes.pages       import create_router as pages_router
    from routes.account     import create_router as account_router
    from routes.positions   import create_router as positions_router
    from routes.scanner     import create_router as scanner_router
    from routes.bot_control import create_router as bot_control_router
    from routes.settings    import create_router as settings_router
    from routes.backtest    import create_router as backtest_router
    from routes.watchlist   import create_router as watchlist_router

    app.include_router(pages_router(bot, login_required))
    app.include_router(account_router(bot, login_required))
    app.include_router(positions_router(bot, login_required))
    app.include_router(scanner_router(bot, login_required))
    app.include_router(bot_control_router(bot, login_required))
    app.include_router(settings_router(bot, login_required))
    app.include_router(backtest_router(bot, login_required))
    app.include_router(watchlist_router(bot, login_required))

    return app


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
        try:
            from alpaca.data.historical import CryptoHistoricalDataClient
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
            bot.crypto_client = CryptoHistoricalDataClient(
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
