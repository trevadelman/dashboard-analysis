"""
Asset type classification — single decision point for equity vs crypto routing.

All data client selection, RS benchmark choice, and market hours gating
flows from classify_symbol(). Nothing else in the codebase makes this
determination independently.
"""

from enum import Enum


class AssetType(Enum):
    EQUITY = "equity"
    CRYPTO = "crypto"


# Canonical crypto symbols traded on Alpaca (slash format)
CRYPTO_SYMBOLS = {
    "BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD",
    "LTC/USD", "BCH/USD", "DOGE/USD", "SHIB/USD", "UNI/USD",
    "AAVE/USD", "DOT/USD", "MATIC/USD", "XRP/USD", "ADA/USD",
}


def classify_symbol(symbol: str) -> AssetType:
    """
    Return the AssetType for a given symbol string.

    Crypto is identified by:
      - A "/" in the symbol (e.g. "BTC/USD")
      - Membership in the known CRYPTO_SYMBOLS set

    Everything else is treated as an equity.
    """
    s = symbol.upper().strip()
    if "/" in s or s in CRYPTO_SYMBOLS:
        return AssetType.CRYPTO
    return AssetType.EQUITY


def is_crypto(symbol: str) -> bool:
    """Convenience wrapper — True if the symbol is a crypto asset."""
    return classify_symbol(symbol) == AssetType.CRYPTO
