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
        # MAX_POSITIONS is a hard safety rail (not the primary constraint).
        # The primary constraint is BOT_MAX_PORTFOLIO_HEAT_PCT — total open risk
        # as a % of equity.  Position count is an output, not an input.
        self.MAX_POSITIONS    = int(get_setting("max_positions") or "12")
        self.RISK_PERCENTAGE  = float(get_setting("risk_percentage") or "1.0")
        self.MAX_POSITION_PCT = float(get_setting("max_position_pct") or "20.0")

        # Heat-based portfolio management
        # BOT_MAX_PORTFOLIO_HEAT_PCT: total open risk budget as % of equity.
        #   Each open position contributes |entry - stop| × qty / equity.
        #   New entries are blocked when current heat ≥ this limit.
        self.BOT_MAX_PORTFOLIO_HEAT_PCT = float(
            get_setting("bot_max_portfolio_heat_pct") or os.getenv("BOT_MAX_PORTFOLIO_HEAT_PCT", "5.0")
        )
        # BOT_MAX_RISK_PER_TRADE_PCT: per-trade risk cap as % of equity.
        #   Prevents any single trade from consuming the entire heat budget.
        self.BOT_MAX_RISK_PER_TRADE_PCT = float(
            get_setting("bot_max_risk_per_trade_pct") or os.getenv("BOT_MAX_RISK_PER_TRADE_PCT", "1.0")
        )
        # BOT_MIN_RISK_PCT: minimum implied risk for an entry to be worth taking.
        #   If the position cap squeezes the position to < this % of equity,
        #   skip the entry — it can't move the needle on the account.
        self.BOT_MIN_RISK_PCT = float(
            get_setting("bot_min_risk_pct") or os.getenv("BOT_MIN_RISK_PCT", "0.25")
        )

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
        # Minimum scanner grade required for auto-entry.
        # Grades: A (80-100), B (60-79), C (40-59), D (0-39).
        # Default "B" — only take setups with strong RS + RVOL confirmation.
        self.BOT_MIN_GRADE = (
            get_setting("bot_min_grade") or os.getenv("BOT_MIN_GRADE", "B")
        ).upper()

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
