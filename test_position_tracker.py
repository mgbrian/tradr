from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from position_tracker import PositionTracker
from _test_utils import FakeEvent



class TestPositionTracker(unittest.TestCase):
    """Unit tests for PositionTracker."""

    def setUp(self):
        """Create a mocked IB object. with list-like (using FakeEvent) event
            hooks and snapshot methods.
        """
        self.ib = MagicMock()

        self.ib.positionEvent = FakeEvent()
        self.ib.accountValueEvent = FakeEvent()

        # start() requires IB.isConnected() to be True
        self.ib.isConnected.return_value = True

        # Initial positions snapshot
        aapl_contract = SimpleNamespace(conId=101, symbol='AAPL', secType='STK', exchange='NASDAQ')
        aapl_pos = SimpleNamespace(account='DU111111', contract=aapl_contract, position=10, avgCost=150.0)
        self.ib.positions.return_value = [aapl_pos]

        # Initial account values snapshot
        av = SimpleNamespace(account='DU111111', tag='NetLiquidation', value='100000', currency='USD')
        self.ib.accountValues.return_value = [av]

        self.tracker = PositionTracker(self.ib)

    def test_start_requires_active_connection(self):
        """start() should raise if IB is not connected."""
        self.ib.isConnected.return_value = False
        with self.assertRaises(RuntimeError):
            self.tracker.start()

        self.ib.isConnected.return_value = True
        self.assertTrue(self.tracker.start())

    def test_start_registers_handlers_and_loads_snapshot(self):
        """start() should register handlers and ingest initial snapshots."""
        result = self.tracker.start()
        self.assertTrue(result)
        self.assertEqual(len(self.ib.positionEvent), 1)
        self.assertEqual(len(self.ib.accountValueEvent), 1)

        # Positions snapshot loaded
        positions = self.tracker.get_positions()
        self.assertEqual(len(positions), 1)
        pos = list(positions.values())[0]
        self.assertEqual(pos['account'], 'DU111111')
        self.assertEqual(pos['position'], 10)
        self.assertEqual(pos['avgCost'], 150.0)
        self.assertEqual(getattr(pos['contract'], 'symbol'), 'AAPL')

        # Account values snapshot loaded
        avs = self.tracker.get_account_values()
        self.assertEqual(len(avs), 1)
        key = ('DU111111', 'NetLiquidation', 'USD')
        self.assertIn(key, avs)
        self.assertEqual(avs[key]['value'], '100000')

    def test_double_start_returns_false_and_does_not_duplicate_handlers(self):
        """Calling start() twice should return False and not add duplicate handlers."""
        self.assertTrue(self.tracker.start())
        before_pos_handlers = len(self.ib.positionEvent)
        before_av_handlers = len(self.ib.accountValueEvent)

        self.assertFalse(self.tracker.start())
        self.assertEqual(len(self.ib.positionEvent), before_pos_handlers)
        self.assertEqual(len(self.ib.accountValueEvent), before_av_handlers)

    def test_position_event_updates_and_zero_removes(self):
        """positionEvent handler should update cache and remove entries when size goes to zero."""
        self.tracker.start()
        handler = self.ib.positionEvent[0]

        # Add TSLA position
        tsla_contract = SimpleNamespace(conId=202, symbol='TSLA', secType='STK', exchange='NASDAQ')
        handler('DU111111', tsla_contract, 5, 700.0)
        positions = self.tracker.get_positions()
        found_tsla = any(v['contract'].conId == 202 and v['position'] == 5 for v in positions.values())
        self.assertTrue(found_tsla)

        # Simulate removal of TSLA
        handler('DU111111', tsla_contract, 0, 700.0)
        positions = self.tracker.get_positions()
        found_tsla_after_removal = any(v['contract'].conId == 202 for v in positions.values())
        self.assertFalse(found_tsla_after_removal)

    def test_account_value_event_updates_cache(self):
        """accountValueEvent handler should update the account values mapping."""
        self.tracker.start()
        handler = self.ib.accountValueEvent[0]

        # Update account's cash balance
        handler('DU111111', 'CashBalance', '50000', 'USD')
        avs = self.tracker.get_account_values()
        acc1_key = ('DU111111', 'CashBalance', 'USD')
        self.assertIn(acc1_key, avs)
        self.assertEqual(avs[acc1_key]['value'], '50000')

        # Update account 2 cash balance
        handler('DU111112', 'CashBalance', '1000000', 'USD')
        avs = self.tracker.get_account_values()
        acc2_key = ('DU111112', 'CashBalance', 'USD')
        self.assertIn(acc2_key, avs)
        self.assertEqual(avs[acc2_key]['value'], '1000000')

        # And... account 1's balance remains unchanged.
        self.assertIn(acc1_key, avs)
        self.assertEqual(avs[acc1_key]['value'], '50000')

    def test_get_position_by_symbol_and_contract(self):
        """get_position() should work for both symbol and contract lookup."""
        self.tracker.start()

        # By symbol
        pos_by_symbol = self.tracker.get_position('AAPL')
        self.assertIsNotNone(pos_by_symbol)
        self.assertEqual(pos_by_symbol['position'], 10)

        # By contract
        any_pos = list(self.tracker.get_positions().values())[0]
        contract = any_pos['contract']
        pos_by_contract = self.tracker.get_position(contract)
        self.assertIsNotNone(pos_by_contract)
        self.assertEqual(pos_by_contract['avgCost'], 150.0)

    def test_get_positions_and_account_values_return_shallow_copies(self):
        """Returned dicts should be copies; mutating them must not affect internal state."""
        self.tracker.start()

        # These confirm that this behaves like a shallow copy.

        # Positions copy
        extracted_positions = self.tracker.get_positions()
        extracted_positions.clear()
        self.assertGreater(len(self.tracker.get_positions()), 0)

        # Account values copy
        extracted_avs = self.tracker.get_account_values()
        extracted_avs.clear()
        self.assertGreater(len(self.tracker.get_account_values()), 0)

        # Additional tests to confirm hybrid "first-layer deepcopy" behaviour:
        # (see note in get_positions)
        # Mutating fields on returned snapshot must not change internal state

        # Positions
        before_positions = self.tracker.get_positions()
        self.assertGreater(len(before_positions), 0)
        pos_key = next(iter(before_positions))
        orig_qty = before_positions[pos_key]['position']
        orig_avg = before_positions[pos_key]['avgCost']

        # Mutate the copy's inner dict
        copy_positions = self.tracker.get_positions()
        copy_positions[pos_key]['position'] = 999
        copy_positions[pos_key]['avgCost'] = 123.45
        copy_positions[pos_key]['new_field'] = 'should_not_leak'

        # Internal state unchanged
        after_positions = self.tracker.get_positions()
        self.assertEqual(after_positions[pos_key]['position'], orig_qty)
        self.assertEqual(after_positions[pos_key]['avgCost'], orig_avg)
        self.assertNotIn('new_field', after_positions[pos_key])

        # Account values
        before_avs = self.tracker.get_account_values()
        self.assertGreater(len(before_avs), 0)
        av_key = next(iter(before_avs))
        orig_val = before_avs[av_key]['value']

        # Mutate the copy's inner dict
        copy_avs = self.tracker.get_account_values()
        copy_avs[av_key]['value'] = '999999'
        copy_avs[av_key]['extra'] = 'no_leak'

        # Internal state unchanged
        after_avs = self.tracker.get_account_values()
        self.assertEqual(after_avs[av_key]['value'], orig_val)
        self.assertNotIn('extra', after_avs[av_key])

    def test_stop_unregisters_handlers(self):
        """stop() should remove handlers and reset internal references."""
        self.tracker.start()
        self.assertTrue(self.tracker.stop())

        # Handlers references cleared
        self.assertIsNone(self.tracker._position_handler)
        self.assertIsNone(self.tracker._account_value_handler)

        # If we try to stop again, it returns False
        self.assertFalse(self.tracker.stop())


class TestPositionTrackerWithDB(unittest.TestCase):
    """Tests that PositionTracker persists to DB on snapshots and events."""

    def setUp(self):
        self.ib = MagicMock()
        self.db = MagicMock()

        # Events as ib_insync-like objects
        self.ib.positionEvent = FakeEvent()
        self.ib.accountValueEvent = FakeEvent()

        # Must be connected for start()
        self.ib.isConnected.return_value = True

        # Initial snapshot
        self.account = 'DU111111'
        self.aapl_contract = SimpleNamespace(conId=101, symbol='AAPL', secType='STK', exchange='NASDAQ')
        self.ib.positions.return_value = [
            SimpleNamespace(account=self.account, contract=self.aapl_contract, position=10, avgCost=150.0)
        ]
        self.ib.accountValues.return_value = [
            SimpleNamespace(account=self.account, tag='NetLiquidation', value='100000', currency='USD')
        ]

        self.tracker = PositionTracker(self.ib, db=self.db)

    def test_start_persists_initial_positions_and_account_values(self):
        """start() should upsert initial positions and set initial account values in DB."""
        self.tracker.start()

        # Position upsert from snapshot
        # key is (conId, account) since conId is present
        expected_pos_key = (self.aapl_contract.conId, self.account)
        self.db.upsert_position.assert_any_call(
            expected_pos_key,
            {
                'account': self.account,
                'contract': self.aapl_contract,
                'position': 10,
                'avgCost': 150.0
            }
        )

        # Account value set from snapshot
        self.db.set_account_value.assert_any_call(self.account, 'NetLiquidation', 'USD', '100000')

    def test_position_event_triggers_db_upsert_and_delete(self):
        """positionEvent should upsert non-zero and delete when zero."""
        self.tracker.start()
        handler = self.ib.positionEvent[0]

        # Add TSLA +5
        tsla = SimpleNamespace(conId=202, symbol='TSLA', secType='STK', exchange='NASDAQ')
        handler(self.account, tsla, 5, 700.0)
        self.db.upsert_position.assert_any_call(
            (202, self.account),
            {
                'account': self.account,
                'contract': tsla,
                'position': 5,
                'avgCost': 700.0
            }
        )

        # Zero out TSLA -> delete
        handler(self.account, tsla, 0, 700.0)
        self.db.delete_position.assert_any_call((202, self.account))

    def test_account_value_event_triggers_db_set(self):
        """accountValueEvent should call db.set_account_value with the new value."""
        self.tracker.start()
        handler = self.ib.accountValueEvent[0]

        handler(self.account, 'CashBalance', '50000', 'USD')
        self.db.set_account_value.assert_any_call(self.account, 'CashBalance', 'USD', '50000')




if __name__ == "__main__":
    unittest.main()
