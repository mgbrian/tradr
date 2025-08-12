from django.db import models


class Order(models.Model):
    """Order master record."""
    # internal id
    order_id = models.BigIntegerField(primary_key=True)

    broker_order_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    # "STK" | "OPT"
    asset_class = models.CharField(max_length=8, db_index=True)
    symbol = models.CharField(max_length=32, db_index=True)
    # "BUY"|"SELL"|"SHORT"|"COVER"
    side = models.CharField(max_length=8, db_index=True)
    quantity = models.IntegerField()
    status = models.CharField(max_length=32, db_index=True, default="SUBMITTED")

    # Cached aggregates (updated by drainer)
    avg_price = models.FloatField(null=True, blank=True)
    filled_qty = models.IntegerField(default=0)

    commission = models.FloatField(null=True, blank=True)
    commission_currency = models.CharField(max_length=8, null=True, blank=True)
    realized_pnl = models.FloatField(null=True, blank=True)

    # Error, etc. TODO: Rename this to notes?
    message = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        db_table = "orders"


class Fill(models.Model):
    """Order fills."""
    fill_id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="fills")

    # broker exec id
    exec_id = models.CharField(max_length=64, db_index=True)
    price = models.FloatField()
    filled_qty = models.IntegerField()
    symbol = models.CharField(max_length=32)
    side = models.CharField(max_length=8)
    # broker-provided text timestamp. TODO: Convert this to actual timestamp?
    time = models.CharField(max_length=64, db_index=True)
    # Record this here in case fill arrives (from broker) before order. Optional though.
    broker_order_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    perm_id = models.BigIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "fills"
        indexes = [
            models.Index(fields=["order", "exec_id"]),
        ]
        unique_together = [("order", "exec_id")]


class AuditLog(models.Model):
    """Append-only audit log for events and debugging."""
    seq_hint = models.BigIntegerField(null=True, blank=True, db_index=True)
    event_type = models.CharField(max_length=64, db_index=True)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "audit_log"
        indexes = [
            models.Index(fields=["event_type", "created_at"]),
        ]


class OutboxCheckpoint(models.Model):
    """Last processed outbox seq per worker/source."""
    # In case we have multiple drainers, give each an id.
    worker_id = models.CharField(max_length=64, primary_key=True)
    last_seq = models.BigIntegerField(default=-1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "outbox_checkpoints"
