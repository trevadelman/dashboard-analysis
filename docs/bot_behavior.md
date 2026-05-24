# Bot Behavior

This document covers the autonomous bot's runtime behavior: scheduler cadence, startup sequence, circuit breakers, trade lifecycle, state files, and operational controls.

For the signal generation and position review strategy, see [strategy.md](strategy.md).

---

## Scheduler Jobs

The scheduler (`scheduler.py`) runs all jobs in a background thread pool and never blocks the Flask request thread.

### Equity watchlist mode (default)

| Job ID | Name | Trigger | Purpose |
|--------|------|---------|---------|
| `market_open_snapshot` | Market open equity snapshot | 9:30 ET Mon–Fri (cron) | Record opening equity; auto-reset circuit-breaker halts only |
| `long_review` | Long position review | 9:35 ET Mon–Fri (cron) | Review daily-timeframe positions |
| `long_scan` | Long timeframe entry scan | 9:40 ET Mon–Fri (cron) | Scan for daily-timeframe signals |
| `exit_poller` | Exit detection poller | Every 5 min (interval, fires immediately on startup) | Detect positions closed by stop/target/manual |
| `swing_review` | Swing position review | Every 60 min (aligned to next :00 boundary) | Review hourly-timeframe positions |
| `swing_scan` | Swing timeframe entry scan | Every 60 min (aligned to next :00 boundary) | Scan for hourly-timeframe signals |
| `short_review` | Short position review | Every 15 min (aligned to next :00/:15/:30/:45) | Review 15-min-timeframe positions |
| `short_scan` | Short timeframe entry scan | Every 15 min (aligned to next :00/:15/:30/:45) | Scan for 15-min-timeframe signals |
| `cancel_stale_orders` | Cancel stale limit orders | 3:45 ET Mon–Fri (cron) | Cancel unfilled limit orders before close |

### Crypto watchlist mode

Activated when `BOT_SCAN_WATCHLIST` is `crypto_top10` or `crypto_all`. The daily jobs shift to midnight ET (7 days/week); interval jobs are unchanged.

| Job ID | Trigger (crypto) |
|--------|-----------------|
| `market_open_snapshot` | Midnight ET daily |
| `long_review` | 00:05 ET daily |
| `long_scan` | 00:10 ET daily |
| `cancel_stale_orders` | **Not registered** — crypto orders don't expire at close |

### Startup clock alignment

Interval jobs (`swing_*`, `short_*`) use `start_date` set to the next clock boundary so they don't all fire simultaneously on startup/restart. This prevents connection pool bursts.

- `exit_poller` fires immediately on startup (intentional — catches exits during downtime)
- `short_*` wait until the next `:00/:15/:30/:45` mark
- `swing_*` wait until the next top-of-hour mark

Example: restart at 9:10 ET → exit_poller fires at 9:10, short jobs fire at 9:15, swing jobs fire at 10:00.

---

## Circuit Breakers

Two hard stops gate all bot activity. Both are checked at the start of every position review and entry scan.

### 1. Manual halt

Set via the dashboard Bot page (Pause/Resume button) or directly via `POST /api/bot/pause`.

Stored as `halted: true` and `halt_source: "manual"` in `data/bot_state.json`. Persists across restarts **and across market opens** — a manual halt requires an explicit resume via the dashboard. It will not auto-reset at 9:30 ET.

Use this when you want to sit out a specific event (Fed decision, CPI print, earnings season) without the bot resuming on its own.

### 2. Daily loss limit

Configured via `BOT_MAX_DAILY_LOSS_PCT` (default: 2%). Checked against `daily_open_equity` recorded at market open.

```
loss_pct = (daily_open_equity - current_equity) / daily_open_equity × 100
if loss_pct >= BOT_MAX_DAILY_LOSS_PCT → halt bot (halt_source="circuit_breaker"), log CIRCUIT_BREAKER action
```

Circuit-breaker halts (`halt_source: "circuit_breaker"`) are auto-reset at the next market open snapshot (9:30 ET equity / midnight ET crypto). Manual halts are never auto-reset.

### Halt source field

`bot_state.json` stores a `halt_source` field alongside `halted` to distinguish the origin:

| `halt_source` | Set by | Auto-resets at market open? |
|---------------|--------|-----------------------------|
| `"manual"` | Dashboard Pause button / `POST /api/bot/pause` | **No** — requires explicit Resume |
| `"circuit_breaker"` | Daily loss limit breach | **Yes** — resets automatically |
| `null` / absent | Legacy state (pre-fix) | **No** — treated as manual for safety |

---

## Trade Lifecycle

```
Scanner finds signal
  → Grade filter (BOT_MIN_GRADE)
  → Tier 4 R:R check (≥ 2:1)
  → Position size calculation (risk-based)
  → can_trade() gate (existing position, pending orders, max positions)
  → Bracket order submitted (limit entry + stop loss + take profit)
  → trade_info written to data/trades.json
  → AUTO_ENTRY logged to data/bot_actions.json

Position open
  → exit_poller runs every 5 min
      → if position gone → EXIT_DETECTED logged to trades.json
  → position_review runs on cadence
      → loads position_state from bot_state.json
      → runs PositionReviewer (two-phase logic)
      → HOLD → no action
      → TRAIL_STOP / PARTIAL_PROFIT / RAISE_TARGET → adjust_orders()
      → EXIT → close_position()
      → saves updated position_state back to bot_state.json
      → logs verdict to bot_actions.json

Position closed (stop hit / target hit / EXIT verdict / manual)
  → position_state removed from bot_state.json
  → exit logged to trades.json (if detected by exit_poller)
```

---

## State Files

All state is stored as JSON in the `data/` directory. No database.

### `data/bot_state.json`

Master state file. Loaded fresh at the start of every job.

```json
{
  "halted": false,
  "daily_open_equity": 25000.00,
  "last_positions": [...],
  "last_action_time": {
    "AAPL": "2026-05-22T09:15:00-04:00"
  },
  "position_state": {
    "AAPL": {
      "entry_price": 150.00,
      "breakout_level": 148.50,
      "initial_stop_price": 144.00,
      "initial_risk": 6.00,
      "bars_since_entry": 7,
      "max_price_since_entry": 158.20,
      "min_price_since_entry": 149.10,
      "phase": "participation"
    }
  }
}
```

Key fields:
- `halted` — circuit breaker flag; gates all bot activity
- `daily_open_equity` — baseline for daily loss calculation; reset each morning
- `last_positions` — snapshot from previous exit_poller run; used to detect closed positions
- `last_action_time` — per-symbol cooldown timestamps; prevents re-entering too soon
- `position_state` — per-position state for the two-phase reviewer (see strategy.md)

### `data/bot_actions.json`

Append-only log of every bot decision. Capped at 5,000 entries (oldest trimmed on write). Visible in the dashboard Bot page.

```json
[
  {
    "timestamp": "2026-05-22T09:15:00-04:00",
    "action_type": "AUTO_ENTRY",
    "symbol": "AAPL",
    "details": { "entry_price": 150.00, "stop_price": 144.00, ... },
    "result": "Bracket order submitted: accepted"
  }
]
```

Action types: `AUTO_ENTRY`, `AUTO_ENTRY_FAILED`, `ENTRY_SKIPPED`, `REVIEW_HOLD`, `REVIEW_TRAIL_STOP`, `REVIEW_EXIT`, `TRAIL_STOP_APPLIED`, `AUTO_EXIT`, `EXIT_DETECTED`, `PHASE_TRANSITION`, `CIRCUIT_BREAKER`, `MANUAL_HALT`, `MANUAL_RESUME`, `DAILY_OPEN`, `STALE_ORDERS_CANCELLED`, `SCAN_ERROR`.

### `data/trades.json`

Append-only log of all entries and exits.

Entry record (written at order submission):
```json
{
  "timestamp": "2026-05-22T09:15:00-04:00",
  "symbol": "AAPL",
  "side": "buy",
  "entry_type": "limit",
  "timeframe": "swing",
  "quantity": 10,
  "entry_price": 150.00,
  "stop_price": 144.00,
  "target_price": 162.00,
  "breakout_level": 148.50,
  "order_id": "abc123",
  "status": "accepted",
  "source": "auto",
  "score": 87,
  "grade": "A"
}
```

Exit record (written by exit_poller when position disappears):
```json
{
  "event": "exit",
  "timestamp": "2026-05-22T14:30:00-04:00",
  "symbol": "AAPL",
  "exit_reason": "stop/target hit",
  "exit_price": 162.00,
  "entry_price": 150.00,
  "quantity": 10,
  "unrealized_pl": 120.00,
  "source": "auto_detected"
}
```

---

## Entry Gates (in order)

All gates must pass before a bracket order is submitted:

1. **Market hours** — equity watchlists only trade Mon–Fri 09:30–16:00 ET; crypto 24/7
2. **Circuit breakers** — halt flag and daily loss limit
3. **BOT_AUTONOMOUS** — must be `true`; if `false`, entry scan is skipped entirely
4. **Symbol blacklist** — symbols in the user blacklist are skipped
5. **Per-symbol cooldown** — `BOT_ENTRY_COOLDOWN_HOURS` (default: 4h) since last bot action on this symbol
6. **Grade filter** — signal grade must meet or exceed `BOT_MIN_GRADE` (default: B)
7. **Tier 4 R:R check** — R:R ≥ 2:1 and price validity (run by `SignalHierarchy.passes_risk_checks()`)
8. **Portfolio heat budget** — `current_heat < BOT_MAX_PORTFOLIO_HEAT_PCT`; per-trade risk derived from remaining budget
9. **Minimum risk floor** — implied position risk must be ≥ `BOT_MIN_RISK_PCT` (default: 0.25%); prevents economically meaningless positions
10. **Position size** — risk-sized quantity must be ≥ 1 share
11. **Max positions safety rail** — `len(open_positions) + entries_this_cycle < MAX_POSITIONS` (hard ceiling, not primary constraint)
12. **can_trade()** — no existing position, no pending orders for this symbol

---

## Operational Controls

### Dashboard Bot page

- **Pause / Resume** — sets `halted` in `bot_state.json`
- **Autonomous toggle** — enables/disables entry scanning (position review always runs)
- **Watchlist selector** — changes `BOT_SCAN_WATCHLIST`; takes effect on next scan cycle
- **Action log** — last 50 entries from `bot_actions.json`

### Symbol blacklist

Symbols in the blacklist are skipped by both entry scan and position review. Toggle via the dashboard or `POST /api/bot/blacklist/{symbol}`.

### Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `BOT_AUTONOMOUS` | `false` | Enable/disable entry scanning |
| `BOT_SCAN_WATCHLIST` | `sp500_top100` | Watchlist to scan |
| `BOT_MIN_GRADE` | `B` | Minimum signal grade for auto-entry |
| `BOT_MAX_DAILY_LOSS_PCT` | `2.0` | Daily loss % that triggers circuit breaker |
| `BOT_ENTRY_COOLDOWN_HOURS` | `4` | Hours between entries on the same symbol |
| `BOT_REVIEW_TIMEFRAMES` | `long,swing,short` | Which timeframes to review positions on |
| `max_positions` | `12` | Hard safety rail on position count (not primary constraint) |
| `risk_percentage` | `1.0` | Fallback per-trade risk % (used by manual trades; bot uses heat system) |
| `BOT_MAX_PORTFOLIO_HEAT_PCT` | `5.0` | Total open risk budget as % of equity (primary position constraint) |
| `BOT_MAX_RISK_PER_TRADE_PCT` | `1.0` | Per-trade risk cap as % of equity |
| `BOT_MIN_RISK_PCT` | `0.25` | Minimum implied risk % to accept an entry |
