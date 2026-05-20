"""
Market Scanner
Batch scan of symbols using Tier 1 + Tier 2 of the SignalHierarchy.
Streams results via SSE as each symbol completes.
Rate-limited to stay under Alpaca's 200 req/min cap.
"""

import json
import logging
import os
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
    SECTORS,
    ALL_SECTOR_SYMBOLS,
    load_cached_universe,
)
from strategies.momentum import SignalHierarchy, TIMEFRAME_CONFIG

logger = logging.getLogger(__name__)

# Pre-built symbol lists exposed to the UI.
# Keys are the values the frontend sends as list_name.
# Sector keys are prefixed with "sector:" so _resolve_symbols can route them.
SYMBOL_LISTS = {
    "sp500_top100":    SP500_TOP100,
    "nasdaq100_top50": NASDAQ100_TOP50,
    "sector_etfs":     MAJOR_ETFS,
    "russell2000":     RUSSELL2000_SAMPLE,
    "all_sectors":     ALL_SECTOR_SYMBOLS,
    "all_universe":    None,   # resolved dynamically from cache at scan time
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
        if timeframe not in TIMEFRAME_CONFIG:
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
        done_payload = {
            "type":    "done",
            "scanned": scanned,
            "signals": signals,
            "elapsed": elapsed,
        }
        yield self._event(done_payload)

    def write_cache(self, results: list, list_name: str, timeframe: str) -> None:
        """
        Upsert completed scan results into data/scan_cache.json.

        The cache is structured as:
          {
            "long":  { "AAPL": {...}, "MSFT": {...}, ... },
            "swing": { ... },
            "short": { ... },
            "last_updated": "ISO timestamp"
          }

        Each scan upserts its results into the appropriate timeframe namespace
        keyed by symbol.  Running a Financials scan on "long" never touches
        the "swing" namespace or any other symbol not in the current scan.
        """
        from datetime import timezone
        cache_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'scan_cache.json')
        cache_path = os.path.normpath(cache_path)
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)

            # Load existing cache (or start fresh)
            cache = {'long': {}, 'swing': {}, 'short': {}}
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, 'r') as f:
                        existing = json.load(f)
                    for tf in ('long', 'swing', 'short'):
                        if isinstance(existing.get(tf), dict):
                            cache[tf] = existing[tf]
                except Exception:
                    pass  # corrupt cache — start fresh

            # Upsert results into the correct timeframe namespace
            tf_bucket = cache.setdefault(timeframe, {})
            for r in results:
                sym = r.get('symbol')
                if sym:
                    tf_bucket[sym] = r

            cache['last_updated'] = datetime.now(timezone.utc).isoformat()

            with open(cache_path, 'w') as f:
                json.dump(cache, f)

            total_in_tf = len(tf_bucket)
            logger.info(f"Scan cache upserted: {len(results)} results into [{timeframe}] "
                        f"(total in timeframe: {total_in_tf})")
        except Exception as e:
            logger.warning(f"Could not write scan cache: {e}")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _resolve_symbols(self, list_name: str, custom: str) -> list:
        """
        Return the symbol list to scan.

        Supported list_name values:
          sp500_top100, nasdaq100_top50, sector_etfs, russell2000
          all_sectors   — all GICS sector symbols (~500)
          all_universe  — cached Alpaca universe (falls back to all_sectors)
          sector:<Name> — a specific GICS sector, e.g. "sector:Technology"
        """
        if custom.strip():
            return [s.strip().upper() for s in custom.split(",") if s.strip()]

        if list_name.startswith("sector:"):
            sector_name = list_name[len("sector:"):]
            return list(SECTORS.get(sector_name, []))

        if list_name == "all_universe":
            return load_cached_universe()

        return list(SYMBOL_LISTS.get(list_name, []))

    def _fetch_bars(self, symbol: str, timeframe: str = "long") -> pd.DataFrame:
        """Fetch bars for a symbol from Alpaca (IEX feed — free tier compatible).

        Bar interval and lookback days are resolved from TIMEFRAME_CONFIG.
        """
        from datetime import timezone
        from alpaca.data.timeframe import TimeFrameUnit

        cfg      = TIMEFRAME_CONFIG.get(timeframe, TIMEFRAME_CONFIG["long"])
        interval = cfg["interval"]
        days     = cfg["days"]
        tf_obj   = {
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

        score, grade = self._score_setup(
            signal=signal,
            bb_width_pct=bb_width_pct,
            rs_vs_spy=rs_vs_spy,
            rvol=rvol,
            rsi=rsi,
            regime=regime,
        )

        return {
            "symbol":       symbol,
            "price":        price,
            "regime":       regime,
            "tier_reached": tier_reached,
            "signal":       signal,
            "score":        score,
            "grade":        grade,
            "rs_vs_spy":    rs_vs_spy,
            "rvol":         rvol,
            "bb_width_pct": bb_width_pct,
            "atr_pct_rank": atr_pct_rank,
            "rsi":          rsi,
            "tier1_reason": tier1_reason,
            "tier2_reason": tier2_reason,
        }

    @staticmethod
    def _score_setup(
        signal: str,
        bb_width_pct: float | None,
        rs_vs_spy: float | None,
        rvol: float | None,
        rsi: float | None,
        regime: str,
    ) -> tuple[int, str]:
        """
        Score a setup 0–100 from the data already collected during the scan.
        Every symbol is scored regardless of whether it produced a signal — this
        surfaces "almost there" setups that are one indicator tick away from
        triggering.  Grade is only assigned to symbols that have a signal;
        symbols with signal == "NONE" receive grade "—".

        Components (total 100 pts):
          BB compression  25 pts — lower percentile = tighter squeeze = higher score
          RS vs SPY       25 pts — outperformance capped at ±10pp range
          RVOL            20 pts — volume expansion, capped at 3x
          RSI momentum    15 pts — distance from 50 in the correct direction
          ATR contraction 15 pts — proxied by BB score headroom

        Grade bands (signals only):
          A  80–100
          B  60–79
          C  40–59
          D  0–39  (or "—" for no-signal rows)
        """
        score = 0.0

        # ── BB compression (25 pts) ───────────────────────────────────────────
        # bb_width_pct is the percentile rank of current BB width over 252 bars.
        # 0 = maximally compressed, 100 = maximally expanded.
        if bb_width_pct is not None:
            score += max(0.0, (50.0 - bb_width_pct) / 50.0 * 25.0)

        # ── RS vs SPY (25 pts) ────────────────────────────────────────────────
        # Map [-10pp, +10pp] → [0, 25]. Anything above +10pp gets full 25 pts.
        if rs_vs_spy is not None:
            score += max(0.0, min(25.0, (rs_vs_spy + 10.0) / 20.0 * 25.0))

        # ── RVOL (20 pts) ─────────────────────────────────────────────────────
        # Map [1x, 3x] → [0, 20]. Anything above 3x gets full 20 pts.
        if rvol is not None:
            score += max(0.0, min(20.0, (rvol - 1.0) / 2.0 * 20.0))

        # ── RSI momentum (15 pts) ─────────────────────────────────────────────
        # For BULLISH: distance above 50, capped at 30 pts above 50 → full 15 pts.
        # For BEARISH: distance below 50, same logic.
        if rsi is not None:
            if regime == "BULLISH":
                score += max(0.0, min(15.0, (rsi - 50.0) / 30.0 * 15.0))
            else:
                score += max(0.0, min(15.0, (50.0 - rsi) / 30.0 * 15.0))

        # ── ATR contraction (15 pts) ──────────────────────────────────────────
        # Proxied by BB score headroom: very compressed BB → full 15 pts.
        if bb_width_pct is not None:
            score += max(0.0, min(15.0, (50.0 - bb_width_pct) / 30.0 * 15.0))

        total = round(score)

        # Grade is only meaningful for symbols that produced a signal
        if signal == "NONE":
            grade = "—"
        elif total >= 80:
            grade = "A"
        elif total >= 60:
            grade = "B"
        elif total >= 40:
            grade = "C"
        else:
            grade = "D"

        return total, grade

    @staticmethod
    def _event(payload: dict) -> str:
        """Format a dict as an SSE data line."""
        return f"data: {json.dumps(payload, default=str)}\n\n"
