"""
debug_signal.py — Timing test for the O(n) backtest engine.
Run: python3 debug_signal.py
"""
import logging
import time
logging.basicConfig(level=logging.WARNING)

from data.profile_store import get_active_profile
from alpaca.data.historical import StockHistoricalDataClient
from backtester.engine import BacktestEngine

active = get_active_profile()
data_client = StockHistoricalDataClient(api_key=active['api_key'], secret_key=active['secret_key'])
engine = BacktestEngine(data_client)

for sym, tf, period in [
    ('CLSK', 'long',  '1y'),
    ('NVDA', 'long',  '2y'),
    ('NVDA', 'swing', '1y'),
]:
    t0 = time.time()
    result = engine.run(symbol=sym, timeframe=tf, period=period)
    elapsed = time.time() - t0
    trades = result.get('signals_generated', 0)
    bars   = result.get('total_bars', 0)
    print(f'{sym} {tf} {period}: {bars} bars, {trades} signals — {elapsed:.2f}s')
    if result.get('trades'):
        for t in result['trades'][:3]:
            print(f"  {t['date'][:10]} {t['action'].upper()} entry={t['entry']} outcome={t['outcome']} r={t['r_multiple']}")
