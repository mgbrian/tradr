"""Order Manager

Handles the creation and submission of orders for stocks and options. Provides
high-level functions for common trade operations (buy, sell, short, cover) and
delegates contract creation to the Contract Factory.

DONE:
- Market orders.
- Direct execution.

TODO:
- Limit, stop, and advanced order types.
- Order status
- Cancel orders.
- Retry and error handling logic.
- DB for logging orders and fills.
- Batch order submission.
"""

from ib_insync import MarketOrder
from contracts import OptionType, create_stock_contract, create_option_contract


class OrderManager:
    """Handles order creation and submission to Interactive Brokers."""

    def __init__(self, ib):
        """Initialize the OrderManager.

        Args:
            ib: IB - An active ib_insync.IB instance for submitting orders.
        """
        self.ib = ib

    def buy_stock(self, symbol, quantity):
        """Submit a market order to buy a stock.

        Args:
            symbol: str - Stock ticker e.g. "AAPL".
            quantity: int - Number of shares to buy.

        Returns:
            Order - The submitted IBKR order object.
        """
        # TODO: Should we validate quantity here or is that handled at  a lower level?
        contract = create_stock_contract(symbol)
        order = MarketOrder('BUY', quantity)
        trade = self.ib.placeOrder(contract, order)

        return trade

    def sell_stock(self, symbol, quantity):
        """Submit a market order to sell a stock.

        Args:
            symbol: str - Stock ticker e.g. "AAPL".
            quantity: int - Number of shares to sell.

        Returns:
            Order - The submitted IBKR order object.
        """
        contract = create_stock_contract(symbol)
        order = MarketOrder('SELL', quantity)
        trade = self.ib.placeOrder(contract, order)

        return trade

    def short_stock(self, symbol, quantity):
        """Submit a market order to short a stock.

        Args:
            symbol: str - Stock ticker symbol.
            quantity: int - Number of shares to short.

        Returns:
            Order - The submitted IBKR order object.
        """
        contract = create_stock_contract(symbol)
        order = MarketOrder('SELL', quantity)
        # TODO: Check shortability before placing order.
        trade = self.ib.placeOrder(contract, order)
        return trade

    def buy_to_cover(self, symbol, quantity):
        """Submit a market order to buy-to-cover a short position.

        Args:
            symbol: str - Stock ticker symbol.
            quantity: int - Number of shares to cover.

        Returns:
            Order - The submitted IBKR order object.
        """
        contract = create_stock_contract(symbol)
        order = MarketOrder('BUY', quantity)
        trade = self.ib.placeOrder(contract, order)
        return trade

    def buy_option(self, symbol, expiry, strike, right, quantity):
        """Submit a market order to buy an option.

        Args:
            symbol: str - Underlying stock ticker symbol.
            expiry: str - Option expiry date in YYYYMMDD format.
            strike: float - Option strike price.
            right: str - 'C' for Call, 'P' for Put.
            quantity: int - Number of option contracts to buy.

        Returns:
            Order - The submitted IBKR order object.
        """
        if right not in ('C', 'P'):
            raise ValueError("right must be 'C' for Call or 'P' for Put")

        contract = create_option_contract(symbol, expiry, strike, OptionType(right))
        order = MarketOrder('BUY', quantity)
        trade = self.ib.placeOrder(contract, order)
        return trade

    def sell_option(self, symbol, expiry, strike, right, quantity):
        """Submit a market order to sell an option.

        Args:
            symbol: str - Underlying stock ticker symbol.
            expiry: str - Option expiry date in YYYYMMDD format.
            strike: float - Option strike price.
            right: str - 'C' for Call, 'P' for Put.
            quantity: int - Number of option contracts to sell.

        Returns:
            Order - The submitted IBKR order object.
        """
        if right not in ('C', 'P'):
            raise ValueError("right must be 'C' for Call or 'P' for Put")

        contract = create_option_contract(symbol, expiry, strike, OptionType(right))
        order = MarketOrder('SELL', quantity)
        trade = self.ib.placeOrder(contract, order)
        return trade

    def get_order_status(self):
        """
        TODO: Implement.
        """
        pass

    def cancel_order(self):
        """
        TODO: Implement.
        """
        pass
