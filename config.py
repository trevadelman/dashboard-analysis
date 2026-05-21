"""
Configuration for the Trading Bot.
All runtime settings are read from the SQLite settings store (data/settings_store.py).
Environment variables / .env are only used as a one-time bootstrap on first run.
"""

import logging

logger = logging.getLogger(__name__)


class Config:
    """
    Runtime configuration.  Values are loaded from the settings store on init.
    Alpaca credentials are NOT stored here — they live in the profile store.
    """

    def __init__(self):
        from data.settings_store import init_from_env, get_setting

        # Bootstrap settings from env on very first run (no-op if already seeded)
        init_from_env()

        # ── Alpaca (still read from env for backward compat / seed_from_env) ──
        import os
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        self.ALPACA_API_KEY    = os.getenv("ALPACA_PUBLIC") or os.getenv("ALPACA_API_KEY")
        self.ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET") or os.getenv("ALPACA_SECRET_KEY")
        self.PAPER_TRADING     = os.getenv("PAPER_TRADING", "true").lower() == "true"
        self.ALPACA_BASE_URL   = (
            "https://paper-api.alpaca.markets" if self.PAPER_TRADING
            else "https://api.alpaca.markets"
        )

        if not self.ALPACA_API_KEY or not self.ALPACA_SECRET_KEY:
            logger.warning("Alpaca credentials not found in .env — use the dashboard to add a profile.")

        # ── AI provider (from settings store) ────────────────────────────────
        self.OPENAI_BASE_URL = get_setting("ai_base_url")
        self.OPENAI_API_KEY  = get_setting("ai_api_key")
        self.OLLAMA_MODEL    = get_setting("ai_model")

        # ── Risk management (from settings store) ─────────────────────────────
        self.MAX_POSITIONS    = int(get_setting("max_positions") or "5")
        self.RISK_PERCENTAGE  = float(get_setting("risk_percentage") or "2.0")
        self.MAX_POSITION_PCT = float(get_setting("max_position_pct") or "20.0")

        # ── Autonomous bot (settings store; .env is fallback for bootstrap) ──
        self.BOT_AUTONOMOUS = (
            get_setting("bot_autonomous") or os.getenv("BOT_AUTONOMOUS", "false")
        ).lower() == "true"
        self.BOT_SCAN_WATCHLIST = (
            get_setting("bot_scan_watchlist") or os.getenv("BOT_SCAN_WATCHLIST", "sp500_top100")
        )
        self.BOT_MAX_DAILY_LOSS_PCT = float(
            get_setting("bot_max_daily_loss_pct") or os.getenv("BOT_MAX_DAILY_LOSS_PCT", "2.0")
        )
        self.BOT_ENTRY_COOLDOWN_HOURS = int(
            get_setting("bot_entry_cooldown_hours") or os.getenv("BOT_ENTRY_COOLDOWN_HOURS", "24")
        )
        self.BOT_REVIEW_TIMEFRAMES = [
            t.strip()
            for t in (
                get_setting("bot_review_timeframes") or os.getenv("BOT_REVIEW_TIMEFRAMES", "swing,long")
            ).split(",")
            if t.strip()
        ]

        # ── Strategy parameters (hardcoded defaults — tuned per timeframe) ────
        # These are not user-facing settings; they live in strategies/momentum.py
        # TIMEFRAME_DEFAULTS.  The dict below is kept for the /api/config endpoint.
        self.STRATEGY_PARAMS = {
            # Moving average periods (EMA-based)
            "ema_short":  9,
            "ema_medium": 21,
            "ema_long":   50,
            # Kept for backward compat
            "sma_short":  20,
            "sma_medium": 50,
            "sma_long":   200,
            # RSI
            "rsi_buy":         55,
            "rsi_sell":        45,
            "rsi_neutral_min": 45,
            "rsi_neutral_max": 55,
            # Compression
            "bb_width_pct_max": 30.0,
            "atr_pct_rank_max": 40.0,
            # Relative strength
            "rs_min":   0.0,
            # Trigger
            "rvol_min": 1.2,
            # Risk / exits
            "atr_multiplier":  2.0,
            "min_rr_ratio":    2.0,
            "price_range_min": 1.0,
            "atr_pct_max":     10.0,
            "atr_pct_min":     1.0,
            # Anti-chase
            "extension_limit": 1.5,
            # Liquidity
            "min_price":         5.0,
            "min_dollar_volume": 5_000_000.0,
            # RS requirement
            "require_rs": True,
            # AI
            "ai_confidence_min": 70,
        }
