"""Quick smoke-test for PositionReviewer — run with: venv/bin/python3 test_position_manager.py"""
from strategies.position_manager import PositionReviewer
from analysis.indicators import TechnicalIndicators
from dataclasses import asdict
import pandas as pd
import json

ORDERS = [
    {"id": "abc123", "type": "stop", "side": "sell", "stop_price": 142.0,
     "limit_price": None, "status": "held", "legs": []},
    {"id": "def456", "type": "limit", "side": "sell", "stop_price": None,
     "limit_price": 158.0, "status": "held", "legs": []},
]


def _make_bars(current_price, ema9):
    n = 60
    prices = [150 + i * 0.2 for i in range(n)]
    prices[-1] = current_price
    df = pd.DataFrame({
        "close":  prices,
        "high":   [p * 1.005 for p in prices],
        "low":    [p * 0.995 for p in prices],
        "open":   prices,
        "volume": [1_000_000] * n,
    })
    df = TechnicalIndicators.calculate_all(df)
    df.loc[df.index[-1], "ema_9"]      = ema9
    df.loc[df.index[-1], "ema_21"]     = 149.0
    df.loc[df.index[-1], "ema_50"]     = 145.0
    df.loc[df.index[-1], "rsi_14"]     = 58.0
    df.loc[df.index[-1], "rvol_20"]    = 1.5
    df.loc[df.index[-1], "atr_14"]     = 2.0
    df.loc[df.index[-1], "ema9_slope"] = 0.1
    df.loc[df.index[-1], "roc_10"]     = 1.5   # positive ROC required for BULLISH regime
    return df


def _position(current_price):
    return {
        "symbol": "AAPL", "qty": 10.0, "avg_entry_price": 150.0,
        "current_price": current_price, "market_value": current_price * 10,
        "unrealized_pl": (current_price - 150) * 10,
        "unrealized_plpc": (current_price - 150) / 150,
    }


reviewer = PositionReviewer(timeframe="long")

# Test 1: HOLD
print("Test 1: HOLD")
r = reviewer.review(_position(154.0), ORDERS, _make_bars(154.0, ema9=152.0))
print(f"  Verdict={r.verdict}  Regime={r.momentum.regime}  vs_EMA9={r.momentum.price_vs_ema9}")
assert r.verdict == "HOLD", f"Expected HOLD, got {r.verdict}"
print("  PASS")

# Test 2: EXIT (price below EMA9)
print("Test 2: EXIT")
r2 = reviewer.review(_position(154.0), ORDERS, _make_bars(154.0, ema9=165.0))
print(f"  Verdict={r2.verdict}  vs_EMA9={r2.momentum.price_vs_ema9}")
assert r2.verdict == "EXIT", f"Expected EXIT, got {r2.verdict}"
print("  PASS")

# Test 3: RAISE_TARGET (price at/above target)
print("Test 3: RAISE_TARGET")
r3 = reviewer.review(_position(162.0), ORDERS, _make_bars(162.0, ema9=159.0))
print(f"  Verdict={r3.verdict}  suggested_target={r3.suggested_target}  suggested_stop={r3.suggested_stop}")
assert r3.verdict == "RAISE_TARGET", f"Expected RAISE_TARGET, got {r3.verdict}"
assert r3.suggested_target is not None and r3.suggested_target > 158.0
print("  PASS")

# Test 4: JSON serialization
print("Test 4: JSON serialization")
s = json.dumps(asdict(r3), default=str)
assert len(s) > 100
print(f"  {len(s)} chars  PASS")

# Test 5: bot method signatures
print("Test 5: bot method signatures")
from bot import TradingBot
import inspect
assert "new_stop"   in inspect.signature(TradingBot.adjust_orders).parameters
assert "new_target" in inspect.signature(TradingBot.adjust_orders).parameters
assert "symbol"     in inspect.signature(TradingBot.close_position).parameters
print("  PASS")

print()
print("All 5 tests passed")
