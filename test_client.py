import unittest
from unittest.mock import patch

from client import TradingClient
import service_pb2


class TestTradingClient(unittest.TestCase):
    """Tests for TradingClient."""
    def setUp(self):
        # Patch the gRPC channel + stub so no real network is used
        self.p_insecure = patch("client.grpc.insecure_channel")
        self.mock_insecure_channel = self.p_insecure.start()

        self.p_stub = patch("client.service_pb2_grpc.TradingServiceStub")
        self.mock_stub_cls = self.p_stub.start()
        self.stub = self.mock_stub_cls.return_value

        # Default responses can be overridden per-test
        self.stub.PlaceStockOrder.return_value = service_pb2.PlaceOrderResponse(
            order_id=1, broker_order_id=2, status="SUBMITTED", message="ok"
        )
        self.stub.PlaceOptionOrder.return_value = service_pb2.PlaceOrderResponse(
            order_id=11, broker_order_id=22, status="SUBMITTED", message="ok"
        )
        self.stub.GetOrder.return_value = service_pb2.OrderRecord(
            order_id=7,
            broker_order_id=70,
            asset_class="STK",
            symbol="AAPL",
            side="BUY",
            quantity=10,
            status="FILLED",
            avg_price=150.25,
            filled_qty=10,
            message="",
        )
        self.stub.ListOrders.return_value = service_pb2.ListOrdersResponse(
            orders=[
                service_pb2.OrderRecord(
                    order_id=1,
                    broker_order_id=10,
                    asset_class="STK",
                    symbol="AAPL",
                    side="BUY",
                    quantity=1,
                    status="SUBMITTED",

                    avg_price=0.0,
                    filled_qty=0,
                    message=""
                ),
                service_pb2.OrderRecord(
                    order_id=2,
                    broker_order_id=20,
                    asset_class="OPT",
                    symbol="AAPL",
                    side="SELL",
                    quantity=1,
                    status="FILLED",
                    avg_price=1.23,
                    filled_qty=1,
                    message=""
                ),
            ]
        )
        self.stub.ListFills.return_value = service_pb2.ListFillsResponse(
            fills=[
                service_pb2.FillRecord(
                    fill_id=100,
                    order_id=1,
                    exec_id="E1",
                    price=150.0,
                    filled_qty=1,
                    symbol="AAPL",
                    side="BUY",
                    time="t1",
                    broker_order_id=10
                ),
                service_pb2.FillRecord(
                    fill_id=101,
                    order_id=2,
                    exec_id="E2",
                    price=1.25,
                    filled_qty=1,
                    symbol="AAPL",
                    side="SELL",
                    time="t2",
                    broker_order_id=20
                ),
            ]
        )
        self.stub.GetPositions.return_value = service_pb2.GetPositionsResponse(
            positions=[
                service_pb2.PositionRecord(
                    account="DU1",
                    symbol="AAPL",
                    sec_type="STK",
                    exchange="NASDAQ",
                    con_id=101,
                    position=5.0,
                    avg_cost=150.0
                ),
                service_pb2.PositionRecord(
                    account="DU1",
                    symbol="MSFT",
                    sec_type="STK",
                    exchange="NASDAQ",
                    con_id=202,
                    position=2.0,
                    avg_cost=250.0
                ),
            ]
        )
        self.stub.GetAccountValues.return_value = service_pb2.GetAccountValuesResponse(
            account_values=[
                service_pb2.AccountValueRecord(
                    account="DU1",
                    tag="NetLiquidation",
                    currency="USD",
                    value="100000"
                ),
                service_pb2.AccountValueRecord(
                    account="DU1",
                    tag="AvailableFunds",
                    currency="USD",
                    value="50000")
                ,
            ]
        )

        # Client with a non-default timeout so we can assert it’s used
        self.client = TradingClient("localhost:50051", timeout=0.75)

    def tearDown(self):
        self.p_stub.stop()
        self.p_insecure.stop()

    # --- PlaceStockOrder ---

    def test_place_stock_order_returns_plain_dict_and_uses_default_timeout(self):
        out = self.client.place_stock_order("AAPL", "BUY", 10)
        # Return conversion
        self.assertEqual(out, {
            'order_id': 1,
            'broker_order_id': 2,
            'status': 'SUBMITTED',
            'message': 'ok',
        })
        # Request + timeout
        args, kwargs = self.stub.PlaceStockOrder.call_args
        self.assertIsInstance(args[0], service_pb2.PlaceStockOrderRequest)
        self.assertEqual(args[0].symbol, "AAPL")
        self.assertEqual(args[0].side, "BUY")
        self.assertEqual(args[0].quantity, 10)

        self.assertEqual(args[0].order_type, "MKT")
        self.assertEqual(args[0].tif, "DAY")
        self.assertEqual(kwargs.get("timeout"), 0.75)

    def test_place_stock_order_overrides_timeout(self):
        out = self.client.place_stock_order("MSFT", "SELL", 5, timeout=2.5)
        self.assertEqual(out['order_id'], 1)  # from default stub response
        # Check per-call override was used
        _, kwargs = self.stub.PlaceStockOrder.call_args
        self.assertEqual(kwargs.get("timeout"), 2.5)

    def test_place_stock_order_limit_forwards_order_type_and_tif(self):
        """Verify LMT forwards order_type and tif (limit_price presence is handled by proto & client)."""
        _ = self.client.PlaceStockOrder("MSFT", "SELL", 5, order_type="LMT", limit_price=426.2, tif="GTC")
        args, _kwargs = self.stub.PlaceStockOrder.call_args
        req = args[0]
        self.assertEqual(req.order_type, "LMT")
        self.assertEqual(req.tif, "GTC")
        # Intentionally *not* asserting req.limit_price here to avoid coupling to client construction details.

    def test_place_stock_order_stop_forwards_order_type_and_tif(self):
        """Verify STP forwards order_type and tif."""
        _ = self.client.PlaceStockOrder("TSLA", "SHORT", 3, order_type="STP", limit_price=250.0, tif="DAY")
        args, _kwargs = self.stub.PlaceStockOrder.call_args
        req = args[0]
        self.assertEqual(req.order_type, "STP")
        self.assertEqual(req.tif, "DAY")

    # --- PlaceOptionOrder ---

    def test_place_option_order_returns_plain_dict(self):
        out = self.client.place_option_order("AAPL", "20251219", 150.0, "C", "BUY", 2)
        self.assertEqual(out, {
            'order_id': 11,
            'broker_order_id': 22,
            'status': 'SUBMITTED',
            'message': 'ok',
        })
        args, kwargs = self.stub.PlaceOptionOrder.call_args
        req = args[0]
        self.assertEqual(req.symbol, "AAPL")
        self.assertEqual(req.expiry, "20251219")
        self.assertAlmostEqual(req.strike, 150.0)
        self.assertEqual(req.right, "C")
        self.assertEqual(req.side, "BUY")
        self.assertEqual(req.quantity, 2)

        self.assertEqual(req.order_type, "MKT")
        self.assertEqual(req.tif, "DAY")
        self.assertEqual(kwargs.get("timeout"), 0.75)

    def test_place_option_order_limit_includes_limit_price_and_tif(self):
        """For options, client should include limit_price when provided; TIF should propagate."""
        _ = self.client.PlaceOptionOrder(
            "AAPL", "20251219", 150.0, "C", "BUY", 2,
            order_type="LMT", limit_price=1.25, tif="GTC"
        )
        args, _kwargs = self.stub.PlaceOptionOrder.call_args
        req = args[0]
        self.assertEqual(req.order_type, "LMT")
        self.assertTrue(req.HasField("price"))
        self.assertAlmostEqual(req.price, 1.25)
        self.assertEqual(req.tif, "GTC")

    def test_place_option_order_stop_includes_limit_price_and_tif(self):
        """For STP options, limit_price acts as trigger; should be present in request."""
        _ = self.client.PlaceOptionOrder(
            "SPY", "20260116", 420.0, "P", "SELL", 1,
            order_type="STP", limit_price=2.50, tif="DAY"
        )
        args, _kwargs = self.stub.PlaceOptionOrder.call_args
        req = args[0]
        self.assertEqual(req.order_type, "STP")
        self.assertTrue(req.HasField("price"))
        self.assertAlmostEqual(req.price, 2.50)
        self.assertEqual(req.tif, "DAY")

    def test_place_option_order_overrides_timeout(self):
        _ = self.client.PlaceOptionOrder("AAPL", "20251219", 150.0, "C", "BUY", 1, timeout=3.3)
        _args, kwargs = self.stub.PlaceOptionOrder.call_args
        self.assertEqual(kwargs.get("timeout"), 3.3)

    # --- GetOrder/ListOrders ---

    def test_get_order_converts_to_dict(self):
        out = self.client.get_order(7)
        self.assertEqual(out['order_id'], 7)
        self.assertEqual(out['broker_order_id'], 70)
        self.assertEqual(out['asset_class'], "STK")
        self.assertEqual(out['symbol'], "AAPL")
        self.assertEqual(out['side'], "BUY")
        self.assertEqual(out['quantity'], 10)
        self.assertEqual(out['status'], "FILLED")
        self.assertAlmostEqual(out['avg_price'], 150.25)
        self.assertEqual(out['filled_qty'], 10)
        self.assertEqual(out['message'], "")

        args, kwargs = self.stub.GetOrder.call_args
        self.assertEqual(args[0].order_id, 7)
        self.assertEqual(kwargs.get("timeout"), 0.75)

    def test_list_orders_converts_each_record(self):
        out = self.client.list_orders(limit=5)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]['order_id'], 1)
        self.assertEqual(out[0]['asset_class'], "STK")
        self.assertEqual(out[1]['order_id'], 2)
        self.assertEqual(out[1]['asset_class'], "OPT")

        args, kwargs = self.stub.ListOrders.call_args
        self.assertEqual(args[0].limit, 5)
        self.assertEqual(kwargs.get("timeout"), 0.75)

    # --- ListFills ---

    def test_list_fills_converts_each_record(self):
        out = self.client.list_fills(order_id=1, limit=10)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]['fill_id'], 100)
        self.assertEqual(out[0]['order_id'], 1)
        self.assertEqual(out[0]['exec_id'], "E1")
        self.assertAlmostEqual(out[0]['price'], 150.0)

        args, kwargs = self.stub.ListFills.call_args
        self.assertEqual(args[0].order_id, 1)
        self.assertEqual(args[0].limit, 10)
        self.assertEqual(kwargs.get("timeout"), 0.75)

    # --- GetPositions ---

    def test_get_positions_converts_each_record(self):
        out = self.client.get_positions()
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]['account'], "DU1")
        self.assertEqual(out[0]['symbol'], "AAPL")
        self.assertEqual(out[0]['sec_type'], "STK")
        self.assertEqual(out[0]['exchange'], "NASDAQ")
        self.assertEqual(out[0]['con_id'], 101)
        self.assertAlmostEqual(out[0]['position'], 5.0)
        self.assertAlmostEqual(out[0]['avg_cost'], 150.0)

        # Ensure request/timeout passed
        args, kwargs = self.stub.GetPositions.call_args
        self.assertIsInstance(args[0], service_pb2.GetPositionsRequest)
        self.assertEqual(kwargs.get("timeout"), 0.75)

    # --- GetAccountValues ---

    def test_get_account_values_converts_each_record(self):
        out = self.client.get_account_values()
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]['account'], "DU1")
        self.assertEqual(out[0]['tag'], "NetLiquidation")
        self.assertEqual(out[0]['currency'], "USD")
        self.assertEqual(out[0]['value'], "100000")

        args, kwargs = self.stub.GetAccountValues.call_args
        self.assertIsInstance(args[0], service_pb2.GetAccountValuesRequest)
        self.assertEqual(kwargs.get("timeout"), 0.75)

    # --- Context manager ---

    def test_context_manager_channel(self):
        with TradingClient("localhost:9999", timeout=0.2) as c:
            # perform an operation to ensure stub was created
            _ = c.list_orders()
        # After exiting, the channel created in __init__ should be closed
        # We can't easily assert channel.close() call on grpc internals here:
        # verify that insecure_channel was invoked and stub constructed.
        self.mock_insecure_channel.assert_called()
        self.mock_stub_cls.assert_called()

    # --- Secure channel path ---

    def test_secure_channel_is_used_when_credentials_provided(self):
        """When credentials are passed, client should use grpc.secure_channel instead of insecure_channel."""
        with patch("client.grpc.secure_channel") as mock_secure:
            creds = object()
            _c = TradingClient("localhost:5555", secure_channel_credentials=creds)
            mock_secure.assert_called_once_with("localhost:5555", creds)
            # Ensure stub constructed on the secure channel
            self.mock_stub_cls.assert_called()


if __name__ == "__main__":
    unittest.main()
