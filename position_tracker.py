"""Position and Account Tracker

Tracks live positions and account values.

Maintains an in-memory cache of positions and account values and exposes
helpers for querying them.

Behaviour:
- When `start()` is called it registers handlers on the provided IB instance:
  - `positionEvent` updates the positions cache incrementally.
  - `accountValueEvent` updates account values cache.
- When `stop()` is called it unregisters handlers.
- `get_positions()` and `get_position()` return the current cached state.

TODO:
- Persist positions/account-values to db.
- More robust reconnection / re-sync on reconnect.
- Stretch Goal: Implement is_shortable() (query IB shortable data or external some service).
- Stretch Goal: Support for multiple accounts: Add subscription filtering
"""

import logging
from threading import Lock

logger = logging.getLogger(__name__)


class PositionTracker:
    """Tracks positions and account values."""

    def __init__(self, ib, db=None):
        """Initialize the PositionTracker.

        Args:
            ib: IB - An active ib_async.IB instance to register event handlers on.
            db: InMemoryDB (Optional) - If provided, persist positions/account values on updates.
        """
        self.ib = ib
        self.db = db
        # positions keyed by (contract.conId or (symbol, secType, exchange, account))
        # value is dict with fields: contract, position, avgCost, account
        self._positions = {}
        # account values keyed by (account, tag, currency)
        # value is dict with fields: account, tag, value, currency
        self._account_values = {}
        self._lock = Lock()

        # holders for handler refs so we can unregister later
        self._position_handler = None
        self._account_value_handler = None

    def _on_position(self, account, contract, position, avgCost):
        """Internal handler for the IB positionEvent.

        Update the positions cache whenever IB reports a change.

        Args:
            account: str - IB account identifier.
            contract: Contract - ib_async Contract object for the position.
            position: float - Net position quantity (positive -> long, negative -> short).
            avgCost: float - Average cost of the position.
        """
        key = self._position_key(contract, account)
        removed = False
        with self._lock:
            if position == 0:
                if key in self._positions:
                    logger.debug("Position zeroed -> removing %s", key)
                    del self._positions[key]
                    removed = True

            else:
                self._positions[key] = {
                    'account': account,
                    'contract': contract,
                    'position': position,
                    'avgCost': avgCost
                }
        logger.debug("Position updated: %s -> %s (avgCost=%s)", key, position, avgCost)

        # Persist to DB (outside lock)
        if self.db:
            try:
                if removed:
                    self.db.delete_position(key)
                else:
                    self.db.upsert_position(key, {
                        'account': account,
                        'contract': contract,
                        'position': position,
                        'avgCost': avgCost
                    })
            except Exception:
                logger.exception("Failed to persist position update for key=%s", key)

    def _on_account_value(self, account, tag, value, currency):
        """Internal handler for the IB accountValueEvent.

        Updates the in-memory account values cache.

        Args:
            account: str - IB account identifier.
            tag: str - Name of the account value (e.g. "NetLiquidation").
            value: str - String form of value (as reported by IB).
            currency: str - Currency code, e.g. "USD".
        """
        key = (account, tag, currency)
        with self._lock:
            self._account_values[key] = {
                'account': account,
                'tag': tag,
                'value': value,
                'currency': currency
            }
        logger.debug("Account value updated: %s/%s = %s", tag, currency, value)

        if self.db:
            try:
                self.db.set_account_value(account, tag, currency, value)
            except Exception:
                logger.exception("Failed to persist account value key=%s", key)

    # TODO: Simplify this (move some logic to helpers..)
    def start(self):
        """Start tracking by registering event handlers and taking a snapshot.

        Returns:
            bool - True if handlers were registered and snapshot taken; False if already started.

        Raises:
            RuntimeError - If the provided IB instance is not connected.
        """
        if self._position_handler is not None:
            logger.warning("PositionTracker already started")
            return False

        if not getattr(self.ib, "isConnected", lambda: False)():
            raise RuntimeError("IB instance is not connected. call session.connect() first")

        def position_handler(account, contract, position, avgCost):
            """Register position handler."""
            try:
                self._on_position(account, contract, position, avgCost)

            except Exception as e:
                # Note on logging.exception vs ~.error:
                # https://docs.python.org/3/library/logging.html#logging.exception
                logger.exception(f"Error in position handler. Details: {e}")

        def account_value_handler(account, tag, value, currency):
            """Register account value handler."""
            try:
                self._on_account_value(account, tag, value, currency)

            except Exception as e:
                logger.exception(f"Error in account value handler. Details: {e}")

        self._position_handler = position_handler
        self._account_value_handler = account_value_handler

        self.ib.positionEvent += self._position_handler
        self.ib.accountValueEvent += self._account_value_handler

        # Request initial snapshot (synchronous calls)
        try:
            for pos in self.ib.positions():
                # ib_async Position has attributes: account, contract, position, avgCost
                account, contract = pos.account, pos.contract
                position, avgCost = pos.position, pos.avgCost

                self._on_position(account, contract, position, avgCost)

        except Exception:
            logger.exception("Failed to fetch initial positions snapshot")

        try:
            for av in self.ib.accountValues():
                # ib_async AccountValue has fields: account, tag, value, currency
                account, tag, value, currency = av.account, av.tag, av.value, av.currency
                self._on_account_value(account, tag, value, currency)

        except Exception:
            logger.exception("Failed to fetch initial account values snapshot")

        logger.info("PositionTracker started and initial snapshot taken")
        return True

    def stop(self):
        """Stop tracking (unregister event handlers).

        Returns:
            bool - True if handlers were removed; False if not running.

            TODO: Should we return False on exception?
        """
        if self._position_handler is None:
            logger.warning("PositionTracker not running")
            return False

        try:
            # Safe remove (ib_async Events behave like lists)
            self.ib.positionEvent.remove(self._position_handler)
            self.ib.accountValueEvent.remove(self._account_value_handler)

        except Exception:
            logger.exception("Error removing handlers from IB instance")

        self._position_handler = None
        self._account_value_handler = None
        logger.info("PositionTracker stopped")

        return True

    def get_positions(self):
        """Return a (shallow copy) snapshot of current positions.

        Returns:
            dict - Mapping position_key -> position dict, where each dict has
                the following keys:
                'account': str - Account id.
                'contract': Contract - ib_async contract object.
                'position': float - Quantity (positive for long, negative for short).
                'avgCost': float - Average cost per unit.
        """
        with self._lock:
            # Hybrid compromise between a shallow copy -- dict(self._positions) and
            # an expensive deep copy. Copy the first layer of the dict to prevent
            # overwrites.
            return {k: v.copy() for k, v in self._positions.items()}

    def get_position(self, contract_or_symbol, account=None):
        """Get a single position by contract object or symbol (ticker).

        Args:
            contract_or_symbol: Contract or str - Contract instance or ticker symbol.
            account: str (Optional) - If provided, restrict results to this account.

        Returns:
            dict - Position dict. See get_positions for structure. Returns
            None if no matching position is found.
        """
        with self._lock:
            # Contract...
            if hasattr(contract_or_symbol, 'conId'):
                target_conid = getattr(contract_or_symbol, 'conId', None)

                for v in self._positions.values():
                    c = v.get('contract')
                    if getattr(c, 'conId', None) == target_conid:
                        if account is None or v.get('account') == account:
                            return v

                return None

            # Ticker...
            symbol = str(contract_or_symbol).upper()
            for v in self._positions.values():
                c = v.get('contract')
                if getattr(c, 'symbol', '').upper() == symbol:
                    if account is None or v.get('account') == account:
                        return v

            return None

    def get_account_values(self):
        """Return (a shallow copy) snapshot of current account values.

        Returns:
            dict - Mapping (account, tag, currency) -> value dict, where each dict
                has the following keys:
                'account': str - Account id.
                'tag': str - Account value tag name.
                'value': str - String representation of Value (as provided by IB).
                'currency': str - Currency code for the value.

        TODO: Perhaps post-process "value" string into number. Find out potential
        gotchas.
        """
        with self._lock:
            # See note in get_positions
            return {k: v.copy() for k, v in self._account_values.items()}

    def is_shortable(self, symbol):
        """Check whether a symbol is shortable.

        Args:
            symbol: str - Ticker symbol to check shortability for.

        Returns:
            bool or None:
                True -> Confirmed shortable
                False -> Confirmed unshortable
                None -> Shortability unknown
        """
        # TODO: Implement this!
        logger.debug(f"is_shortable called for {symbol} - not yet implemented")

        return None

    def _position_key(self, contract, account_id):
        """Utility function to create a stable key for the positions dictionary.

        Prefer conId when available for uniqueness, else use a tuple of
        (symbol, secType, exchange, account).

        Args:
            contract: Contract - Contract object used to derive identity.
            account: str - Account id associated with the position.

        Returns:
            tuple - Tuple key suitable for indexing the internal positions dict.
        """
        conid = getattr(contract, 'conId', None)
        if conid:
            return (conid, account_id)

        return (
            getattr(contract, 'symbol', '').upper(),
            getattr(contract, 'secType', ''),
            getattr(contract, 'exchange', '').upper(),
            account_id
        )
