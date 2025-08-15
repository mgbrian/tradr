import os
import unittest
import importlib
from unittest.mock import MagicMock, patch

import session as session_module
from session import IBSession


class TestIBSession(unittest.TestCase):
    """Unit tests for IBSession using a mocked IB instance."""

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
        self.mock_ib_instance.connect.assert_called_once_with(
            "127.0.0.1", 4002, clientId=123
        )

    def test_connect_raises_runtime_error_when_not_connected(self):
        """connect() should raise if IB.isConnected() is False after connect."""
        self.mock_ib_instance.isConnected.return_value = False
        session = IBSession(host="127.0.0.1", port=4002, client_id=1)
        with self.assertRaises(RuntimeError):
            session.connect()
        # still should have attempted a connect call
        self.mock_ib_instance.connect.assert_called_once()

    def test_disconnect_calls_ib_disconnect(self):
        """disconnect() should call IB.disconnect when currently connected."""
        self.mock_ib_instance.isConnected.return_value = True
        session = IBSession()
        result = session.disconnect()
        self.assertTrue(result)
        self.mock_ib_instance.disconnect.assert_called_once()

    def test_disconnect_noop_when_already_disconnected(self):
        """disconnect() should return False and not call disconnect() if already disconnected."""
        self.mock_ib_instance.isConnected.return_value = False
        session = IBSession()
        result = session.disconnect()
        self.assertFalse(result)
        self.mock_ib_instance.disconnect.assert_not_called()

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

    def test_fallback_values_if_env_vals_invalid(self):
        """Test that the connection settings fallback to defaults if env vars are absent or invalid."""
        # Case 1: both absent -> host "127.0.0.1", port 7497
        with patch.dict(os.environ, {}, clear=True):
            mod = importlib.reload(session_module)
            self.assertEqual(mod.DEFAULT_IB_HOST, "127.0.0.1")
            self.assertEqual(mod.DEFAULT_IB_PORT, 7497)

        # Case 2: PORT not a number -> fallback to 7497
        with patch.dict(os.environ, {"IB_HOST": "10.0.0.5", "IB_PORT": "not-a-number"}, clear=True):
            mod = importlib.reload(session_module)
            # host should come through unchanged
            self.assertEqual(mod.DEFAULT_IB_HOST, "10.0.0.5")
            # invalid port string -> fallback
            self.assertEqual(mod.DEFAULT_IB_PORT, 7497)

        # Case 3: empty strings -> host falls back to "127.0.0.1", port falls back to 7497
        with patch.dict(os.environ, {"IB_HOST": "", "IB_PORT": ""}, clear=True):
            mod = importlib.reload(session_module)
            self.assertEqual(mod.DEFAULT_IB_HOST, "127.0.0.1")
            self.assertEqual(mod.DEFAULT_IB_PORT, 7497)

    def test_constructor_uses_passed_values_without_touching_env(self):
        """IBSession should use provided args verbatim."""
        s = IBSession(host="1.2.3.4", port=4001, client_id=77)
        self.assertEqual(s.host, "1.2.3.4")
        self.assertEqual(s.port, 4001)
        self.assertEqual(s.client_id, 77)

    def test_constructor_does_not_eagerly_connect(self):
        """IBSession constructor should not call IB.connect."""
        _ = IBSession()
        self.mock_ib_instance.connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
