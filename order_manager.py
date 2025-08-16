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
import asyncio

from ib_async import MarketOrder
from contracts import OptionType, create_stock_contract, create_option_contract


class OrderManager:
    """Handles order creation and submission to Interactive Brokers."""

    def __init__(self, ib, default_timeout=10.0):
        """Initialize the OrderManager.

        Args:
            ib: IB - An active ib_async.IB instance for submitting orders.
        """
        self._default_timeout = float(default_timeout)
        self.ib = ib

    def _place_on_ib_loop(self, contract, order, timeout=None):
        """Place an order on IB's asyncio loop (thread-safe).

        Schedules a small coroutine onto IB's own event loop/thread and waits
        for the result from any calling thread (e.g., gRPC worker).

        Args:
            contract: Contract - ib_async Contract to place.
            order: Order - ib_async Order to place.
            timeout: float (Optional) - Seconds to wait. Uses default if None.

        Returns:
            Trade - ib_async Trade object.

        Raises:
            RuntimeError - If IB loop is unavailable or the call times out.
            Exception - Any broker error raised by ib_async.
        """
        loop = getattr(self.ib, 'loop', None)
        if loop is None:
            raise RuntimeError("IB event loop not pinned; did IBSession.connect() run?")

        async def _coro():
            return self.ib.placeOrder(contract, order)

        fut = asyncio.run_coroutine_threadsafe(_coro(), loop)
        return fut.result(timeout or self._default_timeout)

    def buy_stock(self, symbol, quantity):
        """Submit a market order to buy a stock.

        Args:
            symbol: str - Stock ticker e.g. "AAPL".
            quantity: int - Number of shares to buy.

        Returns:
            Trade - The ib_async Trade handle.
        """
        # TODO: Should we validate quantity here or is that handled at  a lower level?
        contract = create_stock_contract(symbol)
        order = MarketOrder('BUY', quantity)
        return self._place_on_ib_loop(contract, order)

    def sell_stock(self, symbol, quantity):
        """Submit a market order to sell a stock.

        Args:
            symbol: str - Stock ticker e.g. "AAPL".
            quantity: int - Number of shares to sell.

        Returns:
            Trade - The ib_async Trade handle.
        """
        contract = create_stock_contract(symbol)
        order = MarketOrder('SELL', quantity)
        return self._place_on_ib_loop(contract, order)

    def short_stock(self, symbol, quantity):
        """Submit a market order to short a stock.

        Args:
            symbol: str - Stock ticker symbol.
            quantity: int - Number of shares to short.

        Returns:
            Trade - The ib_async Trade handle.
        """
        contract = create_stock_contract(symbol)
        order = MarketOrder('SELL', quantity)
        # TODO: Check shortability before placing order.
        return self._place_on_ib_loop(contract, order)

    def buy_to_cover(self, symbol, quantity):
        """Submit a market order to buy-to-cover a short position.

        Args:
            symbol: str - Stock ticker symbol.
            quantity: int - Number of shares to cover.

        Returns:
            Trade - The ib_async Trade handle.
        """
        contract = create_stock_contract(symbol)
        order = MarketOrder('BUY', quantity)
        return self._place_on_ib_loop(contract, order)

    def buy_option(self, symbol, expiry, strike, right, quantity):
        """Submit a market order to buy an option.

        Args:
            symbol: str - Underlying stock ticker symbol.
            expiry: str - Expiry date in YYYYMMDD format.
            strike: float - Strike price.
            right: str - 'C' for Call, 'P' for Put.
            quantity: int - Number of option contracts to buy.

        Returns:
            Trade - The ib_async Trade handle.

        Raises:
            ValueError - If `right` is not 'C' or 'P'.
        """
        if right not in ('C', 'P'):
            raise ValueError("right must be 'C' for Call or 'P' for Put")

        contract = create_option_contract(symbol, expiry, strike, OptionType(right))
        order = MarketOrder('BUY', quantity)
        return self._place_on_ib_loop(contract, order)

    def sell_option(self, symbol, expiry, strike, right, quantity):
        """Submit a market order to sell an option.

        Args:
            symbol: str - Underlying stock ticker symbol.
            expiry: str - Expiry date in YYYYMMDD format.
            strike: float - Strike price.
            right: str - 'C' for Call, 'P' for Put.
            quantity: int - Number of option contracts to sell.

        Returns:
            Trade - The ib_async Trade handle.

        Raises:
            ValueError - If `right` is not 'C' or 'P'.
        """
        if right not in ('C', 'P'):
            raise ValueError("right must be 'C' for Call or 'P' for Put")

        contract = create_option_contract(symbol, expiry, strike, OptionType(right))
        order = MarketOrder('SELL', quantity)

        return self._place_on_ib_loop(contract, order)

    # TODO: get_order_status/cancel_order should use the same _place_on_ib_loop pattern where needed.
