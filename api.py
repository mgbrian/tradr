"""High-level Trading API façade.

Exposes a stable interface that hides the underlying machinery, provides
simple methods for placing orders, querying state, etc.

Design notes:
1. On order submission, persist an order record immediately
2. Call OrderManager to place the order with the broker.
3. Attach the returned broker order/trade metadata (broker order id, etc) to the DB record once available.
4. The underlying machinery handles order status updates and fills automatically via event handlers.

TODO:
- Implement status tracking using order/exec events.
- Add idempotency keys (clientOrderId) to dedupe retries.
- Add risk hooks (pre-trade checks) once Risk Engine is built.
- Map broker errors into normalized error shapes.

- ** RENAME limit_price to price to match proto **


********************
** IMPORTANT NOTE **
This should not be used directly. Use the app handle exposed in runtime.py. See README > Advanced Usage.
********************
"""

import logging
import time

from order_manager import OrderManager, SUPPORTED_TIF_VALUES
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

    def __str__(self):
        """Use dict version of self for string representation."""
        return str(self.to_dict())


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

    def place_stock_order(self, symbol, side, qty, order_type='MKT', limit_price=None, tif='DAY'):
        """Place a stock order and persist it.

        Supports market, limit, and stop orders:

            - order_type: 'MKT' (market), 'LMT' (limit), 'STP' (stop)
            - limit_price: required for 'LMT' and 'STP'
            - tif: time-in-force ('DAY' or 'GTC'), defaults to 'DAY'

        Args:
            symbol: str - Ticker symbol (e.g. 'AAPL').
            side: str - 'BUY' | 'SELL' | 'SHORT' | 'COVER'.
            qty: int - Number of shares.
            order_type: str - Order type; default 'MKT'. Supports 'MKT' | 'LMT' | 'STP'.
            limit_price: float (Optional) - Price for limit/stop orders.
            tif: str (Optional) - Time-in-force, e.g. 'DAY' or 'GTC'. Defaults to 'DAY'.

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

        order_type = (order_type or 'MKT').upper()
        tif = (tif or 'DAY').upper()

        # Validate order type & required price
        if order_type not in ('MKT', 'LMT', 'STP'):
            raise ValueError("order_type must be one of: MKT, LMT, STP")

        if order_type in ('LMT', 'STP') and (limit_price is None):
            raise ValueError(f"{order_type} order requires limit_price")

        if tif not in SUPPORTED_TIF_VALUES:
            raise ValueError(f"Unsupported tif value: {tif}. Must be one of: {', '.join(SUPPORTED_TIF_VALUES)}")

        # Persist a preliminary order record
        order_record = {
            'symbol': symbol,
            'asset_class': 'STK',
            'side': side,
            'qty': qty,
            'order_type': order_type,
            'limit_price': float(limit_price) if limit_price is not None else None,
            'tif': tif,
            # Mark as pending until broker submit succeeds.
            # TODO: evolve to ACKED/FILLED/REJECTED via events
            'status': 'PENDING_SUBMIT',
        }
        order_id = self.db.add_order(order_record)

        # Route to OrderManager
        try:
            if side == 'BUY':
                trade = self.orders.buy_stock(symbol, qty, order_type=order_type, price=limit_price, tif=tif)

            elif side == 'SELL':
                trade = self.orders.sell_stock(symbol, qty, order_type=order_type, price=limit_price, tif=tif)

            elif side == 'SHORT':
                trade = self.orders.short_stock(symbol, qty, order_type=order_type, price=limit_price, tif=tif)

            # Safe to assume that this is COVER due to validation above.
            else:
                trade = self.orders.buy_to_cover(symbol, qty, order_type=order_type, price=limit_price, tif=tif)

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

    def place_option_order(self, symbol, expiry, strike, right, side, qty, order_type='MKT', limit_price=None, tif='DAY'):
        """Place an option order and persist it.

        Supports market, limit, and stop orders:

            - order_type: 'MKT' (market), 'LMT' (limit), 'STP' (stop)
            - limit_price: required for 'LMT' and 'STP'
            - tif: time-in-force (e.g. 'DAY', 'GTC'), defaults to 'DAY'

        Args:
            symbol: str - Underlying symbol (e.g. 'AAPL').
            expiry: str - Expiry in YYYYMMDD format.
            strike: float - Strike price.
            right: str - 'C' for Call or 'P' for Put.
            side: str - 'BUY' or 'SELL' (buy/sell contracts; writing/selling handled by 'SELL').
            qty: int - Number of option contracts.
            order_type: str - Order type; default 'MKT'. Supports 'MKT' | 'LMT' | 'STP'.
            limit_price: float (Optional) - Price for limit/stop orders.
            tif: str (Optional) - Time-in-force, e.g. 'DAY' or 'GTC'. Defaults to 'DAY'.

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

        order_type = (order_type or 'MKT').upper()
        tif = (tif or 'DAY').upper()

        if order_type not in ('MKT', 'LMT', 'STP'):
            raise ValueError("order_type must be one of: MKT, LMT, STP")

        if order_type in ('LMT', 'STP') and (limit_price is None):
            raise ValueError(f"{order_type} order requires limit_price")

        order_record = {
            'symbol': symbol,
            'asset_class': 'OPT',
            'expiry': expiry,
            'strike': strike,
            'right': right,
            'side': side,
            'qty': qty,
            'order_type': order_type,
            'limit_price': float(limit_price) if limit_price is not None else None,
            'tif': tif,
            # Keep status semantics aligned with stocks
            'status': 'PENDING_SUBMIT',
        }
        order_id = self.db.add_order(order_record)

        try:
            if side == 'BUY':
                trade = self.orders.buy_option(
                    symbol, expiry, strike, right, qty,
                    order_type=order_type, price=limit_price, tif=tif
                )

            else:
                trade = self.orders.sell_option(
                    symbol, expiry, strike, right, qty,
                    order_type=order_type, price=limit_price, tif=tif
                )

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

    # --- Cancel/Modify ---

    def cancel_order(self, order_id):
        """Cancel an existing order at the broker and update the DB.

        This method:
            1. Looks up the order in the DB to find the broker order id,
            2. If already FILLED or CANCELLED, returns False without contacting broker,
            3. Otherwise marks the order as 'CANCEL_REQUESTED',
            4. Submits the cancel to IB via OrderManager,
            5. Leaves final state transition (e.g. 'CANCELLED') to the execution tracker
                when the broker confirms the cancellation.

        Args:
            order_id: int - Internal order id to cancel.

        Returns:
            bool - True if a cancel request was submitted to the broker; False when
                    the order is already finalized (FILLED/CANCELLED).

        Raises:
            KeyError - If the order does not exist.
            ValueError - If the order has no broker_order_id yet (cannot target).
            RuntimeError - If broker submission fails.
        """
        rec = self.db.get_order(order_id)
        if not rec:
            raise KeyError(f"order {order_id} not found")

        # If already finalized, do not send a cancel to the broker.
        status = str(rec.get('status') or '').upper()
        if status in ('FILLED', 'CANCELLED'):
            logger.info("Order %s already %s; skipping cancel.", order_id, status)
            return False

        broker_order_id = rec.get('broker_order_id')
        if not broker_order_id:
            # We cannot target a specific order at IB without a broker order id.
            raise ValueError(f"order {order_id} has no broker_order_id yet")

        # Mark as cancel requested; execution tracker will update later to CANCELLED/REJECTED.
        try:
            self.db.update_order(order_id, {'status': 'CANCEL_REQUESTED'})
        except Exception:
            # Non-fatal; proceed to attempt broker cancel.
            logger.exception(
                "Failed to mark order %s as CANCEL_REQUESTED. Proceeding with broker cancel",
                order_id
            )

        try:
            ok = self.orders.cancel_order(int(broker_order_id))
            return bool(ok)
        except Exception as e:
            # Record error for visibility
            try:
                self.db.update_order(order_id, {'status': 'ERROR', 'error': str(e)})
            except Exception:
                logger.exception("Failed to persist cancel error for order %s", order_id)

            logger.exception(
                "Cancel request failed for order %s (broker_order_id=%s)",
                order_id, broker_order_id
            )
            raise RuntimeError(f"Broker cancel failed: {e}")

    def modify_order(self, order_id, *, quantity=None, limit_price=None, tif=None, order_type=None):
        """Modify an existing order at the broker and update the DB.

        IB modifications are effected by re-submitting the order with the same
        broker order id and new fields (quantity, type, price, TIF). This method:
            1. Looks up the order in the DB to get contract details and broker id,
            2. If the order is already final (FILLED/CANCELLED/REJECTED), raises ValueError,
            3. Validates/merges provided fields with existing ones,
            4. Marks DB as 'MODIFY_REQUESTED' (and updates the desired fields),
            5. Submits the modification via OrderManager.

        Args:
            order_id: int - Internal order id to modify.
            quantity: int (Optional) - New total quantity (defaults to current).
            limit_price: float (Optional) - New limit/stop price when applicable.
            tif: str (Optional) - New time-in-force (defaults to current).
            order_type: str (Optional) - 'MKT' | 'LMT' | 'STP' (defaults to current).

        Returns:
            bool - True if a modify request was submitted to the broker.

        Raises:
            KeyError - If the order does not exist.
            ValueError - If finalized or missing broker_order_id, or invalid params.
            RuntimeError - If broker submission fails.
        """
        rec = self.db.get_order(order_id)
        if not rec:
            raise KeyError(f"order {order_id} not found")

        # TODO: Should this return False (same as cancel)?
        # TODO: Enumerate all statuses and match with IB e.g. Submitted (IB) <> SUBMITTED (internal)
        status = str(rec.get('status') or '').upper()
        if status in ('FILLED', 'CANCELLED', 'REJECTED'):
            raise ValueError(f"order {order_id} is already {status}; cannot modify")

        broker_order_id = rec.get('broker_order_id')
        if not broker_order_id:
            raise ValueError(f"order {order_id} has no broker_order_id yet")

        # Merge new values with existing ones
        new_qty = int(quantity if quantity is not None else rec.get('qty') or rec.get('quantity') or 0)
        if new_qty <= 0:
            raise ValueError("quantity must be a positive integer")

        new_tif = (tif or rec.get('tif') or 'DAY').upper()
        if new_tif not in SUPPORTED_TIF_VALUES:
            raise ValueError(f"Unsupported tif value: {new_tif}. Must be one of: {', '.join(SUPPORTED_TIF_VALUES)}")

        new_type = (order_type or rec.get('order_type') or 'MKT').upper()
        if new_type not in ('MKT', 'LMT', 'STP'):
            raise ValueError("order_type must be one of: MKT, LMT, STP")

        # For LMT/STP, require a price. For MKT, ignore price.
        new_price = limit_price if limit_price is not None else rec.get('limit_price')
        if new_type in ('LMT', 'STP') and (new_price is None):
            raise ValueError(f"{new_type} order requires limit_price")

        # Determine IB action from stored side semantics
        side = str(rec.get('side') or '')
        action = 'BUY' if side in ('BUY', 'COVER') else 'SELL'

        # Persist desired new fields and mark modify requested (best-effort)
        try:
            self.db.update_order(order_id, {
                'qty': new_qty,
                'order_type': new_type,
                'limit_price': float(new_price) if new_price is not None else None,
                'tif': new_tif,
                'status': 'MODIFY_REQUESTED',
            })
        except Exception:
            logger.exception("Failed to mark order %s as MODIFY_REQUESTED; proceeding with broker modify", order_id)

        # Rebuild contract and submit modify with same broker order id
        asset = str(rec.get('asset_class') or '').upper()
        try:
            if asset == 'STK':
                symbol = rec.get('symbol')
                trade = self.orders.modify_stock_order(
                    symbol, int(broker_order_id), action, new_qty,
                    order_type=new_type, price=new_price, tif=new_tif
                )

            elif asset == 'OPT':
                symbol = rec.get('symbol')
                expiry = rec.get('expiry')
                strike = rec.get('strike')
                right = rec.get('right')
                trade = self.orders.modify_option_order(
                    symbol, expiry, strike, right, int(broker_order_id), action, new_qty,
                    order_type=new_type, price=new_price, tif=new_tif
                )

            else:
                raise ValueError(f"Unsupported asset_class for modify: {asset}")

            # If call succeeded, consider the request submitted.
            return True

        except Exception as e:
            try:
                self.db.update_order(order_id, {'status': 'ERROR', 'error': str(e)})
            except Exception:
                logger.exception("Failed to persist modify error for order %s", order_id)

            logger.exception("Modify request failed for order %s (broker_order_id=%s)", order_id, broker_order_id)
            raise RuntimeError(f"Broker modify failed: {e}")

    # --- Order Status ---

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
