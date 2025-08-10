import logging

from ib_insync import IB


logger = logging.getLogger(__name__)


class IBSession:
    """Interactive Brokers session manager using ib_insync."""

    def __init__(self, host='127.0.0.1', port=7497, client_id=1):
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

    def connect(self):
        """Connect to IB Gateway/TWS.

        Returns:
            bool - True if connected, False otherwise.

        Raises:
            RuntimeError - If the connection fails.
        """
        logger.info(f"Connecting to IB at {self.host}:{self.port} (clientId={self.client_id})...")
        self.ib.connect(self.host, self.port, self.client_id)

        if not self.ib.isConnected():
            raise RuntimeError("Failed to connect to IB Gateway/TWS")

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
