"""IB Session Manager.

TODO:
- Enforce singleton IBSession per client to ensure system-wide consistency with
  the IB (asyncio) loop.
  See connect().
"""

import logging
import os

from ib_insync import IB, util


logger = logging.getLogger(__name__)

DEFAULT_IB_HOST = os.environ.get("IB_HOST") or "127.0.0.1"  # 'or' to catch "" and None
try:
    DEFAULT_IB_PORT = int(os.environ.get("IB_PORT"))
except (TypeError, ValueError):
    DEFAULT_IB_PORT = 7497

try:
    DEFAULT_IB_CLIENT_ID = int(os.environ.get("IB_CLIENT_ID"))
except (TypeError, ValueError):
    DEFAULT_IB_CLIENT_ID = 1


class IBSession:
    """Interactive Brokers session manager using ib_insync."""

    def __init__(self, host=DEFAULT_IB_HOST, port=DEFAULT_IB_PORT, client_id=DEFAULT_IB_CLIENT_ID):
        """Initialize an IBSession instance.

        Args:
            host: str - Hostname or IP address of IB Gateway/TWS.
            port: int - Port number for IB Gateway/TWS connection.
            client_id: int - Unique client ID for this session.
        """
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = IB()
        self.loop = None  # will be set on connect

    def connect(self):
        """Connect to IB Gateway/TWS and pin the IB asyncio loop."""
        logger.info(f"Connecting to IB at {self.host}:{self.port} (clientId={self.client_id})...")

        # Ensure a running asyncio loop for ib_insync (idempotent)
        util.startLoop()

        # Connect (sync path is fine here)
        self.ib.connect(self.host, self.port, clientId=self.client_id)

        if not self.ib.isConnected():
            raise RuntimeError("Failed to connect to IB Gateway/TWS")

        # Pin the loop used by ib_insync so other threads can target it
        self.loop = util.getLoop()
        setattr(self.ib, 'loop', self.loop)  # OrderManager will read this

        logger.info("Connected to IB Gateway/TWS")
        return True

    def disconnect(self):
        """Disconnect from IB Gateway/TWS.

        Returns:
            bool - True if disconnected successfully, False otherwise.
        """
        if self.ib.isConnected():
            self.ib.disconnect()
            logger.info("Disconnected from IB Gateway/TWS")

            return True

        logger.warning("Already disconnected from IB Gateway/TWS")
        return False

    def is_connected(self):
        """Check if the session is connected.

        Returns:
            bool - True if connected, False otherwise.
        """
        return self.ib.isConnected()
