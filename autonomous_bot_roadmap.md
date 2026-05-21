# Autonomous Trading Bot — Roadmap

## Concept

This application is being evolved from a **human-in-the-loop dashboard** into a
**fully autonomous paper-trading bot** that runs 24/7, makes its own entry and exit
decisions, and logs every action it takes.

### Core design principles

**Score ≠ Signal.**
The scanner score (0–100) measures *setup quality* — how tight the compression is,
how strong RS vs SPY is, how elevated RVOL is. A high score means "watch this closely."
A BUY/SELL signal means "the timing gate has cleared — enter now." An 88-score setup
with no signal is a watchlist candidate; a 62-score setup with a signal is actionable.
The score informs conviction and position sizing when a signal fires; it does not
replace the signal.

**Exit management before entry automation.**
Automated entries without automated exits is dangerous. The position management loop
(TRAIL_STOP, EXIT verdicts) must be running and verified before the entry scanner
fires live orders. Get the exit side right first.

**Signal deduplication.**
A signal that was present last cycle is not a new signal. The bot only acts on signals
that appear for the first time in a given cycle. This prevents re-entering a position
that was just closed and prevents double-ordering on slow-moving setups.

**Circuit breakers are non-negotiable.**
The bot must refuse to trade outside market hours (equities), when the account is down
more than the daily loss limit, or when a per-symbol cooldown is active. Crypto trades
24/7 and bypasses the market hours gate. All other circuit breakers apply to both.

**Notifications = JSON log (for now).**
Every action the bot would notify a human about is written to `data/bot_actions.json`.
This is the audit trail. A Discord/email notification layer can be added later by
reading from this log.

**Settings live in the database, not .env.**
All bot configuration (autonomous mode, watchlist, loss limits, cooldowns) is stored
in the SQLite settings store and managed via the dashboard Settings page. The `.env`
file is only used for secrets (API keys) and one-time bootstrap.

---

## Architecture

```
app.py (FastAPI)
  └── scheduler.py          — APScheduler; registers all jobs; starts with the app
        ├── auto_manager.py — core decision loop (exit + entry)
        │     ├── PositionReviewer  (already built)
        │     ├── bot.adjust_orders (already built)
        │     ├── bot.close_position (already built)
        │     └── bot.execute_trade  (already built)
        └── bot_state.json  — persistent state: positions, signals, cooldowns, daily P&L

analysis/asset_type.py      — AssetType enum + classify_symbol() — single decision point
bot.py                      — routes get_market_data() to equity or crypto client
analysis/indicators.py      — benchmark_data param (SPY for equities, BTC for crypto alts)
strategies/momentum.py      — asset_type-aware RS gate
```

---

## Implementation Checklist

### 🏗️ Infrastructure

- [x] **`scheduler.py`** — APScheduler wrapper; starts with Flask app; registers all jobs;
      graceful shutdown on app exit; exposes `get_scheduler_status()` for the dashboard
- [x] **`strategies/auto_manager.py`** — core autonomous loop; all bot decisions flow here;
      reads `bot_state.json`, acts, writes results back
- [x] **`data/bot_state.json`** — persistent state file:
      - `last_positions`: list of open positions from last poll
      - `last_signals`: dict of symbol → signal dict from last scan cycle per timeframe
      - `last_action_time`: dict of symbol → ISO timestamp of last bot action
      - `daily_open_equity`: float, set at market open each day
      - `halted`: bool, set by circuit breaker
- [x] **`data/bot_actions.json`** — append-only action log (replaces notifications);
      each entry: `{timestamp, action_type, symbol, details, verdict, result}`
- [ ] **Last-scan timestamps** — write `last_scan_time[timeframe]` to `bot_state.json`
      at the end of each scan job so the dashboard can show "last scanned X min ago"

---

### 📤 Exit Side (Position Management)

- [x] **Hourly position review job** — fires every hour during market hours;
      calls `PositionReviewer` for each open swing position; logs verdict to `bot_actions.json`
- [x] **Daily position review job** — fires at 9:35 ET each trading day;
      reviews all long (daily) positions
- [x] **Auto TRAIL_STOP** — if verdict is TRAIL_STOP and `suggested_stop` differs from
      `current_stop` by > $0.01, call `bot.adjust_orders()`; log action
- [x] **Auto EXIT** — if verdict is EXIT, call `bot.close_position()`; log action
- [x] **Exit detection poller** — runs every 5 min; compares `bot_state.last_positions`
      to current `bot.get_positions()`; when a symbol disappears, writes an exit log entry
      to `trades.json` with last known price and reason ("stop/target hit")
- [x] **Cooldown guard** — don't act on the same symbol more than once per review cycle;
      enforced via `last_action_time` in `bot_state.json`

---

### 📥 Entry Side (Signal Scanning)

- [x] **15-min scan job** — fires every 15 min during market hours;
      scans short-timeframe watchlist (configurable in settings store)
- [x] **Hourly scan job** — fires every hour during market hours; scans swing watchlist
- [x] **Daily scan job** — fires at 9:40 ET; scans long-timeframe watchlist
- [x] **Signal deduplication** — compare new signals to `bot_state.last_signals[timeframe]`;
      only act on signals whose symbol was NOT in the previous cycle's signal list
- [x] **`can_trade()` gate** — already implemented; enforces max positions, no duplicate
      symbols, no pending orders; called before every auto-entry
- [x] **Auto-execute bracket order** — when new signal passes all gates, call the same
      bracket order logic as the manual UI (`execute_trade` endpoint logic);
      log to `trades.json` and `bot_actions.json`
- [x] **Per-symbol entry cooldown** — don't re-enter a symbol within 24 hours of closing it;
      enforced via `last_action_time` in `bot_state.json`

---

### 🛡️ Circuit Breakers

- [x] **Market hours check** — no order submission outside 9:30–16:00 ET Mon–Fri for equities;
      crypto bypasses this gate (24/7)
- [x] **Max daily loss** — if account equity drops more than `MAX_DAILY_LOSS_PCT` (default 2%)
      from `daily_open_equity`, set `bot_state.halted = True`; log; halt all new entries
      for the rest of the day; reset at next market open
- [x] **Max concurrent positions** — already in `can_trade()`; enforced in auto-entry path
- [x] **Per-symbol cooldown** — 24-hour cooldown after any close (auto or manual)
- [x] **Scan sanity check** — if a scan returns 0 results or raises an exception,
      log the error and skip; never crash the scheduler

---

### 📊 Dashboard Integration

- [x] **Bot status widget on positions page** — shows: scheduler running/paused,
      next scheduled scan per job, today's auto-actions count, halted status
- [x] **Auto-action log section** — reads `data/bot_actions.json`; shows last 20 actions
      with timestamp, action type, symbol, and result
- [x] **Pause/resume toggle** — API endpoint + UI button to set `bot_state.halted = True/False`
      without restarting the app
- [ ] **Last-scan timestamps in status widget** — show "last scanned X min ago" per timeframe

---

### ⚙️ Settings UI — Autonomous Bot Configuration

Move all bot config from `.env` into the SQLite settings store, managed via the
dashboard Settings page. No restart required — changes take effect on the next
scheduler cycle.

- [ ] **Settings store** — add bot config keys to `data/settings_store.py`:
      `bot_autonomous`, `bot_scan_watchlist`, `bot_max_daily_loss_pct`,
      `bot_entry_cooldown_hours`, `bot_review_timeframes`
- [ ] **`config.py`** — read bot config from settings store (same pattern as
      `max_positions` and `risk_percentage`); fall back to `.env` for bootstrap only
- [ ] **`/api/settings` GET** — include bot config keys in the response
- [ ] **`/api/settings` POST** — accept and save bot config keys
- [ ] **Settings page UI** — new "Autonomous Bot" section with:
      - Master on/off toggle (`bot_autonomous`)
      - Scan watchlist selector (sp500_top100, sp500, nasdaq100, crypto_top10, custom)
      - Max daily loss % (number input, default 2.0)
      - Entry cooldown hours (number input, default 24)
      - Review timeframes (checkboxes: long, swing, short)
- [ ] **Live reload** — when settings are saved, update `bot.config.BOT_*` in memory
      so the running scheduler picks up the new values without restart

---

### 🪙 Crypto Support

The strategy (compression breakout + RS + RVOL) applies directly to crypto.
The only structural changes are: routing to the crypto data client, swapping the
RS benchmark (BTC instead of SPY for altcoins), and bypassing the market hours gate.

- [ ] **`analysis/asset_type.py`** — `AssetType` enum (`EQUITY`, `CRYPTO`) +
      `classify_symbol(symbol)` — single decision point for all routing
- [ ] **`bot.py`** — add `CryptoHistoricalDataClient`; route `get_market_data()` to
      `_get_crypto_data()` or `_get_equity_data()` based on `classify_symbol()`
- [ ] **`analysis/indicators.py`** — rename `spy_data` → `benchmark_data` (backward
      compatible); add `rs_vs_btc_20` calculation alongside `rs_vs_spy_20`
- [ ] **`strategies/momentum.py`** — accept `asset_type` param; swap RS benchmark:
      BTC/USD → no RS gate, crypto alts → RS vs BTC, equities → RS vs SPY (unchanged)
- [ ] **`strategies/auto_manager.py`** — `is_tradeable_now(asset_type)` replaces
      `is_market_hours()` for order submission; crypto always returns True
- [ ] **`screeners/symbol_lists.py`** — add `CRYPTO_WATCHLIST` (BTC/USD, ETH/USD,
      SOL/USD, AVAX/USD, LINK/USD, etc.)
- [ ] **Scanner** — pass `asset_type` through scan pipeline so crypto symbols use
      the correct data client and RS benchmark
- [ ] **Settings UI** — `crypto_top10` option in the scan watchlist selector

---

### ✅ Verification

- [ ] **Paper trading dry run** — run for 1 full trading day; verify all jobs fire at
      correct times; verify no duplicate entries; verify exit detection works
- [ ] **Verify signal deduplication** — confirm a signal that persists across cycles
      does not trigger a second order
- [ ] **Verify exit detection** — manually close a paper position; confirm exit is logged
      in `trades.json` within 5 minutes
- [ ] **Verify circuit breakers** — simulate a halted state; confirm no orders are placed
- [ ] **Verify crypto data** — type BTC/USD in the dashboard; confirm chart and analysis load
- [ ] **Verify crypto signal** — run scanner with crypto_top10; confirm signals fire correctly
- [ ] **Commit and push** — clean commit with all new files

---

## Configuration (settings store keys)

```
bot_autonomous           true/false   — master on/off switch
bot_scan_watchlist       string       — sp500_top100 | sp500 | nasdaq100 | crypto_top10 | custom
bot_max_daily_loss_pct   float        — halt entries if account drops this % in a day (default 2.0)
bot_entry_cooldown_hours int          — hours before re-entering a recently closed symbol (default 24)
bot_review_timeframes    string       — comma-separated: long,swing,short
```

---

## File Map

### New files
```
analysis/asset_type.py        — AssetType enum + classify_symbol()
```

### Modified files
```
scheduler.py                  — write last_scan_time to bot_state after each scan job
strategies/auto_manager.py    — is_tradeable_now(); write last_scan_time
bot.py                        — CryptoHistoricalDataClient; route by asset type
analysis/indicators.py        — benchmark_data param; rs_vs_btc_20
strategies/momentum.py        — asset_type-aware RS gate
screeners/symbol_lists.py     — CRYPTO_WATCHLIST
app.py                        — /api/settings GET+POST for bot config keys
config.py                     — read bot config from settings store
data/settings_store.py        — add bot config keys with defaults
templates/settings.html       — Autonomous Bot section (if exists, else add to layout)
static/js/settings.js         — bot config form fields + save logic
static/js/positions.js        — last-scan timestamps in bot status widget
```
