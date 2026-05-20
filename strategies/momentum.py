"""
Relative Strength Volatility Expansion Strategy
Compression breakout with RS filter and dynamic exits.
"""

import logging
from typing import Any, Dict, Generator, Optional
import pandas as pd

logger = logging.getLogger(__name__)


class SignalHierarchy:
    """
    Four-tier signal hierarchy:
      Tier 1 — Market Regime   (trend direction + volatility environment)
      Tier 2 — Setup Quality   (compression + relative strength)
      Tier 3 — AI Confirmation (optional)
      Tier 4 — Risk Management (R:R, stop validity)
    """

    # Per-timeframe defaults tuned for each interval.
    TIMEFRAME_DEFAULTS = {
        'long': {
            'ema_short': 9, 'ema_medium': 21, 'ema_long': 50,
            'rsi_buy': 55, 'rsi_sell': 45,
            'rsi_neutral_min': 45, 'rsi_neutral_max': 55,
            'bb_width_pct_max': 35.0,   # compression threshold (percentile)
            'atr_pct_rank_max': 45.0,   # ATR percentile compression threshold
            'rs_min': 0.0,              # RS vs SPY minimum (pct pts)
            'rvol_min': 1.2,            # minimum relative volume on trigger
            'atr_multiplier': 2.0, 'min_rr_ratio': 2.0,
            'price_range_min': 1.0, 'atr_pct_max': 10.0, 'atr_pct_min': 0.5,
            'ai_confidence_min': 70,
        },
        'swing': {
            'ema_short': 9, 'ema_medium': 21, 'ema_long': 50,
            'rsi_buy': 60, 'rsi_sell': 40,
            'rsi_neutral_min': 42, 'rsi_neutral_max': 58,
            'bb_width_pct_max': 40.0,
            'atr_pct_rank_max': 50.0,
            'rs_min': 0.0,
            'rvol_min': 1.3,
            'atr_multiplier': 1.5, 'min_rr_ratio': 1.5,
            'price_range_min': 0.5, 'atr_pct_max': 8.0, 'atr_pct_min': 0.3,
            'ai_confidence_min': 65,
        },
        'short': {
            'ema_short': 9, 'ema_medium': 21, 'ema_long': 50,
            'rsi_buy': 65, 'rsi_sell': 35,
            'rsi_neutral_min': 40, 'rsi_neutral_max': 60,
            'bb_width_pct_max': 45.0,
            'atr_pct_rank_max': 55.0,
            'rs_min': 0.0,
            'rvol_min': 1.5,
            'atr_multiplier': 1.0, 'min_rr_ratio': 1.2,
            'price_range_min': 0.2, 'atr_pct_max': 6.0, 'atr_pct_min': 0.2,
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

        # AI Commentary
        if self.ai:
            commentary = self.ai.get_ai_commentary(data, symbol, audit)
            yield {'type': 'ai_commentary', 'timeframe': tf, 'text': commentary}

        # Final verdict
        regime_ok    = regime != 'NO_TRADE'
        ai_ok        = tier3['result'] in ('PASS', 'SKIPPED')
        signal_valid = entry_signal and regime_ok and ai_ok and rr_pass

        blocked_at = None
        if not regime_ok:
            blocked_at = 'Tier 1: Market Regime'
        elif not entry_signal:
            blocked_at = 'Tier 2: Setup & Trigger'
        elif tier3['result'] == 'FAIL':
            blocked_at = 'Tier 3: AI Confirmation'
        elif not rr_pass:
            blocked_at = 'Tier 4: Risk Management'

        if signal_valid:
            entry_signal['audit'] = audit
            logger.info(f"{symbol} [{tf}]: Valid {entry_signal['side']} signal generated")
        else:
            logger.info(f"{symbol} [{tf}]: No signal — blocked at {blocked_at}")

        yield {'type': 'done', 'timeframe': tf, 'symbol': symbol,
               'signals': [entry_signal] if signal_valid else [],
               'audit': audit, 'blocked_at': blocked_at}

    def generate_signal(self, data: pd.DataFrame, symbol: str) -> Optional[Dict[str, Any]]:
        """Run stream_signal and return the final 'done' event."""
        for event in self.stream_signal(data, symbol):
            if event['type'] == 'done':
                return event
        return None

    # ── Tier 1: Market Regime ─────────────────────────────────────────────────

    def check_market_regime(self, data: pd.DataFrame):
        """
        Determine allowed direction and volatility environment.
        Uses EMA alignment (9/21/50) and RSI to classify regime.
        Does NOT check for entry conditions here.
        """
        if data.empty:
            return 'NO_TRADE', ['No data available']

        latest  = data.iloc[-1]
        details = []
        price   = latest['close']

        ema9  = latest.get('ema_9',  price)
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

        # EMA alignment
        bullish_ema = price > ema9 > ema21 > ema50
        bearish_ema = price < ema9 < ema21 < ema50

        details.append(f"EMA stack: price={price:.2f} | 9={ema9:.2f} | 21={ema21:.2f} | 50={ema50:.2f}")
        details.append(f"RSI={rsi:.1f}, ROC(10)={roc:.2f}%")

        if bullish_ema and rsi > 50 and roc > 0:
            details.append("✅ BULLISH regime — EMA aligned + RSI + positive ROC")
            return 'BULLISH', details

        if bearish_ema and rsi < 50 and roc < 0:
            details.append("✅ BEARISH regime — EMA aligned + RSI + negative ROC")
            return 'BEARISH', details

        details.append("❌ Regime unclear — EMAs not fully aligned")
        return 'NO_TRADE', details

    # ── Tier 2: Setup & Trigger ───────────────────────────────────────────────

    def check_setup_and_trigger(self, data: pd.DataFrame, regime: str, symbol: str):
        """
        Two-stage check:
          Setup  — compression (BB width + ATR percentile) + relative strength
          Trigger — breakout candle + RVOL expansion

        Returns (signal_dict | None, details_list)
        """
        if regime == 'NO_TRADE' or len(data) < 3:
            return None, ['Skipped — regime is NO_TRADE']

        latest  = data.iloc[-1]
        prev    = data.iloc[-2]
        details = []

        # ── Setup: Compression ───────────────────────────────────────────────
        bb_pct  = latest.get('bb_width_pct', 50)
        atr_rnk = latest.get('atr_pct_rank', 50)

        bb_compressed  = bb_pct  <= self.params['bb_width_pct_max']
        atr_compressed = atr_rnk <= self.params['atr_pct_rank_max']

        details.append(f"BB width percentile: {bb_pct:.1f} (max={self.params['bb_width_pct_max']}) → {'✅ COMPRESSED' if bb_compressed else '❌ Expanded'}")
        details.append(f"ATR percentile rank: {atr_rnk:.1f} (max={self.params['atr_pct_rank_max']}) → {'✅ COMPRESSED' if atr_compressed else '❌ Expanded'}")

        # At least one compression signal required
        in_compression = bb_compressed or atr_compressed
        if not in_compression:
            details.append("❌ No compression detected — not a setup")
            return None, details

        # ── Setup: Relative Strength ─────────────────────────────────────────
        rs = latest.get('rs_vs_spy_20', float('nan'))
        import math
        rs_required = self.params.get('require_rs', True)
        if math.isnan(rs):
            rs_ok = not rs_required
            details.append(f"RS vs SPY: N/A (SPY data unavailable) → {'❌ FAIL (required)' if rs_required else '✅ Skipped (not required)'}")
        else:
            rs_ok = rs >= self.params['rs_min']
            details.append(f"RS vs SPY (20d): {rs:.2f}pp (min={self.params['rs_min']}) → {'✅ Outperforming' if rs_ok else '❌ Underperforming'}")

        if not rs_ok:
            details.append("❌ Relative strength too weak — not a setup")
            return None, details

        # ── Setup: Price above rising EMA ────────────────────────────────────
        ema9  = latest.get('ema_9',  latest['close'])
        ema21 = latest.get('ema_21', latest['close'])
        ema9_slope = latest.get('ema9_slope', 0)

        above_ema = latest['close'] > ema9 if regime == 'BULLISH' else latest['close'] < ema9
        ema_rising = ema9_slope > 0 if regime == 'BULLISH' else ema9_slope < 0

        details.append(f"Price {'above' if regime == 'BULLISH' else 'below'} EMA9: {latest['close']:.2f} vs {ema9:.2f} → {'✅' if above_ema else '❌'}")
        details.append(f"EMA9 slope: {ema9_slope:.3f}% → {'✅ Rising' if ema_rising else '❌ Flat/Declining'}")

        if not above_ema:
            details.append("❌ Price not on correct side of EMA9")
            return None, details

        # ── Trigger: Explicit breakout/breakdown level ────────────────────────
        # Require price to have broken above the prior 5-bar high (bull) or
        # below the prior 5-bar low (bear). This ensures we enter on the
        # expansion candle, not mid-compression.
        atr   = latest.get('atr_14', latest['close'] * 0.02)
        price = latest['close']

        if regime == 'BULLISH':
            breakout_level = data['high'].iloc[-6:-1].max()
            broke_out      = price > breakout_level
            details.append(f"Breakout level: {breakout_level:.2f} | price={price:.2f} → {'✅ BROKE OUT' if broke_out else '❌ Still inside range'}")
            if not broke_out:
                details.append("❌ No breakout of compression range — wait for expansion")
                return None, details
        else:
            breakdown_level = data['low'].iloc[-6:-1].min()
            broke_out       = price < breakdown_level
            details.append(f"Breakdown level: {breakdown_level:.2f} | price={price:.2f} → {'✅ BROKE DOWN' if broke_out else '❌ Still inside range'}")
            if not broke_out:
                details.append("❌ No breakdown of compression range — wait for expansion")
                return None, details

        # ── Trigger: Anti-chase extension filter ─────────────────────────────
        # If price has stretched too far from EMA9, we're chasing the move.
        extension_limit = self.params.get('extension_limit', 1.5)
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
        min_price        = self.params.get('min_price', 5.0)
        min_dollar_vol   = self.params.get('min_dollar_volume', 5_000_000)
        avg_dollar_vol   = (data['close'] * data['volume']).rolling(20).mean().iloc[-1]
        price_ok         = price >= min_price
        liquidity_ok     = avg_dollar_vol >= min_dollar_vol
        details.append(f"Price: ${price:.2f} (min=${min_price}) → {'✅' if price_ok else '❌'}")
        details.append(f"Avg dollar volume: ${avg_dollar_vol:,.0f} (min=${min_dollar_vol:,.0f}) → {'✅' if liquidity_ok else '❌'}")
        if not price_ok or not liquidity_ok:
            details.append("❌ Liquidity filter failed")
            return None, details

        # ── All conditions met — build signal ─────────────────────────────────
        if regime == 'BULLISH':
            # Tighter structural stop: use max() to pick the closer (higher) stop
            compression_low = data['low'].tail(5).min()
            structure_stop  = compression_low - atr * 0.25
            atr_stop        = price - atr * self.params['atr_multiplier']
            stop_price      = max(structure_stop, atr_stop)   # tighter = higher for buys
            target_price    = price + abs(price - stop_price) * self.params['min_rr_ratio'] * 1.5
            side            = 'buy'
            reason          = (f"Compression breakout above {breakout_level:.2f}: "
                               f"EMA aligned + BB/ATR compressed + RS positive + RVOL {rvol:.1f}x")
        else:
            compression_high = data['high'].tail(5).max()
            structure_stop   = compression_high + atr * 0.25
            atr_stop         = price + atr * self.params['atr_multiplier']
            stop_price       = min(structure_stop, atr_stop)  # tighter = lower for sells
            target_price     = price - abs(stop_price - price) * self.params['min_rr_ratio'] * 1.5
            side             = 'sell'
            reason           = (f"Compression breakdown below {breakdown_level:.2f}: "
                                f"EMA aligned + BB/ATR compressed + RS negative + RVOL {rvol:.1f}x")

        details.append(f"✅ All conditions met — {side.upper()} signal generated")
        return {
            'symbol': symbol, 'side': side,
            'entry_price':  round(price, 2),
            'stop_price':   round(stop_price, 2),
            'target_price': round(target_price, 2),
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

        rr = reward / risk
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
