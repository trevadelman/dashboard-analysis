"""
data/bar_fetcher.py — Single choke point for all Alpaca bar fetching.

Every place in the codebase that needs OHLCV bars goes through here.
Feed selection, timezone-aware date ranges, timeframe mapping, and
MultiIndex normalization all live in exactly one place.

Public API:
    build_timeframe(interval)           → Alpaca TimeFrame
    build_date_range(period, extra_days) → (start, end) UTC datetimes
    fetch_equity_bars(client, symbol, period, interval) → DataFrame
    fetch_equity_bars_batch(client, symbols, period, interval) → {sym: DataFrame}
    fetch_crypto_bars(client, symbol, period, interval) → DataFrame
    fetch_crypto_bars_batch(client, symbols, period, interval) → {sym: DataFrame}
"""

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_PERIOD_DAYS: dict[str, int] = {
    '5d':  5,
    '2w':  14,
    '1mo': 30,
    '3mo': 90,
    '6mo': 180,
    '1y':  365,
    '2y':  730,
    '3y':  1095,
    '5y':  1825,
}

_INTERVAL_TO_TF: dict[str, TimeFrame] = {
    '1d':  TimeFrame.Day,
    '1h':  TimeFrame.Hour,
    '15m': TimeFrame(15, TimeFrameUnit.Minute),
    '5m':  TimeFrame(5, TimeFrameUnit.Minute),
    '1m':  TimeFrame.Minute,
}

# Alpaca's alpaca_tf key (used by TIMEFRAME_CONFIG) → TimeFrame
_ALPACA_TF_KEY_MAP: dict[str, TimeFrame] = {
    'Day':      TimeFrame.Day,
    'Hour':     TimeFrame.Hour,
    'Minute15': TimeFrame(15, TimeFrameUnit.Minute),
}

# Number of symbols per Alpaca bars request for equity batch fetches.
# Alpaca accepts up to ~1000; 50 is a safe, fast batch size.
EQUITY_BATCH_SIZE = 50

# Crypto batch size is kept small because intraday responses can be very large.
# 5 symbols × ~2,880 15-min bars (30 days) ≈ 14,400 rows — safely under the limit.
CRYPTO_BATCH_SIZE = 5


# ── Primitives ────────────────────────────────────────────────────────────────

def build_timeframe(interval: str) -> TimeFrame:
    """Map an interval string ('1d', '1h', '15m', …) to an Alpaca TimeFrame."""
    return _INTERVAL_TO_TF.get(interval, TimeFrame.Day)


def build_timeframe_from_key(alpaca_tf_key: str) -> TimeFrame:
    """Map an TIMEFRAME_CONFIG alpaca_tf key ('Day', 'Hour', 'Minute15') to a TimeFrame."""
    return _ALPACA_TF_KEY_MAP.get(alpaca_tf_key, TimeFrame.Day)


def build_date_range(period: str, extra_days: int = 0) -> tuple[datetime, datetime]:
    """
    Return a (start, end) pair of timezone-aware UTC datetimes.

    Args:
        period:     Lookback period string ('1y', '3mo', etc.)
        extra_days: Additional days to prepend (e.g. indicator warm-up buffer).
    """
    days  = _PERIOD_DAYS.get(period, 365) + extra_days
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start, end


def _normalize(df: pd.DataFrame, symbol: str | None = None) -> pd.DataFrame:
    """Drop MultiIndex symbol level and lowercase column names."""
    if isinstance(df.index, pd.MultiIndex):
        if symbol is not None:
            try:
                df = df.xs(symbol, level='symbol')
            except KeyError:
                df = df.droplevel(0)
        else:
            df = df.droplevel(0)
    df.columns = [c.lower() for c in df.columns]
    return df


# ── Equity fetchers ───────────────────────────────────────────────────────────

def fetch_equity_bars(
    client,
    symbol: str,
    period: str,
    interval: str,
    extra_days: int = 0,
) -> pd.DataFrame:
    """
    Fetch bars for a single equity symbol.

    Returns an empty DataFrame on error or if no data is available.
    """
    tf = build_timeframe(interval)
    start, end = build_date_range(period, extra_days)
    try:
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
            end=end,
            limit=10000,
            feed=DataFeed.IEX,
        )
        df = client.get_stock_bars(req).df
        if df is None or df.empty:
            logger.warning(f"bar_fetcher: no equity data for {symbol}")
            return pd.DataFrame()
        return _normalize(df, symbol)
    except Exception as e:
        logger.error(f"bar_fetcher: equity fetch failed for {symbol}: {e}")
        return pd.DataFrame()


def fetch_equity_bars_batch(
    client,
    symbols: list[str],
    period: str,
    interval: str,
    extra_days: int = 0,
) -> dict[str, pd.DataFrame]:
    """
    Fetch bars for a batch of equity symbols in a single Alpaca request.

    Returns a dict mapping symbol → DataFrame (empty DataFrame if no data).

    Note: no limit= is set here. On multi-symbol requests, limit= is a *total*
    row cap across all symbols combined — setting it to 10,000 silently truncates
    results to the first ~15 symbols on 1h bars and ~12 on 15m bars.
    The SDK handles pagination automatically when no limit is specified.
    """
    tf = build_timeframe(interval)
    start, end = build_date_range(period, extra_days)
    result: dict[str, pd.DataFrame] = {sym: pd.DataFrame() for sym in symbols}
    try:
        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=tf,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )
        df = client.get_stock_bars(req).df
        if df is None or df.empty:
            return result
        df.columns = [c.lower() for c in df.columns]
        if isinstance(df.index, pd.MultiIndex):
            for sym in symbols:
                try:
                    result[sym] = df.xs(sym, level=0)
                except KeyError:
                    pass
        else:
            if symbols:
                result[symbols[0]] = df
    except Exception as e:
        logger.debug(f"bar_fetcher: equity batch fetch failed ({len(symbols)} symbols): {e}")
    return result


# ── Crypto fetchers ───────────────────────────────────────────────────────────

def fetch_crypto_bars(
    client,
    symbol: str,
    period: str,
    interval: str,
    extra_days: int = 0,
) -> pd.DataFrame:
    """
    Fetch bars for a single crypto symbol.

    Returns an empty DataFrame on error or if no data is available.
    """
    tf = build_timeframe(interval)
    start, end = build_date_range(period, extra_days)
    try:
        req = CryptoBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
            end=end,
            limit=10000,
        )
        df = client.get_crypto_bars(req).df
        if df is None or df.empty:
            logger.warning(f"bar_fetcher: no crypto data for {symbol}")
            return pd.DataFrame()
        return _normalize(df, symbol)
    except Exception as e:
        logger.error(f"bar_fetcher: crypto fetch failed for {symbol}: {e}")
        return pd.DataFrame()


def fetch_crypto_bars_batch(
    client,
    symbols: list[str],
    period: str,
    interval: str,
    extra_days: int = 0,
) -> dict[str, pd.DataFrame]:
    """
    Fetch bars for a batch of crypto symbols in a single Alpaca request.

    Returns a dict mapping symbol → DataFrame (empty DataFrame if no data).

    Note: no limit= is set here for the same reason as fetch_equity_bars_batch —
    limit= on multi-symbol requests is a total row cap that silently truncates.
    """
    tf = build_timeframe(interval)
    start, end = build_date_range(period, extra_days)
    result: dict[str, pd.DataFrame] = {sym: pd.DataFrame() for sym in symbols}
    try:
        req = CryptoBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=tf,
            start=start,
            end=end,
        )
        df = client.get_crypto_bars(req).df
        if df is None or df.empty:
            return result
        df.columns = [c.lower() for c in df.columns]
        if isinstance(df.index, pd.MultiIndex):
            for sym in symbols:
                try:
                    result[sym] = df.xs(sym, level=0)
                except KeyError:
                    pass
        else:
            if symbols:
                result[symbols[0]] = df
    except Exception as e:
        logger.debug(f"bar_fetcher: crypto batch fetch failed ({len(symbols)} symbols): {e}")
    return result
