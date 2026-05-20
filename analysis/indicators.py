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
        """Calculate Relative Strength Index."""
        delta    = data[column].diff()
        gain     = delta.where(delta > 0, 0)
        loss     = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
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
    def relative_strength_vs_spy(data, spy_data, period=20):
        """
        Compute rolling relative strength of this stock vs SPY.
        rs_score = stock_return_N - spy_return_N (percentage points)
        Positive = outperforming SPY. Negative = underperforming.
        """
        stock_return = data['close'].pct_change(periods=period) * 100
        spy_return   = spy_data['close'].pct_change(periods=period) * 100
        # Align on index before subtracting
        spy_aligned  = spy_return.reindex(data.index, method='ffill')
        return stock_return - spy_aligned

    @staticmethod
    def vwap(data):
        """
        Calculate VWAP (Volume Weighted Average Price).
        Meaningful for intraday data; on daily data it approximates a
        volume-weighted average close.
        """
        typical_price = (data['high'] + data['low'] + data['close']) / 3
        cum_tp_vol    = (typical_price * data['volume']).cumsum()
        cum_vol       = data['volume'].cumsum()
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
    def stochastic(data, k_period=14, d_period=3):
        """Calculate Stochastic Oscillator."""
        low_min  = data['low'].rolling(window=k_period).min()
        high_max = data['high'].rolling(window=k_period).max()
        k        = 100 * ((data['close'] - low_min) / (high_max - low_min))
        d        = k.rolling(window=d_period).mean()
        return k, d

    @staticmethod
    def calculate_all(data, spy_data=None):
        """
        Calculate all technical indicators.

        Args:
            data (pandas.DataFrame): Price data with OHLC columns
            spy_data (pandas.DataFrame, optional): SPY price data for RS calculation

        Returns:
            pandas.DataFrame: Data with indicators added
        """
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
        df['rsi_14']  = TechnicalIndicators.rsi(df, period=14)
        df['roc_10']  = TechnicalIndicators.rate_of_change(df, period=10)
        df['ema9_slope'] = TechnicalIndicators.ema_slope(df, period=9, slope_period=3)

        # ── Volatility ───────────────────────────────────────────────────────
        upper, middle, lower = TechnicalIndicators.bollinger_bands(df)
        df['bb_upper']  = upper
        df['bb_middle'] = middle
        df['bb_lower']  = lower
        df['bb_width_pct'] = TechnicalIndicators.bb_width_percentile(df)

        df['atr_14']     = TechnicalIndicators.atr(df, period=14)
        df['atr_pct_rank'] = TechnicalIndicators.atr_percentile(df, period=14)

        # ── Volume ───────────────────────────────────────────────────────────
        df['rvol_20'] = TechnicalIndicators.relative_volume(df, period=20)
        df['vwap']    = TechnicalIndicators.vwap(df)

        # ── Relative Strength vs SPY ─────────────────────────────────────────
        if spy_data is not None and not spy_data.empty:
            df['rs_vs_spy_20'] = TechnicalIndicators.relative_strength_vs_spy(df, spy_data, period=20)
        else:
            df['rs_vs_spy_20'] = np.nan

        # ── Stochastic (kept for completeness) ───────────────────────────────
        k, d = TechnicalIndicators.stochastic(df)
        df['stoch_k'] = k
        df['stoch_d'] = d

        logger.info("Calculated all technical indicators")
        return df
