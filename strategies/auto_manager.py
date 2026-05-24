"""
auto_manager.py — Autonomous position management and entry scanning loop.

All bot decisions flow through this module.  It is called by the scheduler
(scheduler.py) on a fixed cadence and never blocks the Flask request thread.

Decision flow
─────────────
1. Circuit breakers — if any hard stop is active, return immediately.
2. Exit detection   — compare current positions to last known; log exits.
3. Position review  — for each open position, run PositionReviewer and act.
4. Entry scan       — run the scanner for the requested timeframe; execute
                      new signals that pass all gates.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_STATE_PATH   = os.path.join("data", "bot_state.json")
_ACTIONS_PATH = os.path.join("data", "bot_actions.json")
_TRADES_PATH  = os.path.join("data", "trades.json")

# Maximum number of entries to keep in bot_actions.json.
# Older entries are trimmed on every write to prevent unbounded growth.
_MAX_ACTIONS = 5000

# Crypto watchlist keys — these trade 24/7 and need different scheduling logic.
_CRYPTO_WATCHLISTS = {"crypto_top10", "crypto_all"}

# Grade ordering — used to enforce BOT_MIN_GRADE.
_GRADE_ORDER = {"A": 4, "B": 3, "C": 2, "D": 1}


# ── State helpers ─────────────────────────────────────────────────────────────

# ── Per-position state helpers ────────────────────────────────────────────────

def _load_position_state(state: dict, symbol: str) -> dict | None:
    """Return the per-position state dict for a symbol, or None if not found."""
    return state.get("position_state", {}).get(symbol.upper())


def _save_position_state(state: dict, symbol: str, pos_state: dict) -> None:
    """Persist updated per-position state into the bot state dict."""
    if "position_state" not in state:
        state["position_state"] = {}
    state["position_state"][symbol.upper()] = pos_state


def _remove_position_state(state: dict, symbol: str) -> None:
    """Remove per-position state when a position is closed."""
    if "position_state" in state:
        state["position_state"].pop(symbol.upper(), None)


def _cleanup_stale_position_states(state: dict, open_symbols: set[str]) -> None:
    """
    Remove position state for any symbol that is no longer in open positions.
    Prevents stale state from contaminating a future trade in the same symbol.
    """
    pos_states = state.get("position_state", {})
    stale = [sym for sym in list(pos_states.keys()) if sym not in open_symbols]
    for sym in stale:
        pos_states.pop(sym, None)
        logger.debug(f"Cleaned up stale position state for {sym}")


def _init_position_state(
    position: dict,
    orders: list[dict],
    trade_log: list[dict],
) -> dict:
    """
    Build the initial per-position state dict when a position is first seen.

    Reads entry_price, initial_stop_price, and breakout_level from the trade log
    (written at order submission time).  Falls back to the position dict and
    bracket orders if the trade log entry is missing.

    initial_risk is computed as abs(entry_price - initial_stop_price).
    If initial_risk <= 0, the state is still created but MFE-based phase
    transitions will be skipped (guarded in _update_position_state).
    """
    sym = position.get("symbol", "").upper()

    # Find the most recent trade log entry for this symbol (non-event entries only)
    trade_entry = None
    for t in reversed(trade_log):
        if t.get("symbol", "").upper() == sym and not t.get("event"):
            trade_entry = t
            break

    entry_price    = float(position.get("avg_entry_price") or 0)
    initial_stop   = None
    breakout_level = None

    if trade_entry:
        if trade_entry.get("entry_price"):
            entry_price = float(trade_entry["entry_price"])
        if trade_entry.get("stop_price"):
            initial_stop = float(trade_entry["stop_price"])
        if trade_entry.get("breakout_level"):
            breakout_level = float(trade_entry["breakout_level"])

    # Fall back to bracket order stop if not in trade log.
    # Check the order itself first, then its legs.  Use a flag so the inner
    # break also exits the outer loop — otherwise we'd continue iterating
    # orders after finding a stop in a leg and potentially overwrite it.
    if initial_stop is None:
        for o in orders:
            if o.get("type") in ("stop", "stop_limit") and o.get("stop_price"):
                initial_stop = float(o["stop_price"])
                break
            for leg in (o.get("legs") or []):
                if leg.get("type") in ("stop", "stop_limit") and leg.get("stop_price"):
                    initial_stop = float(leg["stop_price"])
                    break
            if initial_stop is not None:
                break

    initial_risk = abs(entry_price - initial_stop) if initial_stop is not None else 0.0

    current_price = float(position.get("current_price") or entry_price)

    return {
        "entry_price":           entry_price,
        "breakout_level":        breakout_level or entry_price,
        "initial_stop_price":    initial_stop,
        "initial_risk":          initial_risk,
        "bars_since_entry":      0,
        "max_price_since_entry": current_price,
        "min_price_since_entry": current_price,
        "phase":                 "validation",
    }


def _load_state() -> dict:
    try:
        with open(_STATE_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"Failed to load bot state: {e}")
        return {}


def _save_state(state: dict) -> None:
    try:
        os.makedirs("data", exist_ok=True)
        with open(_STATE_PATH, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Failed to save bot state: {e}")


def _log_action(action_type: str, symbol: str | None, details: dict, result: str) -> None:
    """Append one entry to bot_actions.json (the notification substitute)."""
    entry = {
        "timestamp":   datetime.now(_ET).isoformat(),
        "action_type": action_type,
        "symbol":      symbol,
        "details":     details,
        "result":      result,
    }
    try:
        os.makedirs("data", exist_ok=True)
        try:
            with open(_ACTIONS_PATH) as f:
                actions = json.load(f)
        except FileNotFoundError:
            actions = []
        actions.append(entry)
        # Trim to the most recent _MAX_ACTIONS entries to prevent unbounded growth.
        if len(actions) > _MAX_ACTIONS:
            actions = actions[-_MAX_ACTIONS:]
        with open(_ACTIONS_PATH, "w") as f:
            json.dump(actions, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Failed to log bot action: {e}")

    logger.info(f"[BOT] {action_type} | {symbol or '—'} | {result}")


def _log_trade(trade_info: dict) -> None:
    """Append a trade entry to trades.json."""
    try:
        os.makedirs("data", exist_ok=True)
        try:
            with open(_TRADES_PATH) as f:
                trades = json.load(f)
        except FileNotFoundError:
            trades = []
        trades.append(trade_info)
        with open(_TRADES_PATH, "w") as f:
            json.dump(trades, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Failed to log trade: {e}")


# ── Market hours ──────────────────────────────────────────────────────────────

def is_market_hours() -> bool:
    """Return True if the current ET time is within regular equity market hours Mon–Fri."""
    now = datetime.now(_ET)
    if now.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    market_open  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now < market_close


def is_tradeable_now(symbol: str) -> bool:
    """
    Return True if the symbol can be traded right now.

    Crypto trades 24/7 — always returns True.
    Equities are gated to regular market hours Mon–Fri 09:30–16:00 ET.
    """
    from analysis.asset_type import AssetType, classify_symbol
    if classify_symbol(symbol) == AssetType.CRYPTO:
        return True
    return is_market_hours()


def is_crypto_watchlist(config) -> bool:
    """
    Return True if the configured scan watchlist is a crypto list.

    Crypto watchlists trade 24/7 and require different scheduling:
      - No market-hours gate on position review
      - Daily snapshot fires at midnight ET instead of 9:30 ET
      - Halt resets daily at midnight ET instead of market open
    """
    return config.BOT_SCAN_WATCHLIST in _CRYPTO_WATCHLISTS


# ── Circuit breakers ──────────────────────────────────────────────────────────

def check_circuit_breakers(bot, config, state: dict) -> tuple[bool, str]:
    """
    Return (ok, reason).  ok=False means the bot should not place any orders.

    Checks:
      1. Manually halted via dashboard
      2. Max daily loss exceeded

    halt_source distinguishes why the bot was halted:
      "manual"          — user-initiated via dashboard; persists across market open
      "circuit_breaker" — daily loss limit; auto-resets at next market open snapshot
    """
    if state.get("halted"):
        return False, "Bot is manually halted"

    max_loss_pct = config.BOT_MAX_DAILY_LOSS_PCT
    daily_open   = state.get("daily_open_equity")
    if daily_open and daily_open > 0:
        try:
            account = bot.get_account()
            equity  = account.get("equity")   # None if the API call failed
            if equity is None:
                # get_account() already logged the error; skip the loss check
                # rather than computing against zero and triggering a false halt.
                logger.warning("Circuit breaker: equity unavailable (API error) — skipping daily loss check")
            else:
                loss_pct = (daily_open - float(equity)) / daily_open * 100
                if loss_pct >= max_loss_pct:
                    reason = f"Max daily loss breached: down {loss_pct:.2f}% (limit {max_loss_pct}%)"
                    state["halted"]      = True
                    state["halt_source"] = "circuit_breaker"
                    _save_state(state)
                    _log_action("CIRCUIT_BREAKER", None, {"loss_pct": loss_pct, "limit": max_loss_pct}, reason)
                    return False, reason
        except Exception as e:
            logger.warning(f"Could not check daily loss: {e}")

    return True, "OK"


# ── Exit detection ────────────────────────────────────────────────────────────

def detect_exits(bot, state: dict) -> None:
    """
    Compare current open positions to the last known list.  Any symbol that
    was open and is now gone had its position closed (stop hit, target hit, or
    manual close from the UI).  Log an exit entry to trades.json.
    """
    try:
        current_positions = bot.get_positions()
        current_symbols   = {p["symbol"].upper() for p in current_positions}
        last_positions    = state.get("last_positions", [])

        for pos in last_positions:
            sym = pos.get("symbol", "").upper()
            if sym and sym not in current_symbols:
                exit_info = {
                    "event":        "exit",
                    "timestamp":    datetime.now(_ET).isoformat(),
                    "symbol":       sym,
                    "exit_reason":  "stop/target hit",
                    "exit_price":   pos.get("current_price"),
                    "entry_price":  pos.get("avg_entry_price"),
                    "quantity":     pos.get("qty"),
                    "unrealized_pl": pos.get("unrealized_pl"),
                    "source":       "auto_detected",
                }
                _log_trade(exit_info)
                _log_action(
                    "EXIT_DETECTED", sym,
                    {"last_price": pos.get("current_price"), "pl": pos.get("unrealized_pl")},
                    "Exit logged — position closed externally (stop/target hit or manual)",
                )

        # Update last known positions
        state["last_positions"] = current_positions
        _save_state(state)

    except Exception as e:
        logger.error(f"Exit detection error: {e}")


# ── Position review loop ──────────────────────────────────────────────────────

def _cooldown_ok(symbol: str, state: dict, cooldown_hours: int) -> bool:
    """Return True if enough time has passed since the last bot action on this symbol."""
    last = state.get("last_action_time", {}).get(symbol.upper())
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=_ET)
        return datetime.now(_ET) - last_dt >= timedelta(hours=cooldown_hours)
    except Exception:
        return True


def _record_action_time(symbol: str, state: dict) -> None:
    if "last_action_time" not in state:
        state["last_action_time"] = {}
    state["last_action_time"][symbol.upper()] = datetime.now(_ET).isoformat()


def _apply_trail_or_raise(bot, sym: str, result, state: dict, action_label: str) -> None:
    """
    Shared helper for TRAIL_STOP, PARTIAL_PROFIT, and RAISE_TARGET verdicts.

    All three verdicts want to adjust the stop and/or target — the difference
    is only in the reason logged.  Centralising the adjust_orders call here
    keeps the review loop DRY.
    """
    new_stop     = result.suggested_stop
    new_target   = result.suggested_target
    current_stop = result.current_stop

    stop_changed = (
        new_stop is not None
        and current_stop is not None
        and abs(new_stop - current_stop) > 0.01
    )
    if stop_changed or new_target is not None:
        adj = bot.adjust_orders(
            sym,
            new_stop=new_stop if stop_changed else None,
            new_target=new_target,
        )
        _record_action_time(sym, state)
        _log_action(
            action_label, sym,
            {"new_stop": new_stop, "new_target": new_target, "adj_result": adj},
            adj.get("status", "unknown"),
        )


def run_position_review(bot, config, state: dict, timeframe: str) -> None:
    """
    Review all open positions for the given timeframe and act on verdicts.

    For equity watchlists: gated to regular market hours Mon–Fri 09:30–16:00 ET.
    For crypto watchlists: runs 24/7 — crypto positions can be reviewed any time.

    TRAIL_STOP     → adjust_orders() if suggested stop differs by > $0.01
    PARTIAL_PROFIT → adjust_orders() to trail the stop (partial close not supported
                     on bracket orders; trailing the stop locks in profit instead)
    RAISE_TARGET   → adjust_orders() with new target and trailed stop
    EXIT           → close_position()
    """
    from strategies.position_manager import PositionReviewer

    # Gate equity reviews to market hours; crypto reviews run 24/7.
    if not is_crypto_watchlist(config) and not is_market_hours():
        logger.debug(f"Position review ({timeframe}) skipped — outside market hours")
        return

    ok, reason = check_circuit_breakers(bot, config, state)
    if not ok:
        logger.info(f"Position review ({timeframe}) skipped — circuit breaker: {reason}")
        return

    positions = bot.get_positions()
    if not positions:
        return

    # Load trade log to find the timeframe each position was entered on
    try:
        with open(_TRADES_PATH) as f:
            trade_log = json.load(f)
    except FileNotFoundError:
        trade_log = []

    # Most-recent entry per symbol wins
    entry_timeframes: dict[str, str] = {}
    for t in trade_log:
        if t.get("symbol") and t.get("timeframe") and not t.get("event"):
            entry_timeframes[t["symbol"].upper()] = t["timeframe"]

    from data.settings_store import get_blacklist
    blacklist = get_blacklist()

    for pos in positions:
        sym = pos["symbol"].upper()
        pos_tf = entry_timeframes.get(sym, "swing")

        # Only review positions that match the current review timeframe
        if pos_tf != timeframe:
            continue

        # Skip positions the user has blacklisted from bot activity
        if sym in blacklist:
            logger.debug(f"Skipping {sym} position review — blacklisted from bot activity")
            continue

        # For mixed portfolios: skip equity positions outside market hours
        if not is_tradeable_now(sym):
            logger.debug(f"Skipping {sym} review — outside tradeable hours")
            continue

        if not _cooldown_ok(sym, state, cooldown_hours=1):
            logger.debug(f"Skipping {sym} — cooldown active")
            continue

        try:
            reviewer = PositionReviewer(timeframe=pos_tf)
            # Fetch bars for the review
            tf_cfg = {
                "long":  ("1y",  "1d"),
                "swing": ("3mo", "1h"),
                "short": ("1mo", "15m"),
            }
            period, interval = tf_cfg.get(pos_tf, ("3mo", "1h"))
            data = bot.get_market_data(sym, period=period, interval=interval)
            if data.empty:
                logger.warning(f"No data for position review: {sym}")
                continue

            # Fetch open orders for this symbol so the reviewer can read the
            # current stop and target from the bracket legs.
            all_orders = bot.get_orders(status='open')
            sym_orders = [o for o in all_orders if o.get('symbol', '').upper() == sym]

            # Load per-position state (MFE, bars_since_entry, phase)
            pos_state = _load_position_state(state, sym)

            # Initialize state on first sight of this position
            if not pos_state:
                pos_state = _init_position_state(pos, sym_orders, trade_log)

            result = reviewer.review(pos, sym_orders, data, position_state=pos_state)
            verdict = result.verdict

            # Persist updated state (MFE, bars_since_entry, phase)
            if result.updated_position_state:
                _save_position_state(state, sym, result.updated_position_state)

            # Log phase transition if it just happened
            if result.phase_transition:
                _log_action(
                    "PHASE_TRANSITION", sym,
                    {"from": "validation", "to": "participation", "reason": result.phase_transition},
                    f"{sym}: validation → participation | {result.phase_transition}",
                )
                logger.info(f"{sym}: validation → participation | {result.phase_transition}")

            _log_action(
                f"REVIEW_{verdict}", sym,
                {
                    "timeframe":        pos_tf,
                    "phase":            result.phase,
                    "suggested_stop":   result.suggested_stop,
                    "suggested_target": result.suggested_target,
                    "reason":           result.reason,
                },
                f"Verdict: {verdict} (phase={result.phase})",
            )

            if verdict == "TRAIL_STOP":
                _apply_trail_or_raise(bot, sym, result, state, "TRAIL_STOP_APPLIED")

            elif verdict == "PARTIAL_PROFIT":
                # Alpaca bracket orders don't support partial closes, so we trail
                # the stop to lock in profit instead of taking shares off.
                _apply_trail_or_raise(bot, sym, result, state, "TRAIL_STOP_APPLIED")

            elif verdict == "RAISE_TARGET":
                _apply_trail_or_raise(bot, sym, result, state, "TRAIL_STOP_APPLIED")

            elif verdict == "EXIT":
                close_result = bot.close_position(sym, exit_reason="auto_exit")
                _record_action_time(sym, state)
                _log_action(
                    "AUTO_EXIT", sym,
                    {"reason": result.reason, "close_result": close_result},
                    close_result.get("status", "unknown"),
                )
                # Clean up position state when the position is closed
                _remove_position_state(state, sym)

        except Exception as e:
            logger.error(f"Position review failed for {sym}: {e}")

    # Clean up stale position state for symbols no longer in open positions
    open_symbols = {p["symbol"].upper() for p in positions}
    _cleanup_stale_position_states(state, open_symbols)

    _save_state(state)


# ── Entry scan loop ───────────────────────────────────────────────────────────

def _resolve_watchlist_symbols(config) -> list:
    """
    Return the symbol list for the configured watchlist.
    Dynamic lists (crypto_all, all_universe) are resolved from their caches.
    """
    from screeners.market_scanner import SYMBOL_LISTS
    from screeners.symbol_lists import load_cached_crypto_universe, load_cached_universe
    list_name = config.BOT_SCAN_WATCHLIST
    if list_name == "crypto_all":
        return load_cached_crypto_universe()
    if list_name == "all_universe":
        return load_cached_universe()
    return list(SYMBOL_LISTS.get(list_name, []))


def _grade_passes_minimum(grade: str | None, min_grade: str) -> bool:
    """
    Return True if the signal grade meets or exceeds the configured minimum.

    Grade ordering: A > B > C > D.  A missing or unrecognised grade fails.
    """
    if not grade or grade not in _GRADE_ORDER:
        return False
    return _GRADE_ORDER.get(grade, 0) >= _GRADE_ORDER.get(min_grade, 0)


def _compute_per_trade_risk(
    config,
    current_heat: float,
    equity: float,
) -> float | None:
    """
    Derive the per-trade risk percentage from the remaining heat budget.

    Returns the risk % to pass to calculate_position_size, or None if the
    heat budget is exhausted (caller should block the entry).

    The per-trade risk is the lesser of:
      - The remaining heat budget (max_heat - current_heat)
      - The configured per-trade cap (BOT_MAX_RISK_PER_TRADE_PCT)

    This ensures a single trade never consumes the entire heat budget and
    that sizing scales down naturally as the portfolio fills up.
    """
    max_heat      = config.BOT_MAX_PORTFOLIO_HEAT_PCT
    remaining     = max_heat - current_heat
    if remaining <= 0:
        return None
    return min(remaining, config.BOT_MAX_RISK_PER_TRADE_PCT)


def run_entry_scan(bot, config, state: dict, timeframe: str) -> None:
    """
    Scan the configured watchlist for new signals on the given timeframe.
    Execute bracket orders for signals that are new this cycle and pass all gates.

    Gates (in order):
      1. Market hours / crypto 24/7
      2. Circuit breakers (halt, daily loss)
      3. BOT_AUTONOMOUS flag
      4. Symbol blacklist
      5. Per-symbol entry cooldown
      6. Grade filter (BOT_MIN_GRADE)
      7. Tier 4 R:R check (passes_risk_checks)
      8. Portfolio heat budget (BOT_MAX_PORTFOLIO_HEAT_PCT)
      9. Minimum risk floor (BOT_MIN_RISK_PCT)
     10. can_trade() — position count, existing position, pending orders
     11. Pending-entries guard — prevents exceeding max_positions within one cycle
    """
    from screeners.market_scanner import MarketScanner
    from strategies.momentum import SignalHierarchy

    # Crypto watchlists trade 24/7; equity watchlists are gated to market hours.
    if not is_crypto_watchlist(config) and not is_market_hours():
        logger.debug(f"Entry scan ({timeframe}) skipped — outside market hours for {config.BOT_SCAN_WATCHLIST}")
        return

    ok, reason = check_circuit_breakers(bot, config, state)
    if not ok:
        logger.info(f"Entry scan ({timeframe}) skipped — circuit breaker: {reason}")
        return

    if not config.BOT_AUTONOMOUS:
        logger.debug("BOT_AUTONOMOUS=false — entry scan skipped")
        return

    try:
        scanner = MarketScanner(bot.data_client, crypto_client=bot.crypto_client)
        list_name = config.BOT_SCAN_WATCHLIST

        # Collect all results from the scan stream
        results = []
        for chunk in scanner.scan_stream(list_name=list_name, timeframe=timeframe):
            try:
                payload = json.loads(chunk.replace("data: ", "").strip())
                if payload.get("type") == "result":
                    results.append(payload)
            except Exception:
                pass

        # Write results to the scan cache so Market Pulse and the Scanner's
        # "Load Cache" button always reflect the most recent bot scan.
        if results:
            try:
                scanner.write_cache(results, list_name, timeframe)
            except Exception as e:
                logger.warning(f"Could not write scan cache after bot scan: {e}")

        from data.settings_store import get_blacklist
        blacklist = get_blacklist()

        min_grade = config.BOT_MIN_GRADE

        # Snapshot current position count once before the loop so the
        # pending-entries guard is consistent across all signals this cycle.
        current_positions = bot.get_positions()
        entries_this_cycle = 0

        # Build a SignalHierarchy for the current timeframe so we can run
        # Tier 4 (passes_risk_checks) on each auto-entry candidate.
        tier4_checker = SignalHierarchy(timeframe=timeframe)

        for r in results:
            sym    = (r.get("symbol") or "").upper()
            signal = r.get("signal", "NONE")
            if not sym or signal == "NONE":
                continue

            # Skip symbols the user has blacklisted from bot activity
            if sym in blacklist:
                logger.debug(f"Entry scan: skipping {sym} — blacklisted from bot activity")
                continue

            # Per-symbol entry cooldown
            cooldown_hours = config.BOT_ENTRY_COOLDOWN_HOURS
            if not _cooldown_ok(sym, state, cooldown_hours=cooldown_hours):
                logger.info(f"Entry cooldown active for {sym} — skipping")
                continue

            # Grade filter — skip low-quality setups
            grade = r.get("grade")
            if not _grade_passes_minimum(grade, min_grade):
                logger.info(
                    f"Entry skipped for {sym} — grade {grade!r} below minimum {min_grade!r}"
                )
                _log_action(
                    "ENTRY_SKIPPED", sym,
                    {"reason": "grade below minimum", "grade": grade, "min_grade": min_grade, "timeframe": timeframe},
                    f"Skipped — grade {grade} < {min_grade}",
                )
                continue

            # Build entry parameters from the scan result
            entry_price  = r.get("entry_price")
            stop_price   = r.get("stop_price")
            target_price = r.get("target_price")

            if not all([entry_price, stop_price, target_price]):
                logger.warning(f"Incomplete signal data for {sym} — skipping auto-entry")
                continue

            entry_price  = float(entry_price)
            stop_price   = float(stop_price)
            target_price = float(target_price)

            # Tier 4: R:R and price validity check.
            # The scanner only runs Tier 1 + Tier 2 for speed.  We must run
            # Tier 4 here before committing real capital.
            side = "buy" if signal == "BUY" else "sell"
            signal_dict = {
                "symbol":       sym,
                "side":         side,
                "entry_price":  entry_price,
                "stop_price":   stop_price,
                "target_price": target_price,
            }
            rr_pass, rr_details = tier4_checker.passes_risk_checks(signal_dict)
            if not rr_pass:
                logger.info(f"Entry skipped for {sym} — Tier 4 failed: {rr_details}")
                _log_action(
                    "ENTRY_SKIPPED", sym,
                    {"reason": "Tier 4 R:R check failed", "details": rr_details, "timeframe": timeframe},
                    f"Skipped — {rr_details[-1] if rr_details else 'R:R check failed'}",
                )
                continue

            # ── Heat-based position sizing ────────────────────────────────────
            # Calculate current portfolio heat once per signal (positions list
            # is snapshotted before the loop; heat is re-derived each iteration
            # to account for entries_this_cycle adjustments).
            account_info = bot.get_account()
            equity       = float(account_info.get("equity") or 0)
            current_heat = bot.calculate_portfolio_heat(positions=current_positions)

            per_trade_risk = _compute_per_trade_risk(config, current_heat, equity)
            if per_trade_risk is None:
                logger.info(
                    f"Entry skipped for {sym} — portfolio heat budget exhausted "
                    f"({current_heat:.2f}% / {config.BOT_MAX_PORTFOLIO_HEAT_PCT:.2f}%)"
                )
                _log_action(
                    "ENTRY_SKIPPED", sym,
                    {
                        "reason":       "heat budget exhausted",
                        "current_heat": current_heat,
                        "max_heat":     config.BOT_MAX_PORTFOLIO_HEAT_PCT,
                        "timeframe":    timeframe,
                    },
                    f"Skipped — heat {current_heat:.2f}% ≥ max {config.BOT_MAX_PORTFOLIO_HEAT_PCT:.2f}%",
                )
                continue

            quantity = bot.calculate_position_size(entry_price, stop_price, available_risk_pct=per_trade_risk)

            # Minimum risk floor — skip if the position is economically meaningless.
            # This fires when the position cap constraint dominates and squeezes
            # the actual risk far below the intended per-trade allocation.
            if equity > 0:
                risk_per_share   = abs(entry_price - stop_price)
                implied_risk_pct = (risk_per_share * quantity / equity * 100) if quantity > 0 else 0.0
                if implied_risk_pct < config.BOT_MIN_RISK_PCT:
                    logger.info(
                        f"Entry skipped for {sym} — implied risk {implied_risk_pct:.3f}% "
                        f"below floor {config.BOT_MIN_RISK_PCT:.2f}%"
                    )
                    _log_action(
                        "ENTRY_SKIPPED", sym,
                        {
                            "reason":           "below minimum risk floor",
                            "implied_risk_pct": round(implied_risk_pct, 4),
                            "min_risk_pct":     config.BOT_MIN_RISK_PCT,
                            "entry_price":      entry_price,
                            "stop_price":       stop_price,
                            "timeframe":        timeframe,
                        },
                        f"Skipped — implied risk {implied_risk_pct:.3f}% < floor {config.BOT_MIN_RISK_PCT:.2f}%",
                    )
                    continue

            if quantity < 1:
                # Risk math produced < 1 share even after heat-based sizing.
                # Skip rather than override — the position is too small.
                logger.info(
                    f"Entry skipped for {sym} — risk-sized quantity < 1 share "
                    f"(entry=${entry_price:.2f}, stop=${stop_price:.2f}, risk={per_trade_risk:.3f}%)"
                )
                _log_action(
                    "ENTRY_SKIPPED", sym,
                    {
                        "reason":          "position too small for risk parameters",
                        "entry_price":     entry_price,
                        "stop_price":      stop_price,
                        "per_trade_risk":  per_trade_risk,
                        "timeframe":       timeframe,
                    },
                    "Skipped — risk-sized quantity < 1 share",
                )
                continue

            # Pending-entries guard — hard safety rail (MAX_POSITIONS).
            # Under normal conditions the heat budget is the binding constraint;
            # this guard prevents degenerate cases (e.g., many micro-positions).
            if len(current_positions) + entries_this_cycle >= bot.max_positions:
                logger.info(
                    f"Entry skipped for {sym} — max positions safety rail reached "
                    f"({len(current_positions)} open + {entries_this_cycle} pending this cycle)"
                )
                _log_action(
                    "ENTRY_SKIPPED", sym,
                    {
                        "reason":          "max positions safety rail",
                        "open_positions":  len(current_positions),
                        "pending_entries": entries_this_cycle,
                        "max_positions":   bot.max_positions,
                    },
                    f"Skipped — max positions safety rail ({bot.max_positions})",
                )
                continue

            # can_trade() gate — checks existing position, pending orders
            can, reason_ct = bot.can_trade(sym, side)
            if not can:
                logger.info(f"can_trade({sym}) failed: {reason_ct}")
                continue

            # Submit bracket order
            try:
                from alpaca.trading.requests import LimitOrderRequest, TakeProfitRequest, StopLossRequest
                from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

                order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
                req = LimitOrderRequest(
                    symbol=sym,
                    qty=quantity,
                    side=order_side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=round(entry_price, 2),
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=round(target_price, 2)),
                    stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
                )
                order = bot.trading_client.submit_order(req)

                trade_info = {
                    "timestamp":            datetime.now(_ET).isoformat(),
                    "symbol":               sym,
                    "side":                 side,
                    "entry_type":           "limit",
                    "timeframe":            timeframe,
                    "quantity":             quantity,
                    "entry_price":          entry_price,
                    "stop_price":           stop_price,
                    "target_price":         target_price,
                    "order_id":             str(order.id),
                    "status":               order.status.value,
                    "source":               "auto",
                    "score":                r.get("score"),
                    "grade":                r.get("grade"),
                    "per_trade_risk_pct":   round(per_trade_risk, 4),
                    "portfolio_heat_at_entry": round(current_heat, 4),
                }
                _log_trade(trade_info)
                _record_action_time(sym, state)
                entries_this_cycle += 1

                _log_action(
                    "AUTO_ENTRY", sym,
                    {
                        "timeframe":               timeframe,
                        "side":                    side,
                        "entry_price":             entry_price,
                        "stop_price":              stop_price,
                        "target_price":            target_price,
                        "quantity":                quantity,
                        "score":                   r.get("score"),
                        "grade":                   r.get("grade"),
                        "per_trade_risk_pct":      round(per_trade_risk, 4),
                        "portfolio_heat_at_entry": round(current_heat, 4),
                        "order_id":                str(order.id),
                    },
                    f"Bracket order submitted: {order.status.value}",
                )

            except Exception as e:
                logger.error(f"Auto-entry failed for {sym}: {e}")
                _log_action("AUTO_ENTRY_FAILED", sym, {"error": str(e)}, "error")

        _save_state(state)

    except Exception as e:
        logger.error(f"Entry scan ({timeframe}) failed: {e}")
        _log_action("SCAN_ERROR", None, {"timeframe": timeframe, "error": str(e)}, "error")


# ── Stale order cancellation ──────────────────────────────────────────────────

def cancel_stale_orders(bot, config, state: dict) -> None:
    """
    Cancel open limit orders that have been sitting unfilled for too long.

    For equity watchlists: runs at 3:45 ET Mon–Fri (15 min before close).
    Any limit order older than 4 hours is considered stale — the setup has
    moved on and we don't want it filling at the open the next day.

    For crypto watchlists: not called (crypto orders don't expire at close).
    """
    if not is_market_hours():
        return

    ok, reason = check_circuit_breakers(bot, config, state)
    if not ok:
        logger.info(f"Stale order cancellation skipped — circuit breaker: {reason}")
        return

    try:
        all_orders = bot.get_orders(status='open')
        stale_cutoff = datetime.now(_ET) - timedelta(hours=4)
        cancelled = []

        for order in all_orders:
            # Cancel stale limit entry orders — both buy (long) and sell (short).
            # Guard against cancelling bracket stop/target legs by checking order_class:
            # a top-level bracket entry has order_class="bracket" or None (standalone).
            # Child legs (stop, take-profit) are nested under the parent's legs list and
            # are not returned as top-level orders by get_orders(), so order_class is the
            # correct discriminator here — not side.
            if order.get("type") != "limit":
                continue
            if order.get("order_class") not in ("bracket", None):
                continue

            created_at_str = order.get("created_at")
            if not created_at_str:
                continue

            try:
                created_at = datetime.fromisoformat(created_at_str)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=_ET)
                # Convert to ET for comparison
                created_at_et = created_at.astimezone(_ET)
            except Exception:
                continue

            if created_at_et < stale_cutoff:
                try:
                    bot.trading_client.cancel_order_by_id(order["id"])
                    cancelled.append(order["symbol"])
                    logger.info(
                        f"Cancelled stale limit order for {order['symbol']} "
                        f"(created {created_at_et.strftime('%H:%M ET')})"
                    )
                except Exception as e:
                    logger.warning(f"Could not cancel stale order {order['id'][:8]}: {e}")

        if cancelled:
            _log_action(
                "STALE_ORDERS_CANCELLED", None,
                {"symbols": cancelled, "count": len(cancelled)},
                f"Cancelled {len(cancelled)} stale limit order(s): {', '.join(cancelled)}",
            )

    except Exception as e:
        logger.error(f"Stale order cancellation failed: {e}")


# ── Daily open equity snapshot ────────────────────────────────────────────────

def snapshot_daily_equity(bot, state: dict) -> None:
    """
    Record today's opening equity for the daily loss circuit breaker.

    For equity watchlists: called at 9:30 ET (market open).
    For crypto watchlists: called at midnight ET (start of crypto trading day).

    Halt reset policy:
      - Circuit-breaker halts (halt_source="circuit_breaker") are auto-reset here
        because they are triggered by a daily loss limit that resets each morning.
      - Manual halts (halt_source="manual" or no halt_source) are NOT auto-reset.
        The user explicitly paused the bot and must explicitly resume it via the
        dashboard.  This prevents the bot from trading through a Fed decision,
        CPI print, or any other event the user chose to sit out.
    """
    try:
        account = bot.get_account()
        equity  = account.get("equity", 0)
        state["daily_open_equity"] = equity

        # Only auto-reset circuit-breaker halts; preserve manual halts.
        if state.get("halt_source") == "circuit_breaker":
            state["halted"]      = False
            state["halt_source"] = None
            logger.info("Circuit-breaker halt auto-reset at market open")

        _save_state(state)
        _log_action("DAILY_OPEN", None, {"equity": equity}, f"Daily open equity: ${equity:,.2f}")
    except Exception as e:
        logger.error(f"Failed to snapshot daily equity: {e}")
