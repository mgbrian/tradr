"""Tests focused on the gRPC server veneer (as underlying logic is already tested elsewhere)."""

from concurrent import futures
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import grpc

import service_pb2
import service_pb2_grpc
import server


class TestTradingService(unittest.TestCase):
    def setUp(self):
        # Patch the TradingAPI used in the server to avoid hitting the real backend
        self.patcher = patch.object(server, "TradingAPI", autospec=True)
        self.mock_api_cls = self.patcher.start()
        self.mock_api = self.mock_api_cls.return_value

        # Create a real gRPC server in-process
        self.grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
        service_pb2_grpc.add_TradingServiceServicer_to_server(
            server.TradingServiceServicer(self.mock_api),
            self.grpc_server
        )
        port = self.grpc_server.add_insecure_port("localhost:0")
        self.address = f"localhost:{port}"
        self.grpc_server.start()

        # Create a stub connected to our in-process server
        self.channel = grpc.insecure_channel(self.address)
        self.stub = service_pb2_grpc.TradingServiceStub(self.channel)

    def tearDown(self):
        self.grpc_server.stop(0)
        self.patcher.stop()

    # --- PlaceStockOrder ---

    def test_place_stock_order_calls_api_and_returns_proto(self):
        """Default stock order: server forwards defaults (MKT/DAY, no limit_price) and returns proto."""
        self.mock_api.place_stock_order.return_value = SimpleNamespace(order_id=1)
        # The server reads broker_order_id/status/message from get_order(handle.order_id)
        self.mock_api.get_order.return_value = {
            'order_id': 1,
            'broker_order_id': 42,
            'status': 'SUBMITTED',
            'message': 'ok'
        }

        req = service_pb2.PlaceStockOrderRequest(symbol="AAPL", side="BUY", quantity=10)
        resp = self.stub.PlaceStockOrder(req)

        args, kwargs = self.mock_api.place_stock_order.call_args
        self.assertEqual(args[:3], ("AAPL", "BUY", 10))
        # Defaults applied by veneer
        self.assertEqual(kwargs.get('order_type'), 'MKT')
        self.assertIsNone(kwargs.get('limit_price'))
        self.assertEqual(kwargs.get('tif'), 'DAY')

        self.assertEqual(resp.order_id, 1)
        self.assertEqual(resp.broker_order_id, 42)
        self.assertEqual(resp.status, "SUBMITTED")
        self.assertEqual(resp.message, "ok")

    def test_place_stock_order_limit_routing_and_proto(self):
        """LMT stock order: veneer forwards order_type/limit_price/tif and returns proto."""
        self.mock_api.place_stock_order.return_value = SimpleNamespace(order_id=11)
        self.mock_api.get_order.return_value = {
            'order_id': 11,
            'broker_order_id': 501,
            'status': 'SUBMITTED',
            'message': ''
        }

        req = service_pb2.PlaceStockOrderRequest(
            symbol="MSFT", side="SELL", quantity=5,
            order_type="LMT", price=426.20, tif="GTC"
        )
        resp = self.stub.PlaceStockOrder(req)

        args, kwargs = self.mock_api.place_stock_order.call_args
        self.assertEqual(args[:3], ("MSFT", "SELL", 5))
        self.assertEqual(kwargs.get('order_type'), 'LMT')
        self.assertEqual(kwargs.get('limit_price'), 426.20)
        self.assertEqual(kwargs.get('tif'), 'GTC')

        self.assertEqual(resp.order_id, 11)
        self.assertEqual(resp.broker_order_id, 501)
        self.assertEqual(resp.status, "SUBMITTED")

    def test_place_stock_order_stop_routing(self):
        """STP stock order: veneer forwards trigger price and TIF."""
        self.mock_api.place_stock_order.return_value = SimpleNamespace(order_id=22)
        self.mock_api.get_order.return_value = {
            'order_id': 22,
            'broker_order_id': 777,
            'status': 'SUBMITTED',
            'message': ''
        }

        req = service_pb2.PlaceStockOrderRequest(
            symbol="TSLA", side="SHORT", quantity=3,
            order_type="STP", price=250.0, tif="DAY"
        )
        _ = self.stub.PlaceStockOrder(req)

        args, kwargs = self.mock_api.place_stock_order.call_args
        self.assertEqual(args[:3], ("TSLA", "SHORT", 3))
        self.assertEqual(kwargs.get('order_type'), 'STP')
        self.assertEqual(kwargs.get('limit_price'), 250.0)
        self.assertEqual(kwargs.get('tif'), 'DAY')

    # --- PlaceOptionOrder ---

    def test_place_option_order_market_defaults(self):
        """Option MKT: veneer forwards defaults and returns proto."""
        self.mock_api.place_option_order.return_value = SimpleNamespace(order_id=1001)
        self.mock_api.get_order.return_value = {
            'order_id': 1001,
            'broker_order_id': 9001,
            'status': 'SUBMITTED',
            'message': ''
        }

        req = service_pb2.PlaceOptionOrderRequest(
            symbol="AAPL", expiry="20251219", strike=150.0,
            right="C", side="BUY", quantity=2
        )
        resp = self.stub.PlaceOptionOrder(req)

        args, kwargs = self.mock_api.place_option_order.call_args
        self.assertEqual(args[:6], ("AAPL", "20251219", 150.0, "C", "BUY", 2))
        self.assertEqual(kwargs.get('order_type'), 'MKT')
        self.assertIsNone(kwargs.get('limit_price'))
        self.assertEqual(kwargs.get('tif'), 'DAY')

        self.assertEqual(resp.order_id, 1001)
        self.assertEqual(resp.broker_order_id, 9001)
        self.assertEqual(resp.status, "SUBMITTED")

    def test_place_option_order_limit_routing_and_proto(self):
        """Option LMT: veneer forwards order_type/price/tif and returns proto."""
        self.mock_api.place_option_order.return_value = SimpleNamespace(order_id=1002)
        self.mock_api.get_order.return_value = {
            'order_id': 1002,
            'broker_order_id': 9002,
            'status': 'SUBMITTED',
            'message': ''
        }

        req = service_pb2.PlaceOptionOrderRequest(
            symbol="AAPL", expiry="20251219", strike=150.0,
            right="C", side="BUY", quantity=2,
            order_type="LMT", price=1.25, tif="GTC"
        )
        resp = self.stub.PlaceOptionOrder(req)

        args, kwargs = self.mock_api.place_option_order.call_args
        self.assertEqual(args[:6], ("AAPL", "20251219", 150.0, "C", "BUY", 2))
        self.assertEqual(kwargs.get('order_type'), 'LMT')
        self.assertEqual(kwargs.get('limit_price'), 1.25)
        self.assertEqual(kwargs.get('tif'), 'GTC')
        self.assertEqual(resp.broker_order_id, 9002)

    def test_place_option_order_stop_routing(self):
        """Option STP: veneer forwards trigger price and TIF."""
        self.mock_api.place_option_order.return_value = SimpleNamespace(order_id=1003)
        self.mock_api.get_order.return_value = {
            'order_id': 1003,
            'broker_order_id': 9003,
            'status': 'SUBMITTED',
            'message': ''
        }

        req = service_pb2.PlaceOptionOrderRequest(
            symbol="SPY", expiry="20260116", strike=420.0,
            right="P", side="SELL", quantity=1,
            order_type="STP", price=2.50, tif="DAY"
        )
        _ = self.stub.PlaceOptionOrder(req)

        args, kwargs = self.mock_api.place_option_order.call_args
        self.assertEqual(args[:6], ("SPY", "20260116", 420.0, "P", "SELL", 1))
        self.assertEqual(kwargs.get('order_type'), 'STP')
        self.assertEqual(kwargs.get('limit_price'), 2.50)
        self.assertEqual(kwargs.get('tif'), 'DAY')

    # --- Error mapping (context.abort) ---

    def test_place_stock_order_api_exception_returns_grpc_error(self):
        """Server now aborts with INTERNAL (was previously returning status='ERROR')."""
        self.mock_api.place_stock_order.side_effect = RuntimeError("IBKR down")

        # silence logs so failures don't look like test failures
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        req = service_pb2.PlaceStockOrderRequest(symbol="AAPL", side="BUY", quantity=10)
        with self.assertRaises(grpc.RpcError) as cm:
            self.stub.PlaceStockOrder(req)

        self.assertEqual(cm.exception.code(), grpc.StatusCode.INTERNAL)
        self.assertIn("IBKR down", cm.exception.details())

    def test_place_stock_order_validation_error_maps_to_invalid_argument(self):
        """ValueError in API should map to INVALID_ARGUMENT."""
        self.mock_api.place_stock_order.side_effect = ValueError("LMT requires limit_price")

        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        # Missing limit_price while asking for LMT
        req = service_pb2.PlaceStockOrderRequest(
            symbol="AAPL", side="BUY", quantity=1, order_type="LMT"
        )
        with self.assertRaises(grpc.RpcError) as cm:
            self.stub.PlaceStockOrder(req)

        self.assertEqual(cm.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        self.assertIn("requires", cm.exception.details())

    def test_place_option_order_timeout_maps_to_deadline_exceeded(self):
        """TimeoutError in API should map to DEADLINE_EXCEEDED."""
        self.mock_api.place_option_order.side_effect = TimeoutError("broker timeout")

        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        req = service_pb2.PlaceOptionOrderRequest(
            symbol="AAPL", expiry="20251219", strike=150.0,
            right="C", side="BUY", quantity=1, order_type="MKT"
        )
        with self.assertRaises(grpc.RpcError) as cm:
            self.stub.PlaceOptionOrder(req)

        self.assertEqual(cm.exception.code(), grpc.StatusCode.DEADLINE_EXCEEDED)
        self.assertIn("timeout", cm.exception.details())

    # --- GetPositions/GetOrder mapping changes ---

    def test_get_positions_api_exception_returns_grpc_error(self):
        """RuntimeError now maps to INTERNAL (previously UNKNOWN)."""
        self.mock_api.get_positions.side_effect = RuntimeError("DB unreachable")

        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        req = service_pb2.GetPositionsRequest()
        with self.assertRaises(grpc.RpcError) as cm:
            self.stub.GetPositions(req)

        self.assertEqual(cm.exception.code(), grpc.StatusCode.INTERNAL)
        self.assertIn("DB unreachable", cm.exception.details())

    def test_get_order_status_api_exception_returns_grpc_invalid_argument(self):
        """ValueError now maps to INVALID_ARGUMENT (previously UNKNOWN)."""
        self.mock_api.get_order.side_effect = ValueError("Order not found")

        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        req = service_pb2.GetOrderRequest(order_id=999)
        with self.assertRaises(grpc.RpcError) as cm:
            self.stub.GetOrder(req)

        self.assertEqual(cm.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        self.assertIn("Order not found", cm.exception.details())

    def test_get_order_keyerror_maps_to_not_found(self):
        """KeyError should map to NOT_FOUND."""
        self.mock_api.get_order.side_effect = KeyError("missing")

        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        req = service_pb2.GetOrderRequest(order_id=404)
        with self.assertRaises(grpc.RpcError) as cm:
            self.stub.GetOrder(req)

        self.assertEqual(cm.exception.code(), grpc.StatusCode.NOT_FOUND)
        self.assertIn("missing", cm.exception.details())


if __name__ == "__main__":
    unittest.main()
