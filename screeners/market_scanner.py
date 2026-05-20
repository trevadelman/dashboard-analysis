"""
Market Scanner
Batch scan of symbols using Tier 1 + Tier 2 of the SignalHierarchy.
Streams results via SSE as each symbol completes.
Rate-limited to stay under Alpaca's 200 req/min cap.
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Generator

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

from analysis.indicators import TechnicalIndicators
from screeners.symbol_lists import (
    SP500_TOP100,
    NASDAQ100_TOP50,
    MAJOR_ETFS,
    RUSSELL2000_SAMPLE,
)
from strategies.momentum import SignalHierarchy

logger = logging.getLogger(__name__)

# Pre-built symbol lists exposed to the UI
SYMBOL_LISTS = {
    "sp500_top100":    SP500_TOP100,
    "nasdaq100_top50": NASDAQ100_TOP50,
    "sector_etfs":     MAJOR_ETFS,
    "russell2000":     RUSSELL2000_SAMPLE,
}

# Seconds between Alpaca bar requests — keeps us at ~170 req/min (under 200 limit)
_REQUEST_INTERVAL = 0.36


class MarketScanner:
    """
    Scans a list of symbols against Tier 1 (regime) and Tier 2 (compression/RS/RVOL)
    of the SignalHierarchy using Alpaca daily bars.
    """

    def __init__(self, data_client: StockHistoricalDataClient):
        self._data_client = data_client
        self._spy_data: pd.DataFrame = pd.DataFrame()

    # ── Public API ────────────────────────────────────────────────────────────

    # Timeframe → (interval, days)
    _TF_CONFIG = {
        "long":  ("1d",  365),
        "swing": ("1h",  90),
        "short": ("15m", 14),
    }

    def scan_stream(
        self,
        list_name: str = "sp500_top100",
        custom: str = "",
        timeframe: str = "long",
    ) -> Generator[str, None, None]:
        """
        Scan a symbol list and yield SSE-formatted events.

        Events:
          {"type": "start",    "total": N, "list": list_name, "timeframe": tf}
          {"type": "result",   "symbol": ..., "price": ..., ...}
          {"type": "progress", "scanned": N, "total": N}
          {"type": "done",     "scanned": N, "signals": N, "elapsed": secs}
          {"type": "error",    "message": ...}
        """
        if timeframe not in self._TF_CONFIG:
            timeframe = "long"

        symbols = self._resolve_symbols(list_name, custom)
        if not symbols:
            yield self._event({"type": "error", "message": f"Unknown list '{list_name}'"})
            return

        total    = len(symbols)
        scanned  = 0
        signals  = 0
        start_ts = time.time()

        yield self._event({"type": "start", "total": total, "list": list_name, "timeframe": timeframe})

        # Fetch SPY once as the benchmark for relative strength
        _, spy_days = self._TF_CONFIG[timeframe]
        self._spy_data = self._fetch_bars("SPY", timeframe=timeframe)
        time.sleep(_REQUEST_INTERVAL)

        for symbol in symbols:
            result = self._scan_one(symbol, timeframe=timeframe)
            scanned += 1

            if result is not None:
                if result.get("signal") in ("BUY", "SELL"):
                    signals += 1
                yield self._event({"type": "result", **result})

            yield self._event({"type": "progress", "scanned": scanned, "total": total})
            time.sleep(_REQUEST_INTERVAL)

        elapsed = round(time.time() - start_ts, 1)
        yield self._event({
            "type":    "done",
            "scanned": scanned,
            "signals": signals,
            "elapsed": elapsed,
        })

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _resolve_symbols(self, list_name: str, custom: str) -> list:
        """Return the symbol list to scan."""
        if custom.strip():
            return [s.strip().upper() for s in custom.split(",") if s.strip()]
        return list(SYMBOL_LISTS.get(list_name, []))

    def _fetch_bars(self, symbol: str, timeframe: str = "long") -> pd.DataFrame:
        """Fetch bars for a symbol from Alpaca (IEX feed — free tier compatible)."""
        from datetime import timezone

        interval, days = self._TF_CONFIG.get(timeframe, ("1d", 365))
        from alpaca.data.timeframe import TimeFrameUnit
        tf_obj = {
            "1d":  TimeFrame.Day,
            "1h":  TimeFrame.Hour,
            "15m": TimeFrame(15, TimeFrameUnit.Minute),
        }.get(interval, TimeFrame.Day)

        try:
            end   = datetime.now(timezone.utc)
            start = end - timedelta(days=days)
            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf_obj,
                start=start,
                end=end,
                limit=10000,
                feed=DataFeed.IEX,
            )
            bars = self._data_client.get_stock_bars(request).df
            if bars.empty:
                return pd.DataFrame()
            # alpaca-py returns a MultiIndex (symbol, timestamp) — drop the symbol level
            if isinstance(bars.index, pd.MultiIndex):
                bars = bars.droplevel(0)
            bars.columns = [c.lower() for c in bars.columns]
            return bars
        except Exception as e:
            logger.debug(f"Failed to fetch bars for {symbol}: {e}")
            return pd.DataFrame()

    def _scan_one(self, symbol: str, timeframe: str = "long") -> dict | None:
        """
        Fetch bars, run Tier 1 + Tier 2, and return a result dict.
        Returns None if data could not be fetched.
        """
        bars = self._fetch_bars(symbol, timeframe=timeframe)
        if bars.empty or len(bars) < 50:
            return None

        try:
            bars = TechnicalIndicators.calculate_all(bars, spy_data=self._spy_data)
        except Exception as e:
            logger.debug(f"Indicator error for {symbol}: {e}")
            return None

        # Run Tier 1 + Tier 2 only (no AI for batch scanning)
        strategy = SignalHierarchy(ai_generator=None, timeframe=timeframe)

        regime, tier1_details = strategy.check_market_regime(bars)
        tier1_passed = regime != "NO_TRADE"

        tier2_signal = None
        tier2_details = []
        if tier1_passed:
            tier2_signal, tier2_details = strategy.check_setup_and_trigger(bars, regime, symbol)

        latest = bars.iloc[-1]
        price  = round(float(latest.get("close", 0)), 2)

        # Relative strength vs SPY (last bar's rs_vs_spy_20 column)
        rs_vs_spy = None
        if "rs_vs_spy_20" in bars.columns:
            val = latest.get("rs_vs_spy_20")
            if val is not None and not (isinstance(val, float) and pd.isna(val)):
                rs_vs_spy = round(float(val), 2)

        # RVOL
        rvol = None
        if "rvol_20" in bars.columns:
            val = latest.get("rvol_20")
            if val is not None and not (isinstance(val, float) and pd.isna(val)):
                rvol = round(float(val), 2)

        # BB width percentile
        bb_width_pct = None
        if "bb_width_pct" in bars.columns:
            val = latest.get("bb_width_pct")
            if val is not None and not (isinstance(val, float) and pd.isna(val)):
                bb_width_pct = round(float(val), 1)

        # ATR rank
        atr_pct_rank = None
        if "atr_pct_rank" in bars.columns:
            val = latest.get("atr_pct_rank")
            if val is not None and not (isinstance(val, float) and pd.isna(val)):
                atr_pct_rank = round(float(val), 1)

        # RSI
        rsi = None
        if "rsi_14" in bars.columns:
            val = latest.get("rsi_14")
            if val is not None and not (isinstance(val, float) and pd.isna(val)):
                rsi = round(float(val), 1)

        # Determine tier reached and signal
        tier_reached = 1
        signal       = "NONE"
        tier1_reason = tier1_details[-1] if tier1_details else ""
        tier2_reason = ""

        if tier1_passed:
            tier_reached = 2
            if tier2_signal is not None:
                # Tier 2 passed — valid setup
                if regime == "BULLISH":
                    signal = "BUY"
                elif regime == "BEARISH":
                    signal = "SELL"
            tier2_reason = tier2_details[-1] if tier2_details else ""

        return {
            "symbol":       symbol,
            "price":        price,
            "regime":       regime,
            "tier_reached": tier_reached,
            "signal":       signal,
            "rs_vs_spy":    rs_vs_spy,
            "rvol":         rvol,
            "bb_width_pct": bb_width_pct,
            "atr_pct_rank": atr_pct_rank,
            "rsi":          rsi,
            "tier1_reason": tier1_reason,
            "tier2_reason": tier2_reason,
        }

    @staticmethod
    def _event(payload: dict) -> str:
        """Format a dict as an SSE data line."""
        return f"data: {json.dumps(payload, default=str)}\n\n"
