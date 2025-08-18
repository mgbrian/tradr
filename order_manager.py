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

from ib_async import MarketOrder, LimitOrder, StopOrder
from contracts import OptionType, create_stock_contract, create_option_contract


SUPPORTED_TIF_VALUES = {'DAY', 'GTC'}


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

    @staticmethod
    def _build_order(side, quantity, order_type='MKT', price=None, tif='DAY'):
        """Factory for Market/Limit/Stop orders with TIF applied."""
        ot = (order_type or 'MKT').upper()
        order_kwargs = {}

        if tif:
            tif = str(tif).upper()


            if tif not in SUPPORTED_TIF_VALUES:
                raise ValueError(f"Unsupported tif value: {tif}. Must be one of: {', '.join(SUPPORTED_TIF_VALUES)}")

            order_kwargs["tif"] = tif

        if ot == 'MKT':
            order = MarketOrder(side, int(quantity), **order_kwargs)

        elif ot == 'LMT':
            if price is None:
                raise ValueError("Limit order requires price")
            order = LimitOrder(side, int(quantity), float(price), **order_kwargs)

        elif ot == 'STP':
            if price is None:
                raise ValueError("Stop order requires stop price")
            # IB uses auxPrice for stop trigger
            order = StopOrder(side, int(quantity), float(price), **order_kwargs)

        else:
            raise ValueError(f"Unsupported order_type: {order_type}")

        return order

    def buy_stock(self, symbol, quantity, *, order_type='MKT', price=None, tif='DAY'):
        """Submit a market order to buy a stock.

        Args:
            symbol: str - Stock ticker e.g. "AAPL".
            quantity: int - Number of shares to buy.

            Optional Kwargs:
            ----------------
            order_type: str - 'MKT', 'LMT' or 'STP'. Default = 'MKT'
            price: float - Limit or stop price.
            tif: str - Time in force. 'DAY' or 'GTC'. Default = 'DAY'.

        Returns:
            Trade - The ib_async Trade handle.
        """
        # TODO: Should we validate quantity here or is that handled at  a lower level?
        contract = create_stock_contract(symbol)
        order = self._build_order('BUY', quantity, order_type, price, tif)
        return self._place_on_ib_loop(contract, order)

    def sell_stock(self, symbol, quantity, *, order_type='MKT', price=None, tif='DAY'):
        """Submit a market order to sell a stock.

        Args:
            symbol: str - Stock ticker e.g. "AAPL".
            quantity: int - Number of shares to sell.

            Optional Kwargs:
            ----------------
            order_type: str - 'MKT', 'LMT' or 'STP'. Default = 'MKT'
            price: float - Limit or stop price.
            tif: str - Time in force. 'DAY' or 'GTC'. Default = 'DAY'.

        Returns:
            Trade - The ib_async Trade handle.
        """
        contract = create_stock_contract(symbol)
        order = self._build_order('SELL', quantity, order_type, price, tif)
        return self._place_on_ib_loop(contract, order)

    def short_stock(self, symbol, quantity, *, order_type='MKT', price=None, tif='DAY'):
        """Submit a market order to short a stock.

        Args:
            symbol: str - Stock ticker symbol.
            quantity: int - Number of shares to short.

            Optional Kwargs:
            ----------------
            order_type: str - 'MKT', 'LMT' or 'STP'. Default = 'MKT'
            price: float - Limit or stop price.
            tif: str - Time in force. 'DAY' or 'GTC'. Default = 'DAY'.

        Returns:
            Trade - The ib_async Trade handle.
        """
        contract = create_stock_contract(symbol)
        order = self._build_order('SELL', quantity, order_type, price, tif)
        # TODO: Check shortability before placing order.
        return self._place_on_ib_loop(contract, order)

    def buy_to_cover(self, symbol, quantity, *, order_type='MKT', price=None, tif='DAY'):
        """Submit a market order to buy-to-cover a short position.

        Args:
            symbol: str - Stock ticker symbol.
            quantity: int - Number of shares to cover.

            Optional Kwargs:
            ----------------
            order_type: str - 'MKT', 'LMT' or 'STP'. Default = 'MKT'
            price: float - Limit or stop price.
            tif: str - Time in force. 'DAY' or 'GTC'. Default = 'DAY'.

        Returns:
            Trade - The ib_async Trade handle.
        """
        contract = create_stock_contract(symbol)
        order = self._build_order('BUY', quantity, order_type, price, tif)
        return self._place_on_ib_loop(contract, order)

    def buy_option(self, symbol, expiry, strike, right, quantity, *, order_type='MKT', price=None, tif='DAY'):
        """Submit a market order to buy an option.

        Args:
            symbol: str - Underlying stock ticker symbol.
            expiry: str - Expiry date in YYYYMMDD format.
            strike: float - Strike price.
            right: str - 'C' for Call, 'P' for Put.
            quantity: int - Number of option contracts to buy.

            Optional Kwargs:
            ----------------
            order_type: str - 'MKT', 'LMT' or 'STP'. Default = 'MKT'
            price: float - Limit or stop price.
            tif: str - Time in force. 'DAY' or 'GTC'. Default = 'DAY'.

        Returns:
            Trade - The ib_async Trade handle.

        Raises:
            ValueError - If `right` is not 'C' or 'P'.
        """
        if right not in ('C', 'P'):
            raise ValueError("right must be 'C' for Call or 'P' for Put")

        contract = create_option_contract(symbol, expiry, strike, OptionType(right))
        order = self._build_order('BUY', quantity, order_type, price, tif)
        return self._place_on_ib_loop(contract, order)

    def sell_option(self, symbol, expiry, strike, right, quantity, *, order_type='MKT', price=None, tif='DAY'):
        """Submit a market order to sell an option.

        Args:
            symbol: str - Underlying stock ticker symbol.
            expiry: str - Expiry date in YYYYMMDD format.
            strike: float - Strike price.
            right: str - 'C' for Call, 'P' for Put.
            quantity: int - Number of option contracts to sell.

            Optional Kwargs:
            ----------------
            order_type: str - 'MKT', 'LMT' or 'STP'. Default = 'MKT'
            price: float - Limit or stop price.
            tif: str - Time in force. 'DAY' or 'GTC'. Default = 'DAY'.

        Returns:
            Trade - The ib_async Trade handle.

        Raises:
            ValueError - If `right` is not 'C' or 'P'.
        """
        if right not in ('C', 'P'):
            raise ValueError("right must be 'C' for Call or 'P' for Put")

        contract = create_option_contract(symbol, expiry, strike, OptionType(right))
        order = self._build_order('SELL', quantity, order_type, price, tif)
        return self._place_on_ib_loop(contract, order)

        return self._place_on_ib_loop(contract, order)

    # TODO: get_order_status/cancel_order should use the same _place_on_ib_loop pattern where needed.
