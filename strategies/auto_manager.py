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

# Crypto watchlist keys — these trade 24/7 and need different scheduling logic.
_CRYPTO_WATCHLISTS = {"crypto_top10", "crypto_all"}


# ── State helpers ─────────────────────────────────────────────────────────────

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
    """
    if state.get("halted"):
        return False, "Bot is manually halted"

    max_loss_pct = config.BOT_MAX_DAILY_LOSS_PCT
    daily_open   = state.get("daily_open_equity")
    if daily_open and daily_open > 0:
        try:
            account = bot.get_account()
            equity  = account.get("equity", 0)
            loss_pct = (daily_open - equity) / daily_open * 100
            if loss_pct >= max_loss_pct:
                reason = f"Max daily loss breached: down {loss_pct:.2f}% (limit {max_loss_pct}%)"
                state["halted"] = True
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


def run_position_review(bot, config, state: dict, timeframe: str) -> None:
    """
    Review all open positions for the given timeframe and act on verdicts.

    For equity watchlists: gated to regular market hours Mon–Fri 09:30–16:00 ET.
    For crypto watchlists: runs 24/7 — crypto positions can be reviewed any time.

    TRAIL_STOP → adjust_orders() if suggested stop differs by > $0.01
    EXIT       → close_position()
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

            result = reviewer.review(data, pos)
            verdict = result.get("verdict", "HOLD")

            _log_action(
                f"REVIEW_{verdict}", sym,
                {
                    "timeframe":        pos_tf,
                    "suggested_stop":   result.get("suggested_stop"),
                    "suggested_target": result.get("suggested_target"),
                    "reason":           result.get("reason"),
                },
                f"Verdict: {verdict}",
            )

            if verdict == "TRAIL_STOP":
                new_stop   = result.get("suggested_stop")
                new_target = result.get("suggested_target")
                current_stop = result.get("current_stop")

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
                        "TRAIL_STOP_APPLIED", sym,
                        {"new_stop": new_stop, "new_target": new_target, "adj_result": adj},
                        adj.get("status", "unknown"),
                    )

            elif verdict == "EXIT":
                close_result = bot.close_position(sym, exit_reason="auto_exit")
                _record_action_time(sym, state)
                _log_action(
                    "AUTO_EXIT", sym,
                    {"reason": result.get("reason"), "close_result": close_result},
                    close_result.get("status", "unknown"),
                )

        except Exception as e:
            logger.error(f"Position review failed for {sym}: {e}")

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


def run_entry_scan(bot, config, state: dict, timeframe: str) -> None:
    """
    Scan the configured watchlist for new signals on the given timeframe.
    Execute bracket orders for signals that are new this cycle and pass all gates.
    """
    from screeners.market_scanner import MarketScanner

    # Crypto watchlists trade 24/7; equity watchlists are gated to market hours.
    sample_symbols = _resolve_watchlist_symbols(config)
    first_sym = sample_symbols[0] if sample_symbols else ""
    if not is_tradeable_now(first_sym):
        logger.debug(f"Entry scan ({timeframe}) skipped — outside tradeable hours for {config.BOT_SCAN_WATCHLIST}")
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

        # Previous signal set for this timeframe (symbol → signal dict)
        prev_signals: dict = state.get("last_signals", {}).get(timeframe, {})
        new_signals:  dict = {}

        from data.settings_store import get_blacklist
        blacklist = get_blacklist()

        for r in results:
            sym    = (r.get("symbol") or "").upper()
            signal = r.get("signal", "NONE")
            if not sym or signal == "NONE":
                new_signals[sym] = r
                continue

            new_signals[sym] = r

            # Skip symbols the user has blacklisted from bot activity
            if sym in blacklist:
                logger.debug(f"Entry scan: skipping {sym} — blacklisted from bot activity")
                continue

            # Only act on signals that are NEW this cycle
            if sym in prev_signals:
                logger.debug(f"Signal for {sym} ({timeframe}) already seen last cycle — skipping")
                continue

            # Per-symbol entry cooldown
            cooldown_hours = config.BOT_ENTRY_COOLDOWN_HOURS
            if not _cooldown_ok(sym, state, cooldown_hours=cooldown_hours):
                logger.info(f"Entry cooldown active for {sym} — skipping")
                continue

            # can_trade() gate
            side = "buy" if signal == "BUY" else "sell"
            can, reason_ct = bot.can_trade(sym, side)
            if not can:
                logger.info(f"can_trade({sym}) failed: {reason_ct}")
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

            quantity = bot.calculate_position_size(entry_price, stop_price)
            if quantity < 1:
                quantity = 1

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
                    "timestamp":    datetime.now(_ET).isoformat(),
                    "symbol":       sym,
                    "side":         side,
                    "entry_type":   "limit",
                    "timeframe":    timeframe,
                    "quantity":     quantity,
                    "entry_price":  entry_price,
                    "stop_price":   stop_price,
                    "target_price": target_price,
                    "order_id":     str(order.id),
                    "status":       order.status.value,
                    "source":       "auto",
                    "score":        r.get("score"),
                    "grade":        r.get("grade"),
                }
                _log_trade(trade_info)
                _record_action_time(sym, state)

                _log_action(
                    "AUTO_ENTRY", sym,
                    {
                        "timeframe":    timeframe,
                        "side":         side,
                        "entry_price":  entry_price,
                        "stop_price":   stop_price,
                        "target_price": target_price,
                        "quantity":     quantity,
                        "score":        r.get("score"),
                        "grade":        r.get("grade"),
                        "order_id":     str(order.id),
                    },
                    f"Bracket order submitted: {order.status.value}",
                )

            except Exception as e:
                logger.error(f"Auto-entry failed for {sym}: {e}")
                _log_action("AUTO_ENTRY_FAILED", sym, {"error": str(e)}, "error")

        # Update last signal set for this timeframe
        if "last_signals" not in state:
            state["last_signals"] = {}
        state["last_signals"][timeframe] = new_signals
        _save_state(state)

    except Exception as e:
        logger.error(f"Entry scan ({timeframe}) failed: {e}")
        _log_action("SCAN_ERROR", None, {"timeframe": timeframe, "error": str(e)}, "error")


# ── Daily open equity snapshot ────────────────────────────────────────────────

def snapshot_daily_equity(bot, state: dict) -> None:
    """
    Record today's opening equity for the daily loss circuit breaker.
    Also resets the daily halt flag.

    For equity watchlists: called at 9:30 ET (market open).
    For crypto watchlists: called at midnight ET (start of crypto trading day).
    """
    try:
        account = bot.get_account()
        equity  = account.get("equity", 0)
        state["daily_open_equity"] = equity
        state["halted"] = False          # reset halt at start of new trading day
        _save_state(state)
        _log_action("DAILY_OPEN", None, {"equity": equity}, f"Daily open equity: ${equity:,.2f}")
    except Exception as e:
        logger.error(f"Failed to snapshot daily equity: {e}")
