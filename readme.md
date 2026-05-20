# Alpaca Trading Dashboard

A personal trading dashboard built on FastAPI, Alpaca Markets, and a local or cloud AI model. Provides multi-timeframe technical analysis, a batch market scanner, and one-click bracket order execution — all from a clean web UI with no `.env` file required.

---

## Features

- **Multi-timeframe analysis** — Long (1D/1yr), Swing (1H/3mo), and Short (15m/1mo) tiers streamed live via SSE
- **Four-tier signal hierarchy** — Regime → Setup quality → AI confirmation → Risk management
- **Market scanner** — Batch scan S&P 500, NASDAQ 100, Sector ETFs, Russell 2000, or a custom list; Long (daily) or Swing (hourly) timeframe; results stream in real time
- **Inline scanner results** — Click any row to expand Tier 1/2 reasoning without leaving the page
- **Saved custom lists** — Name and persist custom symbol lists in the browser
- **Bracket order execution** — Entry, stop, and target submitted as a single bracket order
- **AI commentary** — Ollama (local) or DeepSeek (cloud) or any OpenAI-compatible endpoint; configured from the UI
- **Named credential profiles** — Multiple Alpaca accounts (paper/live) stored encrypted in SQLite; switch with one click
- **Unified Settings panel** — Gear icon opens a three-tab modal: Alpaca accounts, AI provider, App settings
- **Candlestick chart** — LightweightCharts with SMA 20/50/200 overlays; auto-selects lookback by interval (1D → 2 years)
- **Zero-config startup** — No `.env` needed; everything is configured from the dashboard on first run

---

## Project Structure

```
dashboard-analysis/
├── app.py              # FastAPI entry point — routes, SSE endpoints, auth
├── bot.py              # TradingBot — market data, account, order execution
├── config.py           # Runtime config (reads from settings store, falls back to env)
├── ai_strategy.py      # AIStrategyGenerator (OpenAI-compatible client)
│
├── analysis/
│   ├── indicators.py   # Technical indicators (EMA, RSI, BB, ATR, RVOL, RS)
│   └── patterns.py     # Chart pattern detection
│
├── strategies/
│   └── momentum.py     # SignalHierarchy — four-tier rule-based strategy
│
├── screeners/
│   ├── market_scanner.py  # Batch scanner with SSE streaming
│   └── symbol_lists.py    # Pre-built symbol lists
│
├── data/
│   ├── profile_store.py   # SQLite + Fernet encrypted Alpaca credential profiles
│   └── settings_store.py  # SQLite key/value app settings (AI, risk, password)
│
├── templates/          # Jinja2 HTML templates (DaisyUI + Tailwind)
├── static/
│   └── js/
│       ├── analysis.js # Multi-timeframe analysis UI + SSE consumer
│       ├── chart.js    # LightweightCharts initialization
│       ├── scanner.js  # Scanner SSE consumer, saved lists, inline expand
│       ├── account.js  # Account summary, positions, trade history
│       ├── config.js   # Strategy parameter editor
│       └── settings.js # Settings modal — profiles, AI, app settings
│
└── requirements.txt
```

---

## Setup

### 1. Clone and create a virtual environment

> **Strongly recommended:** use a virtual environment. Installing into the global Python environment can cause dependency conflicts, especially with `alpaca-trade-api`'s pinned packages.

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

The `SignalHierarchy` runs four tiers in sequence:

| Tier | Name | What it checks |
|------|------|----------------|
| 1 | Market Regime | EMA alignment (9/21/50), RSI trend, positive ROC |
| 2 | Setup Quality | BB width percentile (compression), RS vs SPY, RVOL |
| 3 | AI Confirmation | Optional — reviews the setup and assigns confidence |
| 4 | Risk Management | ATR-based stop, minimum R:R ratio, price range filters |

Each timeframe has its own parameter set (thresholds, multipliers) tuned for that interval.

---

## Scanner

The market scanner runs Tier 1 + Tier 2 only (no AI) for speed. Results stream via SSE as each symbol completes.

**Timeframes:**
- **Long (Daily)** — 1D bars, 1-year lookback. Finds multi-week compression setups.
- **Swing (Hourly)** — 1H bars, 90-day lookback. Finds stocks in confirmed daily uptrends compressing on the hourly.

**Symbol lists:** S&P 500 Top 100, NASDAQ 100 Top 50, Sector ETFs, Russell 2000 Sample, or a custom comma-separated list. Custom lists can be named and saved in the browser.

Click any result row to expand the Tier 1/2 reasoning inline. Use "Open in Dashboard" to navigate to the full multi-timeframe analysis for that symbol.

---

## Troubleshooting

### `pip install` fails with dependency conflicts

`alpaca-trade-api` pins `urllib3<2`, which can conflict with other packages — especially if installing into a global Python environment. **Always use a virtual environment** (see Setup step 1).

If you still hit conflicts, try installing in two steps:

```bash
pip install -r requirements.txt --no-deps
pip install alpaca-trade-api
```

Or install `alpaca-trade-api` last:

```bash
pip install fastapi uvicorn openai cryptography pandas numpy yfinance python-dotenv jinja2 python-multipart itsdangerous starlette
pip install alpaca-trade-api
```

### App starts but Alpaca data doesn't load

Open Settings → Alpaca and add your credentials. The app starts without them intentionally — account/positions/orders will be empty until a profile is activated.

### AI analysis returns nothing / times out

Make sure Ollama is running (`ollama serve`) and the model is pulled (`ollama pull gemma3:4b-it-qat`). You can verify the connection in Settings → AI → Test.

---

## Development

```bash
# Run with auto-reload
uvicorn app:app --host 0.0.0.0 --port 5001 --reload
```
