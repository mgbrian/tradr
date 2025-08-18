import asyncio
import unittest
from unittest.mock import MagicMock, patch
from contracts import OptionType
from order_manager import OrderManager


class TestOrderManager(unittest.TestCase):
    """Unit tests for OrderManager using a mocked IB instance and patched factories."""

    def setUp(self):
        """Patch dependencies in order_manager and set up a mocked IB instance."""
        # Mock IB instance passed into OrderManager
        self.mock_ib = MagicMock()
        # Provide a sentinel loop object since OrderManager now hops onto ib.loop
        self.mock_ib.loop = object()

        # Patch symbols inside order_manager module
        self.patcher_market_order = patch("order_manager.MarketOrder")
        self.patcher_limit_order = patch("order_manager.LimitOrder")
        self.patcher_stop_order = patch("order_manager.StopOrder")
        self.patcher_stock_factory = patch("order_manager.create_stock_contract")
        self.patcher_option_factory = patch("order_manager.create_option_contract")

        # Patch asyncio.run_coroutine_threadsafe so we can synchronously execute the coro
        self.patcher_rcts = patch("order_manager.asyncio.run_coroutine_threadsafe")

        self.MockMarketOrder = self.patcher_market_order.start()
        self.MockLimitOrder = self.patcher_limit_order.start()
        self.MockStopOrder = self.patcher_stop_order.start()
        self.create_stock_contract = self.patcher_stock_factory.start()
        self.create_option_contract = self.patcher_option_factory.start()
        self.mock_rcts = self.patcher_rcts.start()

        self.addCleanup(self.patcher_market_order.stop)
        self.addCleanup(self.patcher_limit_order.stop)
        self.addCleanup(self.patcher_stop_order.stop)
        self.addCleanup(self.patcher_stock_factory.stop)
        self.addCleanup(self.patcher_option_factory.stop)
        self.addCleanup(self.patcher_rcts.stop)

        # Common return values
        self.fake_stock_contract = object()
        self.fake_option_contract = object()
        self.create_stock_contract.return_value = self.fake_stock_contract
        self.create_option_contract.return_value = self.fake_option_contract

        # Market/Limit/Stop orders return distinct objects we can assert on.
        # For limit/stop we use MagicMock so we can assert attribute assignments (e.g., tif).
        self.fake_mkt_order = object()
        self.fake_lmt_order = MagicMock()
        self.fake_stp_order = MagicMock()
        self.MockMarketOrder.return_value = self.fake_mkt_order
        self.MockLimitOrder.return_value = self.fake_lmt_order
        self.MockStopOrder.return_value = self.fake_stp_order

        # placeOrder returns a "trade" object
        self.fake_trade = object()
        self.mock_ib.placeOrder.return_value = self.fake_trade

        # run_coroutine_threadsafe side effect: run the passed coroutine to completion
        # on a fresh event loop and return a FakeFuture whose result() returns the coro result.
        def _rcts_side_effect(coro, loop):
            # Keep a simple assertion that our loop arg is the one we set on IB
            self.assertIs(loop, self.mock_ib.loop)

            class _FakeFuture:
                def __init__(self):
                    self.last_timeout = None

                def result(self_inner, timeout=None):
                    self_inner.last_timeout = timeout
                    new_loop = asyncio.new_event_loop()
                    try:
                        return new_loop.run_until_complete(coro)
                    finally:
                        new_loop.close()

            fut = _FakeFuture()
            self._last_future = fut  # stash so tests can inspect the last timeout used
            return fut

        self._last_future = None
        self.mock_rcts.side_effect = _rcts_side_effect

        self.order_manager = OrderManager(self.mock_ib)

    # --- Tests for Stock Orders ---

    def test_buy_stock_calls_factory_and_place_order(self):
        """buy_stock() should build a BUY MarketOrder and call placeOrder with the stock contract."""
        trade = self.order_manager.buy_stock("AAPL", 10)

        self.create_stock_contract.assert_called_once_with("AAPL")
        self.MockMarketOrder.assert_called_once_with("BUY", 10, tif="DAY")
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_stock_contract, self.fake_mkt_order)
        self.mock_rcts.assert_called_once()  # hopped to IB loop
        self.assertIs(trade, self.fake_trade)

    def test_sell_stock_calls_factory_and_place_order(self):
        """sell_stock() should build a SELL MarketOrder and call placeOrder."""
        trade = self.order_manager.sell_stock("MSFT", 5)

        self.create_stock_contract.assert_called_once_with("MSFT")
        self.MockMarketOrder.assert_called_once_with("SELL", 5, tif="DAY")
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_stock_contract, self.fake_mkt_order)
        self.mock_rcts.assert_called_once()
        self.assertIs(trade, self.fake_trade)

    def test_short_stock_uses_sell_side(self):
        """short_stock() should use SELL side and call placeOrder."""
        trade = self.order_manager.short_stock("TSLA", 3)

        self.create_stock_contract.assert_called_once_with("TSLA")
        # Always called with default TIF
        self.MockMarketOrder.assert_called_once_with("SELL", 3, tif="DAY")
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_stock_contract, self.fake_mkt_order)
        self.mock_rcts.assert_called_once()
        self.assertIs(trade, self.fake_trade)

    def test_buy_to_cover_uses_buy_side(self):
        """buy_to_cover() should use BUY side and call placeOrder."""
        trade = self.order_manager.buy_to_cover("NVDA", 7)

        self.create_stock_contract.assert_called_once_with("NVDA")
        self.MockMarketOrder.assert_called_once_with("BUY", 7, tif="DAY")
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_stock_contract, self.fake_mkt_order)
        self.mock_rcts.assert_called_once()
        self.assertIs(trade, self.fake_trade)

    # --- Tests for Option Orders ---

    def test_buy_option_calls_factory_with_enum_right(self):
        """buy_option() should convert right to OptionType and call placeOrder with BUY."""
        trade = self.order_manager.buy_option("AAPL", "20251219", 150.0, "C", 2)

        self.create_option_contract.assert_called_once_with(
            "AAPL", "20251219", 150.0, OptionType.CALL
        )
        self.MockMarketOrder.assert_called_once_with("BUY", 2, tif="DAY")
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_option_contract, self.fake_mkt_order)
        self.mock_rcts.assert_called_once()
        self.assertIs(trade, self.fake_trade)

    def test_sell_option_calls_factory_with_enum_right(self):
        """sell_option() should convert right to OptionType and call placeOrder with SELL."""
        trade = self.order_manager.sell_option("SPY", "20260116", 420.0, "P", 1)

        self.create_option_contract.assert_called_once_with(
            "SPY", "20260116", 420.0, OptionType.PUT
        )
        self.MockMarketOrder.assert_called_once_with("SELL", 1, tif="DAY")
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_option_contract, self.fake_mkt_order)
        self.mock_rcts.assert_called_once()
        self.assertIs(trade, self.fake_trade)

    def test_buy_option_invalid_right_raises(self):
        """buy_option() should raise ValueError when right is not 'C' or 'P'."""
        with self.assertRaises(ValueError):
            self.order_manager.buy_option("AAPL", "20251219", 150.0, "X", 1)

    def test_sell_option_invalid_right_raises(self):
        """sell_option() should raise ValueError when right is not 'C' or 'P'."""
        with self.assertRaises(ValueError):
            self.order_manager.sell_option("AAPL", "20251219", 150.0, "Q", 1)

    # --- Tests for IB loop/timeout behaviour

    def test_missing_ib_loop_raises_runtime_error(self):
        """If ib.loop is not set/pinned, _place_on_ib_loop should raise."""
        self.mock_ib.loop = None  # simulate missing pinned loop
        with self.assertRaises(RuntimeError):
            self.order_manager.buy_stock("AAPL", 1)
        # rcts should not be called when loop is missing
        self.mock_rcts.assert_not_called()

    def test_timeout_from_future_propagates(self):
        """Timeout from the scheduled future should propagate to caller."""
        def _timeout_side_effect(coro, loop):
            # Consume/cleanup the coroutine so we don't leak a pending awaitable.
            # ** Not doing this results in a warning:
            #    sys:1: RuntimeWarning: coroutine 'OrderManager._place_on_ib_loop.<locals>._coro' was never awaited
            #    RuntimeWarning: Enable tracemalloc to get the object allocation traceback
            try:
                coro.close()
            except Exception:
                pass

            class _TimeoutFuture:
                def result(self, timeout=None):
                    raise TimeoutError("simulated timeout")

            return _TimeoutFuture()

        self.mock_rcts.side_effect = _timeout_side_effect

        with self.assertRaises(TimeoutError):
            self.order_manager.buy_stock("AAPL", 1)
        self.mock_rcts.assert_called_once()

    # --- Limit/Stop Orders and TIF ---

    def test_place_stock_order_limit_buy_sets_tif_and_calls_limitorder(self):
        """place_stock_order() LMT BUY should build LimitOrder(symbol, qty, price), set tif, and place."""
        # Call new generic entrypoint
        trade = self.order_manager.buy_stock(
            symbol="AAPL", quantity=10, order_type="LMT", price=123.45, tif="GTC"
        )

        # Contract factory used
        self.create_stock_contract.assert_called_once_with("AAPL")
        # LimitOrder constructed with (side, qty, limit_price)
        self.MockLimitOrder.assert_called_once_with("BUY", 10, 123.45, tif="GTC")
        # Placed via IB on the stock contract
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_stock_contract, self.fake_lmt_order)
        self.assertIs(trade, self.fake_trade)

    def test_place_stock_order_limit_sell_sets_tif(self):
        """place_stock_order() LMT SELL should pass SELL and respect TIF."""
        trade = self.order_manager.sell_stock(
            symbol="MSFT", quantity=5, order_type="LMT", price=250.0, tif="DAY"
        )
        self.create_stock_contract.assert_called_once_with("MSFT")
        self.MockLimitOrder.assert_called_once_with("SELL", 5, 250., tif="DAY")
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_stock_contract, self.fake_lmt_order)
        self.assertIs(trade, self.fake_trade)

    def test_place_stock_order_stop_buy_sets_tif_and_calls_stoporder(self):
        """place_stock_order() STP BUY should build StopOrder(side, qty, stop_price), set tif, and place."""
        trade = self.order_manager.buy_stock(
            symbol="TSLA", quantity=3, order_type="STP", price=701.25, tif="GTC"
        )
        self.create_stock_contract.assert_called_once_with("TSLA")
        self.MockStopOrder.assert_called_once_with("BUY", 3, 701.25, tif="GTC")
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_stock_contract, self.fake_stp_order)
        self.assertIs(trade, self.fake_trade)

    def test_place_stock_order_stop_sell_sets_tif(self):
        """place_stock_order() STP SELL should pass SELL and set TIF."""
        trade = self.order_manager.sell_stock(
            symbol="NVDA", quantity=4, order_type="STP", price=950.0, tif="DAY"
        )
        self.create_stock_contract.assert_called_once_with("NVDA")
        self.MockStopOrder.assert_called_once_with("SELL", 4, 950.0, tif="DAY")
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_stock_contract, self.fake_stp_order)
        self.assertIs(trade, self.fake_trade)

    def test_place_stock_order_limit_missing_price_raises(self):
        """place_stock_order() LMT without limit_price should raise ValueError."""
        with self.assertRaises(ValueError):
            self.order_manager.buy_stock(
                symbol="AAPL", quantity=1, order_type="LMT", price=None, tif="DAY"
            )

    def test_place_stock_order_stop_missing_price_raises(self):
        """place_stock_order() STP without stop/trigger price (limit_price param) should raise ValueError."""
        with self.assertRaises(ValueError):
            self.order_manager.sell_stock(
                symbol="AAPL", quantity=1, order_type="STP", price=None
            )

    def test_place_stock_order_invalid_type_raises(self):
        """Unsupported order_type should raise ValueError."""
        with self.assertRaises(ValueError):
            self.order_manager.buy_stock(
                symbol="AAPL", quantity=1, order_type="FAKE", price=1.0
            )

    def test_place_stock_order_invalid_tif_raises(self):
        """Unsupported TIF should raise ValueError."""
        with self.assertRaises(ValueError):
            self.order_manager.buy_stock(
                symbol="AAPL", quantity=1, order_type="LMT", price=123.0, tif="IOC"  # assume not yet supported
            )

    # TODO: Tests for option limit/stop orders.


if __name__ == "__main__":
    unittest.main()
