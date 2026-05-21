"""
Position Manager
Reviews open positions against current market conditions and recommends
stop/target adjustments or exits.

Designed as a pure function so the same logic can be called from:
  - The manual review UI (positions page)
  - A future automated hourly loop
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from strategies.momentum import SignalHierarchy

logger = logging.getLogger(__name__)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class MomentumHealth:
    """Snapshot of the momentum indicators for an open position."""
    rsi: Optional[float]
    rsi_trend: str          # 'RISING' | 'FLAT' | 'FALLING'
    ema9_slope: Optional[float]
    ema9_slope_trend: str   # 'RISING' | 'FLAT' | 'FALLING'
    rvol: Optional[float]
    rvol_status: str        # 'ELEVATED' | 'NORMAL' | 'LOW'
    extension: Optional[float]   # distance from EMA9 in ATR units
    extension_status: str        # 'OK' | 'EXTENDED'
    price_vs_ema9: str      # 'ABOVE' | 'BELOW'
    regime: str             # 'BULLISH' | 'BEARISH' | 'NO_TRADE'


@dataclass
class PositionReview:
    """
    Full review result for a single open position.

    verdict values:
      HOLD          — thesis intact, no action needed
      TRAIL_STOP    — momentum stalling; raise stop to lock in profit
      RAISE_TARGET  — momentum accelerated; new target is higher
      PARTIAL_PROFIT — RSI diverging or RVOL fading; take partial profit
      EXIT          — regime flipped or price crossed back below EMA9; close now
    """
    symbol: str
    verdict: str
    reason: str
    momentum: MomentumHealth
    current_entry: Optional[float]
    current_stop: Optional[float]
    current_target: Optional[float]
    suggested_stop: Optional[float]
    suggested_target: Optional[float]
    unrealized_pl: Optional[float]
    unrealized_plpc: Optional[float]
    details: List[str] = field(default_factory=list)


# ── Reviewer ──────────────────────────────────────────────────────────────────

class PositionReviewer:
    """
    Reviews an open position against current market conditions.

    Usage:
        reviewer = PositionReviewer(timeframe='long')
        review   = reviewer.review(position, orders, bars)

    All inputs are plain dicts/DataFrames so this can be called from
    both the web handler and a future automated loop without modification.
    """

    def __init__(self, timeframe: str = 'long'):
        self.timeframe = timeframe
        self._strategy = SignalHierarchy(ai_generator=None, timeframe=timeframe)

    # ── Public API ────────────────────────────────────────────────────────────

    def review(
        self,
        position: Dict[str, Any],
        orders: List[Dict[str, Any]],
        bars: pd.DataFrame,
    ) -> PositionReview:
        """
        Run the full position review.

        Args:
            position: dict from bot.get_positions() — has symbol, qty,
                      avg_entry_price, current_price, unrealized_pl, unrealized_plpc
            orders:   list from bot.get_orders() filtered to this symbol —
                      includes bracket legs (stop, limit/take_profit)
            bars:     DataFrame from bot.get_market_data() with indicators

        Returns:
            PositionReview with verdict, reason, suggested adjustments
        """
        symbol = position.get('symbol', '')
        details = []

        # Extract current price levels from the open bracket orders
        current_entry  = position.get('avg_entry_price')
        current_stop   = self._find_stop(orders)
        current_target = self._find_target(orders)
        current_price  = position.get('current_price')
        side           = self._infer_side(position, orders)

        details.append(f"Side: {side.upper()} | Entry: ${current_entry} | Stop: ${current_stop} | Target: ${current_target}")
        details.append(f"Current price: ${current_price} | Unrealized P&L: ${position.get('unrealized_pl', 0):.2f} ({position.get('unrealized_plpc', 0)*100:.2f}%)")

        if bars.empty or len(bars) < 20:
            return PositionReview(
                symbol=symbol, verdict='HOLD',
                reason='Insufficient bar data to evaluate — holding',
                momentum=self._empty_health(),
                current_entry=current_entry, current_stop=current_stop,
                current_target=current_target,
                suggested_stop=None, suggested_target=None,
                unrealized_pl=position.get('unrealized_pl'),
                unrealized_plpc=position.get('unrealized_plpc'),
                details=details,
            )

        # Sync the latest bar's close to the position's current_price so the
        # momentum health checks use the live price, not the last bar's close.
        if current_price is not None:
            bars = bars.copy()
            bars.loc[bars.index[-1], 'close'] = current_price

        latest = bars.iloc[-1]
        momentum = self._compute_momentum_health(bars, latest, side)
        details.append(f"Regime: {momentum.regime} | RSI: {momentum.rsi} ({momentum.rsi_trend}) | EMA9 slope: {momentum.ema9_slope} ({momentum.ema9_slope_trend})")
        details.append(f"RVOL: {momentum.rvol} ({momentum.rvol_status}) | Extension: {momentum.extension}x ATR ({momentum.extension_status}) | Price vs EMA9: {momentum.price_vs_ema9}")

        # ── Check 1: Regime flipped → EXIT ────────────────────────────────────
        if momentum.regime == 'NO_TRADE':
            details.append("❌ Regime flipped to NO_TRADE — original thesis invalidated")
            return self._make_review(
                symbol, 'EXIT',
                'Market regime has flipped to NO_TRADE — the macro thesis is invalidated. Exit to preserve capital.',
                momentum, position, current_entry, current_stop, current_target,
                suggested_stop=None, suggested_target=None, details=details,
            )

        # ── Check 2: Price crossed back below EMA9 → EXIT ────────────────────
        if momentum.price_vs_ema9 == 'BELOW' and side == 'buy':
            details.append("❌ Price crossed back below EMA9 — trigger invalidated")
            return self._make_review(
                symbol, 'EXIT',
                'Price has crossed back below EMA9. The breakout trigger is invalidated — exit the position.',
                momentum, position, current_entry, current_stop, current_target,
                suggested_stop=None, suggested_target=None, details=details,
            )
        if momentum.price_vs_ema9 == 'ABOVE' and side == 'sell':
            details.append("❌ Price crossed back above EMA9 — trigger invalidated (short)")
            return self._make_review(
                symbol, 'EXIT',
                'Price has crossed back above EMA9. The breakdown trigger is invalidated — cover the short.',
                momentum, position, current_entry, current_stop, current_target,
                suggested_stop=None, suggested_target=None, details=details,
            )

        # ── Check 3: Price has exceeded the current target → RAISE_TARGET ─────
        if current_target and current_price:
            if side == 'buy' and current_price >= current_target * 0.98:
                new_target, new_stop = self._compute_raised_target(bars, latest, current_entry, current_stop, side)
                if new_target and new_target > current_target:
                    details.append(f"✅ Price at/above target — new target: ${new_target} | trail stop to: ${new_stop}")
                    return self._make_review(
                        symbol, 'RAISE_TARGET',
                        f'Price has reached the original target of ${current_target:.2f} with momentum still intact. '
                        f'The re-run signal suggests a new target of ${new_target:.2f}. '
                        f'Raise the take-profit and trail the stop to ${new_stop:.2f} to lock in profit.',
                        momentum, position, current_entry, current_stop, current_target,
                        suggested_stop=new_stop, suggested_target=new_target, details=details,
                    )
            elif side == 'sell' and current_price <= current_target * 1.02:
                new_target, new_stop = self._compute_raised_target(bars, latest, current_entry, current_stop, side)
                if new_target and new_target < current_target:
                    details.append(f"✅ Price at/below target (short) — new target: ${new_target} | trail stop to: ${new_stop}")
                    return self._make_review(
                        symbol, 'RAISE_TARGET',
                        f'Price has reached the original target of ${current_target:.2f} with momentum still intact. '
                        f'The re-run signal suggests a new target of ${new_target:.2f}. '
                        f'Lower the take-profit and trail the stop to ${new_stop:.2f} to lock in profit.',
                        momentum, position, current_entry, current_stop, current_target,
                        suggested_stop=new_stop, suggested_target=new_target, details=details,
                    )

        # ── Check 4: RSI divergence → PARTIAL_PROFIT ─────────────────────────
        # Price making new highs but RSI is falling = distribution signal
        rsi_diverging = self._check_rsi_divergence(bars, side)
        if rsi_diverging:
            new_stop = self._compute_trailing_stop(bars, latest, current_entry, current_stop, side)
            details.append(f"⚠️ RSI divergence detected — trail stop to: ${new_stop}")
            if self._already_applied(new_stop, current_target, current_stop, current_target):
                details.append("✅ Stop already at optimal level — no adjustment needed")
                return self._make_review(
                    symbol, 'HOLD',
                    'RSI divergence detected but the stop is already at the optimal level. '
                    'No adjustment needed — hold the position.',
                    momentum, position, current_entry, current_stop, current_target,
                    suggested_stop=None, suggested_target=None, details=details,
                )
            return self._make_review(
                symbol, 'PARTIAL_PROFIT',
                'RSI is diverging from price (price making new highs but RSI is declining). '
                'This is a distribution signal. Consider taking partial profit and trailing the stop '
                f'to ${new_stop:.2f} on the remainder.',
                momentum, position, current_entry, current_stop, current_target,
                suggested_stop=new_stop, suggested_target=current_target, details=details,
            )

        # ── Check 5: RVOL fading → TRAIL_STOP ────────────────────────────────
        if momentum.rvol_status == 'LOW':
            new_stop = self._compute_trailing_stop(bars, latest, current_entry, current_stop, side)
            details.append(f"⚠️ RVOL fading — trail stop to: ${new_stop}")
            if self._already_applied(new_stop, current_target, current_stop, current_target):
                details.append("✅ Stop already at optimal level — no adjustment needed")
                return self._make_review(
                    symbol, 'HOLD',
                    'Volume is fading but the stop is already at the optimal level. '
                    'No adjustment needed — hold the position.',
                    momentum, position, current_entry, current_stop, current_target,
                    suggested_stop=None, suggested_target=None, details=details,
                )
            in_profit = current_entry is not None and current_price is not None and (
                (side == 'buy'  and current_price > current_entry) or
                (side == 'sell' and current_price < current_entry)
            )
            stop_action = 'lock in profit' if in_profit else 'protect capital'
            return self._make_review(
                symbol, 'TRAIL_STOP',
                f'Volume is fading (RVOL {momentum.rvol:.2f}x). Momentum may be stalling. '
                f'Trail the stop to ${new_stop:.2f} to {stop_action} while giving the trade room to continue.',
                momentum, position, current_entry, current_stop, current_target,
                suggested_stop=new_stop, suggested_target=current_target, details=details,
            )

        # ── Check 6: EMA9 slope flattening → TRAIL_STOP ──────────────────────
        if momentum.ema9_slope_trend == 'FALLING' and momentum.rvol_status == 'NORMAL':
            new_stop = self._compute_trailing_stop(bars, latest, current_entry, current_stop, side)
            details.append(f"⚠️ EMA9 slope flattening — trail stop to: ${new_stop}")
            if self._already_applied(new_stop, current_target, current_stop, current_target):
                details.append("✅ Stop already at optimal level — no adjustment needed")
                return self._make_review(
                    symbol, 'HOLD',
                    'EMA9 slope is flattening but the stop is already at the optimal level. '
                    'No adjustment needed — hold the position.',
                    momentum, position, current_entry, current_stop, current_target,
                    suggested_stop=None, suggested_target=None, details=details,
                )
            in_profit = current_entry is not None and current_price is not None and (
                (side == 'buy'  and current_price > current_entry) or
                (side == 'sell' and current_price < current_entry)
            )
            stop_action = 'lock in profit' if in_profit else 'protect capital'
            return self._make_review(
                symbol, 'TRAIL_STOP',
                f'EMA9 slope is flattening, suggesting the short-term momentum is decelerating. '
                f'Trail the stop to ${new_stop:.2f} to {stop_action}.',
                momentum, position, current_entry, current_stop, current_target,
                suggested_stop=new_stop, suggested_target=current_target, details=details,
            )

        # ── All checks passed → HOLD ──────────────────────────────────────────
        details.append("✅ All momentum checks passed — thesis intact")
        return self._make_review(
            symbol, 'HOLD',
            'Momentum is intact. Regime is bullish, price is above EMA9, RVOL is elevated, '
            'and RSI is trending in the right direction. Hold the position with current levels.',
            momentum, position, current_entry, current_stop, current_target,
            suggested_stop=None, suggested_target=None, details=details,
        )

    # ── Momentum health ───────────────────────────────────────────────────────

    def _compute_momentum_health(
        self, bars: pd.DataFrame, latest: pd.Series, side: str
    ) -> MomentumHealth:
        """Compute all momentum health indicators from the latest bar."""
        price  = float(latest.get('close', 0))
        ema9   = float(latest.get('ema_9', price))
        atr    = float(latest.get('atr_14', price * 0.02))
        rsi    = latest.get('rsi_14')
        rvol   = latest.get('rvol_20', 1.0)
        slope  = latest.get('ema9_slope', 0)

        # RSI trend: compare last 3 bars
        rsi_trend = 'FLAT'
        if 'rsi_14' in bars.columns and len(bars) >= 4:
            rsi_vals = bars['rsi_14'].dropna().tail(4)
            if len(rsi_vals) >= 4:
                if rsi_vals.iloc[-1] > rsi_vals.iloc[-3]:
                    rsi_trend = 'RISING'
                elif rsi_vals.iloc[-1] < rsi_vals.iloc[-3]:
                    rsi_trend = 'FALLING'

        # EMA9 slope trend
        slope_trend = 'FLAT'
        if slope is not None:
            if float(slope) > 0.05:
                slope_trend = 'RISING'
            elif float(slope) < -0.05:
                slope_trend = 'FALLING'

        # RVOL status
        rvol_val = float(rvol) if rvol is not None else 1.0
        if rvol_val >= 1.3:
            rvol_status = 'ELEVATED'
        elif rvol_val >= 0.8:
            rvol_status = 'NORMAL'
        else:
            rvol_status = 'LOW'

        # Extension from EMA9
        extension = abs(price - ema9) / atr if atr > 0 else 0
        ext_limit = self._strategy.params.get('extension_limit', 1.5)
        extension_status = 'EXTENDED' if extension > ext_limit else 'OK'

        # Price vs EMA9
        price_vs_ema9 = 'ABOVE' if price > ema9 else 'BELOW'

        # Regime from Tier 1
        regime, _ = self._strategy.check_market_regime(bars)

        return MomentumHealth(
            rsi=round(float(rsi), 1) if rsi is not None else None,
            rsi_trend=rsi_trend,
            ema9_slope=round(float(slope), 3) if slope is not None else None,
            ema9_slope_trend=slope_trend,
            rvol=round(rvol_val, 2),
            rvol_status=rvol_status,
            extension=round(extension, 2),
            extension_status=extension_status,
            price_vs_ema9=price_vs_ema9,
            regime=regime,
        )

    # ── RSI divergence ────────────────────────────────────────────────────────

    def _check_rsi_divergence(self, bars: pd.DataFrame, side: str) -> bool:
        """
        Detect bearish RSI divergence (for longs): price making higher highs
        but RSI making lower highs over the last 10 bars.
        """
        if 'rsi_14' not in bars.columns or len(bars) < 10:
            return False

        recent = bars.tail(10)
        price_col = 'high' if side == 'buy' else 'low'

        price_vals = recent[price_col].dropna()
        rsi_vals   = recent['rsi_14'].dropna()

        if len(price_vals) < 5 or len(rsi_vals) < 5:
            return False

        # Compare first half vs second half
        mid = len(price_vals) // 2
        price_first_half = price_vals.iloc[:mid].max()
        price_second_half = price_vals.iloc[mid:].max()
        rsi_first_half   = rsi_vals.iloc[:mid].max()
        rsi_second_half  = rsi_vals.iloc[mid:].max()

        if side == 'buy':
            # Bearish divergence: price higher, RSI lower
            return price_second_half > price_first_half and rsi_second_half < rsi_first_half - 3
        else:
            # Bullish divergence (for shorts): price lower, RSI higher
            return price_second_half < price_first_half and rsi_second_half > rsi_first_half + 3

    # ── Stop / target computation ─────────────────────────────────────────────

    def _compute_trailing_stop(
        self,
        bars: pd.DataFrame,
        latest: pd.Series,
        current_entry: Optional[float],
        current_stop: Optional[float],
        side: str,
    ) -> float:
        """
        Compute a new trailing stop.

        Rules (long):
          1. Start from the structural low of the last 5 bars minus 0.25×ATR.
          2. Never lower the stop below the current stop (stops only move in
             the direction of profit).
          3. If the position is in profit (current price > entry), floor the
             stop at breakeven (entry price). A trailing stop that would leave
             the trader with a loss is not a trailing stop — it's just a stop.

        The symmetric rules apply for shorts.
        """
        atr   = float(latest.get('atr_14', latest.get('close', 100) * 0.02))
        price = float(latest.get('close', 0))

        if side == 'buy':
            structural_low = float(bars['low'].tail(5).min())
            new_stop = round(structural_low - atr * 0.25, 2)
            # Never lower the stop
            if current_stop is not None:
                new_stop = max(new_stop, current_stop)
            # If in profit, floor at breakeven so the stop can never produce a loss
            if current_entry is not None and price > current_entry:
                new_stop = max(new_stop, current_entry)
        else:
            structural_high = float(bars['high'].tail(5).max())
            new_stop = round(structural_high + atr * 0.25, 2)
            # Never raise the stop (for shorts, a higher stop = worse)
            if current_stop is not None:
                new_stop = min(new_stop, current_stop)
            # If in profit (price below entry for shorts), ceiling at breakeven
            if current_entry is not None and price < current_entry:
                new_stop = min(new_stop, current_entry)

        return new_stop

    def _compute_raised_target(
        self,
        bars: pd.DataFrame,
        latest: pd.Series,
        current_entry: Optional[float],
        current_stop: Optional[float],
        side: str,
    ) -> tuple:
        """
        Compute a new (higher) target by re-running the signal logic on current bars.

        Returns (new_target, new_stop). Falls back to a 2R extension from the
        current price if the signal doesn't produce a clean target.
        """
        atr = float(latest.get('atr_14', latest.get('close', 100) * 0.02))
        price = float(latest.get('close', 0))

        # Trail the stop first
        new_stop = self._compute_trailing_stop(bars, latest, current_entry, current_stop, side)

        # Compute new target as 2R from current price using the new stop
        risk = abs(price - new_stop)
        if risk == 0:
            return None, new_stop

        if side == 'buy':
            new_target = round(price + risk * 2.0, 2)
        else:
            new_target = round(price - risk * 2.0, 2)

        return new_target, new_stop

    # ── Order parsing ─────────────────────────────────────────────────────────

    def _find_stop(self, orders: List[Dict[str, Any]]) -> Optional[float]:
        """Extract the stop price from the bracket order legs."""
        for o in orders:
            if (o.get('type') in ('stop', 'stop_limit')) and o.get('stop_price'):
                return float(o['stop_price'])
            for leg in (o.get('legs') or []):
                if (leg.get('type') in ('stop', 'stop_limit')) and leg.get('stop_price'):
                    return float(leg['stop_price'])
        return None

    def _find_target(self, orders: List[Dict[str, Any]]) -> Optional[float]:
        """Extract the take-profit limit price from the bracket order legs."""
        for o in orders:
            if o.get('type') == 'limit' and o.get('side') == 'sell' and o.get('limit_price'):
                return float(o['limit_price'])
            for leg in (o.get('legs') or []):
                if leg.get('type') == 'limit' and leg.get('limit_price'):
                    return float(leg['limit_price'])
        return None

    def _infer_side(self, position: Dict[str, Any], orders: List[Dict[str, Any]]) -> str:
        """Infer whether this is a long or short position."""
        qty = position.get('qty', 0)
        if qty > 0:
            return 'buy'
        if qty < 0:
            return 'sell'
        # Fall back to checking orders
        for o in orders:
            if o.get('side') == 'buy':
                return 'buy'
        return 'buy'  # default to long

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _already_applied(
        self,
        suggested_stop: Optional[float],
        suggested_target: Optional[float],
        current_stop: Optional[float],
        current_target: Optional[float],
        tolerance: float = 0.01,
    ) -> bool:
        """
        Return True if the suggested adjustment is already in place.

        A suggestion is considered already applied when both the suggested stop
        and suggested target are within `tolerance` dollars of the current
        levels. If either suggestion is None, that leg is ignored.
        """
        stop_ok = (
            suggested_stop is None
            or current_stop is None
            or abs(suggested_stop - current_stop) <= tolerance
        )
        target_ok = (
            suggested_target is None
            or current_target is None
            or abs(suggested_target - current_target) <= tolerance
        )
        return stop_ok and target_ok

    def _empty_health(self) -> MomentumHealth:
        return MomentumHealth(
            rsi=None, rsi_trend='FLAT',
            ema9_slope=None, ema9_slope_trend='FLAT',
            rvol=None, rvol_status='NORMAL',
            extension=None, extension_status='OK',
            price_vs_ema9='ABOVE', regime='BULLISH',
        )

    def _make_review(
        self,
        symbol: str,
        verdict: str,
        reason: str,
        momentum: MomentumHealth,
        position: Dict[str, Any],
        current_entry: Optional[float],
        current_stop: Optional[float],
        current_target: Optional[float],
        suggested_stop: Optional[float],
        suggested_target: Optional[float],
        details: List[str],
    ) -> PositionReview:
        return PositionReview(
            symbol=symbol,
            verdict=verdict,
            reason=reason,
            momentum=momentum,
            current_entry=current_entry,
            current_stop=current_stop,
            current_target=current_target,
            suggested_stop=suggested_stop,
            suggested_target=suggested_target,
            unrealized_pl=position.get('unrealized_pl'),
            unrealized_plpc=position.get('unrealized_plpc'),
            details=details,
        )
