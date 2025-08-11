import unittest
from unittest.mock import patch
from contracts import (
    create_stock_contract,
    create_option_contract,
    Currency,
    Exchange,
    OptionType,
)


class TestContracts(unittest.TestCase):
    """Unit tests for contracts.py."""

    def test_create_stock_contract_defaults(self):
        """create_stock_contract uses SMART/USD by default and calls Stock with correct args."""
        with patch("contracts.Stock") as MockStock:
            _ = create_stock_contract("AAPL")
            MockStock.assert_called_once_with("AAPL", Exchange.SMART.value, Currency.USD.value)

    def test_create_stock_contract_with_enums(self):
        """create_stock_contract accepts Currency/Exchange enums and forwards their values."""
        with patch("contracts.Stock") as MockStock:
            _ = create_stock_contract("MSFT", currency=Currency.EUR, exchange=Exchange.NASDAQ)
            MockStock.assert_called_once_with("MSFT", Exchange.NASDAQ.value, Currency.EUR.value)

    def test_create_stock_contract_invalid_currency_raises(self):
        """create_stock_contract rejects non-enum currency values."""
        with self.assertRaises(ValueError):
            create_stock_contract("AAPL", currency="USD")  # not a Currency enum

    def test_create_stock_contract_invalid_exchange_raises(self):
        """create_stock_contract rejects non-enum exchange values."""
        with self.assertRaises(ValueError):
            create_stock_contract("AAPL", exchange="SMART")  # not an Exchange enum

    def test_create_option_contract_with_enums(self):
        """create_option_contract accepts enums and calls Option with string right/exchange/currency."""
        with patch("contracts.Option") as MockOption:
            _ = create_option_contract(
                "AAPL",
                "20251219",
                150.0,
                OptionType.CALL,
                currency=Currency.USD,
                exchange=Exchange.SMART,
            )
            MockOption.assert_called_once_with(
                "AAPL", "20251219", 150.0, "C", Exchange.SMART.value, Currency.USD.value
            )

    def test_create_option_contract_invalid_right_type_raises(self):
        """create_option_contract rejects non-enum right values."""
        with self.assertRaises(ValueError):
            create_option_contract("AAPL", "20251219", 150.0, "C")  # not an OptionType enum

    def test_create_option_contract_invalid_currency_raises(self):
        """create_option_contract rejects non-enum currency."""
        with self.assertRaises(ValueError):
            create_option_contract("AAPL", "20251219", 150.0, OptionType.CALL, currency="USD")

    def test_create_option_contract_invalid_exchange_raises(self):
        """create_option_contract rejects non-enum exchange."""
        with self.assertRaises(ValueError):
            create_option_contract("AAPL", "20251219", 150.0, OptionType.CALL, exchange="SMART")


if __name__ == "__main__":
    unittest.main()
