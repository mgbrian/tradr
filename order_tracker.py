"""Order tracker.

Listens to IB order-related events (open orders, status changes, etc.) and
mirrors them into the in-memory DB so that orders created/modified/cancelled
from *outside* this process (e.g., the TWS GUI) are reflected in our system.

Design Notes:
- We use best-effort extraction of fields from the IB contract/order objects.
- If an incoming event references a broker order id we don't know yet, we
  "adopt" it by creating a new DB row with the best metadata we have.
- For already-known orders, we update the existing row.
- The tracker is resilient to missing/partial information.
"""

import logging

logger = logging.getLogger(__name__)


def _safe_upper(s):
    return str(s).upper() if s is not None else ""


def _extract_price_for_order_type(order):
    """Return the price (float) to record for LMT/STP orders if present, else None."""
    # LMT -> lmtPrice; STP -> auxPrice/stopPrice (name varies across wrappers)
    try:
        ot = _safe_upper(getattr(order, "orderType", "")).strip()
        if ot == "LMT":
            p = getattr(order, "lmtPrice", None)
            return float(p) if p is not None else None
        if ot == "STP":
            # Try multiple common names
            for attr in ("auxPrice", "stopPrice"):
                p = getattr(order, attr, None)
                if p is not None:
                    return float(p)
            return None
    except Exception:  # defensive
        return None
    return None


def _extract_fields_from_open_order(contract, order, order_state):
    """Extract our DB fields from IB openOrder triplet."""
    fields = {}

    # Broker-side id
    try:
        fields["broker_order_id"] = int(getattr(order, "orderId", 0) or 0)
    except Exception:
        fields["broker_order_id"] = 0

    # Instrument/asset metadata
    try:
        fields["asset_class"] = str(getattr(contract, "secType", "") or "")
    except Exception:
        fields["asset_class"] = ""

    try:
        # Prefer contract.symbol if present
        fields["symbol"] = str(getattr(contract, "symbol", "") or getattr(contract, "localSymbol", "") or "")
    except Exception:
        fields["symbol"] = ""

    # Order descriptors
    try:
        fields["side"] = str(getattr(order, "action", "") or "")
    except Exception:
        fields["side"] = ""

    try:
        fields["quantity"] = int(getattr(order, "totalQuantity", 0) or 0)
    except Exception:
        fields["quantity"] = 0

    try:
        fields["order_type"] = _safe_upper(getattr(order, "orderType", "") or "")
    except Exception:
        fields["order_type"] = ""

    try:
        fields["tif"] = _safe_upper(getattr(order, "tif", "") or "")
    except Exception:
        fields["tif"] = ""

    # Price (if applicable)
    price = _extract_price_for_order_type(order)
    if price is not None:
        fields["limit_price"] = float(price)

    # Status / message from OrderState
    try:
        raw_status = getattr(order_state, "status", "") or ""
        fields["status"] = _safe_upper(raw_status) or "SUBMITTED"
    except Exception:
        fields["status"] = "SUBMITTED"

    try:
        msg = getattr(order_state, "warningText", "") or getattr(order_state, "initMarginBefore", None)  # fallback
        if msg:
            fields["message"] = str(msg)
    except Exception:
        pass

    return fields


class OrderTracker:
    """Subscribe to IB order events and keep DB in sync."""

    def __init__(self, ib, *, db):
        """Create a tracker.

        Args:
            ib: IB - Connected client with events:
                - openOrderEvent(contract, order, orderState)
                - orderStatusEvent(orderId, status, filled, remaining, avgFillPrice, etc)
            db: InMemoryDB-like object with add_order(), update_order(), list_orders() methods.
        """
        self.ib = ib
        self.db = db
        self._open_order_handler = None
        self._order_status_handler = None

    # --- Lifecycle ---

    def start(self):
        """Attach event listeners. Safe to call multiple times."""
        if self._open_order_handler is None:
            self._open_order_handler = self._on_open_order
            try:
                # ib_async events support += handler
                self.ib.openOrderEvent += self._open_order_handler
            except Exception:
                logger.exception("Failed to attach openOrderEvent handler")

        if self._order_status_handler is None:
            self._order_status_handler = self._on_order_status
            try:
                self.ib.orderStatusEvent += self._order_status_handler

            except Exception:
                logger.exception("Failed to attach orderStatusEvent handler")

    def stop(self):
        """Detach event listeners."""
        try:
            if self._open_order_handler is not None:
                try:
                    self.ib.openOrderEvent -= self._open_order_handler
                except Exception:
                    pass

        finally:
            self._open_order_handler = None

        try:
            if self._order_status_handler is not None:
                try:
                    self.ib.orderStatusEvent -= self._order_status_handler
                except Exception:
                    pass

        finally:
            self._order_status_handler = None

    # --- Event handlers ---

    def _on_open_order(self, contract, order, order_state):
        """Handle openOrderEvent."""
        try:
            fields = _extract_fields_from_open_order(contract, order, order_state)
            broker_id = int(fields.get("broker_order_id") or 0)
            if broker_id <= 0:
                logger.debug("openOrderEvent missing broker_order_id; ignoring")
                return
            # Adopt or update with full metadata snapshot
            self._upsert_by_broker_id(broker_id, fields)

        except Exception:
            logger.exception("Error processing openOrderEvent")

    def _on_order_status(self, order_id, status, filled, remaining, avg_fill_price, *args, **kwargs):
        """Handle orderStatusEvent."""
        try:
            broker_id = int(order_id or 0)

        except Exception:
            broker_id = 0

        if broker_id <= 0:
            logger.debug("orderStatusEvent missing/invalid orderId; ignoring")
            return

        updates = {}
        # Normalize status to uppercase when provided
        if status is not None:
            updates["status"] = _safe_upper(status)

        # Filled qty and average price are optional but highly useful
        try:
            if filled is not None:
                updates["filled_qty"] = int(filled)
        except Exception:
            pass

        try:
            if avg_fill_price is not None:
                updates["avg_price"] = float(avg_fill_price)
        except Exception:
            pass

        # If we've never seen this order, adopt with minimal info
        if not self._have_broker_id(broker_id):
            base = {"broker_order_id": broker_id}
            if "status" not in updates:
                base["status"] = "SUBMITTED"
            base.update(updates)
            self.db.add_order(base)
            return

        # Otherwise update existing record
        self._update_by_broker_id(broker_id, updates)

    # --- DB helpers ---

    def _list_orders(self):
        """Return DB order list (defensive against differing DB interfaces)."""
        try:
            return list(self.db.list_orders(limit=None))

        except TypeError:
            # Some DBs may not accept limit=None
            return list(self.db.list_orders())

    def _find_order_id_by_broker_id(self, broker_order_id):
        """Return internal order_id for this broker id, or None if not found."""
        # Prefer a direct helper if DB exposes one
        try:
            rec = self.db.get_order_by_broker_id(int(broker_order_id))
            if rec:
                return int(rec.get("order_id"))
        except Exception:
            pass

        # Fallback: scan list_orders
        try:
            for r in self._list_orders():
                try:
                    if int(r.get("broker_order_id") or 0) == int(broker_order_id):
                        return int(r.get("order_id"))
                except Exception:
                    continue

        except Exception:
            logger.exception("Failed to scan orders for broker_order_id=%s", broker_order_id)

        return None

    def _have_broker_id(self, broker_order_id):
        """bool: Whether or not we have an order with the given broker_order_id."""
        return self._find_order_id_by_broker_id(broker_order_id) is not None

    def _upsert_by_broker_id(self, broker_order_id, fields):
        """Create or update order by broker id using provided fields."""
        oid = self._find_order_id_by_broker_id(broker_order_id)
        if oid is None:
            # New / adopted order
            payload = dict(fields)
            payload.setdefault("broker_order_id", int(broker_order_id))
            payload.setdefault("status", "SUBMITTED")

            self.db.add_order(payload)
            return

        updates = dict(fields)
        # Do not overwrite order_id on update payload
        updates.pop("order_id", None)
        self.db.update_order(int(oid), updates)

    def _update_by_broker_id(self, broker_order_id, updates):
        """Update an existing order located by broker id."""
        oid = self._find_order_id_by_broker_id(broker_order_id)
        if oid is None:
            # Should not generally happen (handled by _on_order_status), but just to be defensive...
            self.db.add_order({"broker_order_id": int(broker_order_id), **updates})
            return

        self.db.update_order(int(oid), dict(updates))
