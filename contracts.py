"""Contract Factory for IBKR Trading System.

Provides helpers to build Stock and Option contracts with validation.
"""

from ib_insync import Stock, Option
from enum import Enum


class Currency(Enum):
    USD = 'USD'
    EUR = 'EUR'
    GBP = 'GBP'


class Exchange(Enum):
    SMART = 'SMART'
    ISLAND = 'ISLAND'
    NYSE = 'NYSE'
    NASDAQ = 'NASDAQ'


def create_stock_contract(symbol, currency=Currency.USD, exchange=Exchange.SMART):
    """Create a Stock contract.

    Args:
        symbol: str - Stock ticker symbol.
        currency: Currency - Currency enum (default USD).
        exchange: Exchange - Exchange enum (default SMART).

    Returns:
        Stock - IBKR Stock contract instance.

    Raises:
        ValueError - If invalid enum values are passed for currency/exchange.
    """
    if not isinstance(currency, Currency):
        raise ValueError(f"currency must be an instance of Currency enum, got {currency}")

    if not isinstance(exchange, Exchange):
        raise ValueError(f"exchange must be an instance of Exchange enum, got {exchange}")

    contract = Stock(symbol, exchange.value, currency.value)
    return contract


def create_option_contract(symbol, expiry, strike, right, currency=Currency.USD, exchange=Exchange.SMART):
    """Create an Option contract.

    Args:
        symbol: str - Underlying stock ticker symbol.
        expiry: str - Expiry date in YYYYMMDD format.
        strike: float - Strike price.
        right: str - 'C' for Call or 'P' for Put.
        currency: Currency - Currency enum (default USD).
        exchange: Exchange - Exchange enum (default SMART).

    Returns:
        Option - IBKR Option contract instance.

    Raises:
        ValueError - If right is not 'C' or 'P', or currency/exchange enums invalid.
    """
    if right not in ('C', 'P'):
        raise ValueError("right must be 'C' for Call or 'P' for Put")

    if not isinstance(currency, Currency):
        raise ValueError(f"currency must be an instance of Currency enum, got {currency}")

    if not isinstance(exchange, Exchange):
        raise ValueError(f"exchange must be an instance of Exchange enum, got {exchange}")

    contract = Option(symbol, expiry, strike, right, exchange.value, currency.value)
    return contract
