"""High-level Trading API façade.

Exposes a stable interface that hides the underlying machinery, provides
simple methods for placing orders, querying state, etc.

Design notes:
1. On order submission, persist an order record immediately
2. Call OrderManager to place the order with the broker.
3. Attach the returned broker order/trade metadata (broker order id, etc) to the DB record once available.
4. The underlying machinery handles order status updates and fills automatically via event handlers.

TODO:
- Implement cancel and status tracking using order/exec events.
- Add idempotency keys (clientOrderId) to dedupe retries.
- Add risk hooks (pre-trade checks) once Risk Engine is built.
- Map broker errors into normalized error shapes.


********************
** IMPORTANT NOTE **
This should not be used directly. Use the app handle exposed in runtime.py. See README > Advanced Usage.
********************
"""

import logging
import time

from order_manager import OrderManager
from position_tracker import PositionTracker


logger = logging.getLogger(__name__)


class OrderHandle:
    """Lightweight handle for a submitted order.

    Attributes:
        order_id - Internal DB order id.
        broker_order_id - Broker order id (if known).
        symbol - Ticker or contract key.
        side - 'BUY' | 'SELL' (stock) or similar semantic for options.
        qty - Quantity as submitted.
        created_at - Epoch seconds when handle was created.
    """

    def __init__(self, order_id, broker_order_id=None, symbol=None, side=None, qty=None, created_at=None):
        """Create a new OrderHandle.

        Args:
            order_id: int - Internal order id from the DB.
            broker_order_id: int (Optional) - Broker order id, if available at submission.
            symbol: str (Optional) - Symbol for reference.
            side: str (Optional) - Side, e.g. 'BUY' or 'SELL'.
            qty: int (Optional) - Submitted quantity.
            created_at: float (Optional) - Epoch seconds; defaults to now.

        Returns:
            OrderHandle - A simple handle with identifying fields.
        """
        self.order_id = order_id
        self.broker_order_id = broker_order_id
        self.symbol = symbol
        self.side = side
        self.qty = qty
        self.created_at = created_at if created_at is not None else time.time()

    def to_dict(self):
        """Convert the handle to a serializable dict.

        Returns:
            dict - Dictionary containing handle fields.
        """
        return {
            'order_id': self.order_id,
            'broker_order_id': self.broker_order_id,
            'symbol': self.symbol,
            'side': self.side,
            'qty': self.qty,
            'created_at': self.created_at,
        }


class TradingAPI:
    """High-level API exposing stable functionality."""

    def __init__(self, ib, db, order_manager=None, position_tracker=None):
        """Initialize the TradingAPI.

        Args:
            ib: IB - An active ib_async.IB instance.
            db: InMemoryDB - Our in-memory persistence layer.
            order_manager: OrderManager (Optional) - If not provided, a new instance is created with ib.
            position_tracker: PositionTracker (Optional) - If not provided, a new instance is created with ib.
        """
        self.ib = ib
        self.db = db
        self.orders = order_manager if order_manager is not None else OrderManager(ib)
        self.tracker = position_tracker if position_tracker is not None else PositionTracker(ib)

    # --- Stock orders ---

    def place_stock_order(self, symbol, side, qty, order_type='MKT', limit_price=None):
        """Place a stock order and persist it.

        Args:
            symbol: str - Ticker symbol (e.g. 'AAPL').
            side: str - 'BUY' | 'SELL' | 'SHORT' | 'COVER'.
            qty: int - Number of shares.
            order_type: str - Order type, defaults to 'MKT'. TODO: Support 'LMT' etc.
            limit_price: float (Optional) - Limit price when using limit orders.

        Returns:
            OrderHandle - Identifiers for the placed order.

        Raises:
            ValueError - If inputs are invalid or unsupported for now.
            RuntimeError - If submission fails at the broker level.
        """
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("symbol must be a non-empty string")

        if side not in ('BUY', 'SELL', 'SHORT', 'COVER'):
            raise ValueError("side must be one of: BUY, SELL, SHORT, COVER")

        if not isinstance(qty, int) or qty <= 0:
            raise ValueError("qty must be a positive integer")

        if order_type != 'MKT':
            # TODO: implement limit/stop orders in OrderManager + here
            raise ValueError("Only market orders are supported at this time")

        # Persist a preliminary order record
        order_record = {
            'symbol': symbol,
            'asset_class': 'STK',
            'side': side,
            'qty': qty,
            'order_type': order_type,
            'limit_price': limit_price,
            'status': 'SUBMITTED',  # TODO: evolve to ACKED/FILLED/REJECTED via events
        }
        order_id = self.db.add_order(order_record)

        # Route to OrderManager
        try:
            if side == 'BUY':
                trade = self.orders.buy_stock(symbol, qty)

            elif side == 'SELL':
                trade = self.orders.sell_stock(symbol, qty)

            elif side == 'SHORT':
                trade = self.orders.short_stock(symbol, qty)

            # Safe to assume that this is COVER due to validation above.
            else:
                trade = self.orders.buy_to_cover(symbol, qty)

        except Exception as e:
            # Update DB with error state
            self.db.update_order(order_id, {'status': 'ERROR', 'error': str(e)})
            logger.exception("Stock order submission failed: %s %s x%d", side, symbol, qty)
            raise RuntimeError(f"Broker submission failed: {e}")

        # Extract IB order id if available
        broker_order_id = None
        try:
            broker_order_id = getattr(getattr(trade, 'order', None), 'orderId', None)

        except Exception as e:
            logger.error(f"An error occurred while trying to get broker order ID for order {order_id}. Details: {e}")
            pass

        # Update DB with broker-assigned ids
        self.db.update_order(order_id, {
            'broker_order_id': broker_order_id,
            'status': 'SUBMITTED'  # TODO: may update to ACKED upon first status event
        })

        return OrderHandle(
            order_id=order_id,
            broker_order_id=broker_order_id,
            symbol=symbol,
            side=side,
            qty=qty
        )

    # --- Option orders ---

    def place_option_order(self, symbol, expiry, strike, right, side, qty, order_type='MKT', limit_price=None):
        """Place an option order and persist it.

        Args:
            symbol: str - Underlying symbol (e.g. 'AAPL').
            expiry: str - Expiry in YYYYMMDD format.
            strike: float - Strike price.
            right: str - 'C' for Call or 'P' for Put.
            side: str - 'BUY' or 'SELL' (buy/sell contracts; writing/selling handled by 'SELL').
            qty: int - Number of option contracts.
            order_type: str - Order type, defaults to 'MKT'. TODO: Support 'LMT' etc.
            limit_price: float (Optional) - Limit price when using limit orders.

        Returns:
            OrderHandle for the placed order.

        Raises:
            ValueError - If inputs are invalid or unsupported for now.
            RuntimeError - If submission fails at the broker level.
        """
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("symbol must be a non-empty string")

        if right not in ('C', 'P'):
            raise ValueError("right must be 'C' (Call) or 'P' (Put)")

        if side not in ('BUY', 'SELL'):
            raise ValueError("side must be 'BUY' or 'SELL' for options")

        if not isinstance(qty, int) or qty <= 0:
            raise ValueError("qty must be a positive integer")

        if order_type != 'MKT':
            # TODO: implement limit/stop orders in OrderManager + here
            raise ValueError("Only market orders are supported at this time")

        order_record = {
            'symbol': symbol,
            'asset_class': 'OPT',
            'expiry': expiry,
            'strike': strike,
            'right': right,
            'side': side,
            'qty': qty,
            'order_type': order_type,
            'limit_price': limit_price,
            'status': 'SUBMITTED',
        }
        order_id = self.db.add_order(order_record)

        try:
            if side == 'BUY':
                trade = self.orders.buy_option(symbol, expiry, strike, right, qty)
            else:
                trade = self.orders.sell_option(symbol, expiry, strike, right, qty)

        except Exception as e:
            self.db.update_order(order_id, {'status': 'ERROR', 'error': str(e)})
            logger.exception("Option order submission failed: %s %s %s%s x%d",
                             side, symbol, strike, right, qty)
            raise RuntimeError(f"Broker submission failed: {e}")

        broker_order_id = None
        try:
            broker_order_id = getattr(getattr(trade, 'order', None), 'orderId', None)

        except Exception as e:
            logger.error(f"An error occurred while trying to get broker order ID for order {order_id}. Details: {e}")
            pass

        self.db.update_order(order_id, {
            'broker_order_id': broker_order_id,
            'status': 'SUBMITTED'
        })

        return OrderHandle(order_id=order_id, broker_order_id=broker_order_id, symbol=symbol, side=side, qty=qty)

    # --- Cancels / Order Status ---

    def cancel_order(self, order_id):
        """Cancel an existing order at the broker and update the DB.

        Args:
            order_id: int - Internal order id to cancel.

        Returns:
            bool - True if a cancel request was submitted to the broker.

        Raises:
            NotImplementedError - Until wired with broker's cancel mechanics.
        """
        # TODO: Map internal order_id -> broker_order_id and call ib.cancelOrder(...)
        # TODO: Update DB status to 'CANCEL_REQUESTED' then 'CANCELLED' upon confirmation.
        raise NotImplementedError("cancel_order is not implemented yet")

    def get_order_status(self, order_id):
        """Get the current status for an order.

        Args:
            order_id: int - Internal order id.

        Returns:
            dict or None - Order record (copy) from the DB including status fields, or None.
        """
        return self.db.get_order(order_id)

    # --- DB Read APIs ---

    def get_order(self, order_id):
        """Fetch an order record from the DB.

        Args:
            order_id: int - Internal order id.

        Returns:
            dict or None - Copy of the order record, or None if not found.
        """
        return self.db.get_order(order_id)

    def list_orders(self, limit=None):
        """List orders from the DB.

        Args:
            limit: int (Optional) - Maximum number of records to return.

        Returns:
            list - List of order dicts (copies), most-recently-updated first.
        """
        return self.db.list_orders(limit=limit)

    def list_fills(self, order_id=None, limit=None):
        """List fills, optionally filtered by order.

        Args:
            order_id: int (Optional) - Only return fills for this order.
            limit: int (Optional) - Limit the number of records.

        Returns:
            list - List of fill dicts (copies), most recent first.
        """
        return self.db.list_fills(order_id=order_id, limit=limit)

    def get_positions(self):
        """Return current positions (snapshot).

        Returns:
            dict - Mapping of position_key -> position record (first-layer copies).
        """
        return self.db.get_positions()

    def get_account_values(self):
        """Return current account values (snapshot).

        Returns:
            dict - Mapping of (account, tag, currency) -> value record (first-layer copies).
        """
        return self.db.get_account_values()
