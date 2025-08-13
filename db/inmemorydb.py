"""Persistence layer.

Provides a thin, thread-safe in-memory database with copy-on-read semantics.
The long-term plan is to write to the in-memory cache first and
then asynchronously push changes to Postgres or some other persistent db.

Data categories:
- Orders
- Fills
- Positions
- Account values
- Append-only (sequence-based) audit log

TODO:
- Implement asynchronous Postgres writes.
    - Add schema validation and stronger types where appropriate.
      ** This should be covered if we're using the Django ORM.
- Stretch Goal: Provide durable write-ahead if needed for crash recovery.
"""

import itertools
import threading
import time


class InMemoryDB:
    """Thread-safe in-memory store for orders, fills, positions, account values, and logs."""

    def __init__(self):
        """Initialize an empty in-memory database."""
        self._lock = threading.Lock()

        # Primary stores

        # order_id -> dict
        self._orders = {}
        # fill_id -> dict
        self._fills = {}
        # position_key -> dict (see PositionTracker._position_key())
        self._positions = {}
        # (account, tag, currency) -> dict
        self._account_values = {}

        # Append-only audit log (seq -> dict)
        self._log = []

        # ID generators
        self._order_id_seq = itertools.count(1)
        self._fill_id_seq = itertools.count(1)
        self._log_seq = itertools.count(1)

    # --- Order ---

    def add_order(self, order_record):
        """Add a new order record.

        The caller provides a dict describing the order (symbol, side, qty, etc.).
        This function assigns `order_id`, timestamps it, stores it, and logs an event.

        Args:
            order_record: dict - Arbitrary order fields (symbol, side, qty, etc.).

        Returns:
            int - The assigned order_id.

        Raises:
            ValueError - If order_record is not a dict.
        """
        if not isinstance(order_record, dict):
            raise ValueError("order_record must be a dict")

        now = time.time()
        with self._lock:
            order_id = next(self._order_id_seq)
            rec = order_record.copy()
            rec['order_id'] = order_id
            rec.setdefault('created_at', now)
            rec.setdefault('updated_at', now)
            self._orders[order_id] = rec

            self._append_log_locked('order_added', {'order_id': order_id})

            return order_id

    def update_order(self, order_id, updates):
        """Update fields on an existing order and timestamp the change.

        Args:
            order_id: int - The id of the order to update.
            updates: dict - Fields to merge into the existing order record.

        Returns:
            dict - A copy of the updated order record.

        Raises:
            KeyError - If the order_id does not exist.
            ValueError - If updates is not a dict.
        """
        if not isinstance(updates, dict):
            raise ValueError("updates must be a dict")

        with self._lock:
            if order_id not in self._orders:
                raise KeyError(f"order_id {order_id} not found")

            rec = self._orders[order_id]
            rec.update(updates)
            rec['updated_at'] = time.time()

            self._append_log_locked('order_updated', {'order_id': order_id, 'updates': updates.copy()})

            return rec.copy()

    def get_order(self, order_id):
        """Fetch a single order by id.

        Args:
            order_id: int - The id of the order.

        Returns:
            dict or None - A copy of the order record, or None if not found.
        """
        with self._lock:
            rec = self._orders.get(order_id)
            return rec.copy() if rec else None

    def list_orders(self, limit=None):
        """List orders, optionally limited (ordered from most recently updated).

        Args:
            limit: int (Optional) - Maximum number of records to return.

        Returns:
            list - A list of order dict copies, most-recently-updated first.
        """
        with self._lock:
            rows = sorted(self._orders.values(), key=lambda r: r.get('updated_at', 0), reverse=True)
            if limit is not None:
                rows = rows[:int(limit)]

            return [r.copy() for r in rows]

    # --- Fill ---

    def add_fill(self, order_id, fill_record):
        """Add a new fill associated with an order.

        Args:
            order_id: int - The parent order id.
            fill_record: dict - Arbitrary fill fields (price, qty, exec_id, ts, etc.)

        Returns:
            int - The assigned fill_id.

        Raises:
            KeyError - If order_id does not exist.
            ValueError - If fill_record is not a dict.
        """
        if not isinstance(fill_record, dict):
            raise ValueError("fill_record must be a dict")

        with self._lock:
            if order_id not in self._orders:
                raise KeyError(f"order_id {order_id} not found")

            fill_id = next(self._fill_id_seq)
            rec = fill_record.copy()
            rec['fill_id'] = fill_id
            rec['order_id'] = order_id
            rec.setdefault('created_at', time.time())
            self._fills[fill_id] = rec

            # update order's filled_qty/avg_price if provided
            # TODO: Sanity checks here, e.g. that fill is not > order qty?
            order = self._orders[order_id]

            if 'filled_qty' in rec:
                order['filled_qty'] = order.get('filled_qty', 0) + rec['filled_qty']
                order['updated_at'] = time.time()

            if 'avg_price' in rec:
                order['avg_price'] = rec['avg_price']
                order['updated_at'] = time.time()

            self._append_log_locked('fill_added', {'fill_id': fill_id, 'order_id': order_id})

            return fill_id

    def get_fill(self, fill_id):
        """Fetch a single fill by id.

        Args:
            fill_id: int - The id of the fill.

        Returns:
            dict or None - A copy of the fill record, or None if not found.
        """
        with self._lock:
            rec = self._fills.get(fill_id)
            return rec.copy() if rec else None

    def list_fills(self, order_id=None, limit=None):
        """List fills, optionally filtered by order_id and limited (in reverse creation order).

        Args:
            order_id: int (Optional) - If provided, only returns fills for this order.
            limit: int (Optional) - Maximum number of records to return.

        Returns:
            list - A list of fill dict copies, most-recent-first by created_at.
        """
        with self._lock:
            rows = list(self._fills.values())

            if order_id is not None:
                rows = [r for r in rows if r.get('order_id') == order_id]

            rows.sort(key=lambda r: r.get('created_at', 0), reverse=True)

            if limit is not None:
                rows = rows[:int(limit)]

            return [r.copy() for r in rows]

    # --- Position ---

    def upsert_position(self, position_key, position_record):
        """Insert or update a position by key.

        position_key should be a stable tuple using the same logic as in
        PositionTracker._position_key

        TODO: If this is used outside of PositionTracker, perhaps have a friendlier
        interface/way to pass the key.

        Args:
            position_key: tuple - Unique key for the position.
            position_record: dict - Fields like account, contract, position, avgCost.

        Returns:
            dict - A copy of the upserted position record.

        Raises:
            ValueError - If position_record is not a dict.
        """
        if not isinstance(position_record, dict):
            raise ValueError("position_record must be a dict")

        with self._lock:
            self._positions[position_key] = position_record.copy()
            self._append_log_locked('position_upserted', {'position_key': position_key})

            return self._positions[position_key].copy()

    def delete_position(self, position_key):
        """Delete a position by key.

        Args:
            position_key: tuple - Unique key for the position.

        Returns:
            bool - True if a position was removed, False if it wasn't present.
        """
        with self._lock:
            existed = position_key in self._positions
            if existed:
                del self._positions[position_key]
                self._append_log_locked('position_deleted', {'position_key': position_key})

            return existed

    def get_positions(self):
        """Return a snapshot of positions with first-layer copies.

        Returns:
            dict - Mapping position_key -> position dict (each inner dict copied).
        """
        with self._lock:
            # See note in PositionTracker.get_positions
            return {k: v.copy() for k, v in self._positions.items()}

    # --- Account Value ---

    def set_account_value(self, account, tag, currency, value):
        """Set or update an account value.

        Args:
            account: str - Account id.
            tag: str - Account metric name (e.g. 'NetLiquidation').
            currency: str - Currency code for the value.
            value: str - String value as provided by IB.

        Returns:
            dict - A copy of the updated account value record.
        """
        key = (account, tag, currency)
        with self._lock:
            rec = {
                'account': account,
                'tag': tag,
                'currency': currency,
                'value': value
            }
            self._account_values[key] = rec
            self._append_log_locked('account_value_set', {'key': key, 'value': value})

            return rec.copy()

    def get_account_values(self):
        """Return a snapshot of account values with first-layer copies.

        Returns:
            dict - Mapping (account, tag, currency) -> value dict (inner dict copied).
        """
        with self._lock:
            # See note in PositionTracker.get_positions
            return {k: v.copy() for k, v in self._account_values.items()}

    # --- Audit Log APIs ---

    def append_log(self, event_type, payload):
        """Append an event to the audit log.

        Args:
            event_type: str - Label for the event (e.g. 'order_added'). *TODO*: Standardize these!
            payload: dict - Arbitrary event payload.

        Returns:
            int - The assigned log sequence number.
        """
        with self._lock:
            seq = self._append_log_locked(event_type, payload)
            return seq

    def get_logs(self, since_seq=None, limit=1000):
        """Fetch a slice of the audit log.

        Args:
            since_seq: int (Optional) - Only include entries with seq > since_seq.
            limit: int - Maximum number of entries to return (default 1000).

        TODO: Maybe get these by time, OR... set sequence to use timestamp

        Returns:
            list - A list of log entries (each is a dict copy).
        """
        with self._lock:
            if since_seq is None:
                rows = self._log[-limit:]

            else:
                rows = [e for e in self._log if e['seq'] > since_seq][-limit:]

            return [e.copy() for e in rows]

    def get_log_entries_since(self, since_seq, limit):
        """get_logs alias."""
        return self.get_logs(since_seq=since_seq, limit=limit)

    def _append_log_locked(self, event_type, payload):
        """Append a log entry while holding the lock.

        *NOTE*: Internal use only; external callers should use append_log, which
        handles locking.

        Args:
            event_type: str - Event label.
            payload: dict - Arbitrary payload.

        Returns:
            int - The assigned log sequence number.
        """
        seq = next(self._log_seq)
        entry = {
            'seq': seq,
            'ts': time.time(),
            'event_type': event_type,
            'payload': payload.copy() if isinstance(payload, dict) else payload
        }
        self._log.append(entry)
        return seq
