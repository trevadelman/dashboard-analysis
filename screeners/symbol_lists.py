"""
Symbol Lists for Stock and Crypto Screening
Provides pre-built lists and dynamic fetching from various sources
"""

import logging
from typing import List, Dict, Any
from pycoingecko import CoinGeckoAPI
import time

logger = logging.getLogger(__name__)


# ===== STOCK SYMBOL LISTS =====

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
    'NOC', 'CME', 'ETN', 'CL', 'HUM', 'MMM', 'PNC', 'USB', 'GE', 'SLB'
]

# NASDAQ 100 (tech-heavy, high volatility)
NASDAQ100_TOP50 = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AVGO', 'COST', 'NFLX',
    'ADBE', 'CSCO', 'PEP', 'CMCSA', 'INTC', 'TXN', 'QCOM', 'HON', 'AMGN', 'INTU',
    'AMD', 'SBUX', 'GILD', 'BKNG', 'ADI', 'ISRG', 'VRTX', 'REGN', 'ADP', 'MDLZ',
    'PANW', 'LRCX', 'SNPS', 'KLAC', 'CDNS', 'MRVL', 'CRWD', 'FTNT', 'ADSK', 'ABNB',
    'WDAY', 'TEAM', 'DXCM', 'MELI', 'MNST', 'BIIB', 'ORLY', 'CTAS', 'PCAR', 'NXPI'
]

# Russell 2000 (small caps - higher risk/reward)
RUSSELL2000_SAMPLE = [
    'SIRI', 'PLUG', 'LAZR', 'BLNK', 'RIOT', 'MARA', 'CLSK', 'BTBT', 'HUT', 'CIFR',
    'IREN', 'WULF', 'CORZ', 'BITF', 'ARBK', 'SDIG', 'APLD', 'MGNI', 'PUBM', 'APPS'
]

# High-volume ETFs (for market context)
MAJOR_ETFS = [
    'SPY', 'QQQ', 'IWM', 'DIA', 'VTI', 'VOO', 'VEA', 'VWO', 'AGG', 'BND',
    'XLF', 'XLE', 'XLK', 'XLV', 'XLI', 'XLP', 'XLY', 'XLU', 'XLB', 'XLRE'
]


class SymbolListManager:
    """Manages symbol lists for screening."""

    def __init__(self):
        """Initialize symbol list manager."""
        self.cg = CoinGeckoAPI()
        self._crypto_cache = None
        self._crypto_cache_time = 0
        self._cache_duration = 3600  # 1 hour

    def get_stock_universe(self, tier: str = 'liquid') -> List[str]:
        """
        Get stock universe based on tier.

        Args:
            tier: 'liquid' (top 100), 'broad' (top 500), 'all' (all available)

        Returns:
            List of stock symbols
        """
        if tier == 'liquid':
            # Most liquid stocks - safest for swing trading
            return list(set(SP500_TOP100 + NASDAQ100_TOP50 + MAJOR_ETFS))

        elif tier == 'broad':
            # Broader universe - more opportunities
            return list(set(SP500_TOP100 + NASDAQ100_TOP50 + RUSSELL2000_SAMPLE + MAJOR_ETFS))

        elif tier == 'all':
            # Full universe - requires more compute
            # In production, this would fetch from a comprehensive source
            logger.warning("Full universe not implemented - using broad tier")
            return self.get_stock_universe('broad')

        else:
            logger.warning(f"Unknown tier '{tier}' - using liquid")
            return self.get_stock_universe('liquid')

    def get_crypto_universe(self, limit: int = 300) -> List[Dict[str, Any]]:
        """
        Get crypto universe from CoinGecko.

        Args:
            limit: Maximum number of coins to return (default: 300)

        Returns:
            List of dicts with coin info: {symbol, name, market_cap, volume}
        """
        # Check cache
        if self._crypto_cache and (time.time() - self._crypto_cache_time < self._cache_duration):
            logger.debug("Using cached crypto list")
            return self._crypto_cache[:limit]

        try:
            logger.info(f"Fetching top {limit} crypto from CoinGecko...")

            # Get coins by market cap
            coins = self.cg.get_coins_markets(
                vs_currency='usd',
                order='market_cap_desc',
                per_page=min(limit, 250),  # CoinGecko limit per page
                page=1,
                sparkline=False
            )

            # Format for our use
            crypto_list = []
            for coin in coins:
                # Yahoo Finance format: SYMBOL-USD
                yahoo_symbol = f"{coin['symbol'].upper()}-USD"

                crypto_list.append({
                    'symbol': yahoo_symbol,
                    'name': coin['name'],
                    'market_cap': coin.get('market_cap', 0),
                    'volume_24h': coin.get('total_volume', 0),
                    'price': coin.get('current_price', 0),
                    'rank': coin.get('market_cap_rank', 999)
                })

            # Cache results
            self._crypto_cache = crypto_list
            self._crypto_cache_time = time.time()

            logger.info(f"Retrieved {len(crypto_list)} crypto symbols")
            return crypto_list

        except Exception as e:
            logger.error(f"Error fetching crypto list: {e}")
            # Return fallback list
            return self._get_fallback_crypto()

    def _get_fallback_crypto(self) -> List[Dict[str, Any]]:
        """Fallback crypto list if CoinGecko fails."""
        fallback = [
            'BTC-USD', 'ETH-USD', 'BNB-USD', 'SOL-USD', 'XRP-USD',
            'ADA-USD', 'DOGE-USD', 'AVAX-USD', 'DOT-USD', 'MATIC-USD',
            'LINK-USD', 'UNI-USD', 'ATOM-USD', 'LTC-USD', 'BCH-USD',
            'XLM-USD', 'ALGO-USD', 'VET-USD', 'FIL-USD', 'HBAR-USD'
        ]

        return [{'symbol': s, 'name': s.replace('-USD', ''), 'market_cap': 0,
                 'volume_24h': 0, 'price': 0, 'rank': i+1}
                for i, s in enumerate(fallback)]

    def get_crypto_symbols_only(self, limit: int = 300) -> List[str]:
        """
        Get just the crypto symbols (for backward compatibility).

        Args:
            limit: Maximum number of symbols

        Returns:
            List of Yahoo Finance crypto symbols (e.g., 'BTC-USD')
        """
        crypto_list = self.get_crypto_universe(limit)
        return [c['symbol'] for c in crypto_list]


# Convenience functions
def get_stock_symbols(tier: str = 'liquid') -> List[str]:
    """Get stock symbols."""
    manager = SymbolListManager()
    return manager.get_stock_universe(tier)


def get_crypto_symbols(limit: int = 300) -> List[str]:
    """Get crypto symbols."""
    manager = SymbolListManager()
    return manager.get_crypto_symbols_only(limit)


def get_crypto_info(limit: int = 300) -> List[Dict[str, Any]]:
    """Get crypto info with metadata."""
    manager = SymbolListManager()
    return manager.get_crypto_universe(limit)


# Example usage
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    manager = SymbolListManager()

    print("\n=== STOCK UNIVERSE ===")
    liquid_stocks = manager.get_stock_universe('liquid')
    print(f"Liquid stocks: {len(liquid_stocks)}")
    print(f"Sample: {liquid_stocks[:10]}")

    print("\n=== CRYPTO UNIVERSE ===")
    crypto_info = manager.get_crypto_universe(50)
    print(f"Top 50 crypto: {len(crypto_info)}")
    for i, coin in enumerate(crypto_info[:10], 1):
        print(f"{i}. {coin['symbol']:12} - {coin['name']:20} - "
              f"MCap: ${coin['market_cap']:,.0f} - "
              f"Vol: ${coin['volume_24h']:,.0f}")
