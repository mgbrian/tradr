# contracts.py
from ib_insync import Stock, Option


def stock_contract(symbol, exchange='SMART', currency='USD'):
    """Create a stock contract.

    Args:
        symbol: str - Stock ticker symbol.
        exchange: str - Exchange to route orders through. Default is SMART.
        currency: str - Trading currency. Default is USD.

    Returns:
        ib_insync.contract.Stock - The created stock contract.
    """
    return Stock(symbol, exchange, currency)


def option_contract(symbol, last_trade_date, strike, right, exchange='SMART', currency='USD'):
    """Create an option contract.

    Args:
        symbol: str - Underlying stock ticker symbol.
        last_trade_date: str - Expiration date in YYYYMMDD format.
        strike: float - Strike price.
        right: str - 'C' for call or 'P' for put.
        exchange: str - Exchange to route orders through. Default is SMART.
        currency: str - Trading currency. Default is USD.

    Returns:
        ib_insync.contract.Option - The created option contract.
    """
    return Option(symbol, last_trade_date, strike, right, exchange, currency)
