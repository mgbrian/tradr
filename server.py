"""gRPC server implementation for API."""

from concurrent import futures
import logging
import os

import grpc

import service_pb2
import service_pb2_grpc

from api import TradingAPI
from db import InMemoryDB
from execution_tracker import ExecutionTracker
from position_tracker import PositionTracker
from session import IBSession


SERVER_ADDRESS = f"[::]:{50057}"

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
        """Place a stock market order.

        Args:
            request: service_pb2.PlaceStockOrderRequest

        Returns:
            service_pb2.PlaceOrderResponse
        """
        try:
            handle = self.api.place_stock_order(
                request.symbol, request.side, int(request.quantity),
                order_type='MKT'
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
            logger.exception("PlaceStockOrder failed")
            return service_pb2.PlaceOrderResponse(
                order_id=0,
                broker_order_id=0,
                status="ERROR",
                message=str(e)
            )

    def PlaceOptionOrder(self, request, context):
        """Place an option market order.

        Args:
            request: service_pb2.PlaceOptionOrderRequest

        Returns:
            service_pb2.PlaceOrderResponse
        """
        try:
            handle = self.api.place_option_order(
                request.symbol, request.expiry, float(request.strike),
                request.right, request.side, int(request.quantity),
                order_type='MKT'
            )
            rec = self.api.get_order(handle.order_id) or {}

            return service_pb2.PlaceOrderResponse(
                order_id=handle.order_id,
                broker_order_id=int(rec.get('broker_order_id') or 0),
                status=str(rec.get('status') or 'SUBMITTED'),
                message=str(rec.get('error') or rec.get('message') or '')
            )

        except Exception as e:
            logger.exception("PlaceOptionOrder failed")
            return service_pb2.PlaceOrderResponse(
                order_id=0,
                broker_order_id=0,
                status="ERROR",
                message=str(e)
            )

    # --- Orders / Fills ---

    def GetOrder(self, request, context):
        """Fetch a single order.

        Args:
            request: service_pb2.GetOrderRequest - Contains order_id.

        Returns:
            service_pb2.OrderRecord - Order (empty if not found).
        """
        rec = self.api.get_order(int(request.order_id))
        return _order_dict_to_record(rec or {})

    def ListOrders(self, request, context):
        """List recent orders.

        Args:
            request: service_pb2.ListOrdersRequest (with optional limit).

        Returns:
            service_pb2.ListOrdersResponse - Orders list.
        """
        rows = self.api.list_orders(limit=int(request.limit) if request.limit else None)
        return service_pb2.ListOrdersResponse(orders=[_order_dict_to_record(r) for r in rows])

    def ListFills(self, request, context):
        """List fills, optionally filtered by order id.

        Args:
            request: service_pb2.ListFillsRequest - order_id and limit.

        Returns:
            service_pb2.ListFillsResponse - Fills list.
        """
        order_id = int(request.order_id) if request.order_id else None
        rows = self.api.list_fills(order_id=order_id, limit=int(request.limit) if request.limit else None)

        return service_pb2.ListFillsResponse(fills=[_fill_dict_to_record(r) for r in rows])

    # --- Portfolio ---

    def GetPositions(self, request, context):
        """Return current positions snapshot.

        Args:
            request: service_pb2.GetPositionsRequest - Empty.

        Returns:
            service_pb2.GetPositionsResponse - Positions list.
        """
        snap = self.api.get_positions()
        return _positions_snapshot_to_response(snap)

    def GetAccountValues(self, request, context):
        """Return current account values snapshot.

        Args:
            request: service_pb2.GetAccountValuesRequest - Empty.

        Returns:
            service_pb2.GetAccountValuesResponse - Account values list.
        """
        snap = self.api.get_account_values()
        return _account_values_snapshot_to_response(snap)


def serve(address=SERVER_ADDRESS, *, db=None, ib=None, api=None, position_tracker=None,
        execution_tracker=None, auto_connect=True):
    """Start the gRPC server and wire dependencies.

    Args:
        address: str - Optional server address. Default is SERVER_ADDRESS.

        Optional kwargs for shared resources. "Shared" with other parts of the application.
        These should be passed if they've been created elsewhere, e.g. if there's a
        part of the application using the API directly.
        ---
        db: InMemoryDB (optional) - Shared in-memory database instance.
        ib: IB or IBSession (optional) - Shared Interactive Brokers connection/session.
        api: TradingAPI (optional) - API layer object for order placement and queries.
        position_tracker: PositionTracker (optional) - Tracker for positions/account values.
        execution_tracker: ExecutionTracker (optional) - Tracker for executions/fills.
        auto_connect: bool - If True and an IBSession is created internally, connect immediately.

    Raises:
        RuntimeError - If IB session cannot connect.
    """

    # Ideally we want to have one copy of these for the entire application to
    # avoid duplication. These are ideally created for a lower layer and then
    # passed here, if we're also using the lower layer directly. Only create them
    # here if they don't exist yet.
    # TODO: Make this cleaner/more robust!
    db = db or InMemoryDB()
    ib_session = None
    if ib is None:
        ib_session = IBSession()
        if auto_connect:
            ib_session.connect()
        ib = ib_session.ib

    position_tracker = position_tracker or PositionTracker(ib, db=db)
    execution_tracker = execution_tracker or ExecutionTracker(ib, db=db)

    # Start trackers only if you created them here
    created_trackers = []
    if position_tracker is not None and getattr(position_tracker, '_position_handler', None) is None:
        position_tracker.start()
        created_trackers.append(position_tracker)
    if execution_tracker is not None and getattr(execution_tracker, '_exec_handler', None) is None:
        execution_tracker.start()
        created_trackers.append(execution_tracker)

    api = api or TradingAPI(ib, db, position_tracker=position_tracker)


    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    service_pb2_grpc.add_TradingServiceServicer_to_server(TradingServiceServicer(api), server)

    server.add_insecure_port(address)
    server.start()
    logger.info("gRPC Trading Service started on %s", address)

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
