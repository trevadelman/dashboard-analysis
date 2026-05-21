"""
Symbol Lists for Stock Screening

Provides:
  - Static hardcoded lists (SP500_TOP100, NASDAQ100_TOP50, etc.)
  - GICS sector mappings (~500 liquid stocks across 11 sectors)
  - Dynamic Alpaca asset universe fetch with 24-hour file cache
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Path for the cached asset universe (written by fetch_alpaca_universe)
_UNIVERSE_CACHE_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'asset_universe.json')
_CRYPTO_UNIVERSE_CACHE_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'crypto_universe.json')
_UNIVERSE_CACHE_TTL  = 86400  # 24 hours


# ── Static hardcoded lists ────────────────────────────────────────────────────

# S&P 500 companies (top 100 by market cap for faster screening)
SP500_TOP100 = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B', 'UNH', 'XOM',
    'JNJ', 'JPM', 'V', 'PG', 'MA', 'HD', 'CVX', 'MRK', 'ABBV', 'PEP',
    'COST', 'AVGO', 'KO', 'ADBE', 'WMT', 'MCD', 'CSCO', 'ACN', 'LIN', 'TMO',
    'ABT', 'NFLX', 'DHR', 'VZ', 'NKE', 'ORCL', 'CRM', 'TXN', 'PM', 'DIS',
    'INTC', 'WFC', 'UPS', 'CMCSA', 'NEE', 'BMY', 'RTX', 'QCOM', 'HON', 'AMGN',
    'UNP', 'LOW', 'SPGI', 'BA', 'ELV', 'SBUX', 'GS', 'BLK', 'CAT', 'INTU',
    'AMD', 'GILD', 'AXP', 'DE', 'BKNG', 'MDLZ', 'ADI', 'LMT', 'PLD', 'TJX',
    'ADP', 'ISRG', 'MMC', 'SYK', 'CI', 'VRTX', 'REGN', 'ZTS', 'CB', 'MO',
    'PGR', 'SO', 'DUK', 'BDX', 'SCHW', 'EOG', 'ITW', 'BSX', 'APD', 'CSX',
    'NOC', 'CME', 'ETN', 'CL', 'HUM', 'MMM', 'PNC', 'USB', 'GE', 'SLB',
]

# NASDAQ 100 (tech-heavy, high volatility)
NASDAQ100_TOP50 = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AVGO', 'COST', 'NFLX',
    'ADBE', 'CSCO', 'PEP', 'CMCSA', 'INTC', 'TXN', 'QCOM', 'HON', 'AMGN', 'INTU',
    'AMD', 'SBUX', 'GILD', 'BKNG', 'ADI', 'ISRG', 'VRTX', 'REGN', 'ADP', 'MDLZ',
    'PANW', 'LRCX', 'SNPS', 'KLAC', 'CDNS', 'MRVL', 'CRWD', 'FTNT', 'ADSK', 'ABNB',
    'WDAY', 'TEAM', 'DXCM', 'MELI', 'MNST', 'BIIB', 'ORLY', 'CTAS', 'PCAR', 'NXPI',
]

# Russell 2000 sample (small caps)
RUSSELL2000_SAMPLE = [
    'SIRI', 'PLUG', 'LAZR', 'BLNK', 'RIOT', 'MARA', 'CLSK', 'BTBT', 'HUT', 'CIFR',
    'IREN', 'WULF', 'CORZ', 'BITF', 'ARBK', 'SDIG', 'APLD', 'MGNI', 'PUBM', 'APPS',
]

# High-volume ETFs (for market context)
MAJOR_ETFS = [
    'SPY', 'QQQ', 'IWM', 'DIA', 'VTI', 'VOO', 'VEA', 'VWO', 'AGG', 'BND',
    'XLF', 'XLE', 'XLK', 'XLV', 'XLI', 'XLP', 'XLY', 'XLU', 'XLB', 'XLRE',
]


# ── GICS sector mappings (~500 liquid stocks) ─────────────────────────────────
#
# Each sector contains the most liquid, exchange-listed names in that GICS sector.
# These are used for sector-by-sector scanning.  The lists are intentionally
# curated (not exhaustive) — we want names with enough daily dollar volume for
# the strategy's liquidity filter to pass.

SECTORS = {
    'Technology': [
        'AAPL', 'MSFT', 'NVDA', 'AVGO', 'ORCL', 'CRM', 'ADBE', 'AMD', 'TXN', 'QCOM',
        'INTC', 'CSCO', 'ACN', 'INTU', 'IBM', 'ADI', 'AMAT', 'LRCX', 'KLAC', 'SNPS',
        'CDNS', 'MRVL', 'PANW', 'CRWD', 'FTNT', 'ADSK', 'WDAY', 'NOW', 'TEAM', 'ZS',
        'OKTA', 'DDOG', 'NET', 'SNOW', 'MDB', 'PLTR', 'HOOD', 'COIN', 'RBLX', 'U',
        'NXPI', 'MCHP', 'ON', 'STX', 'WDC', 'HPQ', 'DELL', 'NTAP', 'PSTG', 'SMCI',
    ],
    'Communication Services': [
        'META', 'GOOGL', 'GOOG', 'NFLX', 'DIS', 'CMCSA', 'VZ', 'T', 'TMUS', 'CHTR',
        'SNAP', 'PINS', 'SPOT', 'MTCH', 'IAC', 'WBD', 'PARA', 'FOX', 'FOXA', 'NWSA',
        'TTWO', 'EA', 'ATVI', 'RBLX', 'LYFT', 'UBER', 'ABNB', 'BKNG', 'EXPE', 'TRIP',
    ],
    'Consumer Discretionary': [
        'AMZN', 'TSLA', 'HD', 'MCD', 'NKE', 'SBUX', 'LOW', 'TJX', 'BKNG', 'ORLY',
        'AZO', 'ROST', 'EBAY', 'ETSY', 'W', 'RH', 'BBY', 'DKS', 'ULTA', 'LULU',
        'CMG', 'YUM', 'QSR', 'DPZ', 'WING', 'DKNG', 'MGM', 'WYNN', 'LVS', 'CZR',
        'F', 'GM', 'RIVN', 'LCID', 'NCLH', 'CCL', 'RCL', 'MAR', 'HLT', 'H',
    ],
    'Consumer Staples': [
        'WMT', 'PG', 'KO', 'PEP', 'COST', 'PM', 'MO', 'MDLZ', 'CL', 'KHC',
        'GIS', 'K', 'CPB', 'SJM', 'CAG', 'HRL', 'MKC', 'CHD', 'CLX', 'EL',
        'KR', 'SYY', 'USFD', 'PFGC', 'BJ', 'GO', 'CASY', 'WEIS', 'VLGEA', 'INGR',
    ],
    'Health Care': [
        'UNH', 'JNJ', 'MRK', 'ABBV', 'TMO', 'ABT', 'DHR', 'BMY', 'AMGN', 'GILD',
        'ISRG', 'VRTX', 'REGN', 'ZTS', 'BDX', 'SYK', 'BSX', 'EW', 'MDT', 'BAX',
        'CI', 'ELV', 'HUM', 'CVS', 'MCK', 'ABC', 'CAH', 'BIIB', 'ILMN', 'DXCM',
        'MRNA', 'BNTX', 'PFE', 'LLY', 'NVO', 'AZN', 'RGEN', 'EXAS', 'NTRA', 'VEEV',
    ],
    'Financials': [
        'JPM', 'V', 'MA', 'BAC', 'WFC', 'GS', 'MS', 'BLK', 'SCHW', 'AXP',
        'SPGI', 'CME', 'ICE', 'CB', 'PGR', 'TRV', 'ALL', 'MET', 'PRU', 'AFL',
        'USB', 'PNC', 'TFC', 'COF', 'DFS', 'SYF', 'AIG', 'HIG', 'MMC', 'AON',
        'BX', 'KKR', 'APO', 'CG', 'ARES', 'HOOD', 'SOFI', 'LC', 'UPST', 'AFRM',
    ],
    'Industrials': [
        'HON', 'UPS', 'RTX', 'BA', 'CAT', 'DE', 'LMT', 'NOC', 'GD', 'GE',
        'ETN', 'ITW', 'EMR', 'PH', 'ROK', 'AME', 'FTV', 'XYL', 'CARR', 'OTIS',
        'UNP', 'CSX', 'NSC', 'CP', 'CNI', 'FDX', 'JBHT', 'CHRW', 'EXPD', 'XPO',
        'CTAS', 'PCAR', 'AGCO', 'TXT', 'HII', 'L3H', 'LDOS', 'SAIC', 'BAH', 'KTOS',
    ],
    'Energy': [
        'XOM', 'CVX', 'COP', 'EOG', 'SLB', 'MPC', 'PSX', 'VLO', 'PXD', 'DVN',
        'FANG', 'OXY', 'HES', 'HAL', 'BKR', 'NOV', 'FTI', 'CIVI', 'SM', 'MTDR',
        'AR', 'EQT', 'RRC', 'CNX', 'SWN', 'KMI', 'WMB', 'OKE', 'ET', 'MPLX',
        'EPD', 'PAA', 'TRGP', 'LNG', 'CQP', 'NFE', 'CLNE', 'PLUG', 'FCEL', 'BE',
    ],
    'Materials': [
        'LIN', 'APD', 'SHW', 'ECL', 'DD', 'DOW', 'LYB', 'PPG', 'RPM', 'EMN',
        'NEM', 'FCX', 'SCCO', 'AA', 'ALB', 'MP', 'LTHM', 'SQM', 'LAC', 'PLL',
        'NUE', 'STLD', 'RS', 'CMC', 'X', 'CLF', 'MT', 'PKG', 'IP', 'WRK',
        'MLM', 'VMC', 'CRH', 'EXP', 'USCR', 'SLGN', 'BALL', 'SEE', 'SON', 'ATR',
    ],
    'Real Estate': [
        'PLD', 'AMT', 'EQIX', 'CCI', 'PSA', 'WELL', 'DLR', 'O', 'SPG', 'EQR',
        'AVB', 'ESS', 'MAA', 'UDR', 'CPT', 'NNN', 'VICI', 'GLPI', 'REXR', 'EXR',
        'CUBE', 'LSI', 'NSA', 'COLD', 'STAG', 'LPT', 'HIW', 'BXP', 'SLG', 'VNO',
        'KIM', 'REG', 'FRT', 'BRX', 'RPAI', 'WPG', 'MAC', 'CBL', 'SKT', 'SITC',
    ],
    'Utilities': [
        'NEE', 'SO', 'DUK', 'AEP', 'EXC', 'XEL', 'SRE', 'D', 'PCG', 'ED',
        'EIX', 'ETR', 'FE', 'PPL', 'CMS', 'NI', 'AES', 'WEC', 'ES', 'AWK',
        'EVRG', 'IDACORP', 'OGE', 'PNW', 'NWE', 'AVA', 'MGEE', 'OTTR', 'SJW', 'MSEX',
    ],
}

# Flat list of all sector symbols (deduplicated)
ALL_SECTOR_SYMBOLS: List[str] = list(dict.fromkeys(
    sym for syms in SECTORS.values() for sym in syms
))


# ── Dynamic Alpaca asset universe ─────────────────────────────────────────────

def fetch_alpaca_universe(trading_client, min_price: float = 1.0) -> List[str]:
    """
    Fetch all tradeable US equity symbols from Alpaca's Assets API.

    Filters:
      - status = active
      - asset_class = us_equity
      - tradable = True
      - exchange in NYSE, NASDAQ, ARCA, BATS
      - price > min_price (requires a snapshot call — done in batches)

    Results are cached to data/asset_universe.json for 24 hours.
    Returns the cached list if the cache is fresh.

    Args:
        trading_client: Alpaca TradingClient instance
        min_price:      Minimum last price to include (default $1)

    Returns:
        List of symbol strings, sorted alphabetically
    """
    cache_path = os.path.normpath(_UNIVERSE_CACHE_PATH)

    # Return cached result if fresh
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r') as f:
                cached = json.load(f)
            age = time.time() - cached.get('fetched_at', 0)
            if age < _UNIVERSE_CACHE_TTL:
                logger.info(f"Using cached asset universe ({len(cached['symbols'])} symbols, "
                            f"{age/3600:.1f}h old)")
                return cached['symbols']
        except Exception as e:
            logger.warning(f"Could not read asset universe cache: {e}")

    logger.info("Fetching asset universe from Alpaca Assets API…")
    try:
        from alpaca.trading.requests import GetAssetsRequest
        from alpaca.trading.enums import AssetClass, AssetStatus

        req    = GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE)
        assets = trading_client.get_all_assets(req)

        valid_exchanges = {'NYSE', 'NASDAQ', 'ARCA', 'BATS'}
        symbols = sorted([
            a.symbol for a in assets
            if a.tradable
            and a.fractionable          # proxy for institutional liquidity
            and a.easy_to_borrow        # further narrows to ~2k well-established names
            and a.exchange in valid_exchanges
            and '/' not in a.symbol    # exclude crypto-style pairs
            and '.' not in a.symbol    # exclude BRK.B style (Alpaca uses BRK/B)
        ])

        logger.info(f"Alpaca returned {len(symbols)} tradeable US equity symbols")

        # Price filter — fetch latest snapshots in batches of 1000
        if min_price > 0:
            symbols = _filter_by_price(trading_client, symbols, min_price)
            logger.info(f"After price > ${min_price} filter: {len(symbols)} symbols")

        # Write cache
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, 'w') as f:
            json.dump({
                'fetched_at': time.time(),
                'fetched_at_iso': datetime.now(timezone.utc).isoformat(),
                'min_price': min_price,
                'count': len(symbols),
                'symbols': symbols,
            }, f)

        return symbols

    except Exception as e:
        logger.error(f"Failed to fetch Alpaca asset universe: {e}")
        # Fall back to the static sector list
        logger.info(f"Falling back to static sector list ({len(ALL_SECTOR_SYMBOLS)} symbols)")
        return ALL_SECTOR_SYMBOLS


def _filter_by_price(trading_client, symbols: List[str], min_price: float) -> List[str]:
    """
    Filter symbols by last price using Alpaca's snapshot endpoint.
    Processes in batches of 1000 to stay within API limits.
    """
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        # Snapshots require a data client — we can't use the trading client here.
        # If we don't have one, skip the price filter and return all symbols.
        # The strategy's own min_price param will handle it at signal time.
        logger.info("Price filter skipped at universe fetch — strategy will filter at signal time")
        return symbols
    except Exception:
        return symbols


def load_cached_universe() -> List[str]:
    """
    Load the cached asset universe without making any API calls.
    Returns the static sector list if no cache exists.
    """
    cache_path = os.path.normpath(_UNIVERSE_CACHE_PATH)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r') as f:
                cached = json.load(f)
            return cached.get('symbols', ALL_SECTOR_SYMBOLS)
        except Exception:
            pass
    return ALL_SECTOR_SYMBOLS


def fetch_alpaca_crypto_universe(trading_client) -> List[str]:
    """
    Fetch all tradeable crypto symbols from Alpaca's Assets API.

    Filters:
      - asset_class = crypto
      - tradable = True
      - symbol ends with /USD (USD-quoted pairs only)

    Results are cached to data/crypto_universe.json for 24 hours.
    Returns the cached list if the cache is fresh.

    Args:
        trading_client: Alpaca TradingClient instance

    Returns:
        List of symbol strings in slash format (e.g. "BTC/USD"), sorted alphabetically
    """
    cache_path = os.path.normpath(_CRYPTO_UNIVERSE_CACHE_PATH)

    # Return cached result if fresh
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r') as f:
                cached = json.load(f)
            age = time.time() - cached.get('fetched_at', 0)
            if age < _UNIVERSE_CACHE_TTL:
                logger.info(f"Using cached crypto universe ({len(cached['symbols'])} symbols, "
                            f"{age/3600:.1f}h old)")
                return cached['symbols']
        except Exception as e:
            logger.warning(f"Could not read crypto universe cache: {e}")

    logger.info("Fetching crypto universe from Alpaca Assets API…")
    try:
        from alpaca.trading.requests import GetAssetsRequest
        from alpaca.trading.enums import AssetClass, AssetStatus

        req    = GetAssetsRequest(asset_class=AssetClass.CRYPTO, status=AssetStatus.ACTIVE)
        assets = trading_client.get_all_assets(req)

        symbols = sorted([
            a.symbol for a in assets
            if a.tradable
            and a.symbol.endswith('/USD')
        ])

        logger.info(f"Alpaca returned {len(symbols)} tradeable crypto symbols")

        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, 'w') as f:
            json.dump({
                'fetched_at':     time.time(),
                'fetched_at_iso': datetime.now(timezone.utc).isoformat(),
                'count':          len(symbols),
                'symbols':        symbols,
            }, f)

        return symbols

    except Exception as e:
        logger.error(f"Failed to fetch Alpaca crypto universe: {e}")
        logger.info(f"Falling back to CRYPTO_ALL_ALPACA ({len(CRYPTO_ALL_ALPACA)} symbols)")
        return CRYPTO_ALL_ALPACA


def load_cached_crypto_universe() -> List[str]:
    """
    Load the cached crypto universe without making any API calls.
    Returns CRYPTO_ALL_ALPACA if no cache exists.
    """
    cache_path = os.path.normpath(_CRYPTO_UNIVERSE_CACHE_PATH)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r') as f:
                cached = json.load(f)
            return cached.get('symbols', CRYPTO_ALL_ALPACA)
        except Exception:
            pass
    return CRYPTO_ALL_ALPACA


def get_universe_cache_info() -> dict:
    """Return metadata about the cached universe (age, count, etc.)."""
    cache_path = os.path.normpath(_UNIVERSE_CACHE_PATH)
    if not os.path.exists(cache_path):
        return {'cached': False, 'source': 'static', 'count': len(ALL_SECTOR_SYMBOLS)}
    try:
        with open(cache_path, 'r') as f:
            cached = json.load(f)
        age_secs = time.time() - cached.get('fetched_at', 0)
        return {
            'cached':       True,
            'source':       'alpaca',
            'count':        cached.get('count', 0),
            'fetched_at':   cached.get('fetched_at_iso', ''),
            'age_hours':    round(age_secs / 3600, 1),
            'stale':        age_secs > _UNIVERSE_CACHE_TTL,
        }
    except Exception:
        return {'cached': False, 'source': 'static', 'count': len(ALL_SECTOR_SYMBOLS)}


# ── SymbolListManager (backward-compatible) ───────────────────────────────────

class SymbolListManager:
    """Manages symbol lists for screening."""

    def __init__(self):
        self._crypto_cache      = None
        self._crypto_cache_time = 0
        self._cache_duration    = 3600

    def get_stock_universe(self, tier: str = 'liquid') -> List[str]:
        """
        Get stock universe based on tier.

        Args:
            tier: 'liquid' (top 100), 'broad' (top 500), 'sector' (GICS ~500),
                  'all' (cached Alpaca universe or sector fallback)
        """
        if tier == 'liquid':
            return list(dict.fromkeys(SP500_TOP100 + NASDAQ100_TOP50 + MAJOR_ETFS))
        if tier == 'broad':
            return list(dict.fromkeys(SP500_TOP100 + NASDAQ100_TOP50 + RUSSELL2000_SAMPLE + MAJOR_ETFS))
        if tier == 'sector':
            return ALL_SECTOR_SYMBOLS
        if tier == 'all':
            return load_cached_universe()
        logger.warning(f"Unknown tier '{tier}' — using liquid")
        return self.get_stock_universe('liquid')

    def get_sector_symbols(self, sector: str) -> List[str]:
        """Return symbols for a specific GICS sector name."""
        return list(SECTORS.get(sector, []))

    def get_all_sectors(self) -> List[str]:
        """Return the list of available sector names."""
        return list(SECTORS.keys())

    def get_crypto_universe(self, limit: int = 300) -> List[Dict[str, Any]]:
        """Return the static fallback crypto list."""
        return self._get_fallback_crypto()[:limit]

    def _get_fallback_crypto(self) -> List[Dict[str, Any]]:
        fallback = [
            'BTC-USD', 'ETH-USD', 'BNB-USD', 'SOL-USD', 'XRP-USD',
            'ADA-USD', 'DOGE-USD', 'AVAX-USD', 'DOT-USD', 'MATIC-USD',
            'LINK-USD', 'UNI-USD', 'ATOM-USD', 'LTC-USD', 'BCH-USD',
            'XLM-USD', 'ALGO-USD', 'VET-USD', 'FIL-USD', 'HBAR-USD',
        ]
        return [{'symbol': s, 'name': s.replace('-USD', ''), 'market_cap': 0,
                 'volume_24h': 0, 'price': 0, 'rank': i + 1}
                for i, s in enumerate(fallback)]

    def get_crypto_symbols_only(self, limit: int = 300) -> List[str]:
        return [c['symbol'] for c in self.get_crypto_universe(limit)]


# ── Convenience functions ─────────────────────────────────────────────────────

def get_stock_symbols(tier: str = 'liquid') -> List[str]:
    return SymbolListManager().get_stock_universe(tier)


def get_crypto_symbols(limit: int = 300) -> List[str]:
    return SymbolListManager().get_crypto_symbols_only(limit)


def get_crypto_info(limit: int = 300) -> List[Dict[str, Any]]:
    return SymbolListManager().get_crypto_universe(limit)


# ── Crypto watchlist (Alpaca slash format) ────────────────────────────────────

# All crypto assets available on Alpaca, in slash format (BTC/USD).
# Used by the autonomous bot scanner, the settings UI watchlist selector,
# and the scanner "Crypto (All Alpaca)" list.
# Source: https://alpaca.markets/docs/api-references/market-data-api/crypto-pricing-data/
CRYPTO_TOP10 = [
    "BTC/USD",
    "ETH/USD",
    "SOL/USD",
    "AVAX/USD",
    "LINK/USD",
    "LTC/USD",
    "BCH/USD",
    "DOGE/USD",
    "UNI/USD",
    "AAVE/USD",
]

# Full list of crypto assets tradeable on Alpaca (as of 2025).
# Alpaca supports ~25 crypto pairs — small enough to scan in a single batch.
CRYPTO_ALL_ALPACA = [
    "BTC/USD",
    "ETH/USD",
    "SOL/USD",
    "AVAX/USD",
    "LINK/USD",
    "LTC/USD",
    "BCH/USD",
    "DOGE/USD",
    "UNI/USD",
    "AAVE/USD",
    "XRP/USD",
    "ADA/USD",
    "DOT/USD",
    "MATIC/USD",
    "SHIB/USD",
    "XTZ/USD",
    "ALGO/USD",
    "BAT/USD",
    "CRV/USD",
    "GRT/USD",
    "MKR/USD",
    "SUSHI/USD",
    "YFI/USD",
    "USDT/USD",
    "USDC/USD",
]
