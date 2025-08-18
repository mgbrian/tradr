"""gRPC client.

Provides a client class that wraps the generated gRPC stubs and converts protobuf
messages into simple Python data structures (similar to the core API).
Also provides snake case aliases for all the methods.

Example:
    from client import TradingClient

    with TradingClient("localhost:50051") as c:
        resp = c.place_stock_order("AAPL", "BUY", 10)
        # resp -> {'order_id': 123, 'broker_order_id': 456, 'status': 'SUBMITTED', 'message': ''}
        print(resp)
"""

import grpc

import service_pb2
import service_pb2_grpc


def _order_record_to_dict(msg):
    """Convert a service_pb2.OrderRecord into a dict."""
    if msg is None:
        return {}

    return {
        'order_id': int(msg.order_id),
        'broker_order_id': int(msg.broker_order_id),
        'asset_class': msg.asset_class,
        'symbol': msg.symbol,
        'side': msg.side,
        'quantity': int(msg.quantity),
        'status': msg.status,
        'avg_price': float(msg.avg_price),
        'filled_qty': int(msg.filled_qty),
        'message': msg.message,
    }


def _fill_record_to_dict(msg):
    """Convert a service_pb2.FillRecord into a dict."""
    if msg is None:
        return {}

    return {
        'fill_id': int(msg.fill_id),
        'order_id': int(msg.order_id),
        'exec_id': msg.exec_id,
        'price': float(msg.price),
        'filled_qty': int(msg.filled_qty),
        'symbol': msg.symbol,
        'side': msg.side,
        'time': msg.time,
        'broker_order_id': int(msg.broker_order_id),
    }


def _position_record_to_dict(msg):
    """Convert a service_pb2.PositionRecord into a dict."""
    if msg is None:
        return {}

    return {
        'account': msg.account,
        'symbol': msg.symbol,
        'sec_type': msg.sec_type,
        'exchange': msg.exchange,
        'con_id': int(msg.con_id),
        'position': float(msg.position),
        'avg_cost': float(msg.avg_cost),
    }


def _account_value_record_to_dict(msg):
    """Convert a service_pb2.AccountValueRecord into a dict."""
    if msg is None:
        return {}

    return {
        'account': msg.account,
        'tag': msg.tag,
        'currency': msg.currency,
        'value': msg.value,
    }


class TradingClient:
    """Thin gRPC client for TradingService that returns Python datatypes."""

    def __init__(self, address, *, secure_channel_credentials=None, timeout=1.0):
        """Initialize the TradingClient.

        Args:
            address: str - Server address, e.g. "localhost:50051" or a unix socket address.

            Kwargs:
            ---
            secure_channel_credentials: grpc.ChannelCredentials or None -
                Use secure channel if provided.
            timeout: float - Default RPC timeout in seconds for all calls unless overridden.
                Default 1.0.
        """
        self.address = address
        self.timeout = timeout
        if secure_channel_credentials is None:
            self._channel = grpc.insecure_channel(self.address)

        else:
            self._channel = grpc.secure_channel(self.address, secure_channel_credentials)

        self._stub = service_pb2_grpc.TradingServiceStub(self._channel)

    def __enter__(self):
        """Enter context manager and return the client."""
        return self

    def __exit__(self, exc_type, exc, tb):
        """Exit context manager and close the channel."""
        self.close()

    def close(self):
        """Close the underlying gRPC channel."""
        self._channel.close()

    def PlaceStockOrder(self, symbol, side, quantity, *, order_type='MKT', limit_price=None, tif='DAY', timeout=None):
        """Place a stock order (market/limit/stop).

        Args:
            symbol: str - Ticker, e.g. "AAPL".
            side: str - "BUY" | "SELL" | "SHORT" | "COVER".
            quantity: int - Number of shares.
            order_type: str - "MKT" (default), "LMT", or "STP".
            limit_price: float or None - Required for "LMT"/"STP".
            tif: str - Time in force, e.g. "DAY" (default) or "GTC".
            timeout: float - Optional RPC timeout in seconds.
                If not provided, instance-level timeout is used.

        Returns:
            dict - {'order_id', 'broker_order_id', 'status', 'message'}
        """
        timeout = timeout or self.timeout
        # Only include limit_price if provided so proto 'optional' semantics behave properly.
        # TODO ** RENAME limit_price to price to match proto **. Leaving as is to match
        # raw API. Renaming should happen there as well.
        if limit_price is None:
            req = service_pb2.PlaceStockOrderRequest(
                symbol=symbol,
                side=side,
                quantity=int(quantity),
                order_type=order_type,
                tif=tif,
            )
        else:
            req = service_pb2.PlaceStockOrderRequest(
                symbol=symbol,
                side=side,
                quantity=int(quantity),
                order_type=order_type,
                price=float(limit_price),
                tif=tif,
            )

        resp = self._stub.PlaceStockOrder(req, timeout=timeout)
        return {
            'order_id': int(resp.order_id),
            'broker_order_id': int(resp.broker_order_id),
            'status': resp.status,
            'message': resp.message,
        }

    def PlaceOptionOrder(self, symbol, expiry, strike, right, side, quantity, *, order_type='MKT', limit_price=None, tif='DAY', timeout=None):
        """Place an option order (market/limit/stop).

        Args:
            symbol: str - Underlying ticker.
            expiry: str - Expiry in YYYYMMDD.
            strike: float - Strike price.
            right: str - "C" or "P".
            side: str - "BUY" | "SELL".
            quantity: int - Number of contracts.
            order_type: str - "MKT" (default), "LMT", or "STP".
            limit_price: float or None - Required for "LMT"/"STP".
            tif: str - Time in force, e.g. "DAY" (default) or "GTC".
            timeout: float - Optional RPC timeout in seconds.
                If not provided, instance-level timeout is used.

        Returns:
            dict - {'order_id', 'broker_order_id', 'status', 'message'}
        """
        timeout = timeout or self.timeout
        # Only include limit_price when provided to respect proto optional behavior.
        base_kwargs = dict(
            symbol=symbol,
            expiry=expiry,
            strike=float(strike),
            right=right,
            side=side,
            quantity=int(quantity),
            order_type=order_type,
            tif=tif,
        )
        if limit_price is not None:
            base_kwargs['price'] = float(limit_price)

        req = service_pb2.PlaceOptionOrderRequest(**base_kwargs)
        resp = self._stub.PlaceOptionOrder(req, timeout=timeout)
        return {
            'order_id': int(resp.order_id),
            'broker_order_id': int(resp.broker_order_id),
            'status': resp.status,
            'message': resp.message,
        }

    def GetOrder(self, order_id, timeout=None):
        """Fetch a single order by id.

        Args:
            order_id: int - Internal order id.
            timeout: float - Optional RPC timeout in seconds.
                If not provided, instance-level timeout is used.

        Returns:
            dict - Order record (see _order_record_to_dict).
        """
        timeout = timeout or self.timeout
        req = service_pb2.GetOrderRequest(order_id=int(order_id))
        resp = self._stub.GetOrder(req, timeout=timeout)

        return _order_record_to_dict(resp)

    def ListOrders(self, limit=None, timeout=None):
        """List recent orders.

        Args:
            limit: int - Maximum number of orders to return.
            timeout: float - Optional RPC timeout in seconds.
                If not provided, instance-level timeout is used.

        Returns:
            list of dict - List of order records.
        """
        timeout = timeout or self.timeout
        req = service_pb2.ListOrdersRequest(limit=int(limit) if limit is not None else 0)
        resp = self._stub.ListOrders(req, timeout=timeout)

        return [_order_record_to_dict(r) for r in resp.orders]

    def ListFills(self, order_id=None, limit=None, timeout=None):
        """List fills, optionally filtered by order id.

        Args:
            order_id: int - If provided, filter by this order id.
            limit: int - Maximum number of fills to return.
            timeout: float - Optional RPC timeout in seconds.
                If not provided, instance-level timeout is used.

        Returns:
            list of dict - List of fill records.
        """
        timeout = timeout or self.timeout
        req = service_pb2.ListFillsRequest(
            order_id=int(order_id) if order_id is not None else 0,
            limit=int(limit) if limit is not None else 0,
        )
        resp = self._stub.ListFills(req, timeout=timeout)
        return [_fill_record_to_dict(r) for r in resp.fills]

    def GetPositions(self, timeout=None):
        """Return current positions snapshot.

        Args:
            timeout: float - Optional RPC timeout in seconds.
                If not provided, instance-level timeout is used.

        Returns:
            list of dict - List of position records.
        """
        timeout = timeout or self.timeout
        resp = self._stub.GetPositions(service_pb2.GetPositionsRequest(), timeout=timeout)
        return [_position_record_to_dict(r) for r in resp.positions]

    def GetAccountValues(self, timeout=None):
        """Return current account values snapshot.

        Args:
            timeout: float - Optional RPC timeout in seconds.
                If not provided, instance-level timeout is used.

        Returns:
            list of dict - List of account value records.
        """
        timeout = timeout or self.timeout
        resp = self._stub.GetAccountValues(service_pb2.GetAccountValuesRequest(), timeout=timeout)
        return [_account_value_record_to_dict(r) for r in resp.account_values]

    # --- Snake_case aliases

    place_stock_order = PlaceStockOrder
    place_option_order = PlaceOptionOrder
    get_order = GetOrder
    list_orders = ListOrders
    list_fills = ListFills
    get_positions = GetPositions
    get_account_values = GetAccountValues
