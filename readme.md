# Alpaca Trading Dashboard

A personal trading dashboard built on FastAPI, Alpaca Markets, and a local or cloud AI model. Provides multi-timeframe technical analysis, a batch market scanner, autonomous position management, and one-click bracket order execution — all from a clean web UI.

---

## Features

- **Multi-timeframe analysis** — Long (1D/1yr), Swing (1H/3mo), and Short (15m/1mo) tiers streamed live via SSE
- **Four-tier signal hierarchy** — Regime → Setup quality → AI confirmation → Risk management
- **Market scanner** — Batch scan S&P 500, NASDAQ 100, Sector ETFs, Russell 2000, All Crypto, or a custom list; Long/Swing/Short timeframes; results stream in real time
- **Inline scanner results** — Click any row to expand Tier 1/2 reasoning without leaving the page
- **Saved custom lists** — Name and persist custom symbol lists in the browser
- **Bracket order execution** — Entry, stop, and target submitted as a single bracket order
- **Autonomous bot** — APScheduler-driven loop that scans for entries, reviews open positions, trails stops, and exits on regime flip — all without manual intervention
- **Position review** — Per-position momentum health check (regime, RSI divergence, RVOL, EMA9 slope) with TRAIL_STOP / RAISE_TARGET / PARTIAL_PROFIT / EXIT verdicts
- **AI commentary** — Ollama (local) or DeepSeek (cloud) or any OpenAI-compatible endpoint; configured from the UI
- **Named credential profiles** — Multiple Alpaca accounts (paper/live) stored encrypted in SQLite; switch with one click
- **Unified Settings panel** — Gear icon opens a three-tab modal: Alpaca accounts, AI provider, App settings
- **Candlestick chart** — LightweightCharts with SMA 20/50/200 overlays; auto-selects lookback by interval
- **Backtester** — Replay the signal hierarchy against historical bars to evaluate strategy performance
- **Zero-config startup** — No `.env` needed; everything is configured from the dashboard on first run

---

## Project Structure

```
alpacaApp/
├── app.py                  # FastAPI app factory — mounts routes/, middleware, scheduler
├── bot.py                  # TradingBot — market data, account, order execution
├── config.py               # Runtime config (reads from settings store, falls back to env)
├── ai_strategy.py          # AIStrategyGenerator (OpenAI-compatible client)
├── scheduler.py            # APScheduler — registers all background bot jobs
│
├── routes/                 # FastAPI APIRouter modules (one domain per file)
│   ├── pages.py            # HTML page routes (/, /scanner, /positions, /bot, etc.)
│   ├── account.py          # /api/account, /api/orders, /api/trades, /api/profiles, /api/market_data
│   ├── positions.py        # /api/positions/*, /api/execute_trade
│   ├── scanner.py          # /api/scan/*, /api/market/*, /api/analyze/*, /api/config
│   ├── bot_control.py      # /api/bot/*, /api/alerts
│   ├── settings.py         # /api/settings, /api/settings/test-ai
│   ├── backtest.py         # /api/backtest
│   └── watchlist.py        # /api/watchlist/*
│
├── analysis/
│   ├── indicators.py       # Technical indicators (EMA, RSI, BB, ATR, RVOL, RS)
│   ├── asset_type.py       # Equity vs crypto classification
│   └── patterns.py         # Chart pattern detection
│
├── strategies/
│   ├── momentum.py         # SignalHierarchy — four-tier rule-based strategy
│   ├── position_manager.py # PositionReviewer — momentum health + exit/trail verdicts
│   └── auto_manager.py     # Autonomous loop — entry scan, position review, circuit breakers
│
├── screeners/
│   ├── market_scanner.py   # Batch scanner with SSE streaming + setup scorer
│   └── symbol_lists.py     # Pre-built symbol lists (S&P 500, NASDAQ, sectors, crypto)
│
├── backtester/
│   └── engine.py           # Historical backtest engine
│
├── data/
│   ├── bar_fetcher.py      # Batch bar fetching helpers (equity + crypto)
│   ├── high_quality_setups.py  # High-quality setup alert log (score ≥ 85)
│   ├── profile_store.py    # SQLite + Fernet encrypted Alpaca credential profiles
│   ├── settings_store.py   # SQLite key/value app settings (AI, risk, password, blacklist)
│   └── watchlist.py        # Persistent watchlist store
│
├── docs/
│   ├── bot_behavior.md     # Autonomous bot phase logic and decision tree
│   └── strategy.md         # Signal hierarchy and scoring documentation
│
├── templates/              # Jinja2 HTML templates (DaisyUI + Tailwind)
└── static/
    └── js/
        ├── analysis.js     # Multi-timeframe analysis UI + SSE consumer
        ├── chart.js        # LightweightCharts initialization
        ├── scanner.js      # Scanner SSE consumer, saved lists, inline expand
        ├── positions.js    # Positions page — cards, mini charts, position review, bot status
        ├── account.js      # Account summary widget
        ├── market.js       # Market overview page
        ├── backtest.js     # Backtester UI
        ├── bot.js          # Autonomous bot control panel
        ├── config.js       # Strategy parameter editor
        ├── settings.js     # Settings modal — profiles, AI, app settings
        └── watchlist.js    # Watchlist page
```

---

## Setup

### 1. Clone and create a virtual environment

> **Strongly recommended:** use a virtual environment. Installing into the global Python environment can cause dependency conflicts.

```bash
git clone https://github.com/trevadelman/dashboard-analysis
cd dashboard-analysis
python3 -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate         # Windows
pip install -r requirements.txt
```

### 2. Run the dashboard

```bash
python app.py
```

Open [http://localhost:5001](http://localhost:5001). No login required on first run — the app starts passwordless.

### 3. Configure from the UI

Click the **gear icon** (top-right) to open Settings:

| Tab | What you configure |
|-----|--------------------|
| **Alpaca** | Add/switch named credential profiles (paper or live) |
| **AI** | Choose Ollama (local) or DeepSeek (cloud), or enter any OpenAI-compatible endpoint |
| **App** | Max positions, risk % per trade, optional dashboard password |

All settings are stored encrypted in `data/profiles.db` and persist across restarts.

### 4. Start Ollama (optional — for local AI commentary)

```bash
ollama serve
ollama pull gemma3:4b-it-qat
```

Then open Settings → AI → click the **Ollama** card → Save.

---

## Alpaca Credentials

Credentials are stored encrypted in `data/profiles.db` (Fernet/AES-128). You can:

- Add them via **Settings → Alpaca** in the dashboard UI
- Or set `ALPACA_PUBLIC` / `ALPACA_SECRET` in a `.env` file — they'll be auto-imported as a default profile on first run

The free Alpaca tier (IEX feed) is fully supported — all market data requests use `feed="iex"`.

---

## AI Providers

Two presets are built in:

| Provider | Endpoint | Notes |
|----------|----------|-------|
| **Ollama** | `http://localhost:11434/v1` | Local, no API key needed, default model `gemma3:4b-it-qat` |
| **DeepSeek** | `https://api.deepseek.com/v1` | Cloud, requires API key, model `deepseek-chat` |

Any OpenAI-compatible endpoint works — just enter the URL, key, and model manually.

---

## Strategy Overview

The `SignalHierarchy` runs four tiers in sequence. All four tiers run unconditionally and stream results to the UI as they complete.

| Tier | Name | What it checks |
|------|------|----------------|
| 1 | Market Regime | EMA21/50 alignment + RSI trend + ATR% volatility guard |
| 2 | Setup Quality | BB width percentile (compression) + local ATR contraction + RS vs SPY/BTC + EMA9 trigger + breakout level + RVOL + RSI momentum |
| 3 | AI Confirmation | Optional — reviews the setup and assigns confidence; informational only, never gates the signal |
| 4 | Risk Management | R:R ratio check, stop/target price validity, liquidity filter |

**Tier 1 design:** EMA21/50 alignment is the macro regime filter. EMA9 is a fast trigger signal and belongs in Tier 2 — including it in Tier 1 would block the best compression setups, which form while EMA9 is flat.

**Tier 2 design:** Compression is defined by BB width percentile (primary gate). Local ATR contraction (atr_14/atr_50 ratio) supplements BB without the long-memory distortion of a 252-bar ATR percentile rank. RS vs SPY is required for equities; RS vs BTC is used for crypto alts; BTC/USD itself bypasses the RS gate (it is the benchmark).

Each timeframe has its own parameter set (thresholds, multipliers, lookback windows) tuned for that interval:

| Parameter | Long (Daily) | Swing (Hourly) | Short (15-min) |
|-----------|-------------|----------------|----------------|
| EMA regime | 21/50 | 21/50 | 21/50 |
| RSI regime min | 45 | 48 | 50 |
| BB width max | 50th pct | 50th pct | 55th pct |
| ATR contraction max | 0.85 | 0.85 | 0.90 |
| RVOL min | 1.1x | 1.3x | 1.5x |
| Compression lookback | 5 bars | 15 bars | 20 bars |
| Min R:R | 2.0 | 1.5 | 1.2 |

---

## Scanner

The market scanner runs Tier 1 + Tier 2 only (no AI) for speed. Results stream via SSE as each symbol completes. Every symbol receives a score (0–100) and grade (A/B/C/D) regardless of whether it produced a signal, surfacing "almost there" setups.

**Score weighting (100 pts total):**
- RS vs SPY/BTC — 35 pts (primary alpha driver)
- RVOL — 25 pts (breakout confirmation)
- BB compression — 20 pts (setup quality)
- RSI momentum — 10 pts (directional confirmation)
- ATR contraction — 10 pts (volatility squeeze)

**Timeframes:**
- **Long (Daily)** — 1D bars, 1-year lookback. Finds multi-week compression setups.
- **Swing (Hourly)** — 1H bars, 90-day lookback. Finds stocks compressing on the hourly within a confirmed daily uptrend.
- **Short (15-min)** — 15m bars, 30-day lookback. Finds intraday compression breakouts.

**Symbol lists:** S&P 500 Top 100, NASDAQ 100 Top 50, Sector ETFs, Russell 2000 Sample, All Crypto (Alpaca), Full Universe (cached Alpaca ticker list), or a custom comma-separated list. Custom lists can be named and saved in the browser.

Click any result row to expand the Tier 1/2 reasoning inline. Use "Open in Dashboard" to navigate to the full multi-timeframe analysis for that symbol.

---

## Autonomous Bot

The bot runs on a background APScheduler and requires `BOT_AUTONOMOUS=true` in config (or set via the dashboard). It operates in two modes depending on the configured watchlist:

**Equity mode (market hours only):**

| Job | Schedule | What it does |
|-----|----------|--------------|
| `market_open_snapshot` | 9:30 ET Mon–Fri | Snapshot equity for daily loss circuit breaker |
| `long_review` | 9:35 ET Mon–Fri | Review long (daily) positions |
| `long_scan` | 9:40 ET Mon–Fri | Scan for long signals |
| `swing_review` | Every 60 min | Review swing (hourly) positions |
| `swing_scan` | Every 60 min | Scan for swing signals |
| `short_review` | Every 15 min | Review short (15-min) positions |
| `short_scan` | Every 15 min | Scan for short signals |
| `exit_poller` | Every 5 min | Detect closed positions (stop/target hit) |

**Crypto mode (24/7):** Same jobs, but daily jobs fire at midnight ET and all jobs run 7 days a week.

**Circuit breakers:**
- Manual halt via the dashboard Pause button
- Max daily loss % (configurable) — auto-halts and logs when breached

**Position review verdicts:**

| Verdict | Trigger | Action |
|---------|---------|--------|
| `HOLD` | All momentum checks pass | No action |
| `TRAIL_STOP` | RVOL fading or EMA9 slope flattening | Adjust stop order |
| `RAISE_TARGET` | Price at/above current target with momentum intact | Raise take-profit, trail stop |
| `PARTIAL_PROFIT` | RSI divergence detected (swing pivot method) | Suggest partial exit, trail stop |
| `EXIT` | Regime flipped to NO_TRADE or price crossed back below EMA9 | Close position |

---

## Troubleshooting

### `pip install` fails with dependency conflicts

**Always use a virtual environment** (see Setup step 1). Installing globally makes these conflicts much harder to untangle.

If you still hit conflicts, try upgrading pip first:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### App starts but Alpaca data doesn't load

Open Settings → Alpaca and add your credentials. The app starts without them intentionally — account/positions/orders will be empty until a profile is activated.

### AI analysis returns nothing / times out

Make sure Ollama is running (`ollama serve`) and the model is pulled (`ollama pull gemma3:4b-it-qat`). You can verify the connection in Settings → AI → Test.

### Bot is not placing orders

1. Check that `BOT_AUTONOMOUS` is enabled in Settings → App
2. Check the bot is not halted (Positions page → Autonomous Bot section)
3. Check the circuit breaker hasn't tripped (daily loss limit)
4. Check `data/bot_actions.json` for the last logged action and reason

---

## Development

```bash
# Run with auto-reload
python app.py
```
