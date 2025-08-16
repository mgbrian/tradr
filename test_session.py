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

    # --- Basic behaviour

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

    # --- Env/defaults behaviour (host, port, client_id)

    def test_fallback_values_if_env_vals_invalid(self):
        """Test that connection defaults fall back when env vars are absent/invalid/empty.
           Empty strings should be treated as None.
        """
        # Case 1: all absent -> host "127.0.0.1", port 7497, client_id 1
        with patch.dict(os.environ, {}, clear=True):
            mod = importlib.reload(session_module)
            self.assertEqual(mod.DEFAULT_IB_HOST, "127.0.0.1")
            self.assertEqual(mod.DEFAULT_IB_PORT, 7497)
            self.assertEqual(mod.DEFAULT_IB_CLIENT_ID, 1)

        # Case 2: PORT and CLIENT_ID not numbers -> fall back to 7497 and 1; host passes through
        with patch.dict(
            os.environ, {"IB_HOST": "10.0.0.5", "IB_PORT": "not-a-number", "IB_CLIENT_ID": "nope"}, clear=True
        ):
            mod = importlib.reload(session_module)
            self.assertEqual(mod.DEFAULT_IB_HOST, "10.0.0.5")
            self.assertEqual(mod.DEFAULT_IB_PORT, 7497)
            self.assertEqual(mod.DEFAULT_IB_CLIENT_ID, 1)

        # Case 3: empty strings -> host falls back to "127.0.0.1"; port/client fall back
        with patch.dict(os.environ, {"IB_HOST": "", "IB_PORT": "", "IB_CLIENT_ID": ""}, clear=True):
            mod = importlib.reload(session_module)
            self.assertEqual(mod.DEFAULT_IB_HOST, "127.0.0.1")
            self.assertEqual(mod.DEFAULT_IB_PORT, 7497)
            self.assertEqual(mod.DEFAULT_IB_CLIENT_ID, 1)

        # Case 4: valid numeric port & client id, custom host
        with patch.dict(os.environ, {"IB_HOST": "2.2.2.2", "IB_PORT": "4002", "IB_CLIENT_ID": "777"}, clear=True):
            mod = importlib.reload(session_module)
            self.assertEqual(mod.DEFAULT_IB_HOST, "2.2.2.2")
            self.assertEqual(mod.DEFAULT_IB_PORT, 4002)
            self.assertEqual(mod.DEFAULT_IB_CLIENT_ID, 777)

    def test_constructor_uses_passed_values_without_touching_env(self):
        """IBSession should use provided args verbatim."""
        s = IBSession(host="1.2.3.4", port=4001, client_id=77)
        self.assertEqual(s.host, "1.2.3.4")
        self.assertEqual(s.port, 4001)
        self.assertEqual(s.client_id, 77)

    def test_constructor_uses_module_defaults_from_env_when_args_omitted(self):
        """IBSession() with no args should pick up the module defaults derived from environment.
           Use a fresh reload and patch that module's IB to avoid importing the real ib_async.IB.
        """
        with patch.dict(os.environ, {"IB_HOST": "9.9.9.9", "IB_PORT": "4001", "IB_CLIENT_ID": "42"}, clear=True):
            mod = importlib.reload(session_module)
            with patch.object(mod, "IB") as mod_ib:
                mod_ib.return_value = MagicMock()
                s = mod.IBSession()
                self.assertEqual(s.host, "9.9.9.9")
                self.assertEqual(s.port, 4001)
                self.assertEqual(s.client_id, 42)

        # Empty strings should behave like None -> fall back to defaults
        with patch.dict(os.environ, {"IB_HOST": "", "IB_PORT": "", "IB_CLIENT_ID": ""}, clear=True):
            mod = importlib.reload(session_module)
            with patch.object(mod, "IB") as mod_ib:
                mod_ib.return_value = MagicMock()
                s = mod.IBSession()
                self.assertEqual(s.host, "127.0.0.1")
                self.assertEqual(s.port, 7497)
                self.assertEqual(s.client_id, 1)

    def test_constructor_does_not_eagerly_connect(self):
        """IBSession constructor should not call IB.connect."""
        _ = IBSession()
        self.mock_ib_instance.connect.assert_not_called()

    # --- connect() uses provided client_id correctly

    def test_connect_uses_client_id_from_env_default_when_not_overridden(self):
        """
        Ensure connect() uses the client_id derived from env defaults when none is passed.
        """
        with patch.dict(os.environ, {"IB_CLIENT_ID": "55"}, clear=True):
            mod = importlib.reload(session_module)
            with patch.object(mod, "IB") as mod_ib:
                mock_ib_instance = MagicMock()
                mod_ib.return_value = mock_ib_instance
                sess = mod.IBSession(host="127.0.0.1", port=4002)  # no explicit client_id
                mock_ib_instance.isConnected.return_value = True
                sess.connect()
                mock_ib_instance.connect.assert_called_once_with("127.0.0.1", 4002, clientId=55)


if __name__ == "__main__":
    unittest.main()
