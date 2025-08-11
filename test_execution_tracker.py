from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from execution_tracker import ExecutionTracker
from _test_utils import FakeEvent


import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from execution_tracker import ExecutionTracker


class FakeEvent:
    """Minimal ib_insync.Event test double supporting +=, -=, remove(), len(), indexing, and call."""
    def __init__(self):
        self._handlers = []

    def __iadd__(self, handler):
        self._handlers.append(handler)
        return self

    def __isub__(self, handler):
        try:
            self._handlers.remove(handler)
        except ValueError:
            pass
        return self

    def remove(self, handler):
        try:
            self._handlers.remove(handler)
        except ValueError:
            pass

    def __len__(self):
        return len(self._handlers)

    def __getitem__(self, idx):
        return self._handlers[idx]


class TestExecutionTracker(unittest.TestCase):
    """Unit tests for ExecutionTracker."""

    def setUp(self):
        # Mock IB with event hooks
        self.ib = MagicMock()
        self.ib.isConnected.return_value = True
        self.ib.execDetailsEvent = FakeEvent()
        self.ib.commissionReportEvent = FakeEvent()
        self.ib.orderStatusEvent = FakeEvent()

        # Mock DB; list_orders maps broker_order_id -> internal order_id
        self.db = MagicMock()
        self.db.list_orders.return_value = [
            {'order_id': 1, 'broker_order_id': 42}
        ]

        self.tracker = ExecutionTracker(self.ib, self.db)

        # Common trade/objects
        self.trade = SimpleNamespace(
            order=SimpleNamespace(orderId=42, action='BUY'),
            contract=SimpleNamespace(symbol='AAPL')
        )

    def test_start_requires_connected_and_registers_handlers(self):
        """start() should require IB connected and register all handlers."""
        self.assertTrue(self.tracker.start())
        self.assertEqual(len(self.ib.execDetailsEvent), 1)
        self.assertEqual(len(self.ib.commissionReportEvent), 1)
        self.assertEqual(len(self.ib.orderStatusEvent), 1)

    def test_stop_unregisters_handlers(self):
        """stop() should remove handlers and become idempotent."""
        self.tracker.start()
        self.assertTrue(self.tracker.stop())
        # Second stop should return False
        self.assertFalse(self.tracker.stop())

    def test_exec_details_persists_fill_and_updates_order_aggregates(self):
        """execDetailsEvent → db.add_fill(...) and db.update_order(...) with aggregates."""
        self.tracker.start()
        handler = self.ib.execDetailsEvent[0]

        execution = SimpleNamespace(
            execId='E1',
            price=150.25,
            shares=3,
            time='2025-08-11 14:30:00',
            permId=999
        )
        fill = SimpleNamespace(execution=execution)

        handler(self.trade, fill)

        # Fill persisted
        self.db.add_fill.assert_called_once()
        args, kwargs = self.db.add_fill.call_args
        self.assertEqual(args[0], 1)  # internal order_id from list_orders mapping
        fill_payload = args[1]
        self.assertEqual(fill_payload['exec_id'], 'E1')
        self.assertEqual(fill_payload['price'], 150.25)
        self.assertEqual(fill_payload['filled_qty'], 3)
        self.assertEqual(fill_payload['broker_order_id'], 42)
        self.assertEqual(fill_payload['symbol'], 'AAPL')
        self.assertEqual(fill_payload['side'], 'BUY')

        # Aggregates updated on order
        self.db.update_order.assert_called()
        _, upd_kwargs = self.db.update_order.call_args
        # update_order called with positional args; extract dict safely
        _, upd_payload = self.db.update_order.call_args[0]
        self.assertEqual(upd_payload.get('broker_order_id'), 42)
        self.assertEqual(upd_payload.get('last_fill_price'), 150.25)
        self.assertEqual(upd_payload.get('last_exec_id'), 'E1')

    def test_commission_report_updates_order_record(self):
        """commissionReportEvent → db.update_order(...) with commission and realized PnL."""
        self.tracker.start()
        handler = self.ib.commissionReportEvent[0]

        comm = SimpleNamespace(commission=1.23, realizedPNL=4.56, currency='USD')
        handler(self.trade, comm)

        self.db.update_order.assert_called()
        _, payload = self.db.update_order.call_args[0]
        self.assertEqual(payload.get('commission'), 1.23)
        self.assertEqual(payload.get('realized_pnl'), 4.56)
        self.assertEqual(payload.get('commission_currency'), 'USD')
        self.assertEqual(payload.get('broker_order_id'), 42)

    def test_order_status_updates_status_and_fill_aggregates(self):
        """orderStatusEvent → db.update_order(...) with status, filled, remaining, avg price."""
        self.tracker.start()
        handler = self.ib.orderStatusEvent[0]

        status = SimpleNamespace(status='Filled', filled=10, remaining=0, avgFillPrice=151.00)
        trade = SimpleNamespace(order=SimpleNamespace(orderId=42), orderStatus=status)

        handler(trade)

        self.db.update_order.assert_called()
        _, payload = self.db.update_order.call_args[0]
        self.assertEqual(payload.get('broker_order_id'), 42)
        self.assertEqual(payload.get('status'), 'Filled')
        self.assertEqual(payload.get('filled_qty'), 10)
        self.assertEqual(payload.get('remaining_qty'), 0)
        self.assertEqual(payload.get('avg_price'), 151.00)

    def test_events_with_unknown_broker_id_are_ignored(self):
        """If no mapping is found, no DB writes should occur."""
        # Remap DB to have no matching broker_order_id
        self.db.list_orders.return_value = [{'order_id': 1, 'broker_order_id': 999}]
        self.tracker.start()

        # execDetails with broker order 42 (no mapping now)
        exec_handler = self.ib.execDetailsEvent[0]
        fill = SimpleNamespace(execution=SimpleNamespace(execId='E2', price=10, shares=1, time='t', permId=1))
        exec_handler(self.trade, fill)
        self.db.add_fill.assert_not_called()

        # commission with broker 42 (no mapping)
        comm_handler = self.ib.commissionReportEvent[0]
        comm_handler(self.trade, SimpleNamespace(commission=0.5, realizedPNL=0, currency='USD'))
        self.db.update_order.assert_not_called()

        # order status with broker 42 (no mapping)
        status_handler = self.ib.orderStatusEvent[0]
        status = SimpleNamespace(status='Submitted', filled=0, remaining=1, avgFillPrice=0.0)
        status_handler(SimpleNamespace(order=SimpleNamespace(orderId=42), orderStatus=status))
        self.db.update_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
