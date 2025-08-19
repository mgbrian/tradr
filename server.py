"""gRPC server implementation for API."""

from concurrent import futures
import logging
import os

import grpc

import service_pb2
import service_pb2_grpc

from api import TradingAPI
from db.inmemorydb import InMemoryDB
from execution_tracker import ExecutionTracker
from position_tracker import PositionTracker
from session import IBSession


DEFAULT_SERVER_ADDRESS = os.environ.get("GRPC_SERVER_ADDRESS") or f"[::]:{50057}"

logger = logging.getLogger(__name__)


def _order_dict_to_record(d):
    """Convert internal order dict to proto OrderRecord.

    Args:
        d: dict - DB order record.

    Returns:
        service_pb2.OrderRecord.
    """
    if not d:
        # Empty record
        return service_pb2.OrderRecord()

    return service_pb2.OrderRecord(
        order_id=int(d.get('order_id') or 0),
        broker_order_id=int(d.get('broker_order_id') or 0),
        asset_class=str(d.get('asset_class') or ''),
        symbol=str(d.get('symbol') or ''),
        side=str(d.get('side') or ''),
        quantity=int(d.get('qty') or d.get('quantity') or 0),
        status=str(d.get('status') or ''),
        avg_price=float(d.get('avg_price') or 0.0),
        filled_qty=int(d.get('filled_qty') or 0),
        message=str(d.get('error') or d.get('message') or '')
    )


def _fill_dict_to_record(d):
    """Convert internal fill dict to proto FillRecord.

    Args:
        d: dict - DB fill record.

    Returns:
        service_pb2.FillRecord.
    """
    if not d:
        return service_pb2.FillRecord()

    return service_pb2.FillRecord(
        fill_id=int(d.get('fill_id') or 0),
        order_id=int(d.get('order_id') or 0),
        exec_id=str(d.get('exec_id') or ''),
        price=float(d.get('price') or 0.0),
        filled_qty=int(d.get('filled_qty') or 0),
        symbol=str(d.get('symbol') or ''),
        side=str(d.get('side') or ''),
        time=str(d.get('time') or ''),
        broker_order_id=int(d.get('broker_order_id') or 0),
    )


def _positions_snapshot_to_response(snapshot):
    """Convert DB positions snapshot to GetPositionsResponse.

    Args:
        snapshot: dict - Mapping of position_key -> position dict.

    Returns:
        service_pb2.GetPositionsResponse.
    """
    records = []
    for _k, v in snapshot.items():
        c = v.get('contract')
        records.append(
            service_pb2.PositionRecord(
                account=str(v.get('account') or ''),
                symbol=str(getattr(c, 'symbol', '') or ''),
                sec_type=str(getattr(c, 'secType', '') or ''),
                exchange=str(getattr(c, 'exchange', '') or ''),
                con_id=int(getattr(c, 'conId', 0) or 0),
                position=float(v.get('position') or 0.0),
                avg_cost=float(v.get('avgCost') or 0.0),
            )
        )
    return service_pb2.GetPositionsResponse(positions=records)


def _account_values_snapshot_to_response(snapshot):
    """Convert DB account values snapshot to GetAccountValuesResponse.

    Args:
        snapshot: dict - Mapping (account, tag, currency) -> value dict.

    Returns:
        service_pb2.GetAccountValuesResponse.
    """
    records = []
    for (_account, _tag, _ccy), v in snapshot.items():
        records.append(
            service_pb2.AccountValueRecord(
                account=str(v.get('account') or ''),
                tag=str(v.get('tag') or ''),
                currency=str(v.get('currency') or ''),
                value=str(v.get('value') or ''),
            )
        )
    return service_pb2.GetAccountValuesResponse(account_values=records)


def _abort_for_exception(context, exc: Exception):
    """Map backend exceptions to canonical gRPC StatusCodes and abort the call.

    - Business/validation errors return INVALID_ARGUMENT.
    - Infra/timeouts/etc. map to appropriate codes.
    - Everything else maps to INTERNAL.
    """
    if isinstance(exc, ValueError):
        code = grpc.StatusCode.INVALID_ARGUMENT

    elif isinstance(exc, KeyError):
        code = grpc.StatusCode.NOT_FOUND

    elif isinstance(exc, TimeoutError):
        code = grpc.StatusCode.DEADLINE_EXCEEDED

    elif isinstance(exc, PermissionError):
        code = grpc.StatusCode.PERMISSION_DENIED

    else:
        code = grpc.StatusCode.INTERNAL

    # Log with traceback and abort
    logger.exception("RPC failed (%s): %s", code.name, exc)
    context.abort(code, str(exc))


class TradingServiceServicer(service_pb2_grpc.TradingServiceServicer):
    """gRPC servicer that adapts to TradingAPI."""

    def __init__(self, api):
        """Create a new servicer.

        Args:
            api: TradingAPI - High-level API façade.
        """
        self.api = api

    # --- Place Orders ---

    def PlaceStockOrder(self, request, context):
        """Place a stock order.

        Supports market, limit, and stop orders. Server forwards
        the type/price/tif fields to the API and returns the broker/DB ids.

        Args:
            request: service_pb2.PlaceStockOrderRequest

        Returns:
            service_pb2.PlaceOrderResponse
        """
        try:
            order_type = (request.order_type or 'MKT').upper()
            # For LMT/STP, limit_price must be present (validated in API as well)
            limit_price = request.price if request.HasField("price") else None

            tif = (request.tif or 'DAY').upper()

            handle = self.api.place_stock_order(
                request.symbol,
                request.side,
                int(request.quantity),
                order_type=order_type,
                limit_price=limit_price,
                tif=tif,
            )

            # Fetch the order to fill in status/message if available
            rec = self.api.get_order(handle.order_id) or {}

            return service_pb2.PlaceOrderResponse(
                order_id=handle.order_id,
                broker_order_id=int(rec.get('broker_order_id') or 0),
                status=str(rec.get('status') or 'SUBMITTED'),
                message=str(rec.get('error') or rec.get('message') or '')
            )

        except Exception as e:
            _abort_for_exception(context, e)

    def PlaceOptionOrder(self, request, context):
        """Place an option order.

        Supports market, limit, and stop orders. Server forwards
        the type/price/tif fields to the API and returns the broker/DB ids.

        Args:
            request: service_pb2.PlaceOptionOrderRequest

        Returns:
            service_pb2.PlaceOrderResponse
        """
        try:
            order_type = (request.order_type or 'MKT').upper()
            limit_price = request.price if request.HasField("price") else None

            tif = (request.tif or 'DAY').upper()

            handle = self.api.place_option_order(
                request.symbol,
                request.expiry,
                float(request.strike),
                request.right,
                request.side,
                int(request.quantity),
                order_type=order_type,
                limit_price=limit_price,
                tif=tif,
            )
            rec = self.api.get_order(handle.order_id) or {}

            return service_pb2.PlaceOrderResponse(
                order_id=handle.order_id,
                broker_order_id=int(rec.get('broker_order_id') or 0),
                status=str(rec.get('status') or 'SUBMITTED'),
                message=str(rec.get('error') or rec.get('message') or '')
            )

        except Exception as e:
            _abort_for_exception(context, e)

    # --- Cancellation ---

    def CancelOrder(self, request, context):
        """Cancel an order by internal id.

        Args:
            request: service_pb2.CancelOrderRequest - order_id.

        Returns:
            service_pb2.CancelOrderResponse - ok/status/message.
        """
        try:
            order_id = int(request.order_id)
            ok = self.api.cancel_order(order_id)
            rec = self.api.get_order(order_id) or {}

            return service_pb2.CancelOrderResponse(
                ok=bool(ok),
                status=str(rec.get('status') or ('CANCEL_REQUESTED' if ok else '')),
                message=str(rec.get('error') or rec.get('message') or '')
            )

        except Exception as e:
            _abort_for_exception(context, e)

    # --- Modification ---

    def ModifyOrder(self, request, context):
        """Modify an existing order by internal id.

        Args:
            request: service_pb2.ModifyOrderRequest - order_id and optional fields.

        Returns:
            service_pb2.ModifyOrderResponse - ok/status/message.
        """
        try:
            order_id = int(request.order_id)
            kwargs = {}

            if request.HasField("quantity"):
                kwargs["quantity"] = int(request.quantity)
            if request.HasField("order_type"):
                kwargs["order_type"] = (request.order_type or '').upper()
            if request.HasField("price"):
                kwargs["limit_price"] = float(request.price)
            if request.HasField("tif"):
                kwargs["tif"] = (request.tif or '').upper()

            ok = self.api.modify_order(order_id, **kwargs)
            rec = self.api.get_order(order_id) or {}

            return service_pb2.ModifyOrderResponse(
                ok=bool(ok),
                status=str(rec.get('status') or ('MODIFY_REQUESTED' if ok else '')),
                message=str(rec.get('error') or rec.get('message') or '')
            )

        except Exception as e:
            _abort_for_exception(context, e)

    # --- Orders / Fills ---

    def GetOrder(self, request, context):
        """Fetch a single order.

        Args:
            request: service_pb2.GetOrderRequest - Contains order_id.

        Returns:
            service_pb2.OrderRecord - Order (empty if not found).
        """
        try:
            rec = self.api.get_order(int(request.order_id))
            return _order_dict_to_record(rec or {})

        except Exception as e:
            _abort_for_exception(context, e)

    def ListOrders(self, request, context):
        """List recent orders.

        Args:
            request: service_pb2.ListOrdersRequest (with optional limit).

        Returns:
            service_pb2.ListOrdersResponse - Orders list.
        """
        try:
            rows = self.api.list_orders(limit=int(request.limit) if request.limit else None)
            return service_pb2.ListOrdersResponse(orders=[_order_dict_to_record(r) for r in rows])

        except Exception as e:
            _abort_for_exception(context, e)

    def ListFills(self, request, context):
        """List fills, optionally filtered by order id.

        Args:
            request: service_pb2.ListFillsRequest - order_id and limit.

        Returns:
            service_pb2.ListFillsResponse - Fills list.
        """
        try:
            order_id = int(request.order_id) if request.order_id else None
            rows = self.api.list_fills(order_id=order_id, limit=int(request.limit) if request.limit else None)

            return service_pb2.ListFillsResponse(fills=[_fill_dict_to_record(r) for r in rows])

        except Exception as e:
            _abort_for_exception(context, e)

    # --- Portfolio ---

    def GetPositions(self, request, context):
        """Return current positions snapshot.

        Args:
            request: service_pb2.GetPositionsRequest - Empty.

        Returns:
            service_pb2.GetPositionsResponse - Positions list.
        """
        try:
            snap = self.api.get_positions()
            return _positions_snapshot_to_response(snap)

        except Exception as e:
            _abort_for_exception(context, e)

    def GetAccountValues(self, request, context):
        """Return current account values snapshot.

        Args:
            request: service_pb2.GetAccountValuesRequest - Empty.

        Returns:
            service_pb2.GetAccountValuesResponse - Account values list.
        """
        try:
            snap = self.api.get_account_values()
            return _account_values_snapshot_to_response(snap)

        except Exception as e:
            _abort_for_exception(context, e)


def serve(address=DEFAULT_SERVER_ADDRESS, *, db=None, ib=None, api=None, position_tracker=None,
          execution_tracker=None, auto_connect=True, start_trackers=True, wait=True):
    """Start the gRPC server and wire dependencies.

    Args:
        address: str - Optional server address. Default is DEFAULT_SERVER_ADDRESS.

        Optional kwargs for shared resources. "Shared" with other parts of the application.
        These should be passed if they've been created elsewhere, e.g. if there's a
        part of the application using the API directly.
        ---
        db: InMemoryDB (optional) - Shared in-memory database instance.
        ib: IB or IBSession (optional) - Shared Interactive Brokers connection/session.
        api: TradingAPI (optional) - API layer object for order placement and queries.
        position_tracker: PositionTracker (optional) - Tracker for positions/account values.
        execution_tracker: ExecutionTracker (optional) - Tracker for executions/fills.
        auto_connect: bool - If True, start trackers that are not already started.
        start_trackers: bool - If True, start trackers that are not already started.
        wait: bool - If True, block on server.wait_for_termination(); otherwise return immediately.

    Returns:
        tuple - (grpc_server, handles_dict) when wait=False. The handles dict contains:
            {
                'db': db,
                'ib': ib,
                'api': api,
                'position_tracker': position_tracker,
                'execution_tracker': execution_tracker,
                'ib_session': ib_session_or_none
            }

    Raises:
        RuntimeError - If IB session cannot connect or gRPC server cannot bind.
    """
    # Prefer passed-in singletons, lazily create what’s missing
    db = db or InMemoryDB()

    ib_session = None
    if ib is None:
        ib_session = IBSession()
        if auto_connect:
            ib_session.connect()

        ib = ib_session.ib

    elif hasattr(ib, "connect") and hasattr(ib, "ib"):
        # Caller passed an IBSession instead of IB; normalize to IB object
        ib_session = ib
        if auto_connect:
            ib_session.connect()

        ib = ib_session.ib

    # Trackers
    position_tracker = position_tracker or PositionTracker(ib, db=db)
    execution_tracker = execution_tracker or ExecutionTracker(ib, db=db)

    if start_trackers:
        if position_tracker is not None and getattr(position_tracker, '_position_handler', None) is None:
            position_tracker.start()

        if execution_tracker is not None and getattr(execution_tracker, '_exec_handler', None) is None:
            execution_tracker.start()

    # API layer
    api = api or TradingAPI(ib, db, position_tracker=position_tracker)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    service_pb2_grpc.add_TradingServiceServicer_to_server(
        TradingServiceServicer(api), server
    )

    bound = server.add_insecure_port(address)
    if bound == 0:
        raise RuntimeError(f"Failed to bind gRPC server on {address}")

    server.start()
    logger.info("gRPC Trading Service started on %s", address)

    if not wait:
        return server, {
            'db': db,
            'ib': ib,
            'api': api,
            'position_tracker': position_tracker,
            'execution_tracker': execution_tracker,
            'ib_session': ib_session,
        }

    try:
        server.wait_for_termination()

    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        server.stop(0)


if __name__ == "__main__":
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(name)s] %(levelname)s: %(message)s"
    )
    serve()
