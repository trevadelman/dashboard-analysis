"""
Relative Strength Volatility Expansion Strategy
Compression breakout with RS filter and dynamic exits.
"""

import logging
import math
from typing import Any, Dict, Generator, Optional
import pandas as pd

logger = logging.getLogger(__name__)


# ── Single source of truth for timeframe → data configuration ─────────────────
#
# Every component that needs to know what "long", "swing", or "short" means
# in terms of bar interval and lookback period imports this dict.
# Never define these mappings locally in bot.py, market_scanner.py, or
# backtester/engine.py — always read from here.
#
# Fields:
#   interval     — interval string used by bot.get_market_data() and market_scanner
#   alpaca_tf    — string key for the Alpaca TimeFrame lookup in backtester/engine.py
#   days         — lookback in calendar days for full analysis (dashboard, position review)
#   scan_days    — lookback in calendar days for batch scanning only.
#                  Smaller than days to minimise rows per Alpaca request and avoid
#                  SDK pagination.  Must be large enough for all indicator lookbacks:
#                    BB width percentile: 252 bars
#                    ATR50 / EMA50: 50 bars
#                  For hourly bars: 252 bars ≈ 18 trading days ≈ 25 calendar days.
#                  30 days gives a comfortable warm-up buffer.
#                  For 15-min bars: 252 bars ≈ 4.5 trading days ≈ 7 calendar days.
#                  14 days is more than sufficient.
#   label        — human-readable label for the UI
TIMEFRAME_CONFIG = {
    'long':  {'interval': '1d',  'alpaca_tf': 'Day',      'days': 365, 'scan_days': 365, 'label': 'Long (Daily)'},
    'swing': {'interval': '1h',  'alpaca_tf': 'Hour',     'days': 90,  'scan_days': 30,  'label': 'Swing (Hourly)'},
    'short': {'interval': '15m', 'alpaca_tf': 'Minute15', 'days': 30,  'scan_days': 14,  'label': 'Short (15-min)'},
}


class SignalHierarchy:
    """
    Four-tier signal hierarchy:
      Tier 1 — Market Regime   (loose trend direction + volatility guard)
      Tier 2 — Setup Quality   (compression + relative strength + trigger)
      Tier 3 — AI Confirmation (optional)
      Tier 4 — Risk Management (R:R, stop validity)

    Design principles:
    - Tier 1 is a MACRO filter — it should reject bad environments, not identify entries.
      EMA9 is a fast signal and belongs in Tier 2 (trigger), not Tier 1 (regime).
    - Tier 2 is where alpha lives — compression + RS + breakout confirmation.
    - Compression is defined by BB width percentile alone (primary squeeze indicator).
      ATR is used in Tier 1 as a volatility guard, not as a second compression gate.
    - RS vs SPY is required. If SPY data is unavailable, signal generation fails closed
      rather than silently degrading the edge.
    - Local ATR contraction (short-window ratio) supplements BB compression in Tier 2
      without the long-memory distortion of a 252-bar ATR percentile rank.
    """

    # Per-timeframe defaults tuned for each interval.
    TIMEFRAME_DEFAULTS = {
        'long': {
            # ── Tier 1 ──────────────────────────────────────────────────────────
            # Regime uses ema21/ema50 only — ema9 is a trigger signal, not a regime gate.
            # ROC is excluded: EMA21/50 alignment + RSI is sufficient for regime detection.
            # A ROC > -1.0 threshold was so loose it passed on virtually every setup,
            # adding no filtering value while creating a maintenance hazard.
            'ema_medium': 21, 'ema_long': 50,
            'rsi_regime_min': 45,       # RSI floor for BULLISH regime (daily bars are slower)
            'atr_pct_max': 10.0,        # ATR as % of price — volatility guard
            # ── Tier 2 ──────────────────────────────────────────────────────────
            'bb_width_pct_max': 50.0,   # BB width in bottom 50th percentile = compressed
            # Local ATR contraction: atr_14 / atr_50 ratio. < 0.85 = contracting.
            'atr_contraction_max': 0.85,
            'rs_min': 0.0,              # RS vs SPY minimum (pct pts). Fail closed if NaN.
            'rvol_min': 1.1,            # minimum RVOL on trigger (daily bars are slower)
            'rsi_buy': 55,              # RSI trigger threshold (bull)
            'rsi_sell': 45,             # RSI trigger threshold (bear)
            'extension_limit': 1.5,     # max distance from EMA9 in ATR units (anti-chase)
            # compression_lookback: bars used to define the compression zone for stop
            # placement and breakout level. Daily = 1 trading week; hourly = ~2 days;
            # 15-min = ~5 hours. Must be wide enough to capture the full compression zone.
            'compression_lookback': 5,
            # ── Tier 4 ──────────────────────────────────────────────────────────
            'min_rr_ratio': 2.0,
            # ── Misc ────────────────────────────────────────────────────────────
            'atr_multiplier': 2.0,
            'price_range_min': 1.0, 'atr_pct_min': 0.5,
            'min_price': 1.0, 'min_dollar_volume': 5_000_000,
            'ai_confidence_min': 70,
        },
        'swing': {
            'ema_medium': 21, 'ema_long': 50,
            'rsi_regime_min': 48,
            'atr_pct_max': 8.0,
            'bb_width_pct_max': 50.0,
            'atr_contraction_max': 0.85,
            'rs_min': 0.0,
            'rvol_min': 1.3,
            'rsi_buy': 58,
            'rsi_sell': 42,
            'extension_limit': 1.5,
            'compression_lookback': 15,
            'min_rr_ratio': 1.5,
            'atr_multiplier': 1.5,
            'price_range_min': 0.5, 'atr_pct_min': 0.3,
            'min_price': 1.0, 'min_dollar_volume': 5_000_000,
            'ai_confidence_min': 65,
        },
        'short': {
            'ema_medium': 21, 'ema_long': 50,
            'rsi_regime_min': 50,
            'atr_pct_max': 6.0,
            'bb_width_pct_max': 55.0,
            'atr_contraction_max': 0.90,
            'rs_min': 0.0,
            'rvol_min': 1.5,
            'rsi_buy': 60,
            'rsi_sell': 40,
            'extension_limit': 1.5,
            'compression_lookback': 20,
            'min_rr_ratio': 1.2,
            'atr_multiplier': 1.0,
            'price_range_min': 0.2, 'atr_pct_min': 0.2,
            'min_price': 1.0, 'min_dollar_volume': 5_000_000,
            'ai_confidence_min': 60,
        },
    }

    def __init__(self, ai_generator=None, params=None, timeframe='long'):
        """
        Args:
            ai_generator: Optional AI strategy generator for confirmation
            params: Optional dict of overrides applied on top of timeframe defaults
            timeframe: 'long' | 'swing' | 'short'
        """
        self.ai        = ai_generator
        self.timeframe = timeframe
        self.params    = dict(self.TIMEFRAME_DEFAULTS.get(timeframe, self.TIMEFRAME_DEFAULTS['long']))
        if params:
            self.params.update(params)

    # ── Public API ────────────────────────────────────────────────────────────

    def stream_signal(self, data: pd.DataFrame, symbol: str) -> Generator[Dict[str, Any], None, None]:
        """
        Run all four tiers unconditionally, yielding each result as it completes.
        Every event carries a 'timeframe' key so the UI can route it correctly.
        Final yield has type='done'.
        """
        # Store symbol so check_setup_and_trigger can read it for the BTC RS bypass.
        self._symbol = symbol

        audit        = []
        entry_signal = None
        tf           = self.timeframe

        # TIER 1 — Market Regime
        regime, regime_details = self.check_market_regime(data)
        tier1 = {'type': 'tier', 'timeframe': tf, 'tier': 1, 'name': 'Market Regime',
                 'result': regime, 'details': regime_details}
        audit.append(tier1)
        yield tier1

        # TIER 2 — Setup Quality (compression + RS + trigger)
        entry_signal, entry_details = self.check_setup_and_trigger(data, regime, symbol)
        tier2 = {'type': 'tier', 'timeframe': tf, 'tier': 2, 'name': 'Setup & Trigger',
                 'result': 'PASS' if entry_signal else 'FAIL', 'details': entry_details}
        audit.append(tier2)
        yield tier2

        # TIER 3 — AI Confirmation
        if self.ai:
            ai_conf = self.get_ai_confirmation(data, entry_signal, symbol)
            tier3 = {'type': 'tier', 'timeframe': tf, 'tier': 3, 'name': 'AI Confirmation',
                     'result': 'PASS' if ai_conf['approved'] else 'FAIL',
                     'details': ai_conf['reason'],
                     'confidence': ai_conf.get('confidence', 0)}
            if entry_signal and ai_conf['approved']:
                entry_signal['ai_confidence'] = ai_conf['confidence']
                entry_signal['ai_reasoning']  = ai_conf['reason']
        else:
            tier3 = {'type': 'tier', 'timeframe': tf, 'tier': 3, 'name': 'AI Confirmation',
                     'result': 'SKIPPED', 'details': 'AI not enabled'}
        audit.append(tier3)
        yield tier3

        # TIER 4 — Risk Management
        if entry_signal:
            rr_pass, rr_details = self.passes_risk_checks(entry_signal)
        else:
            rr_pass, rr_details = False, ['Skipped — no entry signal to evaluate']
        tier4 = {'type': 'tier', 'timeframe': tf, 'tier': 4, 'name': 'Risk Management',
                 'result': 'PASS' if rr_pass else 'FAIL', 'details': rr_details}
        audit.append(tier4)
        yield tier4

        # Final verdict — AI confirmation is informational only, never gates the signal
        regime_ok    = regime != 'NO_TRADE'
        signal_valid = entry_signal and regime_ok and rr_pass

        blocked_at = None
        if not regime_ok:
            blocked_at = 'Tier 1: Market Regime'
        elif not entry_signal:
            blocked_at = 'Tier 2: Setup & Trigger'
        elif not rr_pass:
            blocked_at = 'Tier 4: Risk Management'

        if signal_valid:
            entry_signal['audit'] = audit
            logger.info(f"{symbol} [{tf}]: Valid {entry_signal['side']} signal generated")
        else:
            logger.info(f"{symbol} [{tf}]: No signal — blocked at {blocked_at}")

        # Include the current price and regime in the done event so the
        # dashboard can snapshot them for the watchlist without an extra fetch.
        current_price = float(data.iloc[-1]['close']) if not data.empty else None
        yield {'type': 'done', 'timeframe': tf, 'symbol': symbol,
               'signals': [entry_signal] if signal_valid else [],
               'audit': audit, 'blocked_at': blocked_at,
               'regime': regime, 'price': current_price}

    def generate_signal(self, data: pd.DataFrame, symbol: str) -> Optional[Dict[str, Any]]:
        """Run stream_signal and return the final 'done' event."""
        for event in self.stream_signal(data, symbol):
            if event['type'] == 'done':
                return event
        return None

    # ── Tier 1: Market Regime ─────────────────────────────────────────────────

    def check_market_regime(self, data: pd.DataFrame):
        """
        Loose macro filter — reject bad environments, not identify entries.

        Uses ema21/ema50 alignment (NOT ema9 — that is a trigger signal).
        EMA9 is fast and frequently crosses during healthy consolidation phases,
        which is exactly when compression setups form. Including it here would
        block the best setups.

        Returns: ('BULLISH' | 'BEARISH' | 'NO_TRADE', details_list)
        """
        if data.empty:
            return 'NO_TRADE', ['No data available']

        latest  = data.iloc[-1]
        details = []
        price   = latest['close']

        ema21 = latest.get('ema_21', price)
        ema50 = latest.get('ema_50', price)
        rsi   = latest.get('rsi_14', 50)
        roc   = latest.get('roc_10', 0)

        atr     = latest.get('atr_14', 0)
        atr_pct = (atr / price) * 100 if price > 0 else 0

        # Volatility guard — don't trade into extreme expansion
        too_volatile = atr_pct > self.params['atr_pct_max']
        details.append(f"ATR%={atr_pct:.2f}% (max={self.params['atr_pct_max']}%) → {'❌ TOO HIGH' if too_volatile else '✅ OK'}")
        if too_volatile:
            return 'NO_TRADE', details

        # Macro trend: price > ema21 > ema50 (bull) or price < ema21 < ema50 (bear)
        bullish_ema = price > ema21 > ema50
        bearish_ema = price < ema21 < ema50

        details.append(f"EMA stack: price={price:.2f} | 21={ema21:.2f} | 50={ema50:.2f}")
        details.append(f"RSI={rsi:.1f} (min={self.params['rsi_regime_min']})")

        if bullish_ema and rsi >= self.params['rsi_regime_min']:
            details.append("✅ BULLISH regime — EMA21/50 aligned + RSI confirming")
            return 'BULLISH', details

        if bearish_ema and rsi <= (100 - self.params['rsi_regime_min']):
            details.append("✅ BEARISH regime — EMA21/50 aligned + RSI confirming")
            return 'BEARISH', details

        details.append("❌ Regime unclear — EMA21/50 not aligned or RSI not confirming")
        return 'NO_TRADE', details

    # ── Tier 2: Setup & Trigger ───────────────────────────────────────────────

    def check_setup_and_trigger(self, data: pd.DataFrame, regime: str, symbol: str):
        """
        Two-stage check:
          Setup   — BB compression + local ATR contraction + RS vs SPY
          Trigger — EMA9 reclaim + breakout above prior high + RVOL + RSI

        Compression is defined by BB width percentile (primary squeeze indicator).
        Local ATR contraction (atr_14 / atr_50 ratio) supplements BB without the
        long-memory distortion of a 252-bar ATR percentile rank.

        RS vs SPY is required. If SPY data is unavailable (NaN), signal fails closed.

        Returns (signal_dict | None, details_list)
        """
        if regime == 'NO_TRADE' or len(data) < 3:
            return None, ['Skipped — regime is NO_TRADE']

        latest  = data.iloc[-1]
        details = []

        # ── Setup: BB Compression ────────────────────────────────────────────
        bb_pct       = latest.get('bb_width_pct', 50)
        bb_compressed = bb_pct <= self.params['bb_width_pct_max']
        details.append(f"BB width percentile: {bb_pct:.1f} (max={self.params['bb_width_pct_max']}) → {'✅ COMPRESSED' if bb_compressed else '❌ Expanded'}")

        if not bb_compressed:
            details.append("❌ No BB compression — not a setup")
            return None, details

        # ── Setup: Local ATR Contraction ─────────────────────────────────────
        # atr_14 / atr_50 < threshold means recent volatility is contracting
        # relative to the medium-term baseline. This avoids the long-memory
        # distortion of a 252-bar ATR percentile rank.
        atr_14 = latest.get('atr_14', None)
        atr_50 = data['atr_14'].rolling(50).mean().iloc[-1] if 'atr_14' in data.columns else None

        if atr_14 is not None and atr_50 is not None and atr_50 > 0:
            atr_ratio = atr_14 / atr_50
            atr_contracting = atr_ratio <= self.params['atr_contraction_max']
            details.append(f"ATR contraction ratio: {atr_ratio:.2f} (max={self.params['atr_contraction_max']}) → {'✅ CONTRACTING' if atr_contracting else '⚠️ Elevated'}")
            # ATR contraction is informational — it supplements BB but does not gate.
            # The primary compression gate is BB width percentile.
        else:
            details.append("ATR contraction: N/A (insufficient data)")

        # ── Setup: Relative Strength vs benchmark ────────────────────────────
        # For equities: RS vs SPY.  For crypto alts: RS vs BTC/USD.
        # BTC/USD itself has no benchmark — rs_vs_spy_20 will be NaN and we
        # skip the gate rather than failing closed (BTC IS the benchmark).
        # The column is always named rs_vs_spy_20 for backward compatibility.
        rs = latest.get('rs_vs_spy_20', float('nan'))
        if math.isnan(rs):
            # Determine whether this is BTC (no benchmark) or a data gap.
            # We detect BTC by checking whether the symbol was passed in.
            # SignalHierarchy receives symbol in stream_signal/generate_signal
            # but not here — use the presence of a non-NaN rs column as the
            # proxy.  If rs is NaN and the symbol is BTC/USD, skip the gate.
            # For all other assets, fail closed to preserve edge.
            if getattr(self, '_symbol', '').upper() in ('BTC/USD', 'BTCUSD', 'BTC'):
                details.append("RS vs benchmark: N/A — BTC/USD is the benchmark → ✅ SKIPPED")
            else:
                details.append("RS vs benchmark: N/A — benchmark data unavailable → ❌ FAIL (failing closed to preserve edge)")
                return None, details
        else:
            rs_ok = rs >= self.params['rs_min']
            details.append(f"RS vs benchmark (20d): {rs:.2f}pp (min={self.params['rs_min']}) → {'✅ Outperforming' if rs_ok else '❌ Underperforming'}")
            if not rs_ok:
                details.append("❌ Relative strength too weak — not a setup")
                return None, details

        # ── Trigger: EMA9 reclaim ─────────────────────────────────────────────
        # Price must be on the correct side of EMA9 — this is the short-term
        # trigger signal, not a regime gate.
        ema9       = latest.get('ema_9', latest['close'])
        ema9_slope = latest.get('ema9_slope', 0)
        price      = latest['close']

        above_ema9 = price > ema9 if regime == 'BULLISH' else price < ema9
        details.append(f"Price {'above' if regime == 'BULLISH' else 'below'} EMA9: {price:.2f} vs {ema9:.2f} → {'✅' if above_ema9 else '❌'}")
        details.append(f"EMA9 slope: {ema9_slope:.3f}% → {'✅ Rising' if ema9_slope > 0 else '❌ Flat/Declining'}")

        if not above_ema9:
            details.append("❌ Price not on correct side of EMA9 — no trigger")
            return None, details

        # ── Trigger: Breakout above prior 15-bar high ─────────────────────────
        # 15 bars covers 3 trading weeks on daily, ~2 days on hourly, ~4 hours on 15m.
        # A 5-bar window was too reactive — it triggered on minor intraday noise
        # rather than a true range expansion out of the compression zone.
        atr = latest.get('atr_14', price * 0.02)

        if regime == 'BULLISH':
            breakout_level = data['high'].iloc[-16:-1].max()
            broke_out      = price > breakout_level
            details.append(f"Breakout level (15-bar high): {breakout_level:.2f} | price={price:.2f} → {'✅ BROKE OUT' if broke_out else '❌ Still inside range'}")
            if not broke_out:
                details.append("❌ No breakout of compression range — wait for expansion")
                return None, details
        else:
            breakdown_level = data['low'].iloc[-16:-1].min()
            broke_out       = price < breakdown_level
            details.append(f"Breakdown level (15-bar low): {breakdown_level:.2f} | price={price:.2f} → {'✅ BROKE DOWN' if broke_out else '❌ Still inside range'}")
            if not broke_out:
                details.append("❌ No breakdown of compression range — wait for expansion")
                return None, details

        # ── Trigger: Anti-chase extension filter ─────────────────────────────
        extension_limit = self.params['extension_limit']
        extension       = abs(price - ema9) / atr if atr > 0 else 0
        extension_ok    = extension <= extension_limit
        details.append(f"Extension from EMA9: {extension:.2f}x ATR (max={extension_limit}) → {'✅ OK' if extension_ok else '❌ CHASING'}")
        if not extension_ok:
            details.append("❌ Price too extended from EMA9 — do not chase")
            return None, details

        # ── Trigger: RVOL expansion ───────────────────────────────────────────
        rvol    = latest.get('rvol_20', 1.0)
        rvol_ok = rvol >= self.params['rvol_min']
        details.append(f"RVOL: {rvol:.2f}x (min={self.params['rvol_min']}) → {'✅ Elevated' if rvol_ok else '❌ Low'}")
        if not rvol_ok:
            details.append("❌ Volume not confirming — no trigger")
            return None, details

        # ── Trigger: RSI momentum ─────────────────────────────────────────────
        rsi    = latest.get('rsi_14', 50)
        rsi_ok = (rsi > self.params['rsi_buy']) if regime == 'BULLISH' else (rsi < self.params['rsi_sell'])
        details.append(f"RSI: {rsi:.1f} {'>' if regime == 'BULLISH' else '<'} {self.params['rsi_buy'] if regime == 'BULLISH' else self.params['rsi_sell']} → {'✅' if rsi_ok else '❌'}")
        if not rsi_ok:
            details.append("❌ RSI not confirming momentum")
            return None, details

        # ── Liquidity filter ─────────────────────────────────────────────────
        min_price      = self.params['min_price']
        min_dollar_vol = self.params['min_dollar_volume']
        avg_dollar_vol = (data['close'] * data['volume']).rolling(20).mean().iloc[-1]
        price_ok       = price >= min_price
        liquidity_ok   = avg_dollar_vol >= min_dollar_vol
        details.append(f"Price: ${price:.2f} (min=${min_price}) → {'✅' if price_ok else '❌'}")
        details.append(f"Avg dollar volume: ${avg_dollar_vol:,.0f} (min=${min_dollar_vol:,.0f}) → {'✅' if liquidity_ok else '❌'}")
        if not price_ok or not liquidity_ok:
            details.append("❌ Liquidity filter failed")
            return None, details

        # ── All conditions met — build signal ─────────────────────────────────
        # Entry: limit at the breakout level + small ATR buffer.
        # Stop: structural invalidation (compression low/high ± 0.25×ATR).
        #   Uses compression_lookback bars (timeframe-specific) to define the
        #   compression zone. Daily=5 bars, hourly=15 bars, 15-min=20 bars.
        #   A fixed 5-bar window was too narrow for intraday timeframes — the
        #   compression zone spans many more bars, so the stop ended up inside
        #   the zone rather than below it, causing premature exits.
        # Target: 2R from the limit entry.
        rs_str = f"{rs:.1f}pp" if not math.isnan(rs) else "N/A (benchmark)"
        lookback = self.params['compression_lookback']

        if regime == 'BULLISH':
            limit_entry     = round(breakout_level + atr * 0.10, 2)
            compression_low = data['low'].tail(lookback).min()
            stop_price      = round(compression_low - atr * 0.25, 2)
            risk            = abs(limit_entry - stop_price)
            target_price    = round(limit_entry + risk * 2.0, 2)
            side            = 'buy'
            reason          = (f"Compression breakout above {breakout_level:.2f}: "
                               f"EMA21/50 aligned + BB compressed + RS {rs_str} + RVOL {rvol:.1f}x")
        else:
            limit_entry      = round(breakdown_level - atr * 0.10, 2)
            compression_high = data['high'].tail(lookback).max()
            stop_price       = round(compression_high + atr * 0.25, 2)
            risk             = abs(stop_price - limit_entry)
            target_price     = round(limit_entry - risk * 2.0, 2)
            side             = 'sell'
            reason           = (f"Compression breakdown below {breakdown_level:.2f}: "
                                f"EMA21/50 aligned + BB compressed + RS {rs_str} + RVOL {rvol:.1f}x")

        details.append(f"✅ All conditions met — {side.upper()} signal generated")
        details.append(f"Limit entry: ${limit_entry} | Stop: ${stop_price} | Target: ${target_price} | R:R 2:1")
        return {
            'symbol':       symbol,
            'side':         side,
            'entry_type':   'limit',
            'entry_price':  limit_entry,
            'stop_price':   stop_price,
            'target_price': target_price,
            'rr':           2.0,
            'confidence':   'deterministic',
            'reason':       reason,
            'regime':       regime,
        }, details

    # ── Tier 3: AI Confirmation ───────────────────────────────────────────────

    def get_ai_confirmation(self, data: pd.DataFrame, signal: Optional[Dict[str, Any]], symbol: str) -> Dict[str, Any]:
        """AI confirms or rejects the deterministic signal. Cannot override it."""
        if not self.ai:
            return {'approved': True, 'confidence': 100, 'reason': 'No AI available'}
        if not signal:
            return {'approved': False, 'confidence': 0, 'reason': 'No entry signal to confirm'}

        try:
            ai_analysis = self.ai.generate_strategy(data, symbol)
            ai_signals  = ai_analysis.get('signals', [])

            if not ai_signals:
                return {'approved': False, 'confidence': 0, 'reason': 'AI produced no signals'}

            ai_side       = ai_signals[0].get('side', 'hold')
            ai_confidence = ai_analysis.get('confidence', 0)
            agrees        = (signal['side'] == 'buy' and ai_side == 'buy') or \
                            (signal['side'] == 'sell' and ai_side == 'sell')

            if not agrees:
                return {'approved': False, 'confidence': 0,
                        'reason': f"AI suggests {ai_side}, conflicts with {signal['side']}"}

            if ai_confidence < self.params['ai_confidence_min']:
                return {'approved': False, 'confidence': ai_confidence,
                        'reason': f"AI confidence too low: {ai_confidence}%"}

            return {'approved': True, 'confidence': ai_confidence,
                    'reason': ai_analysis.get('reasoning', 'AI confirms signal')}

        except Exception as e:
            logger.error(f"Error getting AI confirmation: {e}")
            return {'approved': False, 'confidence': 0,
                    'reason': f'AI error — failing safe: {e}'}

    # ── Tier 4: Risk Management ───────────────────────────────────────────────

    def passes_risk_checks(self, signal: Dict[str, Any]):
        """Final R:R and price validity checks."""
        entry   = signal['entry_price']
        stop    = signal['stop_price']
        target  = signal['target_price']
        details = []

        risk   = abs(entry - stop)
        reward = abs(target - entry)

        if risk == 0:
            details.append("❌ Risk is zero — invalid signal")
            return False, details

        rr = round(reward / risk, 2)
        details.append(f"R:R = {rr:.2f} (min={self.params['min_rr_ratio']}) → {'✅' if rr >= self.params['min_rr_ratio'] else '❌'}")
        details.append(f"Entry: ${entry:.2f} | Stop: ${stop:.2f} | Target: ${target:.2f}")

        if rr < self.params['min_rr_ratio']:
            details.append("❌ R:R too low")
            return False, details

        if entry <= 0 or stop <= 0 or target <= 0:
            details.append("❌ Invalid prices")
            return False, details

        if signal['side'] == 'buy' and stop >= entry:
            details.append("❌ Buy stop above entry")
            return False, details

        if signal['side'] == 'sell' and stop <= entry:
            details.append("❌ Sell stop below entry")
            return False, details

        details.append("✅ Risk management checks passed")
        return True, details
