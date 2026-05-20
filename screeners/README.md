# Universe Screener System

Automated stock and crypto screening for swing trading opportunities.

## 📋 Overview

The screener system scans thousands of stocks and crypto to find high-quality swing trade setups based on technical analysis. It uses a two-tier approach:

- **Stock Screener**: Conservative filters for liquid, technically sound stocks
- **Crypto Screener**: Aggressive filters for wider opportunity search across top 300 crypto

## 🎯 Features

### Stock Screener
- **Universe**: 140+ liquid stocks (S&P 500, NASDAQ 100, major ETFs)
- **Filters**: Price, volume, volatility, trend strength
- **Scoring**: 0-100 based on trend, momentum, MACD, volume, volatility
- **Conservative**: Requires price above 50-day MA, moderate volatility (1.5-8% ATR)

### Crypto Screener
- **Universe**: Top 300 crypto by market cap (via CoinGecko API)
- **Filters**: Market cap, 24h volume, price, volatility
- **Scoring**: 0-100 based on rank, trend, momentum, MACD, volume, volatility
- **Aggressive**: Higher volatility tolerance (3-25% ATR), wider search

## 🚀 Usage

### Run Both Screeners
```bash
python -m screeners.run_screener
```

### Stock Screener Only
```bash
python -m screeners.run_screener --stocks-only --stock-max 50
```

### Crypto Screener Only
```bash
python -m screeners.run_screener --crypto-only --crypto-limit 300 --crypto-max 100
```

### Custom Parameters
```bash
python -m screeners.run_screener \
  --stock-tier liquid \
  --stock-max 50 \
  --crypto-limit 300 \
  --crypto-max 100
```

## 📊 Output Files

### screened_stocks.json
```json
{
  "screened_at": "2025-11-15T15:05:25.249257",
  "count": 10,
  "stocks": [
    {
      "symbol": "BDX",
      "price": 193.04,
      "volume": 1966239,
      "atr_pct": 2.62,
      "rsi": 59.75,
      "macd_hist": 1.75,
      "sma50": 186.66,
      "score": 90.0,
      "screened_at": "2025-11-15T15:03:57.499772"
    }
  ]
}
```

### screened_crypto.json
```json
{
  "screened_at": "2025-11-15T15:10:30.123456",
  "count": 50,
  "crypto": [
    {
      "symbol": "BTC-USD",
      "name": "Bitcoin",
      "price": 95556.69,
      "market_cap": 1904834098168,
      "volume_24h": 56533528178,
      "rank": 1,
      "atr_pct": 4.57,
      "rsi": 28.76,
      "score": 85.0
    }
  ]
}
```

## 🔧 Configuration

### Stock Screener Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `tier` | `'liquid'` | Universe tier: 'liquid' (140 stocks) or 'broad' (200+ stocks) |
| `min_price` | `10.0` | Minimum stock price |
| `max_price` | `500.0` | Maximum stock price |
| `min_volume` | `1_000_000` | Minimum daily volume |
| `min_atr_pct` | `1.5` | Minimum volatility (ATR%) |
| `max_atr_pct` | `8.0` | Maximum volatility (ATR%) |
| `require_trend` | `True` | Require price above 50-day MA |
| `max_results` | `50` | Maximum results to return |

### Crypto Screener Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `limit` | `300` | Number of top crypto to scan |
| `min_price` | `0.01` | Minimum price |
| `max_price` | `100_000.0` | Maximum price |
| `min_volume_usd` | `500_000.0` | Minimum 24h volume in USD |
| `min_atr_pct` | `3.0` | Minimum volatility (ATR%) |
| `max_atr_pct` | `25.0` | Maximum volatility (ATR%) |
| `min_market_cap` | `50_000_000.0` | Minimum market cap |
| `max_results` | `100` | Maximum results to return |

## 📈 Scoring System

### Stock Scoring (0-100)

1. **Trend Strength (30 points)**
   - Strong uptrend (price > SMA50 > SMA200): 30 pts
   - Uptrend (price > SMA50): 20 pts
   - Weak uptrend (price > SMA200): 10 pts

2. **Momentum (25 points)**
   - RSI 55-70 (bullish, not overbought): 25 pts
   - RSI 50-55 (neutral to bullish): 15 pts
   - RSI 45-50 (neutral): 10 pts

3. **MACD (20 points)**
   - Bullish crossover (line > signal, hist > 0): 20 pts
   - Bullish (line > signal): 10 pts

4. **Volume (15 points)**
   - Recent volume > 1.5x average: 15 pts
   - Recent volume > 1.2x average: 10 pts
   - Recent volume > average: 5 pts

5. **Volatility (10 points)**
   - ATR 2-5% (ideal for swing trading): 10 pts
   - ATR 1.5-2% or 5-6% (acceptable): 5 pts

### Crypto Scoring (0-100)

1. **Market Cap Rank (15 points)**
   - Top 10: 15 pts
   - Top 50: 12 pts
   - Top 100: 8 pts
   - Top 200: 5 pts

2. **Trend Strength (25 points)**
   - Strong uptrend: 25 pts
   - Uptrend: 18 pts
   - Weak uptrend: 10 pts
   - Strong downtrend (short opportunity): 15 pts

3. **Momentum (25 points)**
   - RSI 55-75 (bullish): 25 pts
   - RSI 50-55 (neutral to bullish): 18 pts
   - RSI 45-50 (neutral): 12 pts
   - RSI 25-35 (oversold bounce): 20 pts

4. **MACD (15 points)**
   - Bullish crossover: 15 pts
   - Bullish: 10 pts
   - Bearish (short opportunity): 8 pts

5. **Volume (10 points)**
   - Volume/Market Cap > 0.5: 10 pts
   - Volume/Market Cap > 0.3: 8 pts
   - Volume/Market Cap > 0.1: 5 pts

6. **Volatility (10 points)**
   - ATR 5-15% (ideal for crypto): 10 pts
   - ATR 3-5% or 15-20% (acceptable): 6 pts

## 🔄 Automation

### Daily Screening (Recommended)
```bash
# Add to crontab for daily 6am screening
0 6 * * * cd /path/to/alpacaApp && python -m screeners.run_screener
```

### Hourly Screening (Aggressive)
```bash
# Add to crontab for hourly screening during market hours
0 9-16 * * 1-5 cd /path/to/alpacaApp && python -m screeners.run_screener
```

## 📦 Dependencies

- `yfinance`: Stock and crypto price data
- `pycoingecko`: Crypto market cap and volume data
- `pandas`: Data manipulation
- `analysis.indicators`: Technical indicator calculations

## 🎓 Example Results

### High-Scoring Stocks (Score 90)
- **BDX**: $193.04, RSI 59.75, ATR 2.62%, Strong uptrend
- **CSCO**: $78.00, RSI 69.71, ATR 2.55%, Bullish momentum

### Medium-Scoring Stocks (Score 75-80)
- **MNST**: $71.31, RSI 55.62, ATR 2.64%, Neutral to bullish
- **MRK**: $92.92, RSI 65.41, ATR 2.77%, Good momentum

## 🔍 Integration with Trading Bot

The screener outputs are designed to be consumed by the trading bot:

```python
import json
from screeners import StockScreener, CryptoScreener

# Load screened results
with open('screened_stocks.json') as f:
    stocks = json.load(f)['stocks']

with open('screened_crypto.json') as f:
    crypto = json.load(f)['crypto']

# Get top candidates
top_stocks = [s['symbol'] for s in stocks[:20]]
top_crypto = [c['symbol'] for c in crypto[:10]]

# Feed to trading bot
watchlist = top_stocks + top_crypto
```

## 📝 Notes

- **Rate Limiting**: The screener includes automatic rate limiting to avoid API restrictions
- **Caching**: CoinGecko data is cached for 1 hour to reduce API calls
- **Error Handling**: Failed symbol lookups are logged but don't stop the screening process
- **Conservative vs Aggressive**: Stocks use stricter filters (higher quality), crypto uses wider filters (more opportunities)

## 🐛 Troubleshooting

### "Too Many Requests" Error
- Increase sleep time in screener code
- Reduce universe size
- Run less frequently

### No Results Found
- Check filter parameters (may be too strict)
- Verify market conditions (choppy markets = fewer signals)
- Review log files for errors

### CoinGecko API Errors
- Check internet connection
- Verify pycoingecko is installed
- Falls back to hardcoded top 20 crypto if API fails

## 📚 Further Reading

- [Technical Analysis Basics](https://www.investopedia.com/terms/t/technicalanalysis.asp)
- [Swing Trading Strategies](https://www.investopedia.com/articles/trading/06/swingtrading.asp)
- [Risk Management](https://www.investopedia.com/terms/r/riskmanagement.asp)
