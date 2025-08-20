import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from order_tracker import OrderTracker
from _test_utils import FakeEvent


class TestOrderTracker(unittest.TestCase):
    def setUp(self):
        # Fake IB with events; leave .loop absent to avoid snapshot threading
        self.ib = SimpleNamespace(
            openOrderEvent=FakeEvent(),
            orderStatusEvent=FakeEvent(),
            # .loop intentionally not set; snapshot path will no-op
        )
        # Mock DB
        self.db = MagicMock()
        self.db.list_orders.return_value = []

        # Build tracker; stub out the initial snapshot kick so start() stays deterministic
        self.tracker = OrderTracker(self.ib, db=self.db)
        self.tracker.refresh_now = MagicMock()
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
        self.tracker.refresh_now = MagicMock()
        self.tracker.start()
        self.assertGreater(self.ib.openOrderEvent.handler_count, 0)

    # --- openOrderEvent (Trade payload) ---

    def test_open_order_adopts_unknown_trade_with_positive_order_id(self):
        # Expect add_order with full metadata and both ids present
        self.tracker._schedule_snapshot_refresh = MagicMock()

        contract = SimpleNamespace(symbol="AAPL", localSymbol="AAPL", secType="STK")
        order = SimpleNamespace(
            orderId=1001,
            permId=5550001,
            action="BUY",
            totalQuantity=10,
            orderType="LMT",
            lmtPrice=123.45,
            tif="DAY",
        )
        order_status = SimpleNamespace(status="Submitted", filled=0, avgFillPrice=0.0)

        trade = SimpleNamespace(contract=contract, order=order, orderStatus=order_status)

        self.ib.openOrderEvent.emit(trade)

        self.db.add_order.assert_called_once()
        payload = self.db.add_order.call_args[0][0]
        self.assertEqual(payload.get("broker_order_id"), 1001)
        self.assertEqual(payload.get("perm_id"), 5550001)
        self.assertEqual(payload.get("asset_class"), "STK")
        self.assertEqual(payload.get("symbol"), "AAPL")
        self.assertEqual(payload.get("side"), "BUY")
        self.assertEqual(payload.get("quantity"), 10)
        self.assertEqual(payload.get("order_type"), "LMT")
        self.assertAlmostEqual(payload.get("limit_price"), 123.45)
        self.assertEqual(payload.get("tif"), "DAY")
        self.assertEqual(payload.get("status"), "SUBMITTED")  # normalized
        self.tracker._schedule_snapshot_refresh.assert_called_once()

    def test_open_order_adopts_unknown_trade_with_only_perm_id(self):
        # TWS-originated: negative orderId but valid permId -> key by permId only
        self.tracker._schedule_snapshot_refresh = MagicMock()

        contract = SimpleNamespace(symbol="MSFT", localSymbol="MSFT", secType="STK")
        order = SimpleNamespace(
            orderId=-14,        # <= 0 (TWS manual order)
            permId=656673104,
            action="BUY",
            totalQuantity=5,
            orderType="LMT",
            lmtPrice=200.0,
            tif="DAY",
        )
        # Sometimes permId also appears on orderStatus; include to mirror real payloads
        order_status = SimpleNamespace(status="PreSubmitted", permId=656673104, filled=0, avgFillPrice=0.0)

        trade = SimpleNamespace(contract=contract, order=order, orderStatus=order_status)

        self.ib.openOrderEvent.emit(trade)

        self.db.add_order.assert_called_once()
        payload = self.db.add_order.call_args[0][0]
        # broker_order_id should be absent (or not positive) when orderId <= 0
        self.assertTrue("broker_order_id" not in payload or (payload.get("broker_order_id") in (None, 0)))
        self.assertEqual(payload.get("perm_id"), 656673104)
        self.assertEqual(payload.get("symbol"), "MSFT")
        self.assertEqual(payload.get("order_type"), "LMT")
        self.assertAlmostEqual(payload.get("limit_price"), 200.0)
        self.assertEqual(payload.get("status"), "SUBMITTED")
        self.tracker._schedule_snapshot_refresh.assert_called_once()

    def test_open_order_updates_existing_by_broker_id(self):
        # Existing order discovered via list_orders() by broker_order_id
        self.db.list_orders.return_value = [{"order_id": 1, "broker_order_id": 2002}]

        self.tracker._schedule_snapshot_refresh = MagicMock()

        contract = SimpleNamespace(symbol="NVDA", secType="STK")
        order = SimpleNamespace(
            orderId=2002,
            permId=999001,
            action="SELL",
            totalQuantity=3,
            orderType="STP",
            auxPrice=250.0,
            tif="GTC",
        )
        order_status = SimpleNamespace(status="Submitted", filled=0, avgFillPrice=0.0)
        trade = SimpleNamespace(contract=contract, order=order, orderStatus=order_status)

        self.ib.openOrderEvent.emit(trade)

        self.db.update_order.assert_called_once()
        args, _ = self.db.update_order.call_args
        self.assertEqual(args[0], 1)  # internal id
        upd = args[1]
        self.assertEqual(upd.get("symbol"), "NVDA")
        self.assertEqual(upd.get("order_type"), "STP")
        self.assertAlmostEqual(upd.get("limit_price"), 250.0)
        self.assertEqual(upd.get("tif"), "GTC")
        self.assertEqual(upd.get("side"), "SELL")
        self.tracker._schedule_snapshot_refresh.assert_called_once()

    def test_open_order_updates_existing_by_perm_id(self):
        # No broker id, match by perm_id
        self.db.list_orders.return_value = [{"order_id": 7, "perm_id": 888888}]
        self.tracker._schedule_snapshot_refresh = MagicMock()

        contract = SimpleNamespace(symbol="TSLA", secType="STK")
        order = SimpleNamespace(
            orderId=0,
            permId=888888,
            action="BUY",
            totalQuantity=2,
            orderType="LMT",
            lmtPrice=190.5,
            tif="DAY",
        )
        order_status = SimpleNamespace(status="Submitted", filled=0, avgFillPrice=0.0, permId=888888)
        trade = SimpleNamespace(contract=contract, order=order, orderStatus=order_status)

        self.ib.openOrderEvent.emit(trade)

        self.db.update_order.assert_called_once()
        args, _ = self.db.update_order.call_args
        self.assertEqual(args[0], 7)
        upd = args[1]
        self.assertEqual(upd.get("symbol"), "TSLA")
        self.assertEqual(upd.get("order_type"), "LMT")
        self.assertAlmostEqual(upd.get("limit_price"), 190.5)
        self.tracker._schedule_snapshot_refresh.assert_called_once()

    def test_open_order_unexpected_payload_schedules_snapshot_only(self):
        # Emit with no args -> should only schedule snapshot, no DB I/O
        self.tracker._schedule_snapshot_refresh = MagicMock()
        self.ib.openOrderEvent.emit()
        self.db.add_order.assert_not_called()
        self.db.update_order.assert_not_called()
        self.tracker._schedule_snapshot_refresh.assert_called_once()

    # --- orderStatusEvent (Trade payload) ---

    def test_order_status_updates_known_order_fields_by_broker_id(self):
        # Known order id mapping
        self.db.list_orders.return_value = [{"order_id": 5, "broker_order_id": 777}]
        self.tracker._schedule_snapshot_refresh = MagicMock()

        order = SimpleNamespace(orderId=777, permId=101010)
        st = SimpleNamespace(status="Filled", filled=3, remaining=0, avgFillPrice=150.25)
        trade = SimpleNamespace(order=order, orderStatus=st, contract=SimpleNamespace(symbol="AAPL", secType="STK"))

        self.ib.orderStatusEvent.emit(trade)

        self.db.update_order.assert_called_once()
        args, _ = self.db.update_order.call_args
        self.assertEqual(args[0], 5)
        updates = args[1]
        self.assertEqual(updates.get("status"), "FILLED")
        self.assertEqual(updates.get("filled_qty"), 3)
        self.assertAlmostEqual(updates.get("avg_price"), 150.25)
        self.tracker._schedule_snapshot_refresh.assert_called_once()

    def test_order_status_adopts_unknown_order_minimally_by_perm_id(self):
        # Unknown order -> should add minimal record, keyed by perm_id
        self.db.list_orders.return_value = []
        self.tracker._schedule_snapshot_refresh = MagicMock()

        order = SimpleNamespace(orderId=-1, permId=909090)
        st = SimpleNamespace(status="Submitted", filled=0, remaining=10, avgFillPrice=0.0, permId=909090)
        trade = SimpleNamespace(order=order, orderStatus=st)

        self.ib.orderStatusEvent.emit(trade)

        self.db.add_order.assert_called_once()
        payload = self.db.add_order.call_args[0][0]
        # Should prefer perm_id since orderId <= 0
        self.assertTrue("broker_order_id" not in payload or (payload.get("broker_order_id") in (None, 0)))
        self.assertEqual(payload.get("perm_id"), 909090)
        self.assertEqual(payload.get("status"), "SUBMITTED")
        self.tracker._schedule_snapshot_refresh.assert_called_once()

    def test_order_status_ignores_unexpected_payload_but_schedules_snapshot(self):
        self.tracker._schedule_snapshot_refresh = MagicMock()
        # No args / unexpected -> no DB calls, but snapshot should be queued
        self.ib.orderStatusEvent.emit()
        self.db.update_order.assert_not_called()
        self.db.add_order.assert_not_called()
        self.tracker._schedule_snapshot_refresh.assert_called_once()

    # --- Stop behaviour ---

    def test_stop_prevents_further_processing(self):
        self.tracker.stop()

        contract = SimpleNamespace(symbol="AAPL", secType="STK")
        order = SimpleNamespace(orderId=101, permId=1, action="BUY", totalQuantity=1, orderType="MKT", tif="DAY")
        state = SimpleNamespace(status="Submitted")
        trade = SimpleNamespace(contract=contract, order=order, orderStatus=state)

        # Emitting now should have no effect
        self.ib.openOrderEvent.emit(trade)
        self.db.add_order.assert_not_called()
        self.db.update_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
