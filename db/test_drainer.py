import logging
import unittest
from unittest.mock import MagicMock, patch


from db import drainer


class FakeMemDB:
    """Tiny test DB similar to in-memory DB, exposing get_logs/get_order/get_fill."""
    def __init__(self, logs=None, orders=None, fills=None):
        # logs are append-only dicts with keys: seq, event_type, payload
        self._logs = list(logs or [])
        self._orders = dict(orders or {})
        self._fills = dict(fills or {})

    def get_logs(self, since_seq=None, limit=1000):
        since = -1 if since_seq is None else since_seq
        out = [e for e in self._logs if e.get('seq', -1) > since]
        return out[:limit]

    def get_order(self, order_id):
        return self._orders.get(order_id)

    def get_fill(self, fill_id):
        return self._fills.get(fill_id)


class TestOutboxDrainer(unittest.TestCase):
    def setUp(self):
        # Patch ORM models and transaction.atomic
        self.p_order = patch.object(drainer, "Order")
        self.p_fill = patch.object(drainer, "Fill")
        self.p_audit = patch.object(drainer, "AuditLog")
        self.p_cp = patch.object(drainer, "OutboxCheckpoint")
        self.p_tx = patch.object(drainer, "transaction")

        self.MockOrder = self.p_order.start()
        self.MockFill = self.p_fill.start()
        self.MockAudit = self.p_audit.start()
        self.MockCP = self.p_cp.start()
        self.MockTx = self.p_tx.start()

        # transaction.atomic as a no-op context manager
        self.MockTx.atomic.return_value.__enter__.return_value = None
        self.MockTx.atomic.return_value.__exit__.return_value = None

        # Default checkpoint row behavior: exists with last_seq = -1
        self.cp_row = MagicMock()
        self.cp_row.last_seq = -1
        self.MockCP.objects.get_or_create.return_value = (self.cp_row, False)
        self.MockCP.objects.select_for_update.return_value = self.MockCP.objects
        self.MockCP.objects.filter.return_value = self.MockCP.objects
        self.MockCP.objects.first.return_value = self.cp_row

        # Common Order.objects chain setup
        self.MockOrder.objects.update_or_create.return_value = (MagicMock(), True)
        self.MockOrder.objects.filter.return_value = self.MockOrder.objects
        self.MockOrder.objects.get.return_value = MagicMock()  # order instance

        # Common Fill.objects chain setup
        self.MockFill.objects.get_or_create.return_value = (MagicMock(), True)

    def tearDown(self):
        self.p_tx.stop()
        self.p_cp.stop()
        self.p_audit.stop()
        self.p_fill.stop()
        self.p_order.stop()

    def _mk_logs(self, *entries):
        """Helper to assign monotonically increasing seq if caller omits it."""
        fixed = []
        seq = 1

        for e in entries:
            d = dict(e)
            if 'seq' not in d:
                d['seq'] = seq
            seq = d['seq'] + 1
            fixed.append(d)

        return fixed


    def test_load_checkpoint_on_init(self):
        db = FakeMemDB()
        dr = drainer.OutboxDrainer(db, worker_id="test")
        self.MockCP.objects.get_or_create.assert_called_once_with(
            worker_id="test", defaults={'last_seq': -1}
        )
        self.assertEqual(dr._last_seq, -1)

    def test_order_added_upserts_from_mem_and_advances_checkpoint(self):
        # In-memory order snapshot
        order_rec = {
            'order_id': 101,
            'asset_class': 'STK',
            'symbol': 'AAPL',
            'side': 'BUY',
            'quantity': 10,
            'status': 'SUBMITTED',
            'avg_price': None,
            'filled_qty': 0,
            'broker_order_id': 555,
        }
        logs = self._mk_logs({'event_type': 'order_added', 'payload': {'order_id': 101}})
        db = FakeMemDB(logs=logs, orders={101: order_rec})
        dr = drainer.OutboxDrainer(db, worker_id="w1")

        applied = dr.drain_once()
        self.assertEqual(applied, 1)

        # Upsert called with snapshot fields
        self.MockOrder.objects.update_or_create.assert_called_once()
        args, kwargs = self.MockOrder.objects.update_or_create.call_args
        self.assertEqual(kwargs['order_id'], 101)
        dfl = kwargs['defaults']
        self.assertEqual(dfl['asset_class'], 'STK')
        self.assertEqual(dfl['symbol'], 'AAPL')
        self.assertEqual(dfl['side'], 'BUY')
        self.assertEqual(dfl['quantity'], 10)
        self.assertEqual(dfl['status'], 'SUBMITTED')
        self.assertEqual(dfl['filled_qty'], 0)
        self.assertEqual(dfl['broker_order_id'], 555)

        # Checkpoint advanced atomically
        self.MockCP.objects.filter.assert_called_with(worker_id="w1")
        args, kw = self.MockCP.objects.update.call_args
        self.assertEqual(args, ())  # no positional args
        self.assertIn('last_seq', kw)
        self.assertEqual(kw['last_seq'], logs[-1]['seq'])
        self.assertEqual(dr._last_seq, logs[-1]['seq'])

    def test_order_updated_uses_update_or_create_and_advances_checkpoint(self):
        order_rec = {
            'order_id': 7, 'asset_class': 'OPT', 'symbol': 'SPY',
            'side': 'SELL', 'quantity': 4, 'status': 'PARTIAL', 'filled_qty': 2,
        }
        logs = self._mk_logs({'event_type': 'order_updated', 'payload': {'order_id': 7}})
        db = FakeMemDB(logs=logs, orders={7: order_rec})
        dr = drainer.OutboxDrainer(db, worker_id="w2")

        applied = dr.drain_once()
        self.assertEqual(applied, 1)

        self.MockOrder.objects.update_or_create.assert_called_once()
        _, kwargs = self.MockOrder.objects.update_or_create.call_args
        self.assertEqual(kwargs['order_id'], 7)
        self.assertEqual(kwargs['defaults']['status'], 'PARTIAL')
        self.assertEqual(kwargs['defaults']['filled_qty'], 2)

        # checkpoint advanced
        self.MockCP.objects.update.assert_called_once()

    def test_fill_added_inserts_fill_and_updates_order(self):
        # In-memory order and fill snapshots
        order_rec = {
            'order_id': 3, 'asset_class': 'STK', 'symbol': 'MSFT',
            'side': 'BUY', 'quantity': 10, 'status': 'SUBMITTED',
            'filled_qty': 5, 'avg_price': 300.5, 'broker_order_id': 999,
        }
        fill_rec = {
            'fill_id': 2001, 'order_id': 3, 'exec_id': 'E123',
            'price': 300.5, 'filled_qty': 5, 'time': 'T1',
            'symbol': 'MSFT', 'side': 'BUY', 'broker_order_id': 999,
        }
        logs = self._mk_logs({'event_type': 'fill_added', 'payload': {'fill_id': 2001, 'order_id': 3}})
        db = FakeMemDB(logs=logs, orders={3: order_rec}, fills={2001: fill_rec})
        dr = drainer.OutboxDrainer(db, worker_id="w3")

        # The order instance returned by ORM get() needs a .fills manager for aggregate recompute path.
        order_obj = MagicMock()
        # Provide .fills.all().aggregate(...) dummy values in case recompute is used
        order_obj.fills.all().aggregate.return_value = {'total': 5, 'vwap_num': 1502.5}
        self.MockOrder.objects.get.return_value = order_obj

        applied = dr.drain_once()
        self.assertEqual(applied, 1)

        # Ensure order upsert happened (persist-from-mem)
        self.MockOrder.objects.update_or_create.assert_any_call(
            order_id=3,
            defaults=unittest.mock.ANY
        )

        # Fill get_or_create called with order + exec_id
        self.MockFill.objects.get_or_create.assert_called_once()
        args, kwargs = self.MockFill.objects.get_or_create.call_args
        self.assertIs(kwargs['order'], order_obj)
        self.assertEqual(kwargs['exec_id'], 'E123')
        self.assertEqual(kwargs['defaults']['price'], 300.5)
        self.assertEqual(kwargs['defaults']['filled_qty'], 5)
        self.assertEqual(kwargs['defaults']['broker_order_id'], 999)

        # Order aggregates updated (update called with filled_qty/avg_price/status)
        self.MockOrder.objects.filter.assert_called_with(pk=order_obj.pk)
        self.MockOrder.objects.update.assert_called()

        # checkpoint advanced
        self.MockCP.objects.update.assert_called_once()

    def test_deferred_events_are_acknowledged_but_not_persisted(self):
        logs = self._mk_logs(
            {'event_type': 'position_upserted', 'payload': {'position_key': ('AAPL',)}},
            {'event_type': 'position_deleted', 'payload': {'position_key': ('AAPL',)}},
            {'event_type': 'account_value_set', 'payload': {'key': ('DU1', 'NetLiq', 'USD')}},
        )
        db = FakeMemDB(logs=logs)
        dr = drainer.OutboxDrainer(db, worker_id="w4")

        applied = dr.drain_once()
        # All handled (acknowledged), but no ORM writes beyond checkpoint
        self.assertEqual(applied, 3)
        self.MockOrder.objects.update_or_create.assert_not_called()
        self.MockFill.objects.get_or_create.assert_not_called()
        # checkpoint still advanced
        self.MockCP.objects.update.assert_called_once()

    def test_unknown_events_are_skipped_and_checkpoint_still_advances(self):
        logs = self._mk_logs({'event_type': 'totally_unknown', 'payload': {'x': 1}})
        db = FakeMemDB(logs=logs)
        dr = drainer.OutboxDrainer(db, worker_id="w5")

        applied = dr.drain_once()
        # Not applied (False), but seq advanced
        self.assertEqual(applied, 0)
        self.MockCP.objects.update.assert_called_once()

    def test_failure_inside_transaction_does_not_advance_checkpoint(self):
        # This test by design triggers a call to logger.exception in drainer.py, which
        # prints out a stack trace to the terminal, which can make it seem like
        # the tests are failing. Silence that here!
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        # Make Fill path raise
        logs = self._mk_logs({'event_type': 'fill_added', 'payload': {'fill_id': 1, 'order_id': 10}})
        db = FakeMemDB(logs=logs, orders={10: {'order_id': 10}}, fills={1: {'fill_id': 1, 'order_id': 10}})
        dr = drainer.OutboxDrainer(db, worker_id="w6")

        # Cause _persist_fill_from_mem to raise by making Order.objects.get blow up
        self.MockOrder.objects.get.side_effect = RuntimeError("DB error")

        with self.assertRaises(RuntimeError):
            dr.drain_once()

        # Because the exception was raised within the atomic block, checkpoint update should not be called.
        self.MockCP.objects.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
