# Strategy Notes

Living document. Updated as the strategy evolves. This is the "why" behind the code — design decisions, things tried and discarded, tuning notes, and position management philosophy.

---

## Core Philosophy

The strategy is a **compression breakout with relative strength confirmation**. The thesis:

1. Stocks that compress (volatility contracts) while maintaining relative strength vs the market are coiling energy — they're being accumulated, not distributed.
2. When that compression resolves with volume expansion (RVOL), the move tends to be directional and sustained.
3. We want to be in the move early (limit entry at the breakout level) with a structural stop (below the compression zone), not chasing after the move has already happened.

The four-tier hierarchy enforces this discipline mechanically so emotion doesn't override it.

---

## Tier 1 — Market Regime

**What it checks:** EMA21/50 alignment + RSI trend + ATR% volatility guard.

**Design decisions:**

- **EMA21/50 only, not EMA9.** EMA9 is a fast trigger signal — it flattens during compression, which is exactly when the best setups form. Including EMA9 in Tier 1 would block the setups we're trying to find. EMA9 belongs in Tier 2 as a breakout trigger.

- **ROC was removed.** A ROC > -1.0 threshold was so loose it passed on virtually every setup, adding no filtering value while creating a maintenance hazard. EMA21/50 alignment + RSI is sufficient for regime detection.

- **ATR% as a volatility guard, not a compression gate.** High ATR% (>10% of price) means the stock is already in a volatile expansion phase — not a compression. This is a regime filter, not a setup quality filter. ATR compression belongs in Tier 2.

- **RSI floor is timeframe-dependent.** Daily bars are slower to respond, so the RSI floor is lower (45) than hourly (48) or 15-min (50). A daily RSI of 45 is still constructive; a 15-min RSI of 45 on a stock we want to buy is a yellow flag.

---

## Tier 2 — Setup Quality

**What it checks:** BB width percentile + local ATR contraction + RS vs SPY/BTC + EMA9 trigger + breakout level + RVOL + RSI momentum.

**Design decisions:**

- **BB width percentile is the primary compression gate.** Bollinger Band width in the bottom 50th percentile of its own history means the stock is as compressed as it's been in the lookback window. This is the cleanest single measure of compression.

- **Local ATR contraction supplements BB.** `atr_14 / atr_50` ratio < 0.85 means short-term volatility is contracting relative to medium-term. This catches setups where BB width is moderate but volatility is actively shrinking. We use a ratio, not a percentile rank, to avoid the long-memory distortion of a 252-bar ATR percentile rank (which would still "remember" a volatility spike from a year ago).

- **RS vs SPY is required for equities, RS vs BTC for crypto alts.** If a stock can't outperform the benchmark during a compression, it's not being accumulated — it's just drifting. We fail closed if benchmark data is unavailable rather than silently degrading the edge.

- **BTC/USD bypasses the RS gate.** BTC is the benchmark for crypto. Requiring BTC to outperform itself is circular.

- **EMA9 as a trigger, not a regime gate.** EMA9 slope turning positive (price crossing above EMA9) is the trigger that the compression is resolving. This is a Tier 2 signal, not a Tier 1 regime filter.

- **Compression lookback is timeframe-specific.** Daily = 5 bars (1 trading week), hourly = 15 bars (~2 days), 15-min = 20 bars (~5 hours). A fixed 5-bar window was too narrow for intraday timeframes — the compression zone spans many more bars, so the stop ended up inside the zone rather than below it, causing premature exits.

---

## Tier 3 — AI Confirmation

**What it checks:** Optional AI review of the setup. Assigns a confidence score.

**Design decisions:**

- **Informational only — never gates the signal.** The AI can add color and catch things the deterministic rules miss, but it cannot veto a signal that passed Tiers 1, 2, and 4. The deterministic rules are the edge; the AI is commentary.

- **Used for consolidated multi-timeframe commentary.** After all three timeframes complete, the AI gets the combined audit trail and produces a single narrative. This is more useful than three separate per-timeframe comments.

---

## Tier 4 — Risk Management

**What it checks:** R:R ratio, stop/target price validity, stop direction.

**Design decisions:**

- **Minimum R:R is timeframe-dependent.** Long (daily) = 2.0, swing (hourly) = 1.5, short (15-min) = 1.2. Intraday setups have tighter spreads and faster resolution, so a lower R:R threshold is acceptable. Daily setups should have room to run.

- **Tier 4 runs on auto-entries too.** The scanner only runs Tier 1+2 for speed. Before the bot submits any bracket order, it runs `passes_risk_checks()` on the signal. A bad stop placement (e.g. stop above entry on a buy) would be caught here.

---

## Stop and Target Placement

**Entry:** Limit at the breakout level + 0.10× ATR buffer. The buffer prevents the order from sitting exactly at resistance where it might not fill.

**Stop:** Structural invalidation — the low of the compression zone (for longs) minus 0.25× ATR. The compression zone is defined by `compression_lookback` bars. The 0.25× ATR buffer prevents a single wick from stopping us out.

**Target:** 2R from the limit entry (hardcoded). The target is always 2× the risk amount from entry, regardless of timeframe. This ensures the minimum R:R is always met at signal generation time.

---

## Scanner Scoring

Every symbol gets a score (0–100) and grade (A/B/C/D) regardless of whether it produced a signal. This surfaces "almost there" setups for manual review.

| Component | Weight | Rationale |
|-----------|--------|-----------|
| RS vs SPY/BTC | 35 pts | Primary alpha driver — if it's not outperforming, the setup has no edge |
| RVOL | 25 pts | Breakout confirmation — volume expansion validates the move |
| BB compression | 20 pts | Setup quality — tighter compression = more coiled energy |
| RSI momentum | 10 pts | Directional confirmation |
| ATR contraction | 10 pts | Volatility squeeze — supplements BB |

**Grade thresholds:** A = 80–100, B = 60–79, C = 40–59, D = 0–39.

**Bot minimum grade:** Default `B`. D-grade signals are barely above the minimum thresholds — the RS and RVOL confirmation is weak. Taking D-grade signals autonomously is not worth the risk. Configurable via `BOT_MIN_GRADE`.

---

## Position Management

### Verdict logic (PositionReviewer)

| Verdict | Trigger | Bot action |
|---------|---------|------------|
| `HOLD` | All momentum checks pass | No action |
| `TRAIL_STOP` | RVOL fading or EMA9 slope flattening | `adjust_orders()` — raise stop |
| `PARTIAL_PROFIT` | RSI divergence (swing pivot method) | `adjust_orders()` — trail stop to lock in profit (Alpaca doesn't support partial closes on bracket orders) |
| `RAISE_TARGET` | Price at/above current target with momentum intact | `adjust_orders()` — raise take-profit, trail stop |
| `EXIT` | Regime flipped to NO_TRADE or price crossed back below EMA9 | `close_position()` |

### Why PARTIAL_PROFIT trails the stop instead of closing shares

Alpaca bracket orders don't support partial closes — you'd have to cancel the bracket, close partial shares at market, and re-enter a new bracket for the remaining shares. That's three API calls with race conditions between them. Trailing the stop to the current price achieves the same economic outcome (locking in profit) without the complexity.

### Cooldown

After any bot action on a symbol (entry or review action), a 1-hour cooldown prevents the bot from acting on the same symbol again immediately. This prevents thrashing on volatile symbols where the review might flip between HOLD and TRAIL_STOP on consecutive cycles.

---

## Automation Gates (in order)

Every auto-entry candidate passes through these gates in sequence. The first failure skips the entry and logs `ENTRY_SKIPPED`.

1. **Market hours** — equity watchlists only trade Mon–Fri 09:30–16:00 ET
2. **Circuit breakers** — manual halt or daily loss limit breached
3. **BOT_AUTONOMOUS flag** — must be enabled in settings
4. **Symbol blacklist** — user can blacklist individual symbols from bot activity
5. **New-signal deduplication** — signal must be new this cycle (not seen last scan)
6. **Per-symbol cooldown** — configurable, default 24h between entries on same symbol
7. **Grade filter** — signal grade must meet `BOT_MIN_GRADE` (default B)
8. **Tier 4 R:R check** — `passes_risk_checks()` validates R:R and price validity
9. **Position size check** — risk-sized quantity must be ≥ 1 share
10. **Pending-entries guard** — `open_positions + entries_this_cycle < max_positions`
11. **`can_trade()`** — no existing position, no pending orders for this symbol

---

## Things Tried and Discarded

| What | Why removed |
|------|-------------|
| EMA9 in Tier 1 regime filter | Flattens during compression — blocks the best setups |
| ROC > -1.0 in Tier 1 | So loose it passed everything — no filtering value |
| 252-bar ATR percentile rank | Long memory distortion — a spike from a year ago still affects the rank |
| `max(1, ...)` floor in `calculate_position_size` | Silently overrides risk parameters — better to skip the entry than take a 1-share position that bypasses sizing |
| Fixed 5-bar compression lookback for all timeframes | Too narrow for intraday — stop ended up inside the compression zone |

---

## Tuning Notes

### When to tighten Tier 1 RSI floor
If the bot is entering too many setups in choppy/sideways markets, raise `rsi_regime_min`. The current defaults (45/48/50) are intentionally loose — the RS filter in Tier 2 does most of the heavy lifting.

### When to tighten the grade filter
If win rate is low, raise `BOT_MIN_GRADE` to `A`. This will reduce trade frequency significantly but should improve quality. A-grade signals have strong RS (>28 pts out of 35) and elevated RVOL (>20 pts out of 25).

### When to widen BB compression threshold
If the scanner is returning very few results, the `bb_width_pct_max` threshold may be too tight for current market conditions (low-volatility environments compress everything). Raising from 50th to 60th percentile will surface more setups.

### Crypto-specific
Crypto alts are more volatile than equities — the ATR contraction threshold (`atr_contraction_max`) may need to be loosened (e.g. 0.90 instead of 0.85) for crypto alts to avoid filtering out valid setups. BTC/USD itself tends to have cleaner compression patterns than alts.
