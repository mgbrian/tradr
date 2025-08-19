import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from order_tracker import OrderTracker


class FakeEvent:
    """Tiny event helper mimicking ib_async/ib_insync += / -= and emit()."""
    def __init__(self):
        self._handlers = []

    def __iadd__(self, h):
        self._handlers.append(h)
        return self

    def __isub__(self, h):
        try:
            self._handlers.remove(h)
        except ValueError:
            pass
        return self

    # Convenience for tests
    def emit(self, *args, **kwargs):
        for h in list(self._handlers):
            h(*args, **kwargs)

    # Handy for assertions
    @property
    def handler_count(self):
        return len(self._handlers)


class TestOrderTracker(unittest.TestCase):
    def setUp(self):
        # Fake IB with events
        self.ib = SimpleNamespace(
            openOrderEvent=FakeEvent(),
            orderStatusEvent=FakeEvent(),
        )
        # Mock DB
        self.db = MagicMock()
        self.db.list_orders.return_value = []

        self.tracker = OrderTracker(self.ib, db=self.db)
        self.tracker.start()

    def tearDown(self):
        self.tracker.stop()

    # --- Wiring ---

    def test_start_attaches_handlers_and_stop_detaches(self):
        self.assertGreater(self.ib.openOrderEvent.handler_count, 0)
        self.assertGreater(self.ib.orderStatusEvent.handler_count, 0)

        self.tracker.stop()
        self.assertEqual(self.ib.openOrderEvent.handler_count, 0)
        self.assertEqual(self.ib.orderStatusEvent.handler_count, 0)

        # start again is idempotent-ish
        self.tracker.start()
        self.assertGreater(self.ib.openOrderEvent.handler_count, 0)

    # --- openOrderEvent ---

    def test_open_order_adopts_unknown_order_with_full_fields(self):
        # No existing orders -> should call add_order with payload extracted from triplet.
        contract = SimpleNamespace(symbol="AAPL", secType="STK")
        order = SimpleNamespace(
            orderId=1001,
            action="BUY",
            totalQuantity=10,
            orderType="LMT",
            lmtPrice=123.45,
            tif="DAY",
        )
        order_state = SimpleNamespace(status="Submitted", warningText="")

        self.ib.openOrderEvent.emit(contract, order, order_state)

        self.db.add_order.assert_called_once()
        payload = self.db.add_order.call_args[0][0]
        self.assertEqual(payload.get("broker_order_id"), 1001)
        self.assertEqual(payload.get("asset_class"), "STK")
        self.assertEqual(payload.get("symbol"), "AAPL")
        self.assertEqual(payload.get("side"), "BUY")
        self.assertEqual(payload.get("quantity"), 10)
        self.assertEqual(payload.get("order_type"), "LMT")
        self.assertAlmostEqual(payload.get("limit_price"), 123.45)
        self.assertEqual(payload.get("tif"), "DAY")
        self.assertEqual(payload.get("status"), "SUBMITTED")  # normalized

    def test_open_order_updates_existing_order(self):
        # Existing order discovered via list_orders() by broker_order_id
        self.db.list_orders.return_value = [
            {"order_id": 1, "broker_order_id": 2002}
        ]

        contract = SimpleNamespace(symbol="MSFT", secType="STK")
        order = SimpleNamespace(
            orderId=2002,
            action="SELL",
            totalQuantity=5,
            orderType="STP",
            auxPrice=250.0,
            tif="GTC",
        )
        order_state = SimpleNamespace(status="Submitted", warningText=None)

        self.ib.openOrderEvent.emit(contract, order, order_state)

        self.db.update_order.assert_called_once()
        args, kwargs = self.db.update_order.call_args
        self.assertEqual(args[0], 1)  # internal id
        upd = args[1]
        self.assertEqual(upd.get("symbol"), "MSFT")
        self.assertEqual(upd.get("order_type"), "STP")
        self.assertAlmostEqual(upd.get("limit_price"), 250.0)
        self.assertEqual(upd.get("tif"), "GTC")
        self.assertEqual(upd.get("side"), "SELL")

    # --- orderStatusEvent ---

    def test_order_status_updates_known_order_fields(self):
        # Known order id mapping
        self.db.list_orders.return_value = [
            {"order_id": 5, "broker_order_id": 777}
        ]

        # Emit status: filled=3, avgFillPrice=150.25
        self.ib.orderStatusEvent.emit(777, "Filled", 3, 0, 150.25)

        self.db.update_order.assert_called_once()
        args, _ = self.db.update_order.call_args
        self.assertEqual(args[0], 5)
        updates = args[1]
        self.assertEqual(updates.get("status"), "FILLED")
        self.assertEqual(updates.get("filled_qty"), 3)
        self.assertAlmostEqual(updates.get("avg_price"), 150.25)

    def test_order_status_adopts_unknown_order_minimally(self):
        # No known orders -> should add a minimal record
        self.db.list_orders.return_value = []

        self.ib.orderStatusEvent.emit(9001, "Submitted", 0, 10, 0.0)

        self.db.add_order.assert_called_once()
        payload = self.db.add_order.call_args[0][0]
        self.assertEqual(payload.get("broker_order_id"), 9001)
        self.assertEqual(payload.get("status"), "SUBMITTED")
        # filled/avg may be present as well depending on event; not required here

    def test_handlers_noop_when_invalid_order_id(self):
        # Invalid id -> ignored (no db calls)
        self.ib.orderStatusEvent.emit(0, "Submitted", 0, 0, 0.0)
        self.db.update_order.assert_not_called()
        self.db.add_order.assert_not_called()

    def test_stop_prevents_further_processing(self):
        self.tracker.stop()
        contract = SimpleNamespace(symbol="AAPL", secType="STK")
        order = SimpleNamespace(orderId=101, action="BUY", totalQuantity=1, orderType="MKT", tif="DAY")
        state = SimpleNamespace(status="Submitted")
        # Emitting now should have no effect
        self.ib.openOrderEvent.emit(contract, order, state)
        self.db.add_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
