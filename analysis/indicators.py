"""
Technical indicators for market analysis.
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


class TechnicalIndicators:
    """Class for calculating technical indicators."""

    @staticmethod
    def sma(data, column='close', period=20):
        """Calculate Simple Moving Average."""
        return data[column].rolling(window=period).mean()

    @staticmethod
    def ema(data, column='close', period=20):
        """Calculate Exponential Moving Average."""
        return data[column].ewm(span=period, adjust=False).mean()

    @staticmethod
    def rsi(data, column='close', period=14):
        """
        Calculate Relative Strength Index using Wilder's Smoothed Moving Average (SMMA).

        Wilder's original RSI uses a smoothed/exponential average with alpha = 1/period,
        equivalent to ewm(com=period-1).  This matches TradingView, Bloomberg, and all
        major charting platforms.  The simple rolling mean diverges by 2–8 points in
        trending markets and would miscalibrate the RSI thresholds used in SignalHierarchy.
        """
        delta    = data[column].diff()
        gain     = delta.where(delta > 0, 0)
        loss     = -delta.where(delta < 0, 0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs       = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def rate_of_change(data, column='close', period=10):
        """
        Calculate Rate of Change (momentum without MACD lag).
        ROC = (close - close[n]) / close[n] * 100
        """
        return data[column].pct_change(periods=period) * 100

    @staticmethod
    def ema_slope(data, column='close', period=9, slope_period=3):
        """
        Calculate the slope of an EMA as a proxy for trend acceleration.
        Returns the percentage change of the EMA over slope_period bars.
        """
        ema_vals = data[column].ewm(span=period, adjust=False).mean()
        return ema_vals.pct_change(periods=slope_period) * 100

    @staticmethod
    def bollinger_bands(data, column='close', period=20, std_dev=2):
        """Calculate Bollinger Bands."""
        middle_band = TechnicalIndicators.sma(data, column, period)
        std         = data[column].rolling(window=period).std()
        upper_band  = middle_band + (std * std_dev)
        lower_band  = middle_band - (std * std_dev)
        return upper_band, middle_band, lower_band

    @staticmethod
    def bb_width_percentile(data, column='close', period=20, lookback=252):
        """
        Bollinger Band width as a percentile of its own recent history.
        Low percentile = compression. High percentile = expansion.
        Returns a 0–100 percentile series.
        """
        upper, middle, lower = TechnicalIndicators.bollinger_bands(data, column, period)
        bb_width = (upper - lower) / middle
        return bb_width.rolling(window=lookback, min_periods=20).rank(pct=True) * 100

    @staticmethod
    def atr(data, period=14):
        """Calculate Average True Range."""
        high_low   = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close  = np.abs(data['low'] - data['close'].shift())
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return true_range.rolling(window=period).mean()

    @staticmethod
    def atr_percentile(data, period=14, lookback=252):
        """
        ATR as a percentile of its own recent history.
        Low percentile = volatility compression. High = expansion.
        Returns a 0–100 percentile series.
        """
        atr_vals = TechnicalIndicators.atr(data, period)
        return atr_vals.rolling(window=lookback, min_periods=20).rank(pct=True) * 100

    @staticmethod
    def relative_strength_vs_benchmark(data, benchmark_data, period=20):
        """
        Compute rolling relative strength of this asset vs a benchmark.
        rs_score = asset_return_N - benchmark_return_N (percentage points)
        Positive = outperforming benchmark. Negative = underperforming.

        Works for both equities (benchmark = SPY) and crypto (benchmark = BTC/USD).
        """
        asset_return     = data['close'].pct_change(periods=period) * 100
        bench_return     = benchmark_data['close'].pct_change(periods=period) * 100
        bench_aligned    = bench_return.reindex(data.index, method='ffill')
        return asset_return - bench_aligned

    @staticmethod
    def session_vwap(data):
        """
        Calculate session-reset VWAP (Volume Weighted Average Price).

        Resets at the start of each trading session (date boundary) so the value
        is meaningful on both daily and intraday bars.  On daily bars each bar IS
        its own session, so VWAP equals the typical price weighted by that day's
        volume — equivalent to the daily typical price.

        The previous cumulative implementation compounded across sessions, making
        the value meaningless after the first day on intraday timeframes.
        """
        typical_price = (data['high'] + data['low'] + data['close']) / 3

        # Determine the session date for each bar
        if hasattr(data.index, 'date'):
            session_date = pd.Series(data.index.date, index=data.index)
        else:
            session_date = pd.Series(data.index, index=data.index)

        tp_vol = typical_price * data['volume']

        # Cumsum within each session group
        cum_tp_vol = tp_vol.groupby(session_date).cumsum()
        cum_vol    = data['volume'].groupby(session_date).cumsum()
        return cum_tp_vol / cum_vol

    @staticmethod
    def relative_volume(data, period=20):
        """
        Relative volume: today's volume vs rolling average.
        RVOL > 1.5 = elevated participation.
        """
        avg_vol = data['volume'].rolling(window=period).mean()
        return data['volume'] / avg_vol

    @staticmethod
    def calculate_all(data, benchmark_data=None, spy_data=None):
        """
        Calculate all technical indicators.

        Args:
            data (pandas.DataFrame): Price data with OHLC columns
            benchmark_data (pandas.DataFrame, optional): Benchmark bars for RS calculation.
                For equities this is SPY; for crypto alts this is BTC/USD.
                When empty or None, rs_vs_spy_20 is set to NaN (no RS gate).
            spy_data (pandas.DataFrame, optional): Deprecated alias for benchmark_data.
                Accepted for backward compatibility — callers that pass spy_data= still work.

        Returns:
            pandas.DataFrame: Data with indicators added
        """
        # Backward-compat: spy_data= is an alias for benchmark_data=
        if benchmark_data is None and spy_data is not None:
            benchmark_data = spy_data

        df = data.copy()

        # ── Moving Averages ──────────────────────────────────────────────────
        # Keep SMAs for backward compatibility with chart display
        df['sma_20']  = TechnicalIndicators.sma(df, period=20)
        df['sma_50']  = TechnicalIndicators.sma(df, period=50)
        df['sma_200'] = TechnicalIndicators.sma(df, period=200)

        # EMAs — primary signals for momentum strategy
        df['ema_9']   = TechnicalIndicators.ema(df, period=9)
        df['ema_21']  = TechnicalIndicators.ema(df, period=21)
        df['ema_50']  = TechnicalIndicators.ema(df, period=50)
        df['ema_200'] = TechnicalIndicators.ema(df, period=200)

        # ── Momentum ─────────────────────────────────────────────────────────
        df['rsi_14']     = TechnicalIndicators.rsi(df, period=14)
        df['roc_10']     = TechnicalIndicators.rate_of_change(df, period=10)
        df['ema9_slope'] = TechnicalIndicators.ema_slope(df, period=9, slope_period=3)

        # ── Volatility ───────────────────────────────────────────────────────
        upper, middle, lower = TechnicalIndicators.bollinger_bands(df)
        df['bb_upper']     = upper
        df['bb_middle']    = middle
        df['bb_lower']     = lower
        df['bb_width_pct'] = TechnicalIndicators.bb_width_percentile(df)

        df['atr_14']       = TechnicalIndicators.atr(df, period=14)
        df['atr_pct_rank'] = TechnicalIndicators.atr_percentile(df, period=14)

        # ── Volume ───────────────────────────────────────────────────────────
        df['rvol_20'] = TechnicalIndicators.relative_volume(df, period=20)
        df['vwap']    = TechnicalIndicators.session_vwap(df)

        # ── Relative Strength vs benchmark ───────────────────────────────────
        # Column is always named rs_vs_spy_20 for backward compatibility with
        # all downstream strategy and scanner code.
        if benchmark_data is not None and not benchmark_data.empty:
            df['rs_vs_spy_20'] = TechnicalIndicators.relative_strength_vs_benchmark(
                df, benchmark_data, period=20
            )
        else:
            df['rs_vs_spy_20'] = np.nan

        logger.debug("Calculated all technical indicators")
        return df
