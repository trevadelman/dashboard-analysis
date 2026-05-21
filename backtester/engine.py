"""
backtester/engine.py — Walk-forward backtest engine for SignalHierarchy strategy.

Design principles:
- Strict no-lookahead: signal generation at bar i only sees bars 0..i
- O(n) performance: indicators are computed ONCE on the full dataset upfront.
  At each bar we read the pre-computed row for bar i rather than recomputing
  over a growing slice. Rolling indicators (EMA, BB, ATR, RVOL, RS) are
  inherently causal — the value at bar i only depends on bars 0..i — so
  pre-computing them on the full dataset introduces no lookahead.
  The only exception is bb_width_percentile and atr_percentile which use a
  rolling rank — these are also causal (rank at bar i uses only bars 0..i).
- SPY data is fetched once and aligned so rs_vs_spy_20 is always populated.
- Outcome detection: walk forward from entry bar; first bar where low <= stop = loss,
  first bar where high >= target = win; neither by end of data = timeout (open)
- Returns a plain dict — no side effects, no I/O
"""

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from analysis.indicators import TechnicalIndicators
from analysis.patterns import ChartPatterns
from strategies.momentum import SignalHierarchy, TIMEFRAME_CONFIG

logger = logging.getLogger(__name__)

# Map period string → timedelta for date arithmetic
_PERIOD_DAYS = {
    '1mo':  30,
    '3mo':  90,
    '6mo':  180,
    '1y':   365,
    '2y':   730,
    '3y':   1095,
    '5y':   1825,
}

# Minimum bars needed before we start generating signals (warm-up for indicators)
_WARMUP_BARS = 50


class BacktestEngine:
    """
    Walk-forward backtest engine.

    Usage:
        engine = BacktestEngine(data_client)
        result = engine.run(symbol='CLSK', timeframe='long', period='1y')
    """

    def __init__(self, data_client):
        self.data_client = data_client
        # SPY bars are cached for the lifetime of this engine instance so that
        # multiple run() calls don't re-fetch SPY each time.
        self._spy_cache: dict[str, pd.DataFrame] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, symbol: str, timeframe: str = 'long', period: str = '1y') -> dict:
        """
        Run a walk-forward backtest.

        Args:
            symbol:    Ticker symbol (e.g. 'CLSK')
            timeframe: 'long', 'swing', or 'short' — maps to the same defaults
                       as SignalHierarchy
            period:    Lookback period string ('1mo', '3mo', '6mo', '1y', '2y', '3y', '5y')

        Returns:
            {
                symbol, timeframe, period,
                total_bars, signals_generated,
                summary: { total, wins, losses, timeouts, win_rate, loss_rate,
                           avg_win_r, avg_loss_r, expectancy, total_r,
                           max_drawdown_r },
                trades: [ { bar_index, date, entry, stop, target, r_risk,
                            outcome, r_multiple, exit_date, exit_price,
                            bars_held } ]
            }
        """
        bars = self._fetch_bars(symbol, period, timeframe)
        if bars is None or len(bars) < _WARMUP_BARS + 1:
            return self._empty_result(symbol, timeframe, period,
                                      reason='Insufficient data')

        # Fetch SPY for the same window so rs_vs_spy_20 is populated.
        # If symbol IS SPY, skip to avoid self-comparison (RS would always be 0).
        spy_bars = None
        if symbol.upper() != 'SPY':
            spy_bars = self._fetch_spy(period, timeframe)

        # Pre-compute all indicators ONCE on the full dataset.
        # This is O(n) instead of O(n²) — critical for swing/short timeframes
        # where n can be 3,000–50,000 bars.
        enriched = self._precompute_indicators(bars, spy_bars)

        trades = self._walk_forward(bars, enriched, timeframe)
        summary = self._summarise(trades)

        return {
            'symbol':            symbol,
            'timeframe':         timeframe,
            'period':            period,
            'total_bars':        len(bars),
            'signals_generated': len(trades),
            'summary':           summary,
            'trades':            trades,
        }

    # ── Data fetching ─────────────────────────────────────────────────────────

    def _fetch_bars(self, symbol: str, period: str, timeframe: str) -> pd.DataFrame | None:
        """
        Fetch OHLCV bars via bar_fetcher (single choke point for all Alpaca fetches).
        Returns a DataFrame indexed by timestamp, sorted ascending.

        Bar interval is resolved from TIMEFRAME_CONFIG (single source of truth).
        """
        from data.bar_fetcher import fetch_equity_bars, build_timeframe_from_key

        tf_key = TIMEFRAME_CONFIG.get(timeframe, TIMEFRAME_CONFIG['long'])['alpaca_tf']
        interval_map = {'Day': '1d', 'Hour': '1h', 'Minute15': '15m'}
        interval = interval_map.get(tf_key, '1d')

        days = _PERIOD_DAYS.get(period, 365)
        # Add extra warm-up buffer so indicators are valid at the start of the test window
        extra_days = _WARMUP_BARS * 2

        df = fetch_equity_bars(self.data_client, symbol, period, interval, extra_days=extra_days)
        if df is None or df.empty:
            logger.warning(f'BacktestEngine: no data for {symbol}')
            return None

        df.index.name = 'timestamp'
        return df.sort_index()

    def _fetch_spy(self, period: str, timeframe: str) -> pd.DataFrame | None:
        """
        Fetch SPY bars for the same period/timeframe, using an in-memory cache
        so repeated run() calls don't re-fetch.
        """
        cache_key = f'{period}_{timeframe}'
        if cache_key in self._spy_cache:
            return self._spy_cache[cache_key]

        spy = self._fetch_bars('SPY', period, timeframe)
        if spy is not None:
            self._spy_cache[cache_key] = spy
        return spy

    # ── Indicator pre-computation ─────────────────────────────────────────────

    def _precompute_indicators(self, bars: pd.DataFrame,
                               spy_bars: pd.DataFrame | None) -> pd.DataFrame:
        """
        Compute all indicators and patterns on the full dataset in one pass.

        All rolling indicators (EMA, BB, ATR, RVOL, RS, bb_width_pct) are
        inherently causal — the value at row i depends only on rows 0..i.
        Pre-computing them on the full dataset is therefore equivalent to
        computing them on bars[0..i] at each step, with no lookahead.

        ChartPatterns uses centered rolling windows (find_swing_highs uses
        center=True) which DO introduce lookahead. We replace them with
        causal equivalents here.
        """
        enriched = TechnicalIndicators.calculate_all(bars, benchmark_data=spy_bars)
        enriched = self._add_causal_patterns(enriched)
        return enriched

    def _add_causal_patterns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add pattern columns using strictly causal (non-centered) windows.
        ChartPatterns.find_swing_highs uses center=True which looks ahead;
        for backtesting we use a trailing window instead.

        We only add the columns that SignalHierarchy actually reads.
        The full ChartPatterns suite (double_top, H&S, etc.) is not used
        by the signal hierarchy and is expensive — skip it in the backtest.
        """
        # Trailing swing high: bar i is a swing high if it's the max of the
        # prior 5 bars (bars i-4..i). This is causal and fast.
        df['swing_high'] = df['high'].rolling(5).apply(
            lambda x: float(x[-1] == x.max()), raw=True
        )
        df['swing_low'] = df['low'].rolling(5).apply(
            lambda x: float(x[-1] == x.min()), raw=True
        )
        # Remaining pattern columns — set to False (not used by strategy)
        df['double_top']                  = False
        df['double_bottom']               = False
        df['head_and_shoulders']          = False
        df['inverse_head_and_shoulders']  = False
        return df

    # ── Walk-forward loop ─────────────────────────────────────────────────────

    def _walk_forward(self, bars: pd.DataFrame, enriched: pd.DataFrame,
                      timeframe: str) -> list[dict]:
        """
        Iterate bar by bar.  At each bar, pass enriched.iloc[:i+1] to
        SignalHierarchy — this is a zero-copy slice of the pre-computed
        DataFrame, so no indicator recomputation occurs.

        We skip bars that are within the hold period of an already-open trade
        (no pyramiding — one position at a time).
        """
        trades = []
        n = len(bars)
        i = _WARMUP_BARS  # start after warm-up
        in_trade_until = -1  # bar index when the current trade resolves

        while i < n - 1:
            if i <= in_trade_until:
                i += 1
                continue

            # Pass the pre-computed slice — no indicator recomputation
            signal = self._generate_signal(enriched.iloc[:i + 1], timeframe)

            if signal is None or signal.get('action') not in ('buy', 'sell'):
                i += 1
                continue

            entry  = signal.get('entry_price')
            stop   = signal.get('stop_price')
            target = signal.get('target_price')

            if not all([entry, stop, target]):
                i += 1
                continue

            r_risk = abs(entry - stop)
            if r_risk == 0:
                i += 1
                continue

            # Walk forward from bar i+1 to find outcome
            outcome, exit_bar, exit_price = self._find_outcome(
                bars, i + 1, entry, stop, target, signal['action']
            )

            bars_held  = exit_bar - i if exit_bar is not None else n - 1 - i
            r_multiple = self._calc_r(entry, exit_price, stop, signal['action']) \
                         if exit_price is not None else 0.0

            trade = {
                'bar_index':  i,
                'date':       bars.index[i].isoformat() if hasattr(bars.index[i], 'isoformat') else str(bars.index[i]),
                'entry':      round(entry, 4),
                'stop':       round(stop, 4),
                'target':     round(target, 4),
                'r_risk':     round(r_risk, 4),
                'action':     signal['action'],
                'outcome':    outcome,
                'r_multiple': round(r_multiple, 3),
                'exit_date':  bars.index[exit_bar].isoformat() if exit_bar is not None and hasattr(bars.index[exit_bar], 'isoformat') else None,
                'exit_price': round(exit_price, 4) if exit_price is not None else None,
                'bars_held':  bars_held,
                'tier':       signal.get('tier', 0),
                'confidence': signal.get('confidence', 0),
            }
            trades.append(trade)

            in_trade_until = exit_bar if exit_bar is not None else n - 1
            i = in_trade_until + 1

        return trades

    def _generate_signal(self, enriched_slice: pd.DataFrame,
                         timeframe: str) -> dict | None:
        """
        Run SignalHierarchy on a pre-computed indicator slice.
        No indicator recomputation — just reads the last row of the slice.

        Returns a normalised signal dict with 'action' key, or None.
        """
        try:
            hierarchy = SignalHierarchy(timeframe=timeframe)
            done      = hierarchy.generate_signal(enriched_slice, symbol='_backtest')
            if done is None:
                return None
            signals = done.get('signals', [])
            if not signals:
                return None
            sig = dict(signals[0])  # copy so we don't mutate the original
            # Strategy returns 'side'; engine expects 'action'
            if 'side' in sig:
                sig['action'] = sig['side']
            return sig
        except Exception as e:
            logger.debug(f'BacktestEngine: signal error at bar {len(enriched_slice)}: {e}')
            return None

    def _find_outcome(self, bars: pd.DataFrame, start_i: int,
                      entry: float, stop: float, target: float,
                      action: str) -> tuple[str, int | None, float | None]:
        """
        Walk forward from start_i.  Return (outcome, exit_bar_index, exit_price).

        For a long (buy):
          - win  if high >= target
          - loss if low  <= stop
        For a short (sell):
          - win  if low  <= target
          - loss if high >= stop

        If both conditions are met on the same bar, we conservatively call it a loss
        (stop hit first intrabar).
        """
        n = len(bars)
        for j in range(start_i, n):
            row = bars.iloc[j]
            if action == 'buy':
                stopped  = row['low']  <= stop
                targeted = row['high'] >= target
            else:
                stopped  = row['high'] >= stop
                targeted = row['low']  <= target

            if stopped and targeted:
                return 'loss', j, stop
            if stopped:
                return 'loss', j, stop
            if targeted:
                return 'win', j, target

        return 'timeout', None, None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _calc_r(entry: float, exit_price: float, stop: float, action: str) -> float:
        """Return the R multiple for a trade."""
        r_risk = abs(entry - stop)
        if r_risk == 0:
            return 0.0
        if action == 'buy':
            return (exit_price - entry) / r_risk
        return (entry - exit_price) / r_risk

    @staticmethod
    def _summarise(trades: list[dict]) -> dict:
        """Compute aggregate statistics from a list of trade dicts."""
        if not trades:
            return {
                'total': 0, 'wins': 0, 'losses': 0, 'timeouts': 0,
                'win_rate': 0.0, 'loss_rate': 0.0,
                'avg_win_r': 0.0, 'avg_loss_r': 0.0,
                'expectancy': 0.0, 'total_r': 0.0,
                'max_drawdown_r': 0.0,
            }

        wins     = [t for t in trades if t['outcome'] == 'win']
        losses   = [t for t in trades if t['outcome'] == 'loss']
        timeouts = [t for t in trades if t['outcome'] == 'timeout']
        total    = len(trades)

        win_rate  = len(wins)  / total
        loss_rate = len(losses) / total

        avg_win_r  = sum(t['r_multiple'] for t in wins)   / len(wins)   if wins   else 0.0
        avg_loss_r = sum(t['r_multiple'] for t in losses) / len(losses) if losses else 0.0

        expectancy = (win_rate * avg_win_r) + (loss_rate * avg_loss_r)

        # Equity curve in R units for drawdown calculation
        r_curve   = []
        running_r = 0.0
        peak_r    = 0.0
        max_dd    = 0.0
        for t in trades:
            running_r += t['r_multiple']
            r_curve.append(running_r)
            if running_r > peak_r:
                peak_r = running_r
            dd = peak_r - running_r
            if dd > max_dd:
                max_dd = dd

        return {
            'total':           total,
            'wins':            len(wins),
            'losses':          len(losses),
            'timeouts':        len(timeouts),
            'win_rate':        round(win_rate, 4),
            'loss_rate':       round(loss_rate, 4),
            'avg_win_r':       round(avg_win_r, 3),
            'avg_loss_r':      round(avg_loss_r, 3),
            'expectancy':      round(expectancy, 3),
            'total_r':         round(running_r, 3),
            'max_drawdown_r':  round(max_dd, 3),
            'r_curve':         [round(r, 3) for r in r_curve],
        }

    @staticmethod
    def _empty_result(symbol: str, timeframe: str, period: str,
                      reason: str = '') -> dict:
        return {
            'symbol':            symbol,
            'timeframe':         timeframe,
            'period':            period,
            'total_bars':        0,
            'signals_generated': 0,
            'error':             reason,
            'summary':           BacktestEngine._summarise([]),
            'trades':            [],
        }
