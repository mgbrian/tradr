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

        # Patch symbols inside order_manager module
        self.patcher_market_order = patch("order_manager.MarketOrder")
        self.patcher_stock_factory = patch("order_manager.create_stock_contract")
        self.patcher_option_factory = patch("order_manager.create_option_contract")

        self.MockMarketOrder = self.patcher_market_order.start()
        self.create_stock_contract = self.patcher_stock_factory.start()
        self.create_option_contract = self.patcher_option_factory.start()

        self.addCleanup(self.patcher_market_order.stop)
        self.addCleanup(self.patcher_stock_factory.stop)
        self.addCleanup(self.patcher_option_factory.stop)

        # Common return values
        self.fake_stock_contract = object()
        self.fake_option_contract = object()
        self.create_stock_contract.return_value = self.fake_stock_contract
        self.create_option_contract.return_value = self.fake_option_contract

        # MarketOrder returns a distinct object we can assert on
        self.fake_order = object()
        self.MockMarketOrder.return_value = self.fake_order

        # placeOrder returns a "trade" object
        self.fake_trade = object()
        self.mock_ib.placeOrder.return_value = self.fake_trade

        self.order_manager = OrderManager(self.mock_ib)

    # --- Tests for Stock Orders ---

    def test_buy_stock_calls_factory_and_place_order(self):
        """buy_stock() should build a BUY MarketOrder and call placeOrder with the stock contract."""
        trade = self.order_manager.buy_stock("AAPL", 10)

        self.create_stock_contract.assert_called_once_with("AAPL")
        self.MockMarketOrder.assert_called_once_with("BUY", 10)
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_stock_contract, self.fake_order)

        self.assertIs(trade, self.fake_trade)

    def test_sell_stock_calls_factory_and_place_order(self):
        """sell_stock() should build a SELL MarketOrder and call placeOrder."""
        trade = self.order_manager.sell_stock("MSFT", 5)

        self.create_stock_contract.assert_called_once_with("MSFT")
        self.MockMarketOrder.assert_called_once_with("SELL", 5)
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_stock_contract, self.fake_order)

        self.assertIs(trade, self.fake_trade)

    def test_short_stock_uses_sell_side(self):
        """short_stock() should use SELL side and call placeOrder."""
        trade = self.order_manager.short_stock("TSLA", 3)

        self.create_stock_contract.assert_called_once_with("TSLA")
        self.MockMarketOrder.assert_called_once_with("SELL", 3)
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_stock_contract, self.fake_order)

        self.assertIs(trade, self.fake_trade)

    def test_buy_to_cover_uses_buy_side(self):
        """buy_to_cover() should use BUY side and call placeOrder."""
        trade = self.order_manager.buy_to_cover("NVDA", 7)

        self.create_stock_contract.assert_called_once_with("NVDA")
        self.MockMarketOrder.assert_called_once_with("BUY", 7)
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_stock_contract, self.fake_order)

        self.assertIs(trade, self.fake_trade)

    # --- Tests for Option Orders ---

    def test_buy_option_calls_factory_with_enum_right(self):
        """buy_option() should convert right to OptionType and call placeOrder with BUY."""
        trade = self.order_manager.buy_option("AAPL", "20251219", 150.0, "C", 2)

        self.create_option_contract.assert_called_once_with(
            "AAPL", "20251219", 150.0, OptionType.CALL
        )
        self.MockMarketOrder.assert_called_once_with("BUY", 2)
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_option_contract, self.fake_order)

        self.assertIs(trade, self.fake_trade)

    def test_sell_option_calls_factory_with_enum_right(self):
        """sell_option() should convert right to OptionType and call placeOrder with SELL."""
        trade = self.order_manager.sell_option("SPY", "20260116", 420.0, "P", 1)

        self.create_option_contract.assert_called_once_with(
            "SPY", "20260116", 420.0, OptionType.PUT
        )
        self.MockMarketOrder.assert_called_once_with("SELL", 1)
        self.mock_ib.placeOrder.assert_called_once_with(self.fake_option_contract, self.fake_order)

        self.assertIs(trade, self.fake_trade)

    def test_buy_option_invalid_right_raises(self):
        """buy_option() should raise ValueError when right is not 'C' or 'P'."""
        with self.assertRaises(ValueError):
            self.order_manager.buy_option("AAPL", "20251219", 150.0, "X", 1)

    def test_sell_option_invalid_right_raises(self):
        """sell_option() should raise ValueError when right is not 'C' or 'P'."""
        with self.assertRaises(ValueError):
            self.order_manager.sell_option("AAPL", "20251219", 150.0, "Q", 1)


if __name__ == "__main__":
    unittest.main()
