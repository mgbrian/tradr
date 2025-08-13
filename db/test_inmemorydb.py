import unittest
from unittest.mock import patch

from db.inmemorydb import InMemoryDB


class TestInMemoryDB(unittest.TestCase):
    """Unit tests for the in-memory database."""

    def setUp(self):
        """Create a fresh DB before each test."""
        self.db = InMemoryDB()

    # --- Orders ---

    def test_add_and_get_order_assigns_id_and_copies(self):
        """add_order assigns an id and get_order returns a copy (not same object)."""
        order_id = self.db.add_order({'symbol': 'AAPL', 'side': 'BUY', 'qty': 10})
        self.assertEqual(order_id, 1)

        order_record = self.db.get_order(order_id)
        self.assertIsNotNone(order_record)
        self.assertEqual(order_record['order_id'], 1)
        self.assertEqual(order_record['symbol'], 'AAPL')

        # Mutate returned copy; internal record should not change
        order_record['symbol'] = 'TSLA'
        order_record_again = self.db.get_order(order_id)
        self.assertEqual(order_record_again['symbol'], 'AAPL')

    def test_list_orders_sorted_by_updated_at_desc(self):
        """list_orders returns orders sorted by updated_at descending."""
        with patch('time.time', side_effect=[100.0, 100.0, 150.0, 150.0, 300.0, 300.0]):
            order_id_1 = self.db.add_order({'symbol': 'AAPL'})
            order_id_2 = self.db.add_order({'symbol': 'MSFT'})
            _ = self.db.update_order(order_id_1, {'note': 'touched later'})

        listed = self.db.list_orders()
        self.assertEqual([r['order_id'] for r in listed], [1, 2])  # o1 updated last, then o2

    def test_update_order_merges_and_timestamps(self):
        """update_order merges fields and updates timestamp."""
        with patch('time.time', return_value=100.0):
            order_id = self.db.add_order({'symbol': 'AAPL'})

        with patch('time.time', return_value=200.0):
            rec = self.db.update_order(order_id, {'status': 'SUBMITTED'})

        self.assertEqual(rec['status'], 'SUBMITTED')
        self.assertEqual(rec['updated_at'], 200.0)

    # --- Fills ---

    def test_add_fill_links_to_order_and_updates_aggregates(self):
        """add_fill attaches to order and can update order aggregates."""
        order_id = self.db.add_order({'symbol': 'AAPL', 'filled_qty': 0})
        fill_id = self.db.add_fill(order_id, {'exec_id': 'E1', 'filled_qty': 5, 'avg_price': 150.5})
        self.assertEqual(fill_id, 1)

        fill = self.db.get_fill(fill_id)
        self.assertEqual(fill['order_id'], order_id)
        self.assertEqual(fill['exec_id'], 'E1')

        # Order aggregates update
        order = self.db.get_order(order_id)
        self.assertEqual(order['filled_qty'], 5)
        self.assertEqual(order['avg_price'], 150.5)

    def test_list_fills_filters_by_order_and_limits(self):
        """list_fills supports filtering by order_id and limiting."""
        order_id_1 = self.db.add_order({'symbol': 'AAPL'})
        order_id_2 = self.db.add_order({'symbol': 'MSFT'})
        self.db.add_fill(order_id_1, {'exec_id': 'A', 'created_at': 10})
        self.db.add_fill(order_id_1, {'exec_id': 'B', 'created_at': 20})
        self.db.add_fill(order_id_2, {'exec_id': 'C', 'created_at': 30})

        all_fills = self.db.list_fills()
        self.assertEqual([f['exec_id'] for f in all_fills], ['C', 'B', 'A'])  # by created_at desc

        order_1_fills = self.db.list_fills(order_id=order_id_1)
        self.assertEqual([f['exec_id'] for f in order_1_fills], ['B', 'A'])

        limited = self.db.list_fills(limit=2)
        self.assertEqual(len(limited), 2)

    # --- Positions ---

    def test_upsert_and_delete_position_and_copy_on_read(self):
        """upsert_position stores a copy; get_positions returns copies; delete removes."""
        key = ('AAPL', 'STK', 'SMART', 'DU1')
        rec = {'account': 'DU1', 'contract': {'symbol': 'AAPL'}, 'position': 10, 'avgCost': 150.0}

        upserted_position_record = self.db.upsert_position(key, rec)
        self.assertEqual(upserted_position_record['position'], 10)

        # Returned snapshot is a copy (mutations do not leak)
        snap = self.db.get_positions()
        snap[key]['position'] = 999
        upserted_position_record_again = self.db.get_positions()
        self.assertEqual(upserted_position_record_again[key]['position'], 10)

        # Delete
        self.assertTrue(self.db.delete_position(key))
        self.assertFalse(self.db.delete_position(key))  # second delete no-op
        self.assertEqual(self.db.get_positions(), {})

    # --- Account Values ---

    def test_set_and_get_account_values_copy_on_read(self):
        """set_account_value stores; get_account_values returns copies."""
        self.db.set_account_value('DU1', 'NetLiquidation', 'USD', '100000')
        account_snapshot = self.db.get_account_values()
        key = ('DU1', 'NetLiquidation', 'USD')
        self.assertIn(key, account_snapshot)
        self.assertEqual(account_snapshot[key]['value'], '100000')

        # Mutate copy; internal state remains unchanged
        account_snapshot[key]['value'] = '0'
        again = self.db.get_account_values()
        self.assertEqual(again[key]['value'], '100000')

    # --- Audit Log ---

    def test_append_log_and_get_logs_with_since_and_limit(self):
        """append_log increments seq; get_logs supports since_seq and limit."""
        with patch('time.time', side_effect=[10.0, 20.0, 30.0]):
            s1 = self.db.append_log('event1', {'a': 1})
            s2 = self.db.append_log('event2', {'b': 2})
            s3 = self.db.append_log('event3', {'c': 3})

        self.assertEqual((s1, s2, s3), (1, 2, 3))

        # All (default limit)
        all_rows = self.db.get_logs()
        self.assertEqual([r['seq'] for r in all_rows], [1, 2, 3])

        # Since seq
        rows_after_1 = self.db.get_logs(since_seq=1)
        self.assertEqual([r['seq'] for r in rows_after_1], [2, 3])

        # Limit
        limited = self.db.get_logs(limit=2)
        self.assertEqual([r['seq'] for r in limited], [2, 3])


if __name__ == "__main__":
    unittest.main()
