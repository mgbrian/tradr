import logging
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from api import TradingAPI, OrderHandle


class TestTradingAPI(unittest.TestCase):
    """Unit tests for high level TradingAPI."""

    def setUp(self):
        """Create a TradingAPI with mocked dependencies."""
        self.mock_ib = MagicMock(name="IB")
        self.mock_db = MagicMock(name="InMemoryDB")
        self.mock_orders = MagicMock(name="OrderManager")
        self.mock_tracker = MagicMock(name="PositionTracker")

        # add_order returns incremental ids; default to 1 for simplicity
        self.mock_db.add_order.return_value = 1

        # list/get methods return simple values for assertions
        self.mock_db.get_order.return_value = {'order_id': 1, 'status': 'SUBMITTED'}
        self.mock_db.list_orders.return_value = [{'order_id': 1}]
        self.mock_db.list_fills.return_value = [{'fill_id': 10, 'order_id': 1}]
        self.mock_db.get_positions.return_value = {('k',): {'position': 1}}
        self.mock_db.get_account_values.return_value = {('acct', 'tag', 'USD'): {'value': '123'}}

        self.api = TradingAPI(
            ib=self.mock_ib,
            db=self.mock_db,
            order_manager=self.mock_orders,
            position_tracker=self.mock_tracker
        )

    # --- Validation tests ---

    def test_place_stock_order_validates_inputs(self):
        """Stock order input validation: symbol, side, qty, order_type/TIF/price where required."""
        with self.assertRaises(ValueError):
            self.api.place_stock_order('', 'BUY', 1)

        with self.assertRaises(ValueError):
            self.api.place_stock_order('AAPL', 'NOPE', 1)

        with self.assertRaises(ValueError):
            self.api.place_stock_order('AAPL', 'BUY', 0)

        # Invalid order_type should raise
        with self.assertRaises(ValueError):
            self.api.place_stock_order('AAPL', 'BUY', 1, order_type='FAKE')

        # LMT requires limit_price
        with self.assertRaises(ValueError):
            self.api.place_stock_order('AAPL', 'BUY', 1, order_type='LMT', limit_price=None)

        # STP requires limit/trigger price (same param)
        with self.assertRaises(ValueError):
            self.api.place_stock_order('AAPL', 'SELL', 1, order_type='STP', limit_price=None)

        # Invalid TIF should raise
        with self.assertRaises(ValueError):
            self.api.place_stock_order('AAPL', 'BUY', 1, order_type='LMT', limit_price=123.0, tif='IOC')

    def test_place_option_order_validates_inputs(self):
        """Option order input validation: symbol, right, side, qty, order_type/price where required."""
        with self.assertRaises(ValueError):
            self.api.place_option_order('', '20251219', 150.0, 'C', 'BUY', 1)

        with self.assertRaises(ValueError):
            self.api.place_option_order('AAPL', '20251219', 150.0, 'X', 'BUY', 1)

        with self.assertRaises(ValueError):
            self.api.place_option_order('AAPL', '20251219', 150.0, 'C', 'HOLD', 1)

        with self.assertRaises(ValueError):
            self.api.place_option_order('AAPL', '20251219', 150.0, 'C', 'BUY', 0)

        # Invalid order type should raise
        with self.assertRaises(ValueError):
            self.api.place_option_order('AAPL', '20251219', 150.0, 'C', 'BUY', 1, order_type='FAKE')

        # LMT/STP require limit_price
        with self.assertRaises(ValueError):
            self.api.place_option_order('AAPL', '20251219', 150.0, 'C', 'BUY', 1, order_type='LMT', limit_price=None)

        with self.assertRaises(ValueError):
            self.api.place_option_order('AAPL', '20251219', 150.0, 'P', 'SELL', 1, order_type='STP', limit_price=None)

    # --- Success paths ---

    def _make_trade_with_order_id(self, order_id=777):
        """Helper to produce a trade-like object with order.orderId set."""
        return SimpleNamespace(order=SimpleNamespace(orderId=order_id))

    def test_place_stock_order_persists_before_broker_and_updates_ib_id(self):
        """Persist order, call broker, then update DB with broker_order_id (market)."""
        self.mock_orders.buy_stock.return_value = self._make_trade_with_order_id(42)

        handle = self.api.place_stock_order('AAPL', 'BUY', 10)

        # Persisted once before broker call
        self.mock_db.add_order.assert_called_once()
        # Persisted order should contain basic shape (incl. order_type/tif fields now present)
        add_args, _ = self.mock_db.add_order.call_args
        self.assertEqual(add_args[0].get('order_type'), 'MKT')
        self.assertIn('tif', add_args[0])  # may be None/default

        # Then updated with broker_order_id
        self.mock_db.update_order.assert_any_call(1, {'broker_order_id': 42, 'status': 'SUBMITTED'})
        # Broker called with correct mapping
        self.mock_orders.buy_stock.assert_called_once_with('AAPL', 10, order_type='MKT', price=None, tif='DAY')

        # Handle returned
        self.assertIsInstance(handle, OrderHandle)
        self.assertEqual(handle.order_id, 1)
        self.assertEqual(handle.broker_order_id, 42)
        self.assertEqual(handle.symbol, 'AAPL')
        self.assertEqual(handle.side, 'BUY')
        self.assertEqual(handle.qty, 10)

    def test_place_stock_order_side_routing(self):
        """Verify side maps to correct OrderManager method for market orders."""
        # Return unique trades so we can assert all were called
        self.mock_orders.sell_stock.return_value = self._make_trade_with_order_id(1)
        self.mock_orders.short_stock.return_value = self._make_trade_with_order_id(2)
        self.mock_orders.buy_to_cover.return_value = self._make_trade_with_order_id(3)

        self.api.place_stock_order('MSFT', 'SELL', 5)
        self.api.place_stock_order('TSLA', 'SHORT', 3)
        self.api.place_stock_order('NVDA', 'COVER', 7)

        self.mock_orders.sell_stock.assert_called_once_with('MSFT', 5, order_type='MKT', price=None, tif='DAY')
        self.mock_orders.short_stock.assert_called_once_with('TSLA', 3, order_type='MKT', price=None, tif='DAY')
        self.mock_orders.buy_to_cover.assert_called_once_with('NVDA', 7, order_type='MKT', price=None, tif='DAY')

    def test_place_stock_order_limit_success_calls_generic_and_updates_broker_id(self):
        """LMT uses generic place_stock_order with price/TIF and updates DB with broker id."""
        self.mock_orders.buy_stock.return_value = self._make_trade_with_order_id(99)

        h = self.api.place_stock_order('AAPL', 'BUY', 10, order_type='LMT', limit_price=123.45, tif='GTC')

        self.mock_orders.buy_stock.assert_called_once_with(
            'AAPL', 10, order_type='LMT', price=123.45, tif='GTC'
        )
        # Persisted LMT fields captured
        add_args, _ = self.mock_db.add_order.call_args
        self.assertEqual(add_args[0]['order_type'], 'LMT')
        self.assertEqual(add_args[0]['limit_price'], 123.45)
        self.assertEqual(add_args[0]['tif'], 'GTC')

        self.mock_db.update_order.assert_any_call(1, {'broker_order_id': 99, 'status': 'SUBMITTED'})
        self.assertIsInstance(h, OrderHandle)
        self.assertEqual(h.broker_order_id, 99)

    def test_place_stock_order_stop_success_calls_generic_and_updates_broker_id(self):
        """STP uses generic place_stock_order with trigger price/TIF and updates DB."""
        self.mock_orders.sell_stock.return_value = self._make_trade_with_order_id(101)

        h = self.api.place_stock_order('TSLA', 'SELL', 3, order_type='STP', limit_price=700.0, tif='DAY')

        self.mock_orders.sell_stock.assert_called_once_with(
            'TSLA', 3, order_type='STP', price=700.0, tif='DAY'
        )
        add_args, _ = self.mock_db.add_order.call_args
        self.assertEqual(add_args[0]['order_type'], 'STP')
        self.assertEqual(add_args[0]['limit_price'], 700.0)
        self.assertEqual(add_args[0]['tif'], 'DAY')

        self.mock_db.update_order.assert_any_call(1, {'broker_order_id': 101, 'status': 'SUBMITTED'})
        self.assertIsInstance(h, OrderHandle)
        self.assertEqual(h.broker_order_id, 101)

    def test_place_option_order_success_buy_and_sell(self):
        """BUY uses buy_option; SELL uses sell_option; DB updated with broker_order_id."""
        self.mock_orders.buy_option.return_value = self._make_trade_with_order_id(1001)
        self.mock_orders.sell_option.return_value = self._make_trade_with_order_id(1002)

        h1 = self.api.place_option_order('AAPL', '20251219', 150.0, 'C', 'BUY', 2)
        h2 = self.api.place_option_order('SPY', '20260116', 420.0, 'P', 'SELL', 1)

        self.mock_orders.buy_option.assert_called_once_with('AAPL', '20251219', 150.0, 'C', 2, order_type='MKT', price=None, tif='DAY')
        self.mock_orders.sell_option.assert_called_once_with('SPY', '20260116', 420.0, 'P', 1, order_type='MKT', price=None, tif='DAY')

        # We expect two updates; check that update was called at least twice
        self.assertGreaterEqual(self.mock_db.update_order.call_count, 2)
        self.assertIsInstance(h1, OrderHandle)
        self.assertIsInstance(h2, OrderHandle)
        self.assertEqual(h1.broker_order_id, 1001)
        self.assertEqual(h2.broker_order_id, 1002)

    def test_place_option_order_limit_success_calls_generic_and_updates_broker_id(self):
        """Options LMT uses generic buy/sell_option with price/TIF and updates DB with broker id."""
        self.mock_orders.buy_option.return_value = self._make_trade_with_order_id(2112)

        h = self.api.place_option_order(
            'AAPL', '20251219', 150.0, 'C', 'BUY', 2,
            order_type='LMT', limit_price=1.25, tif='GTC'
        )

        self.mock_orders.buy_option.assert_called_once_with(
            'AAPL', '20251219', 150.0, 'C', 2,
            order_type='LMT', price=1.25, tif='GTC'
        )

        # Persisted fields captured
        add_args, _ = self.mock_db.add_order.call_args
        self.assertEqual(add_args[0]['asset_class'], 'OPT')
        self.assertEqual(add_args[0]['order_type'], 'LMT')
        self.assertEqual(add_args[0]['limit_price'], 1.25)
        self.assertEqual(add_args[0]['tif'], 'GTC')
        self.assertEqual(add_args[0]['status'], 'PENDING_SUBMIT')

        # DB updated with broker id
        self.mock_db.update_order.assert_any_call(1, {'broker_order_id': 2112, 'status': 'SUBMITTED'})
        self.assertIsInstance(h, OrderHandle)
        self.assertEqual(h.broker_order_id, 2112)

    def test_place_option_order_stop_success_calls_generic_and_updates_broker_id(self):
        """Options STP uses generic buy/sell_option with trigger price/TIF and updates DB."""
        self.mock_orders.sell_option.return_value = self._make_trade_with_order_id(31415)

        h = self.api.place_option_order(
            'SPY', '20260116', 420.0, 'P', 'SELL', 1,
            order_type='STP', limit_price=2.50, tif='DAY'
        )

        self.mock_orders.sell_option.assert_called_once_with(
            'SPY', '20260116', 420.0, 'P', 1,
            order_type='STP', price=2.50, tif='DAY'
        )

        add_args, _ = self.mock_db.add_order.call_args
        self.assertEqual(add_args[0]['order_type'], 'STP')
        self.assertEqual(add_args[0]['limit_price'], 2.50)
        self.assertEqual(add_args[0]['tif'], 'DAY')

        self.mock_db.update_order.assert_any_call(1, {'broker_order_id': 31415, 'status': 'SUBMITTED'})
        self.assertIsInstance(h, OrderHandle)
        self.assertEqual(h.broker_order_id, 31415)

    # --- Error paths ---

    def test_place_stock_order_broker_error_updates_db_and_raises(self):
        """If broker call fails (market path), DB status becomes ERROR and we raise RuntimeError."""
        # This test by design triggers a call to logger.exception in api.py, which
        # prints out a stack trace to the terminal, which can make it seem like
        # the tests are failing. Silence that here!
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        self.mock_orders.buy_stock.side_effect = RuntimeError("IBKR down")

        with self.assertRaises(RuntimeError):
            self.api.place_stock_order('AAPL', 'BUY', 1)

        # add_order called once
        self.mock_db.add_order.assert_called_once()
        # update_order called with error state
        args, kwargs = self.mock_db.update_order.call_args
        self.assertEqual(args[0], 1)
        self.assertEqual(args[1].get('status'), 'ERROR')
        self.assertIn('error', args[1])

    def test_place_stock_order_limit_broker_error_updates_db_and_raises(self):
        """If broker call fails (LMT path), DB status becomes ERROR and we raise RuntimeError."""
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        self.mock_orders.buy_stock.side_effect = RuntimeError("route down")

        with self.assertRaises(RuntimeError):
            self.api.place_stock_order('AAPL', 'BUY', 1, order_type='LMT', limit_price=101.0, tif='DAY')

        self.mock_db.add_order.assert_called_once()
        args, kwargs = self.mock_db.update_order.call_args
        self.assertEqual(args[0], 1)
        self.assertEqual(args[1].get('status'), 'ERROR')
        self.assertIn('error', args[1])

    def test_place_option_order_broker_error_updates_db_and_raises(self):
        """If option broker call fails, DB status becomes ERROR and we raise RuntimeError."""
        # See note in test_place_stock_order_broker_error_updates_db_and_raises
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        self.mock_orders.buy_option.side_effect = Exception("No route to host")
        with self.assertRaises(RuntimeError):
            self.api.place_option_order('AAPL', '20251219', 150.0, 'C', 'BUY', 1)

        self.mock_db.add_order.assert_called_once()
        args, kwargs = self.mock_db.update_order.call_args
        self.assertEqual(args[0], 1)
        self.assertEqual(args[1].get('status'), 'ERROR')
        self.assertIn('error', args[1])

    def test_place_option_order_limit_broker_error_updates_db_and_raises(self):
        """If option broker call fails (LMT path), DB status becomes ERROR and we raise RuntimeError."""
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        self.mock_orders.buy_option.side_effect = RuntimeError("route down (options)")

        with self.assertRaises(RuntimeError):
            self.api.place_option_order(
                'AAPL', '20251219', 150.0, 'C', 'BUY', 2,
                order_type='LMT', limit_price=1.10, tif='DAY'
            )

        self.mock_db.add_order.assert_called_once()
        args, _ = self.mock_db.update_order.call_args
        self.assertEqual(args[0], 1)
        self.assertEqual(args[1].get('status'), 'ERROR')
        self.assertIn('error', args[1])

    # --- Read APIs ---

    def test_get_order_status_proxies_to_db(self):
        """get_order_status() should proxy to db.get_order()."""
        out = self.api.get_order_status(1)
        self.mock_db.get_order.assert_called_once_with(1)
        self.assertEqual(out['status'], 'SUBMITTED')

    def test_list_orders_list_fills_proxies(self):
        """list_orders and list_fills proxy to DB with limit/order_id parameters."""
        _ = self.api.list_orders(limit=5)
        self.mock_db.list_orders.assert_called_once_with(limit=5)

        _ = self.api.list_fills(order_id=1, limit=10)
        self.mock_db.list_fills.assert_called_once_with(order_id=1, limit=10)

    def test_get_positions_and_account_values_proxy_to_db(self):
        """Positions and account values come from DB snapshots."""
        pos = self.api.get_positions()
        avs = self.api.get_account_values()
        self.mock_db.get_positions.assert_called_once()
        self.mock_db.get_account_values.assert_called_once()
        self.assertIn(('k',), pos)
        self.assertIn(('acct', 'tag', 'USD'), avs)

    # --- Cancellation ---

    def test_cancel_order_happy_path_calls_order_manager_and_marks_cancel_requested(self):
        """cancel_order() should look up the order, mark CANCEL_REQUESTED, and ask OrderManager to cancel by broker_order_id."""
        # Prepare DB to return an order with broker_order_id
        self.mock_db.get_order.return_value = {
            'order_id': 1, 'status': 'SUBMITTED', 'broker_order_id': 42
        }
        self.mock_orders.cancel_order.return_value = True

        ok = self.api.cancel_order(1)

        # DB looked up by internal id
        self.mock_db.get_order.assert_called_once_with(1)
        # Status marked as CANCEL_REQUESTED before/around broker call
        # (We don't enforce exact ordering here; just ensure it was requested)
        found_cancel_req = False
        for call in self.mock_db.update_order.call_args_list:
            if call[0][0] == 1 and call[0][1].get('status') == 'CANCEL_REQUESTED':
                found_cancel_req = True
                break
        self.assertTrue(found_cancel_req, "Expected CANCEL_REQUESTED status update")

        # OrderManager asked to cancel by broker id
        self.mock_orders.cancel_order.assert_called_once_with(42)
        self.assertTrue(ok)

    def test_cancel_order_already_finalized_returns_false(self):
        """If order is already FILLED or CANCELLED, do not call broker and return False."""
        self.mock_db.get_order.return_value = {'order_id': 1, 'status': 'FILLED', 'broker_order_id': 50}

        out = self.api.cancel_order(1)

        self.assertFalse(out)
        self.mock_orders.cancel_order.assert_not_called()
        # No status flip to CANCEL_REQUESTED expected
        for call in self.mock_db.update_order.call_args_list:
            self.assertNotEqual(call[0][1].get('status'), 'CANCEL_REQUESTED')

        # Repeat for CANCELLED state
        self.mock_db.get_order.reset_mock()
        self.mock_db.update_order.reset_mock()
        self.mock_orders.cancel_order.reset_mock()

        self.mock_db.get_order.return_value = {'order_id': 1, 'status': 'CANCELLED', 'broker_order_id': 51}
        out2 = self.api.cancel_order(1)
        self.assertFalse(out2)
        self.mock_orders.cancel_order.assert_not_called()

    def test_cancel_order_missing_order_raises_key_error(self):
        """If the order id is unknown in DB, raise ValueError."""
        self.mock_db.get_order.return_value = None
        with self.assertRaises(KeyError):
            self.api.cancel_order(999)
        self.mock_orders.cancel_order.assert_not_called()

    def test_cancel_order_missing_broker_id_raises_value_error(self):
        """If broker_order_id is not yet known, raise ValueError to the caller."""
        self.mock_db.get_order.return_value = {'order_id': 1, 'status': 'SUBMITTED', 'broker_order_id': None}
        with self.assertRaises(ValueError):
            self.api.cancel_order(1)
        self.mock_orders.cancel_order.assert_not_called()

    def test_cancel_order_broker_error_updates_db_and_raises(self):
        """If OrderManager.cancel_order raises, update DB to ERROR and bubble as RuntimeError."""
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        self.mock_db.get_order.return_value = {'order_id': 1, 'status': 'SUBMITTED', 'broker_order_id': 77}
        self.mock_orders.cancel_order.side_effect = RuntimeError("IB cancel failed")

        with self.assertRaises(RuntimeError):
            self.api.cancel_order(1)

        # Ensure error recorded
        args, _ = self.mock_db.update_order.call_args
        self.assertEqual(args[0], 1)
        self.assertEqual(args[1].get('status'), 'ERROR')
        self.assertIn('error', args[1])

    # --- Modification ---

    def test_modify_order_stock_happy_path_updates_db_and_calls_order_manager(self):
        """modify_order() on a stock should mark MODIFY_REQUESTED and call modify_stock_order with merged fields."""
        self.mock_db.get_order.return_value = {
            'order_id': 1,
            'status': 'SUBMITTED',
            'broker_order_id': 42,
            'asset_class': 'STK',
            'symbol': 'MSFT',
            'side': 'SELL',
            'qty': 10,
            'order_type': 'LMT',
            'limit_price': 250.0,
            'tif': 'DAY',
        }

        self.mock_orders.modify_stock_order.return_value = object()

        ok = self.api.modify_order(
            1, quantity=15, limit_price=251.0, tif='GTC', order_type='LMT'
        )
        self.assertTrue(ok)

        # DB looked up
        self.mock_db.get_order.assert_called_once_with(1)

        # Status marked as MODIFY_REQUESTED with new fields
        found_modify_req = False
        for call in self.mock_db.update_order.call_args_list:
            if call[0][0] == 1 and call[0][1].get('status') == 'MODIFY_REQUESTED':
                self.assertEqual(call[0][1]['qty'], 15)
                self.assertEqual(call[0][1]['order_type'], 'LMT')
                self.assertEqual(call[0][1]['limit_price'], 251.0)
                self.assertEqual(call[0][1]['tif'], 'GTC')
                found_modify_req = True
                break
        self.assertTrue(found_modify_req, "Expected MODIFY_REQUESTED status update")

        # SELL side remains SELL action on modify
        self.mock_orders.modify_stock_order.assert_called_once_with(
            'MSFT', 42, 'SELL', 15, order_type='LMT', price=251.0, tif='GTC'
        )

    def test_modify_order_option_happy_path_uses_defaults_and_calls_order_manager(self):
        """When some params are omitted, modify_order() should default to existing values from DB (options)."""
        self.mock_db.get_order.return_value = {
            'order_id': 1,
            'status': 'SUBMITTED',
            'broker_order_id': 77,
            'asset_class': 'OPT',
            'symbol': 'AAPL',
            'expiry': '20251219',
            'strike': 150.0,
            'right': 'C',
            'side': 'BUY',
            'qty': 2,
            'order_type': 'LMT',
            'limit_price': 1.00,
            'tif': 'DAY',
        }

        self.mock_orders.modify_option_order.return_value = object()

        # Change only type/price; quantity and tif should fall back to DB values
        ok = self.api.modify_order(1, order_type='STP', limit_price=2.0)
        self.assertTrue(ok)

        self.mock_orders.modify_option_order.assert_called_once_with(
            'AAPL', '20251219', 150.0, 'C', 77, 'BUY', 2,
            order_type='STP', price=2.0, tif='DAY'
        )

        # DB was marked as modify requested with merged fields
        found_modify_req = False
        for call in self.mock_db.update_order.call_args_list:
            if call[0][0] == 1 and call[0][1].get('status') == 'MODIFY_REQUESTED':
                self.assertEqual(call[0][1]['qty'], 2)
                self.assertEqual(call[0][1]['order_type'], 'STP')
                self.assertEqual(call[0][1]['limit_price'], 2.0)
                self.assertEqual(call[0][1]['tif'], 'DAY')
                found_modify_req = True
                break
        self.assertTrue(found_modify_req)

    def test_modify_order_final_state_raises(self):
        """Finalized orders cannot be modified."""
        self.mock_db.get_order.return_value = {
            'order_id': 1, 'status': 'FILLED', 'broker_order_id': 10
        }
        with self.assertRaises(ValueError):
            self.api.modify_order(1, quantity=5)
        self.mock_orders.modify_stock_order.assert_not_called()
        self.mock_orders.modify_option_order.assert_not_called()

    def test_modify_order_missing_broker_id_raises(self):
        """If no broker_order_id yet, modification should raise."""
        self.mock_db.get_order.return_value = {
            'order_id': 1, 'status': 'SUBMITTED', 'broker_order_id': None, 'asset_class': 'STK', 'qty': 1
        }
        with self.assertRaises(ValueError):
            self.api.modify_order(1, quantity=2)
        self.mock_orders.modify_stock_order.assert_not_called()

    def test_modify_order_invalid_tif_raises(self):
        """Unsupported TIF passed to modify should raise and not call OrderManager."""
        self.mock_db.get_order.return_value = {
            'order_id': 1, 'status': 'SUBMITTED', 'broker_order_id': 5,
            'asset_class': 'STK', 'symbol': 'AAPL', 'side': 'BUY', 'qty': 1,
            'order_type': 'MKT', 'tif': 'DAY'
        }
        with self.assertRaises(ValueError):
            self.api.modify_order(1, tif='IOC')
        self.mock_orders.modify_stock_order.assert_not_called()

    def test_modify_order_invalid_type_raises(self):
        """Unsupported order_type passed to modify should raise."""
        self.mock_db.get_order.return_value = {
            'order_id': 1, 'status': 'SUBMITTED', 'broker_order_id': 5,
            'asset_class': 'STK', 'symbol': 'AAPL', 'side': 'BUY', 'qty': 1,
            'order_type': 'MKT', 'tif': 'DAY'
        }
        with self.assertRaises(ValueError):
            self.api.modify_order(1, order_type='FAKE')
        self.mock_orders.modify_stock_order.assert_not_called()

    def test_modify_order_lmt_missing_price_raises(self):
        """LMT/STP without price should raise when DB doesn't have a price either."""
        self.mock_db.get_order.return_value = {
            'order_id': 1, 'status': 'SUBMITTED', 'broker_order_id': 5,
            'asset_class': 'STK', 'symbol': 'AAPL', 'side': 'BUY', 'qty': 1,
            'order_type': 'MKT', 'limit_price': None, 'tif': 'DAY'
        }
        with self.assertRaises(ValueError):
            self.api.modify_order(1, order_type='LMT')
        self.mock_orders.modify_stock_order.assert_not_called()

    def test_modify_order_nonpositive_qty_raises(self):
        """Quantity must be positive."""
        self.mock_db.get_order.return_value = {
            'order_id': 1, 'status': 'SUBMITTED', 'broker_order_id': 5,
            'asset_class': 'STK', 'symbol': 'AAPL', 'side': 'BUY', 'qty': 1,
            'order_type': 'MKT', 'tif': 'DAY'
        }
        with self.assertRaises(ValueError):
            self.api.modify_order(1, quantity=0)
        self.mock_orders.modify_stock_order.assert_not_called()

    def test_modify_order_broker_error_updates_db_and_raises(self):
        """If OrderManager.modify_* raises, DB should record ERROR and RuntimeError should bubble."""
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        self.mock_db.get_order.return_value = {
            'order_id': 1, 'status': 'SUBMITTED', 'broker_order_id': 42,
            'asset_class': 'STK', 'symbol': 'MSFT', 'side': 'SELL', 'qty': 10,
            'order_type': 'LMT', 'limit_price': 250.0, 'tif': 'DAY'
        }
        self.mock_orders.modify_stock_order.side_effect = RuntimeError("route down")

        with self.assertRaises(RuntimeError):
            self.api.modify_order(1, quantity=12, limit_price=251.0)

        # Ensure error state recorded
        args, _ = self.mock_db.update_order.call_args
        self.assertEqual(args[0], 1)
        self.assertEqual(args[1].get('status'), 'ERROR')
        self.assertIn('error', args[1])

    # --- OrderHandle ---

    def test_order_handle_to_dict(self):
        """OrderHandle.to_dict returns expected fields."""
        h = OrderHandle(order_id=5, broker_order_id=77, symbol='AAPL', side='BUY', qty=10, created_at=1.23)
        d = h.to_dict()
        self.assertEqual(d['order_id'], 5)
        self.assertEqual(d['broker_order_id'], 77)
        self.assertEqual(d['symbol'], 'AAPL')
        self.assertEqual(d['side'], 'BUY')
        self.assertEqual(d['qty'], 10)
        self.assertEqual(d['created_at'], 1.23)


if __name__ == "__main__":
    unittest.main()
