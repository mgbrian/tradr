"""Execution & Order Status Tracker.

Listens to IBKR order/execution events and persists fills and order aggregates into the db.

Events handled:
- execDetailsEvent (execution fills)
- commissionReportEvent (per-fill commission/realized P&L)
- orderStatusEvent (status transitions & filled qty/avg price)
"""

import logging

logger = logging.getLogger(__name__)


class ExecutionTracker:
    """Subscribes to IBKR exec/order events and writes fills/updates to DB."""

    def __init__(self, ib, db):
        """Initialize the ExecutionTracker.

        Args:
            ib: IB - An active ib_async.IB instance (events source).
            db: InMemoryDB - Persistence layer for orders/fills/aggregates.
        """
        self.ib = ib
        self.db = db

        self._exec_handler = None
        self._comm_handler = None
        self._status_handler = None

    # --- Lifecycle

    def start(self):
        """Register event handlers on the IB instance.

        Returns:
            bool - True if handlers were registered; False if already started.

        Raises:
            RuntimeError - If IB is not connected.
        """
        if self._exec_handler is not None:
            logger.warning("ExecutionTracker already started")
            return False

        if not getattr(self.ib, "isConnected", lambda: False)():
            raise RuntimeError("IB instance is not connected; call session.connect() first")

        # Handlers
        def exec_handler(trade, fill):
            """On execDetailsEvent, persist a fill and update aggregates.

            Args:
                trade: Trade - ib_async Trade object (has .order, .contract).
                fill: Fill - ib_async Fill object (has .execution, .commissionReport optional).
            """
            try:
                self._on_exec_details(trade, fill)

            except Exception:
                logger.exception("Error handling execDetailsEvent")

        def comm_handler(trade, comm_report):
            """On commissionReportEvent, update commission and realized P&L.

            Args:
                trade: Trade - ib_async Trade object.
                comm_report: CommissionReport - ib_async object with commission, realizedPNL.
            """
            try:
                self._on_commission_report(trade, comm_report)

            except Exception:
                logger.exception("Error handling commissionReportEvent")

        def status_handler(trade):
            """On orderStatusEvent, update order aggregates/status.

            Args:
                trade: Trade - ib_async Trade object with .orderStatus fields.
            """
            try:
                self._on_order_status(trade)

            except Exception:
                logger.exception("Error handling orderStatusEvent")

        # Register event handlers
        self._exec_handler = exec_handler
        self._comm_handler = comm_handler
        self._status_handler = status_handler

        self.ib.execDetailsEvent += self._exec_handler
        self.ib.commissionReportEvent += self._comm_handler
        self.ib.orderStatusEvent += self._status_handler

        logger.info("ExecutionTracker started")
        return True

    def stop(self):
        """Unregister event handlers.

        Returns:
            bool - True if handlers were removed; False if not running.
        """
        if self._exec_handler is None:
            logger.warning("ExecutionTracker not running")
            return False

        try:
            self.ib.execDetailsEvent.remove(self._exec_handler)
            self.ib.commissionReportEvent.remove(self._comm_handler)
            self.ib.orderStatusEvent.remove(self._status_handler)

        except Exception:
            logger.exception("Error removing handlers from IB instance")

        self._exec_handler = None
        self._comm_handler = None
        self._status_handler = None
        logger.info("ExecutionTracker stopped")
        return True

    # --- Event handlers

    def _on_exec_details(self, trade, fill):
        """Persist a fill and update basic order aggregates.

        Args:
            trade: Trade - ib_async Trade object including .order, .orderId and .contract.
            fill: Fill - ib_async Fill with .execution (price, shares, time, execId, etc.).
        """
        order = getattr(trade, 'order', None)
        broker_order_id = getattr(order, 'orderId', None)
        if broker_order_id is None:
            logger.warning("execDetails without broker orderId; skipping")
            return

        # Map broker -> internal order_id
        internal_id = self._find_internal_order_id(broker_order_id)
        if internal_id is None:
            logger.warning("No internal order mapping for broker orderId=%s", broker_order_id)
            return

        ex = getattr(fill, 'execution', None)
        if not ex:
            logger.warning("Fill missing execution payload for broker orderId=%s", broker_order_id)
            return

        # Persist fill
        try:
            self.db.add_fill(internal_id, {
                'exec_id': getattr(ex, 'execId', None),
                'price': getattr(ex, 'price', None),
                'filled_qty': getattr(ex, 'shares', None),
                'time': getattr(ex, 'time', None),
                'broker_order_id': broker_order_id,
                'permid': getattr(ex, 'permId', None),
                'side': getattr(order, 'action', None),
                'symbol': getattr(getattr(trade, 'contract', None), 'symbol', None),
            })
        except Exception:
            logger.exception("Failed to persist fill for internal order %s", internal_id)

        # Basic aggregate updates (optional; db.add_fill also touches aggregates)
        try:
            current = self.db.get_order(internal_id) or {}
            # If the order is fully filled according to trade status, we may set status later in _on_order_status
            self.db.update_order(internal_id, {
                'last_fill_price': getattr(ex, 'price', None),
                'last_exec_id': getattr(ex, 'execId', None),
                'broker_order_id': broker_order_id,
            })

        except Exception:
            logger.exception("Failed to update order aggregates for %s", internal_id)

    def _on_commission_report(self, trade, comm_report):
        """Update commission and realized P&L on the order record.

        Args:
            trade: Trade - ib_async Trade object.
            comm_report: CommissionReport - commission and realizedPNL.
        """
        order = getattr(trade, 'order', None)
        broker_order_id = getattr(order, 'orderId', None)

        if broker_order_id is None:
            return
        internal_id = self._find_internal_order_id(broker_order_id)

        if internal_id is None:
            return

        try:
            self.db.update_order(internal_id, {
                'commission': getattr(comm_report, 'commission', None),
                'realized_pnl': getattr(comm_report, 'realizedPNL', None),
                'commission_currency': getattr(comm_report, 'currency', None),
                'broker_order_id': broker_order_id,
            })
        except Exception:
            logger.exception("Failed to update commission for internal order %s", internal_id)

    def _on_order_status(self, trade):
        """Update order status and aggregates from orderStatusEvent.

        Args:
            trade: Trade - ib_async Trade with .orderStatus (status, filled, remaining, avgFillPrice).
        """
        order = getattr(trade, 'order', None)
        status = getattr(trade, 'orderStatus', None)
        broker_order_id = getattr(order, 'orderId', None)

        if broker_order_id is None or status is None:
            return

        internal_id = self._find_internal_order_id(broker_order_id)
        if internal_id is None:
            return

        payload = {
            'broker_order_id': broker_order_id,
            'status': getattr(status, 'status', None),
            'filled_qty': getattr(status, 'filled', None),
            'remaining_qty': getattr(status, 'remaining', None),
            'avg_price': getattr(status, 'avgFillPrice', None),
        }

        try:
            self.db.update_order(internal_id, payload)
        except Exception:
            logger.exception("Failed to update order status for internal order %s", internal_id)

    # --- Utils

    def _find_internal_order_id(self, broker_order_id):
        """Map an IBKR broker order id to our DB internal order id.

        Args:
            broker_order_id: int - The broker-assigned orderId.

        Returns:
            int or None - Internal order_id if found; otherwise None.
        """
        # TODO: This currently scans recent orders. Optimize with a real map later.
        for rec in self.db.list_orders(limit=1000):
            if rec.get('broker_order_id') == broker_order_id or rec.get('ib_order_id') == broker_order_id:
                return rec['order_id']

        return None
