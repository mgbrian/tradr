import unittest
from unittest.mock import MagicMock, patch
from session import IBSession


class TestIBSession(unittest.TestCase):
    """Unit tests for SessionManager using a mocked IB instance."""

    def setUp(self):
        """Patch the IB class in session.py before each test."""
        patcher = patch("session.IB")
        self.addCleanup(patcher.stop)
        self.mock_ib_class = patcher.start()
        self.mock_ib_instance = MagicMock()
        self.mock_ib_class.return_value = self.mock_ib_instance

    def test_connect_calls_ib_connect(self):
        """connect() should call IB.connect with the correct arguments."""
        session = IBSession(host="127.0.0.1", port=4002, client_id=123)
        session.connect()
        self.mock_ib_instance.connect.assert_called_once_with("127.0.0.1", 4002, clientId=123)

    def test_disconnect_calls_ib_disconnect(self):
        """disconnect() should call IB.disconnect."""
        session = IBSession()
        session.disconnect()
        self.mock_ib_instance.disconnect.assert_called_once()

    def test_is_connected_returns_true(self):
        """is_connected() should return True when IB.isConnected is True."""
        self.mock_ib_instance.isConnected.return_value = True
        session = IBSession()
        self.assertTrue(session.is_connected())

    def test_is_connected_returns_false(self):
        """is_connected() should return False when IB.isConnected is False."""
        self.mock_ib_instance.isConnected.return_value = False
        session = IBSession()
        self.assertFalse(session.is_connected())


if __name__ == "__main__":
    unittest.main()
