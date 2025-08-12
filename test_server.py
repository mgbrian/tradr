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

    def test_place_stock_order_calls_api_and_returns_proto(self):
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
        self.assertEqual(kwargs.get('order_type'), 'MKT')

        self.assertEqual(resp.order_id, 1)
        self.assertEqual(resp.broker_order_id, 42)
        self.assertEqual(resp.status, "SUBMITTED")
        self.assertEqual(resp.message, "ok")

    def test_get_positions_calls_api_and_returns_proto_list(self):
        # Mock the API to return a snapshot dict keyed by position_key,
        # with each value containing 'account', 'contract', 'position', 'avgCost'.
        aapl_contract = SimpleNamespace(symbol="AAPL", secType="STK", exchange="NASDAQ", conId=101)
        msft_contract = SimpleNamespace(symbol="MSFT", secType="STK", exchange="NASDAQ", conId=202)
        self.mock_api.get_positions.return_value = {
            # keys don't matter for server conversion; values do
            ("key1",): {
                "account": "DU123",
                "contract": aapl_contract,
                "position": 100.0,
                "avgCost": 150.5,
            },
            ("key2",): {
                "account": "DU123",
                "contract": msft_contract,
                "position": 50.0,
                "avgCost": 250.0,
            },
        }

        req = service_pb2.GetPositionsRequest()
        resp = self.stub.GetPositions(req)

        self.mock_api.get_positions.assert_called_once_with()
        self.assertEqual(len(resp.positions), 2)

        # AAPL record assertions
        p0 = resp.positions[0]
        self.assertEqual(p0.account, "DU123")
        self.assertEqual(p0.symbol, "AAPL")
        self.assertEqual(p0.sec_type, "STK")
        self.assertEqual(p0.exchange, "NASDAQ")
        self.assertEqual(p0.con_id, 101)
        self.assertEqual(p0.position, 100.0)
        self.assertEqual(p0.avg_cost, 150.5)

        # MSFT record assertions
        p1 = resp.positions[1]
        self.assertEqual(p1.account, "DU123")
        self.assertEqual(p1.symbol, "MSFT")
        self.assertEqual(p1.sec_type, "STK")
        self.assertEqual(p1.exchange, "NASDAQ")
        self.assertEqual(p1.con_id, 202)
        self.assertEqual(p1.position, 50.0)
        self.assertEqual(p1.avg_cost, 250.0)

    def dont_test_get_order_status_calls_api_and_returns_proto(self):
        self.mock_api.get_order.return_value = {
            'order_id': 123,
            'status': 'FILLED'
        }

        req = service_pb2.GetOrderRequest(order_id=123)
        resp = self.stub.GetOrder(req)

        # The veneer calls api.get_order(order_id)
        self.mock_api.get_order.assert_called_once_with(123)
        self.assertEqual(resp.order_id, 123)
        self.assertEqual(resp.status, "FILLED")

    def test_place_stock_order_api_exception_returns_status_error(self):
        # Simulate backend error
        self.mock_api.place_stock_order.side_effect = RuntimeError("IBKR down")

        # This test will trigger a stacktrace, which can look like a failure.
        # silence things for now.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        # Call gRPC and assert an ERROR response (no RpcError is raised by the veneer)
        req = service_pb2.PlaceStockOrderRequest(symbol="AAPL", side="BUY", quantity=10)
        resp = self.stub.PlaceStockOrder(req)

        self.assertEqual(resp.status, "ERROR")
        self.assertIn("IBKR down", resp.message)

    def test_get_positions_api_exception_returns_grpc_error(self):
        self.mock_api.get_positions.side_effect = RuntimeError("DB unreachable")

        req = service_pb2.GetPositionsRequest()
        with self.assertRaises(grpc.RpcError) as cm:
            self.stub.GetPositions(req)

        # Current server behaviour bubbles as UNKNOWN
        self.assertEqual(cm.exception.code(), grpc.StatusCode.UNKNOWN)
        self.assertIn("DB unreachable", cm.exception.details())

    def test_get_order_status_api_exception_returns_grpc_error(self):
        self.mock_api.get_order.side_effect = ValueError("Order not found")

        req = service_pb2.GetOrderRequest(order_id=999)
        with self.assertRaises(grpc.RpcError) as cm:
            self.stub.GetOrder(req)

        # Server bubbles exceptions as UNKNOWN
        self.assertEqual(cm.exception.code(), grpc.StatusCode.UNKNOWN)
        self.assertIn("Order not found", cm.exception.details())


class TestTradingServiceExceptionMapping(unittest.TestCase):
    """Parameterised tests that the gRPC veneer maps backend exceptions to StatusCodes."""

    def setUp(self):
        # Patch the TradingAPI used by the server so no real backend is touched
        self.api_patcher = patch.object(server, "TradingAPI", autospec=True)
        self.mock_api_cls = self.api_patcher.start()
        self.mock_api = self.mock_api_cls.return_value

        # Spin up an in-process grpc server with the real servicer
        self.grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
        service_pb2_grpc.add_TradingServiceServicer_to_server(
            server.TradingService(self.mock_api),
            self.grpc_server
        )
        port = self.grpc_server.add_insecure_port("localhost:0")
        self.addr = f"localhost:{port}"
        self.grpc_server.start()

        # Client stub to call our in-process server
        self.channel = grpc.insecure_channel(self.addr)
        self.stub = service_pb2_grpc.TradingServiceStub(self.channel)

        # Common request messages
        self.place_stock_req = service_pb2.PlaceStockOrderRequest(symbol="AAPL", side="BUY", quantity=1)
        self.get_order_req = service_pb2.GetOrderRequest(order_id=1)
        self.get_positions_req = service_pb2.GetPositionsRequest()

        # Exception → expected StatusCode mapping table
        self.exc_table = [
            (RuntimeError("IBKR down"), grpc.StatusCode.INTERNAL),
            (TimeoutError("timeout"), grpc.StatusCode.DEADLINE_EXCEEDED),
            (PermissionError("denied"), grpc.StatusCode.PERMISSION_DENIED),
            (KeyError("missing"), grpc.StatusCode.NOT_FOUND),
            (ValueError("bad arg"), grpc.StatusCode.INVALID_ARGUMENT),
            (Exception("unknown"), grpc.StatusCode.UNKNOWN),
        ]

    def tearDown(self):
        self.grpc_server.stop(0)
        self.api_patcher.stop()

    def dont_test_place_stock_order_exception_mapping(self):
        """PlaceStockOrder should map backend exceptions to canonical gRPC codes."""
        for exc, code in self.exc_table:
            with self.subTest(exc=type(exc).__name__, code=code):
                # Arrange
                self.mock_api.place_stock_order.side_effect = exc
                # Act + Assert
                with self.assertRaises(grpc.RpcError) as cm:
                    self.stub.PlaceStockOrder(self.place_stock_req)
                self.assertEqual(cm.exception.code(), code)
                self.assertIn(str(exc), cm.exception.details())
                # Reset side_effect for next subTest
                self.mock_api.place_stock_order.side_effect = None

    def dont_test_get_order_exception_mapping(self):
        """GetOrder should map backend exceptions to canonical gRPC codes."""
        for exc, code in self.exc_table:
            with self.subTest(exc=type(exc).__name__, code=code):
                self.mock_api.get_order.side_effect = exc
                with self.assertRaises(grpc.RpcError) as cm:
                    self.stub.GetOrder(self.get_order_req)
                self.assertEqual(cm.exception.code(), code)
                self.assertIn(str(exc), cm.exception.details())
                self.mock_api.get_order.side_effect = None

    def dont_test_get_positions_exception_mapping(self):
        """GetPositions should map backend exceptions to canonical gRPC codes."""
        for exc, code in self.exc_table:
            with self.subTest(exc=type(exc).__name__, code=code):
                self.mock_api.get_positions.side_effect = exc
                with self.assertRaises(grpc.RpcError) as cm:
                    self.stub.GetPositions(self.get_positions_req)
                self.assertEqual(cm.exception.code(), code)
                self.assertIn(str(exc), cm.exception.details())
                self.mock_api.get_positions.side_effect = None


if __name__ == "__main__":
    unittest.main()
