"""Tests focused on the gRPC server veneer (as underlying logic is already tested elsewhere)."""

from concurrent import futures
import unittest
from unittest.mock import patch

import grpc

import service_pb2
import service_pb2_grpc
import server


class TestTradingServiceGRPC(unittest.TestCase):
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
        self.mock_api.place_stock_order.return_value = {
            'order_id': 1,
            'broker_order_id': 42,
            'status': 'SUBMITTED',
            'message': 'ok'
        }

        req = service_pb2.PlaceStockOrderRequest(symbol="AAPL", side="BUY", quantity=10)
        resp = self.stub.PlaceStockOrder(req)

        self.mock_api.place_stock_order.assert_called_once_with("AAPL", "BUY", 10)

        self.assertEqual(resp.order_id, 1)
        self.assertEqual(resp.broker_order_id, 42)
        self.assertEqual(resp.status, "SUBMITTED")
        self.assertEqual(resp.message, "ok")

    def dont_test_get_positions_calls_api_and_returns_proto_list(self):
        self.mock_api.get_positions.return_value = [
            {"symbol": "AAPL", "qty": 100},
            {"symbol": "MSFT", "qty": 50}
        ]

        req = service_pb2.GetPositionsRequest()
        resp = self.stub.GetPositions(req)

        self.mock_api.get_positions.assert_called_once_with()
        self.assertEqual(len(resp.positions), 2)
        self.assertEqual(resp.positions[0].symbol, "AAPL")
        self.assertEqual(resp.positions[0].qty, 100)
        self.assertEqual(resp.positions[1].symbol, "MSFT")
        self.assertEqual(resp.positions[1].qty, 50)

    def dont_test_get_order_status_calls_api_and_returns_proto(self):
        self.mock_api.get_order_status.return_value = {
            'order_id': 123,
            'status': 'FILLED'
        }

        req = service_pb2.GetOrderRequest(order_id=123)
        resp = self.stub.GetOrder(req)

        self.mock_api.get_order_status.assert_called_once_with(123)
        self.assertEqual(resp.order_id, 123)
        self.assertEqual(resp.status, "FILLED")

    def dont_test_place_stock_order_api_exception_returns_grpc_error(self):
        # imulate backend error
        self.mock_api.place_stock_order.side_effect = RuntimeError("IBKR down")

        # gRPC should wrap as RpcError
        req = service_pb2.PlaceStockOrderRequest(symbol="AAPL", side="BUY", quantity=10)
        with self.assertRaises(grpc.RpcError) as cm:
            self.stub.PlaceStockOrder(req)
        self.assertEqual(cm.exception.code(), grpc.StatusCode.INTERNAL)
        # self.assertIn("IBKR down", cm.exception.details())

    def dont_test_get_positions_api_exception_returns_grpc_error(self):
        self.mock_api.get_positions.side_effect = RuntimeError("DB unreachable")

        req = service_pb2.GetPositionsRequest()
        with self.assertRaises(grpc.RpcError) as cm:
            self.stub.GetPositions(req)
        self.assertEqual(cm.exception.code(), grpc.StatusCode.INTERNAL)
        # self.assertIn("DB unreachable", cm.exception.details())

    def dont_test_get_order_status_api_exception_returns_grpc_error(self):
        self.mock_api.get_order_status.side_effect = ValueError("Order not found")

        req = service_pb2.GetOrderRequest(order_id=999)
        with self.assertRaises(grpc.RpcError) as cm:
            self.stub.GetOrder(req)
        self.assertEqual(cm.exception.code(), grpc.StatusCode.NOT_FOUND)
        # self.assertIn("Order not found", cm.exception.details())


if __name__ == "__main__":
    unittest.main()
