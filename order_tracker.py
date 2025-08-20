"""Order tracker.

Listens to IB order-related events (Trade objects) and mirrors them into the
in-memory DB so that orders created/modified/cancelled from *outside* this
process (e.g. the TWS GUI) are reflected in our system.

Design Notes:
- Modern ib_async events emit a single Trade object (not tuples unlike ib_insync).
  Extract contract/order/orderStatus from it.
- TWS-originated orders often have orderId <= 0 while providing a valid permId.
  Therefore key by either broker_order_id (orderId) OR perm_id.
- If an incoming event references an order we don't know yet, we "adopt" it by
  creating a new DB row with the best metadata we have.
- We also treat events as a "notification" and reconcile against a fresh
  open-orders snapshot (openOrdersAsync) with a short debounce to robustly fill
  in any missing pieces.
"""

import asyncio
import logging
import threading

logger = logging.getLogger(__name__)


def _safe_upper(s):
    return str(s).upper() if s is not None else ""


def _extract_price_for_order_type(order):
    """Return the price (float) to record for LMT/STP orders if present, else None."""
    try:
        ot = _safe_upper(getattr(order, "orderType", "")).strip()
        if ot == "LMT":
            p = getattr(order, "lmtPrice", None)
            return float(p) if p is not None else None
        if ot == "STP":
            for attr in ("auxPrice", "stopPrice"):
                p = getattr(order, attr, None)
                if p is not None:
                    return float(p)
            return None
    except Exception:
        return None
    return None


def _extract_fields_from_open_order(contract, order, order_state):
    """Extract our DB fields from contract/order/order_state triplet."""
    fields = {}

    # Broker-side id
    try:
        oid = int(getattr(order, "orderId", 0) or 0)
    except Exception:
        oid = 0
    fields["broker_order_id"] = oid

    # Instrument/asset metadata
    try:
        fields["asset_class"] = str(getattr(contract, "secType", "") or "")
    except Exception:
        fields["asset_class"] = ""

    try:
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
        msg = getattr(order_state, "warningText", "") or getattr(order_state, "initMarginBefore", None)
        if msg:
            fields["message"] = str(msg)
    except Exception:
        pass

    return fields


def _extract_ids_from_trade(trade):
    """Return (order_id, perm_id) ints from a Trade object, being defensive."""
    order_id = 0
    perm_id = 0
    try:
        order = getattr(trade, "order", None)
        if order is not None:
            try:
                order_id = int(getattr(order, "orderId", 0) or 0)
            except Exception:
                order_id = 0
            try:
                perm_id = int(getattr(order, "permId", 0) or 0)
            except Exception:
                pass
        st = getattr(trade, "orderStatus", None)
        if perm_id <= 0 and st is not None:
            try:
                perm_id = int(getattr(st, "permId", 0) or 0)
            except Exception:
                pass
    except Exception:
        pass
    return order_id, perm_id


class OrderTracker:
    """Subscribe to IB order events and keep DB in sync."""

    def __init__(self, ib, *, db):
        """Create a tracker.

        Args:
            ib: IB - Connected client with events that emit Trade objects:
                - openOrderEvent(trade)
                - orderStatusEvent(trade)
              Also provides (optionally):
                - openOrdersAsync() -> coroutine yielding a snapshot of open trades
                - .loop             -> asyncio loop used by the IB client (session pins this)
            db: InMemoryDB-like object with add_order(), update_order(), list_orders() methods.
        """
        self.ib = ib
        self.db = db
        self._open_order_handler = None
        self._order_status_handler = None

        # Snapshot reconcile machinery
        self._snapshot_timer = None
        self._snapshot_lock = threading.Lock()
        self._snapshot_inflight = False
        self._debounce_seconds = 0.25  # short burst coalescing

    # --- Lifecycle ---

    def start(self):
        """Attach event listeners. Safe to call multiple times."""
        logger.info("Starting OrderTracker")
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
        """Detach event listeners and cancel any pending snapshot."""
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

        # Cancel pending debounce timer if any
        with self._snapshot_lock:
            if self._snapshot_timer is not None:
                try:
                    self._snapshot_timer.cancel()
                except Exception:
                    pass
                finally:
                    self._snapshot_timer = None

    # --- Event handlers ---

    def _on_open_order(self, *args):
        """Handle openOrderEvent (ib_async emits a single Trade).

        We perform a best-effort immediate upsert from the payload, then schedule
        a debounced snapshot reconcile using openOrdersAsync().
        """
        try:
            trade = args[0] if len(args) == 1 else None
            if trade is None or not hasattr(trade, "order"):
                # Unknown shape -> just schedule a snapshot reconcile
                logger.debug("openOrderEvent unexpected payload; scheduling snapshot")
                self._schedule_snapshot_refresh()
                return

            contract = getattr(trade, "contract", None)
            order = getattr(trade, "order", None)
            order_state = getattr(trade, "orderStatus", None)

            # Extract fields + ids
            fields = _extract_fields_from_open_order(contract, order, order_state)
            order_id, perm_id = _extract_ids_from_trade(trade)
            if order_id and order_id > 0:
                fields["broker_order_id"] = int(order_id)

            if perm_id and perm_id > 0:
                fields["perm_id"] = int(perm_id)

            # Upsert keyed by either broker_order_id or perm_id
            self._upsert_by_any(order_id, perm_id, fields)

        except Exception:
            logger.exception("Error processing openOrderEvent")

        # Always schedule a debounced snapshot reconcile
        self._schedule_snapshot_refresh()

    def _on_order_status(self, *args, **kwargs):
        """Handle orderStatusEvent (ib_async emits a single Trade).

        Keep the incremental update (status/filled/avg) and schedule a snapshot
        reconcile to settle any missing fields.
        """
        try:
            trade = args[0] if len(args) == 1 else None
            if trade is None or not hasattr(trade, "orderStatus"):
                logger.debug("orderStatusEvent unexpected payload; scheduling snapshot")
                self._schedule_snapshot_refresh()
                return

            st = getattr(trade, "orderStatus", None)
            order = getattr(trade, "order", None)

            order_id, perm_id = _extract_ids_from_trade(trade)

            updates = {}
            if st is not None:
                if getattr(st, "status", None) is not None:
                    updates["status"] = _safe_upper(st.status)
                try:
                    if getattr(st, "filled", None) is not None:
                        updates["filled_qty"] = int(st.filled)
                except Exception:
                    pass
                try:
                    if getattr(st, "avgFillPrice", None) is not None:
                        updates["avg_price"] = float(st.avgFillPrice)
                except Exception:
                    pass

            # Also capture orderType/TIF changes if they drift
            if order is not None:
                try:
                    ot = getattr(order, "orderType", None)
                    if ot:
                        updates["order_type"] = _safe_upper(ot)
                except Exception:
                    pass
                try:
                    tf = getattr(order, "tif", None)
                    if tf:
                        updates["tif"] = _safe_upper(tf)
                except Exception:
                    pass

            # Adopt/update keyed by either id
            if not self._have_any(order_id, perm_id):
                base = {}
                if order_id > 0:
                    base["broker_order_id"] = int(order_id)

                if perm_id > 0:
                    base["perm_id"] = int(perm_id)

                if "status" not in updates:
                    base["status"] = "SUBMITTED"
                base.update(updates)
                self.db.add_order(base)
            else:
                self._update_by_any(order_id, perm_id, updates)

        except Exception:
            logger.exception("Error processing orderStatusEvent")

        self._schedule_snapshot_refresh()

    # --- Snapshot reconcile ---

    def refresh_now(self):
        """Trigger an immediate open-orders snapshot reconcile (no debounce)."""
        try:
            with self._snapshot_lock:
                if self._snapshot_timer is not None:
                    try:
                        self._snapshot_timer.cancel()
                    except Exception:
                        pass
                    finally:
                        self._snapshot_timer = None
                if self._snapshot_inflight:
                    return
            t = threading.Thread(
                target=self._run_snapshot_refresh,
                name="orders-refresh-now",
                daemon=True,
            )
            t.start()
        except Exception:
            logger.exception("refresh_now failed")

    def _schedule_snapshot_refresh(self):
        """Debounce/schedule a snapshot fetch via openOrdersAsync()."""
        with self._snapshot_lock:
            if self._snapshot_timer is not None:
                try:
                    self._snapshot_timer.cancel()

                except Exception:
                    pass

                finally:
                    self._snapshot_timer = None

            self._snapshot_timer = threading.Timer(self._debounce_seconds, self._run_snapshot_refresh)
            self._snapshot_timer.daemon = True
            self._snapshot_timer.start()

    def _run_snapshot_refresh(self):
        """Timer target: kick off an async snapshot fetch (if not already running)."""
        with self._snapshot_lock:
            self._snapshot_timer = None
            if self._snapshot_inflight:
                return
            self._snapshot_inflight = True

        try:
            self._fetch_and_reconcile_snapshot()

        finally:
            with self._snapshot_lock:
                self._snapshot_inflight = False

    def _fetch_and_reconcile_snapshot(self):
        """Fetch open orders (prefer openOrdersAsync) and reconcile them into the DB."""
        loop = getattr(self.ib, "loop", None)
        get_async = getattr(self.ib, "openOrdersAsync", None)
        if loop and callable(get_async):
            try:
                fut = asyncio.run_coroutine_threadsafe(get_async(), loop)
                timeout = getattr(self.ib, "RequestTimeout", 10.0) or 10.0
                trades = fut.result(timeout=timeout)
            except Exception:
                logger.exception("openOrdersAsync snapshot fetch failed")
                return

        else:
            logger.debug("openOrdersAsync or loop not available; skipping snapshot reconcile")
            return

        for trade in list(trades or []):
            try:
                contract = getattr(trade, "contract", None)
                order = getattr(trade, "order", None)
                order_state = getattr(trade, "orderStatus", None)
                if order is None or contract is None:
                    continue

                fields = _extract_fields_from_open_order(contract, order, order_state)
                order_id, perm_id = _extract_ids_from_trade(trade)
                if order_id > 0:
                    fields["broker_order_id"] = int(order_id)

                if perm_id > 0:
                    fields["perm_id"] = int(perm_id)

                self._upsert_by_any(order_id, perm_id, fields)
            except Exception:
                logger.exception("Error reconciling snapshot item; continuing")

    # --- DB helpers / identity resolution ---

    def _list_orders(self):
        """Return DB order list (defensive against differing DB interfaces)."""
        try:
            return list(self.db.list_orders(limit=None))

        except TypeError:
            return list(self.db.list_orders())

    def _find_order_internal_id_by_broker(self, broker_order_id):
        """Return internal order_id for a given broker_order_id, or None."""
        # Prefer a direct helper if DB exposes one
        try:
            get_fn = getattr(self.db, "get_order_by_broker_id", None)
            if callable(get_fn):
                rec = get_fn(int(broker_order_id))
                if isinstance(rec, dict):
                    oid = rec.get("order_id")
                    return int(oid) if oid is not None else None

        except Exception:
            pass

        # Fallback: scan list_orders
        try:
            for r in self._list_orders():
                if not isinstance(r, dict):
                    continue

                try:
                    if int(r.get("broker_order_id") or 0) == int(broker_order_id):
                        oid = r.get("order_id")
                        return int(oid) if oid is not None else None

                except Exception:
                    continue

        except Exception:
            logger.exception("Failed to scan orders for broker_order_id=%s", broker_order_id)

        return None

    def _find_order_internal_id_by_perm(self, perm_id):
        """Return internal order_id for a given perm_id, or None."""
        # Optional direct helper
        try:
            get_fn = getattr(self.db, "get_order_by_perm_id", None)
            if callable(get_fn):
                rec = get_fn(int(perm_id))
                if isinstance(rec, dict):
                    oid = rec.get("order_id")
                    return int(oid) if oid is not None else None

        except Exception:
            pass

        # Fallback: scan
        try:
            for r in self._list_orders():
                if not isinstance(r, dict):
                    continue
                try:
                    if int(r.get("perm_id") or 0) == int(perm_id):
                        oid = r.get("order_id")
                        return int(oid) if oid is not None else None

                except Exception:
                    continue

        except Exception:
            logger.exception("Failed to scan orders for perm_id=%s", perm_id)

        return None

    def _find_order_internal_id_by_any(self, broker_order_id, perm_id):
        """Resolve internal id using broker_order_id first (if valid) else perm_id."""
        if broker_order_id and broker_order_id > 0:
            oid = self._find_order_internal_id_by_broker(broker_order_id)
            if oid is not None:
                return oid

        if perm_id and perm_id > 0:
            oid = self._find_order_internal_id_by_perm(perm_id)
            if oid is not None:
                return oid

        return None

    def _have_any(self, broker_order_id, perm_id):
        """bool: whether we have an order identified by broker_order_id or perm_id."""
        return self._find_order_internal_id_by_any(broker_order_id, perm_id) is not None

    def _upsert_by_any(self, broker_order_id, perm_id, fields):
        """Create or update order by (broker_order_id | perm_id) using provided fields."""
        oid = self._find_order_internal_id_by_any(broker_order_id, perm_id)
        if oid is None:
            payload = dict(fields)
            # Only include positive broker_order_id
            if broker_order_id and broker_order_id > 0:
                payload.setdefault("broker_order_id", int(broker_order_id))

            else:
                payload.pop("broker_order_id", None)

            if perm_id and perm_id > 0:
                payload.setdefault("perm_id", int(perm_id))

            payload.setdefault("status", "SUBMITTED")
            self.db.add_order(payload)
            return

        updates = dict(fields)
        # Do not overwrite order_id on update payload
        updates.pop("order_id", None)
        self.db.update_order(int(oid), updates)

    def _update_by_any(self, broker_order_id, perm_id, updates):
        """Update an existing order located by broker_order_id or perm_id."""
        oid = self._find_order_internal_id_by_any(broker_order_id, perm_id)
        if oid is None:
            base = {}
            if broker_order_id and broker_order_id > 0:
                base["broker_order_id"] = int(broker_order_id)

            if perm_id and perm_id > 0:
                base["perm_id"] = int(perm_id)

            base.update(updates)
            self.db.add_order(base)
            return

        self.db.update_order(int(oid), dict(updates))
