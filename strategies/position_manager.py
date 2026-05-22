"""
Position Manager
Reviews open positions against current market conditions and recommends
stop/target adjustments or exits.

Designed as a pure function so the same logic can be called from:
  - The manual review UI (positions page)
  - The automated hourly review loop (auto_manager.py)
  - The backtest engine (backtester/engine.py)

Two-phase review architecture
──────────────────────────────
Phase 1 — Validation (immediately after entry, until transition confirmed)
  Goal: kill failed breakouts quickly.
  Exits: regime flip, EMA9 cross + slope falling, catastrophic RVOL collapse (< 0.5x).
  Transition to Phase 2 when ANY of:
    - MFE >= 1R (trade has reached 1× initial risk in profit)
    - 3 consecutive closes above EMA9 with slope rising
    - Close > entry_price + 1.0× ATR (price has moved away from the breakout level)

Phase 2 — Participation (after transition)
  Goal: let the trend run; preserve convexity.
  Exits: regime flip, close < EMA21 AND rs_vs_spy_10 <= -5% (structural breakdown).
  Suppressed: RVOL fading, EMA9 slope flattening, RSI divergence.
  RAISE_TARGET still fires — extend winners when momentum confirms.

The phase is tracked in position_state (a plain dict persisted in bot_state.json
by auto_manager.py).  When position_state is None (manual UI review, first call),
the reviewer defaults to validation-phase behavior — safe and conservative.
"""

import logging
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
      HOLD           — thesis intact, no action needed
      TRAIL_STOP     — momentum stalling; raise stop to lock in profit
      RAISE_TARGET   — momentum accelerated; new target is higher
      PARTIAL_PROFIT — RSI diverging; take partial profit (validation phase only)
      EXIT           — regime flipped, structural breakdown, or failed breakout; close now

    updated_position_state: the caller should persist this back to bot_state.json
      so the next review cycle has accurate MFE, bars_since_entry, and phase.
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
    phase: str = "validation"                    # 'validation' | 'participation'
    phase_transition: Optional[str] = None       # reason for transition, if it just happened
    updated_position_state: Optional[dict] = None
    details: List[str] = field(default_factory=list)


# ── Reviewer ──────────────────────────────────────────────────────────────────

class PositionReviewer:
    """
    Reviews an open position against current market conditions.

    Usage (automated loop):
        reviewer = PositionReviewer(timeframe='swing')
        review   = reviewer.review(position, orders, bars, position_state=state)
        # persist review.updated_position_state back to bot_state.json

    Usage (manual UI / backtest — no persistent state):
        reviewer = PositionReviewer(timeframe='long')
        review   = reviewer.review(position, orders, bars)
        # defaults to validation-phase behavior

    All inputs are plain dicts/DataFrames — no Alpaca SDK objects.
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
        position_state: Optional[Dict[str, Any]] = None,
    ) -> PositionReview:
        """
        Run the full position review.

        Args:
            position:       dict from bot.get_positions() — has symbol, qty,
                            avg_entry_price, current_price, unrealized_pl, unrealized_plpc
            orders:         list from bot.get_orders() filtered to this symbol —
                            includes bracket legs (stop, limit/take_profit)
            bars:           DataFrame from bot.get_market_data() with indicators
            position_state: optional persistent state dict (see module docstring).
                            When None, defaults to validation-phase behavior.

        Returns:
            PositionReview with verdict, reason, suggested adjustments, and
            updated_position_state for the caller to persist.
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
                phase='validation',
                updated_position_state=position_state,
                details=details,
            )

        # Sync the latest bar's close to the position's current_price so the
        # momentum health checks use the live price, not the last bar's close.
        if current_price is not None:
            bars = bars.copy()
            bars.loc[bars.index[-1], 'close'] = current_price

        latest   = bars.iloc[-1]
        momentum = self._compute_momentum_health(bars, latest, side)
        details.append(f"Regime: {momentum.regime} | RSI: {momentum.rsi} ({momentum.rsi_trend}) | EMA9 slope: {momentum.ema9_slope} ({momentum.ema9_slope_trend})")
        details.append(f"RVOL: {momentum.rvol} ({momentum.rvol_status}) | Extension: {momentum.extension}x ATR ({momentum.extension_status}) | Price vs EMA9: {momentum.price_vs_ema9}")

        # Update position state (MFE, bars_since_entry, phase transition)
        updated_state, phase, phase_transition = self._update_position_state(
            position_state, current_entry, current_price, current_stop,
            side, bars, latest,
        )
        if phase_transition:
            details.append(f"📈 Phase transition: validation → participation | {phase_transition}")

        if phase == 'validation':
            return self._review_validation_phase(
                symbol, side, momentum, position, bars, latest,
                current_entry, current_stop, current_target, current_price,
                updated_state, phase, details,
            )
        else:
            return self._review_participation_phase(
                symbol, side, momentum, position, bars, latest,
                current_entry, current_stop, current_target, current_price,
                updated_state, phase, phase_transition, details,
            )

    # ── Phase routing ─────────────────────────────────────────────────────────

    def _review_validation_phase(
        self, symbol, side, momentum, position, bars, latest,
        current_entry, current_stop, current_target, current_price,
        updated_state, phase, details,
    ) -> PositionReview:
        """
        Validation phase: aggressive protection.
        Kill failed breakouts quickly.  Most exits are allowed.
        """
        details.append(f"Phase: VALIDATION")

        # ── Check 1: Regime flipped → EXIT ────────────────────────────────────
        if momentum.regime == 'NO_TRADE':
            details.append("❌ Regime flipped to NO_TRADE — original thesis invalidated")
            return self._make_review(
                symbol, 'EXIT',
                'Market regime has flipped to NO_TRADE — the macro thesis is invalidated. Exit to preserve capital.',
                momentum, position, current_entry, current_stop, current_target,
                suggested_stop=None, suggested_target=None,
                phase=phase, updated_state=updated_state, details=details,
            )

        # ── Check 2: Price crossed back below EMA9 AND slope is falling → EXIT ─
        if momentum.price_vs_ema9 == 'BELOW' and side == 'buy' and momentum.ema9_slope_trend == 'FALLING':
            details.append("❌ Price crossed back below EMA9 with EMA9 slope falling — trigger invalidated")
            return self._make_review(
                symbol, 'EXIT',
                'Price has crossed back below EMA9 and the EMA9 slope is falling. The breakout trigger is invalidated — exit the position.',
                momentum, position, current_entry, current_stop, current_target,
                suggested_stop=None, suggested_target=None,
                phase=phase, updated_state=updated_state, details=details,
            )
        if momentum.price_vs_ema9 == 'ABOVE' and side == 'sell' and momentum.ema9_slope_trend == 'RISING':
            details.append("❌ Price crossed back above EMA9 with EMA9 slope rising — trigger invalidated (short)")
            return self._make_review(
                symbol, 'EXIT',
                'Price has crossed back above EMA9 and the EMA9 slope is rising. The breakdown trigger is invalidated — cover the short.',
                momentum, position, current_entry, current_stop, current_target,
                suggested_stop=None, suggested_target=None,
                phase=phase, updated_state=updated_state, details=details,
            )

        # ── Check 3: Catastrophic RVOL collapse → EXIT ────────────────────────
        # Only fires on truly catastrophic volume collapse (< 0.5x), not normal
        # post-breakout normalization.  This catches failed breakouts where
        # participation evaporates immediately after entry.
        if momentum.rvol is not None and momentum.rvol < 0.5:
            details.append(f"❌ Catastrophic RVOL collapse ({momentum.rvol:.2f}x) — breakout participation evaporated")
            return self._make_review(
                symbol, 'EXIT',
                f'RVOL has collapsed to {momentum.rvol:.2f}x — breakout participation has evaporated. '
                'This is a failed breakout. Exit to preserve capital.',
                momentum, position, current_entry, current_stop, current_target,
                suggested_stop=None, suggested_target=None,
                phase=phase, updated_state=updated_state, details=details,
            )

        # ── Check 4: Price has exceeded the current target → RAISE_TARGET ─────
        if current_target and current_price:
            if side == 'buy' and current_price >= current_target * 0.98:
                new_target, new_stop = self._compute_raised_target(bars, latest, current_entry, current_stop, side)
                if new_target and new_target > current_target:
                    details.append(f"✅ Price at/above target — new target: ${new_target} | trail stop to: ${new_stop}")
                    return self._make_review(
                        symbol, 'RAISE_TARGET',
                        f'Price has reached the original target of ${current_target:.2f} with momentum still intact. '
                        f'Raise the take-profit to ${new_target:.2f} and trail the stop to ${new_stop:.2f} to lock in profit.',
                        momentum, position, current_entry, current_stop, current_target,
                        suggested_stop=new_stop, suggested_target=new_target,
                        phase=phase, updated_state=updated_state, details=details,
                    )
            elif side == 'sell' and current_price <= current_target * 1.02:
                new_target, new_stop = self._compute_raised_target(bars, latest, current_entry, current_stop, side)
                if new_target and new_target < current_target:
                    details.append(f"✅ Price at/below target (short) — new target: ${new_target} | trail stop to: ${new_stop}")
                    return self._make_review(
                        symbol, 'RAISE_TARGET',
                        f'Price has reached the original target of ${current_target:.2f} with momentum still intact. '
                        f'Lower the take-profit to ${new_target:.2f} and trail the stop to ${new_stop:.2f} to lock in profit.',
                        momentum, position, current_entry, current_stop, current_target,
                        suggested_stop=new_stop, suggested_target=new_target,
                        phase=phase, updated_state=updated_state, details=details,
                    )

        # ── Check 5: RSI divergence → PARTIAL_PROFIT ─────────────────────────
        rsi_diverging = self._check_rsi_divergence(bars, side)
        if rsi_diverging:
            new_stop = self._compute_trailing_stop(bars, latest, current_entry, current_stop, side)
            details.append(f"⚠️ RSI divergence detected — trail stop to: ${new_stop}")
            if self._already_applied(new_stop, current_target, current_stop, current_target):
                details.append("✅ Stop already at optimal level — no adjustment needed")
                return self._make_review(
                    symbol, 'HOLD',
                    'RSI divergence detected but the stop is already at the optimal level. No adjustment needed.',
                    momentum, position, current_entry, current_stop, current_target,
                    suggested_stop=None, suggested_target=None,
                    phase=phase, updated_state=updated_state, details=details,
                )
            return self._make_review(
                symbol, 'PARTIAL_PROFIT',
                'RSI is diverging from price (price making new highs but RSI is declining). '
                'This is a distribution signal. Consider taking partial profit and trailing the stop '
                f'to ${new_stop:.2f} on the remainder.',
                momentum, position, current_entry, current_stop, current_target,
                suggested_stop=new_stop, suggested_target=current_target,
                phase=phase, updated_state=updated_state, details=details,
            )

        # ── Check 6: RVOL fading → TRAIL_STOP ────────────────────────────────
        if momentum.rvol_status == 'LOW':
            new_stop = self._compute_trailing_stop(bars, latest, current_entry, current_stop, side)
            details.append(f"⚠️ RVOL fading — trail stop to: ${new_stop}")
            if self._already_applied(new_stop, current_target, current_stop, current_target):
                details.append("✅ Stop already at optimal level — no adjustment needed")
                return self._make_review(
                    symbol, 'HOLD',
                    'Volume is fading but the stop is already at the optimal level. No adjustment needed.',
                    momentum, position, current_entry, current_stop, current_target,
                    suggested_stop=None, suggested_target=None,
                    phase=phase, updated_state=updated_state, details=details,
                )
            in_profit = current_entry is not None and current_price is not None and (
                (side == 'buy'  and current_price > current_entry) or
                (side == 'sell' and current_price < current_entry)
            )
            stop_action = 'lock in profit' if in_profit else 'protect capital'
            return self._make_review(
                symbol, 'TRAIL_STOP',
                f'Volume is fading (RVOL {momentum.rvol:.2f}x). Momentum may be stalling. '
                f'Trail the stop to ${new_stop:.2f} to {stop_action}.',
                momentum, position, current_entry, current_stop, current_target,
                suggested_stop=new_stop, suggested_target=current_target,
                phase=phase, updated_state=updated_state, details=details,
            )

        # ── Check 7: EMA9 slope flattening → TRAIL_STOP ──────────────────────
        if momentum.ema9_slope_trend == 'FALLING' and momentum.rvol_status == 'NORMAL':
            new_stop = self._compute_trailing_stop(bars, latest, current_entry, current_stop, side)
            details.append(f"⚠️ EMA9 slope flattening — trail stop to: ${new_stop}")
            if self._already_applied(new_stop, current_target, current_stop, current_target):
                details.append("✅ Stop already at optimal level — no adjustment needed")
                return self._make_review(
                    symbol, 'HOLD',
                    'EMA9 slope is flattening but the stop is already at the optimal level. No adjustment needed.',
                    momentum, position, current_entry, current_stop, current_target,
                    suggested_stop=None, suggested_target=None,
                    phase=phase, updated_state=updated_state, details=details,
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
                suggested_stop=new_stop, suggested_target=current_target,
                phase=phase, updated_state=updated_state, details=details,
            )

        # ── All checks passed → HOLD ──────────────────────────────────────────
        details.append("✅ All validation checks passed — thesis intact")
        return self._make_review(
            symbol, 'HOLD',
            'Momentum is intact. Regime is bullish, price is above EMA9, RVOL is elevated, '
            'and RSI is trending in the right direction. Hold the position with current levels.',
            momentum, position, current_entry, current_stop, current_target,
            suggested_stop=None, suggested_target=None,
            phase=phase, updated_state=updated_state, details=details,
        )

    def _review_participation_phase(
        self, symbol, side, momentum, position, bars, latest,
        current_entry, current_stop, current_target, current_price,
        updated_state, phase, phase_transition, details,
    ) -> PositionReview:
        """
        Participation phase: convexity-first.
        Let the trend run.  Only exit on structural breakdown.
        RVOL fading, EMA9 slope noise, and RSI divergence are suppressed.
        """
        details.append(f"Phase: PARTICIPATION")

        # ── Check 1: Regime flipped → EXIT ────────────────────────────────────
        if momentum.regime == 'NO_TRADE':
            details.append("❌ Regime flipped to NO_TRADE — structural breakdown")
            return self._make_review(
                symbol, 'EXIT',
                'Market regime has flipped to NO_TRADE — the macro thesis is invalidated. Exit to preserve capital.',
                momentum, position, current_entry, current_stop, current_target,
                suggested_stop=None, suggested_target=None,
                phase=phase, updated_state=updated_state, details=details,
            )

        # ── Check 2: Close < EMA21 AND RS vs SPY 10-bar <= -5% → EXIT ─────────
        # Both conditions must be true — neither alone is sufficient.
        # This avoids killing normal pullbacks but exits when price structure
        # and relative strength both break simultaneously.
        ema21 = float(latest.get('ema_21', float('inf')))
        rs10  = latest.get('rs_vs_spy_10')
        price = float(latest.get('close', 0))

        if side == 'buy':
            price_below_ema21 = price < ema21
            rs_deteriorating  = rs10 is not None and not pd.isna(rs10) and float(rs10) <= -5.0
            if price_below_ema21 and rs_deteriorating:
                details.append(f"❌ Structural breakdown: price below EMA21 (${ema21:.2f}) AND RS vs SPY 10-bar = {float(rs10):.1f}%")
                return self._make_review(
                    symbol, 'EXIT',
                    f'Structural breakdown: price has fallen below EMA21 (${ema21:.2f}) and relative strength '
                    f'vs SPY over 10 bars is {float(rs10):.1f}% — both price structure and RS have broken. Exit.',
                    momentum, position, current_entry, current_stop, current_target,
                    suggested_stop=None, suggested_target=None,
                    phase=phase, updated_state=updated_state, details=details,
                )
        else:
            price_above_ema21 = price > ema21
            rs_deteriorating  = rs10 is not None and not pd.isna(rs10) and float(rs10) >= 5.0
            if price_above_ema21 and rs_deteriorating:
                details.append(f"❌ Structural breakdown (short): price above EMA21 (${ema21:.2f}) AND RS vs SPY 10-bar = {float(rs10):.1f}%")
                return self._make_review(
                    symbol, 'EXIT',
                    f'Structural breakdown (short): price has risen above EMA21 (${ema21:.2f}) and relative strength '
                    f'vs SPY over 10 bars is {float(rs10):.1f}% — both price structure and RS have broken. Cover.',
                    momentum, position, current_entry, current_stop, current_target,
                    suggested_stop=None, suggested_target=None,
                    phase=phase, updated_state=updated_state, details=details,
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
                        f'Raise the take-profit to ${new_target:.2f} and trail the stop to ${new_stop:.2f} to lock in profit.',
                        momentum, position, current_entry, current_stop, current_target,
                        suggested_stop=new_stop, suggested_target=new_target,
                        phase=phase, updated_state=updated_state, details=details,
                    )
            elif side == 'sell' and current_price <= current_target * 1.02:
                new_target, new_stop = self._compute_raised_target(bars, latest, current_entry, current_stop, side)
                if new_target and new_target < current_target:
                    details.append(f"✅ Price at/below target (short) — new target: ${new_target} | trail stop to: ${new_stop}")
                    return self._make_review(
                        symbol, 'RAISE_TARGET',
                        f'Price has reached the original target of ${current_target:.2f} with momentum still intact. '
                        f'Lower the take-profit to ${new_target:.2f} and trail the stop to ${new_stop:.2f} to lock in profit.',
                        momentum, position, current_entry, current_stop, current_target,
                        suggested_stop=new_stop, suggested_target=new_target,
                        phase=phase, updated_state=updated_state, details=details,
                    )

        # ── All checks passed → HOLD ──────────────────────────────────────────
        details.append("✅ Participation phase: trend intact — holding for convexity")
        return self._make_review(
            symbol, 'HOLD',
            'Trade is in the participation phase. Trend is intact — regime is healthy, '
            'no structural breakdown detected. Holding for full trend participation.',
            momentum, position, current_entry, current_stop, current_target,
            suggested_stop=None, suggested_target=None,
            phase=phase, updated_state=updated_state, details=details,
        )

    # ── Position state management ─────────────────────────────────────────────

    def _update_position_state(
        self,
        state: Optional[Dict[str, Any]],
        current_entry: Optional[float],
        current_price: Optional[float],
        current_stop: Optional[float],
        side: str,
        bars: pd.DataFrame,
        latest: pd.Series,
    ) -> tuple[dict, str, Optional[str]]:
        """
        Update per-position state: MFE, bars_since_entry, phase.

        Returns (updated_state, phase, phase_transition_reason).
        phase_transition_reason is non-None only when the phase just changed
        from validation to participation this cycle.
        """
        if state is None:
            # No persistent state — default to validation phase
            return {}, 'validation', None

        # Increment bars counter
        bars_since_entry = state.get('bars_since_entry', 0) + 1
        state = dict(state)  # copy — don't mutate the caller's dict
        state['bars_since_entry'] = bars_since_entry

        # Update MFE high/low
        bar_high = float(latest.get('high', current_price or 0))
        bar_low  = float(latest.get('low',  current_price or 0))

        if side == 'buy':
            prev_max = state.get('max_price_since_entry', current_entry or 0)
            state['max_price_since_entry'] = max(prev_max, bar_high)
        else:
            prev_min = state.get('min_price_since_entry', current_entry or float('inf'))
            state['min_price_since_entry'] = min(prev_min, bar_low)

        # If already in participation phase, stay there
        current_phase = state.get('phase', 'validation')
        if current_phase == 'participation':
            return state, 'participation', None

        # Check transition conditions
        initial_risk = state.get('initial_risk', 0)
        entry_price  = state.get('entry_price', current_entry)
        atr          = float(latest.get('atr_14', 0))

        transition_reason = None

        # Condition 1: MFE >= 1R
        if initial_risk and initial_risk > 0 and entry_price is not None:
            if side == 'buy':
                mfe = state.get('max_price_since_entry', entry_price) - entry_price
            else:
                mfe = entry_price - state.get('min_price_since_entry', entry_price)
            mfe_r = mfe / initial_risk
            if mfe_r >= 1.0:
                transition_reason = f"MFE >= 1R ({mfe_r:.2f}R)"

        # Condition 2: 3 consecutive closes above EMA9 with slope rising
        if transition_reason is None and 'ema_9' in bars.columns and len(bars) >= 3:
            last3 = bars.tail(3)
            ema9_vals = last3['ema_9']
            closes    = last3['close']
            slope     = float(latest.get('ema9_slope', 0) or 0)
            if side == 'buy':
                if all(closes.values > ema9_vals.values) and slope > 0:
                    transition_reason = "3 consecutive closes above EMA9 with slope rising"
            else:
                if all(closes.values < ema9_vals.values) and slope < 0:
                    transition_reason = "3 consecutive closes below EMA9 with slope falling"

        # Condition 3: Close > entry + 1.0× ATR
        if transition_reason is None and entry_price is not None and atr > 0:
            close = float(latest.get('close', 0))
            if side == 'buy' and close > entry_price + atr:
                transition_reason = f"Close > entry + 1.0×ATR (${entry_price + atr:.2f})"
            elif side == 'sell' and close < entry_price - atr:
                transition_reason = f"Close < entry - 1.0×ATR (${entry_price - atr:.2f})"

        if transition_reason:
            state['phase'] = 'participation'
            return state, 'participation', transition_reason

        return state, 'validation', None

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
        Detect bearish RSI divergence (for longs): price making a higher swing
        high but RSI making a lower swing high over the last 20 bars.

        Uses local swing highs/lows (bars where the value is higher/lower than
        both its immediate neighbours) rather than half-window maxima.  The
        half-window approach generated false positives on any healthy uptrend
        where the second half simply had higher prices than the first half —
        which is the normal state of a trending move.

        Requires at least two swing pivots in the window to make a comparison.
        Returns False (no divergence) if fewer than two pivots are found.
        """
        if 'rsi_14' not in bars.columns or len(bars) < 20:
            return False

        recent    = bars.tail(20).reset_index(drop=True)
        price_col = 'high' if side == 'buy' else 'low'

        # Find local swing highs (for longs) or swing lows (for shorts).
        pivots = []
        for i in range(1, len(recent) - 1):
            if side == 'buy':
                is_pivot = (recent[price_col].iloc[i] > recent[price_col].iloc[i - 1] and
                            recent[price_col].iloc[i] > recent[price_col].iloc[i + 1])
            else:
                is_pivot = (recent[price_col].iloc[i] < recent[price_col].iloc[i - 1] and
                            recent[price_col].iloc[i] < recent[price_col].iloc[i + 1])
            if is_pivot:
                pivots.append(i)

        if len(pivots) < 2:
            return False

        prev_idx = pivots[-2]
        last_idx = pivots[-1]

        prev_price = recent[price_col].iloc[prev_idx]
        last_price = recent[price_col].iloc[last_idx]
        prev_rsi   = recent['rsi_14'].iloc[prev_idx]
        last_rsi   = recent['rsi_14'].iloc[last_idx]

        if side == 'buy':
            return last_price > prev_price and last_rsi < prev_rsi - 3
        else:
            return last_price < prev_price and last_rsi > prev_rsi + 3

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
             stop at breakeven (entry price).
        """
        atr   = float(latest.get('atr_14', latest.get('close', 100) * 0.02))
        price = float(latest.get('close', 0))

        if side == 'buy':
            structural_low = float(bars['low'].tail(5).min())
            new_stop = round(structural_low - atr * 0.25, 2)
            if current_stop is not None:
                new_stop = max(new_stop, current_stop)
            if current_entry is not None and price > current_entry:
                new_stop = max(new_stop, current_entry)
        else:
            structural_high = float(bars['high'].tail(5).max())
            new_stop = round(structural_high + atr * 0.25, 2)
            if current_stop is not None:
                new_stop = min(new_stop, current_stop)
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
        Returns (new_target, new_stop).
        """
        atr   = float(latest.get('atr_14', latest.get('close', 100) * 0.02))
        price = float(latest.get('close', 0))

        new_stop = self._compute_trailing_stop(bars, latest, current_entry, current_stop, side)

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
        for o in orders:
            if o.get('side') == 'buy':
                return 'buy'
        return 'buy'

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _already_applied(
        self,
        suggested_stop: Optional[float],
        suggested_target: Optional[float],
        current_stop: Optional[float],
        current_target: Optional[float],
        tolerance: float = 0.01,
    ) -> bool:
        """Return True if the suggested adjustment is already in place."""
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
        phase: str,
        updated_state: Optional[dict],
        details: List[str],
        phase_transition: Optional[str] = None,
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
            phase=phase,
            phase_transition=phase_transition,
            updated_position_state=updated_state,
            details=details,
        )
