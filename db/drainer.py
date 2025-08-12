"""Drainer bridging the in-memory DB and main (Postgres) DB.

Supported event types:
- 'order_added' - upsert Order from current in-memory snapshot
- 'order_updated' - upsert Order from current in-memory snapshot
- 'fill_added' - insert Fill from in-memory snapshot and update Order aggregates
- 'audit_log' - append to AuditLog

TODO:
Persisting the following event types is on hold now (Aug 12, 2025), pending decision
on the merits/design implications of doing so. Events are acknowledged though, just
not sent to Postgres.
- 'position_upserted'
- 'position_deleted'
- 'account_value_set'

Checkpointing:
- The last processed sequence is stored in Postgres and advanced within the same
  transaction as data writes for tighter guarantees.
"""

import logging
import time
import threading

from django.db import models, transaction

from .models import Order, Fill, AuditLog, OutboxCheckpoint

logger = logging.getLogger(__name__)


class OutboxDrainer:
    """Replay in-memory DB log entries into Django models with DB-backed checkpoint."""

    def __init__(self, memdb, worker_id="core-drainer", batch_size=500, poll_interval=0.25):
        """Initialize a drainer.

        Args:
            memdb: InMemoryDB, or similar object exposing the following:
                - get_logs(since_seq: int | None, limit: int) -> list[dict]
                - get_order(order_id: int) -> dict | None
                - get_fill(fill_id: int) -> dict | None
            worker_id: str - Optional. Identifier for the checkpoint row (unique per drainer stream).
                Default = "core-drainer"
            batch_size: int - Max entries to process per cycle.
            poll_interval: float - Sleep seconds between cycles when no work.
        """
        self.db = memdb
        self.worker_id = str(worker_id)
        self.batch_size = int(batch_size)
        self.poll_interval = float(poll_interval)
        self._stop = threading.Event()
        self._last_seq = self._load_checkpoint()

    def start(self):
        """Start a background thread that drains continuously.

        Returns:
            threading.Thread - The started daemon thread.
        """
        thread = threading.Thread(
            target=self.run_forever,
            name=f"OutboxDrainer[{self.worker_id}]",
            daemon=True
        )
        thread.start()

        return thread

    def stop(self):
        """Signal the background thread to stop."""
        self._stop.set()

    def run_forever(self):
        """Continuously drain the outbox until stopped."""
        while not self._stop.is_set():
            try:
                processed = self.drain_once()

            except Exception:
                logger.exception("Outbox drain cycle failed; will retry.")
                processed = 0

            if processed == 0:
                time.sleep(self.poll_interval)

    def drain_once(self):
        """Process up to `batch_size` log entries once.

        Returns:
            int - Number of entries applied.
        """
        entries = self._fetch_batch(self._last_seq, self.batch_size)
        if not entries:
            return 0

        max_seq_seen = self._last_seq
        applied = 0

        with transaction.atomic():
            # Lock/ensure checkpoint row exists within the transaction
            checkpoint = (
                OutboxCheckpoint.objects.select_for_update().filter(worker_id=self.worker_id).first()
            )
            if checkpoint is None:
                checkpoint = OutboxCheckpoint.objects.create(worker_id=self.worker_id, last_seq=self._last_seq)

            for entry in entries:
                seq = entry.get('seq')
                event_type = entry.get('event_type') or entry.get('event') or ''
                payload = entry.get('payload') or {}

                if seq is None:
                    logger.warning("Skipping log entry without seq: %s", entry)
                    continue

                try:
                    handled = self._apply_event(event_type, payload)

                except Exception:
                    logger.exception("Failed to apply event_type=%s seq=%s", event_type, seq)
                    # Don't advance checkpoint on failure within this transaction
                    raise

                if handled:
                    applied += 1

                else:
                    # Unknown/unsupported event; don't block the stream
                    logger.warning("Skipped unknown event_type=%s seq=%s", event_type, seq)

                if seq > max_seq_seen:
                    max_seq_seen = seq

            # Atomically advance checkpoint with data writes
            OutboxCheckpoint.objects.filter(worker_id=self.worker_id).update(last_seq=max_seq_seen)

        # After commit, update in-memory pointer
        self._last_seq = max_seq_seen
        return applied

    # --- I/O helpers ---

    def _fetch_batch(self, since_seq, limit):
        """Fetch a batch of log entries from the in-memory DB."""
        # since_seq is exclusive in InMemoryDB.get_logs: returns seq > since_seq
        return self.db.get_logs(since_seq=since_seq, limit=limit)

    def _load_checkpoint(self):
        """Load last processed seq from Postgres (creates row if missing).

        Returns:
            int - The last processed sequence (or -1 if none).
        """
        checkpoint, _ = OutboxCheckpoint.objects.get_or_create(
            worker_id=self.worker_id,
            defaults={'last_seq': -1}
        )
        return int(checkpoint.last_seq)

    def _apply_event(self, event_type, payload):
        """Apply one event.

        Args:
            event_type: str - Event type label.
            payload: dict - Event payload.

        Returns:
            bool - True if applied or intentionally skipped as 'known', False if unknown.
        """
        if event_type == 'order_added':
            return self._persist_order_from_mem(payload)

        if event_type == 'order_updated':
            return self._persist_order_from_mem(payload)

        if event_type == 'fill_added':
            return self._persist_fill_from_mem(payload)

        if event_type == 'audit_log':
            _append_audit(payload)
            return True

        # These event types are currently not yetpersisted, so skip, but return True.
        # TODO: Still deciding whether to persist positions/account...
        if event_type in ('position_upserted', 'position_deleted', 'account_value_set'):
            logger.debug("Persistence for event_type=%s not yet implemented.", event_type)
            return True

        return False

    def _persist_order_from_mem(self, payload):
        """Upsert an Order row using the authoritative in-memory snapshot.

        Args:
            payload: dict - Should contain 'order_id'.

        Returns:
            bool - True if applied, False if missing data.
        """
        order_id = payload.get('order_id')
        if not order_id:
            logger.warning("order event missing order_id: %s", payload)
            return False

        rec = self.db.get_order(order_id)
        if not rec:
            logger.warning("in-memory order %s not found; skipping.", order_id)
            return False

        defaults = {
            'broker_order_id': rec.get('broker_order_id'),
            'asset_class': rec.get('asset_class', ''),
            'symbol': rec.get('symbol', ''),
            'side': rec.get('side', ''),
            'quantity': rec.get('qty') if rec.get('qty') is not None else rec.get('quantity', 0),
            'status': rec.get('status', 'SUBMITTED'),
            'avg_price': rec.get('avg_price'),
            'filled_qty': rec.get('filled_qty', 0),
            'commission': rec.get('commission'),
            'commission_currency': rec.get('commission_currency'),
            'realized_pnl': rec.get('realized_pnl'),
            'message': rec.get('message') or rec.get('error'),
        }
        Order.objects.update_or_create(order_id=order_id, defaults=defaults)
        return True

    def _persist_fill_from_mem(self, payload):
        """Insert a Fill row from the authoritative in-memory snapshot and update its Order.

        Args:
            payload: dict - Should contain 'fill_id' and 'order_id'.

        Returns:
            bool - True if applied, False if missing data.
        """
        fill_id = payload.get('fill_id')
        order_id = payload.get('order_id')

        if not fill_id or not order_id:
            logger.warning("fill event missing fill_id/order_id: %s", payload)
            return False

        fill = self.db.get_fill(fill_id)
        if not fill:
            logger.warning("in-memory fill %s not found; skipping.", fill_id)
            return False

        # Ensure order exists/up-to-date before inserting fill
        self._persist_order_from_mem({'order_id': order_id})
        order = Order.objects.get(order_id=order_id)

        exec_id = fill.get('exec_id') or str(fill_id)  # ensure uniqueness per fill
        created = False
        # Idempotent insert: if (order, exec_id) exists, do nothing
        fill_obj, created = Fill.objects.get_or_create(
            order=order,
            exec_id=exec_id,
            defaults={
                'price': float(fill.get('price') or 0.0),
                'filled_qty': int(fill.get('filled_qty') or 0),
                'symbol': fill.get('symbol') or order.symbol,
                'side': fill.get('side') or order.side,
                'time': str(fill.get('time') or ''),
                'broker_order_id': fill.get('broker_order_id') or order.broker_order_id,
                'perm_id': fill.get('permid') or fill.get('perm_id'),
            }
        )

        # Update cached aggregates on Order from in-memory snapshot, if provided
        rec = self.db.get_order(order_id) or {}
        updates = {}
        if rec.get('filled_qty') is not None:
            updates['filled_qty'] = int(rec['filled_qty'])

        if rec.get('avg_price') is not None:
            updates['avg_price'] = float(rec['avg_price'])

        if rec.get('status'):
            updates['status'] = rec['status']

        # If no hints from in-memory and a new fill was created, recompute VWAP from DB
        if not updates and created:
            agg = order.fills.all().aggregate(
                total=models.Sum('filled_qty'),
                vwap_num=models.Sum(models.F('price') * models.F('filled_qty')),
            )
            total = agg.get('total') or 0
            vwap_num = agg.get('vwap_num') or 0.0
            updates['filled_qty'] = int(total)
            updates['avg_price'] = float(vwap_num / total) if total else None

        if updates:
            Order.objects.filter(pk=order.pk).update(**updates)

        return True


def _append_audit(payload):
    """Append an audit row with the original payload.

    Args:
        payload: dict - Event payload copied into the audit row.
    """
    try:
        AuditLog.objects.create(
            seq_hint=payload.get('seq'),
            event_type=payload.get('event_type') or 'audit',
            payload=payload,
        )

    except Exception:
        logger.exception("Failed to append audit row")
