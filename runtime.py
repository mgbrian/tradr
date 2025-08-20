"""Runtime singletons and wiring for IB, DB, trackers, API, and gRPC.

This module is the single source of truth for process-wide shared instances.
It exposes:
  - App: the container that owns shared state and lifecycle
  - APP: the global handle once started
  - start_app()/get_app()/close_app(): convenience helpers

Typical usage (in-process, bypassing CLI [main.py]):
    from runtime import get_app
    app = get_app()          # starts once (idempotent)
    api = app.api            # same shared API used by gRPC
    ib  = app.ib_session.ib  # same IB connection
    db  = app.db

Notes:
- The gRPC server is started by App.start() (non-blocking). For the CLI, main.py
  simply parses args and calls start_app(...), then waits for signals.
"""
import logging
import os
import signal
import time
import threading

from api import TradingAPI
from db.inmemorydb import InMemoryDB
from execution_tracker import ExecutionTracker
from order_tracker import OrderTracker
from position_tracker import PositionTracker
import server as grpc_server_module
from session import IBSession, DEFAULT_IB_CLIENT_ID, DEFAULT_IB_HOST, DEFAULT_IB_PORT


# Process-global application handle
APP = None
_APP_LOCK = threading.Lock()


class App:
    """Owns shared state and the gRPC server lifecycle."""

    def __init__(self,
                 grpc_addr=grpc_server_module.DEFAULT_SERVER_ADDRESS,
                 ib_host=DEFAULT_IB_HOST,
                 ib_port=DEFAULT_IB_PORT,
                 ib_client_id=DEFAULT_IB_CLIENT_ID,
                 enable_drainer=False,
                 drainer_worker_id="core-drainer"):
        """Initialize the App container.

        Args:
            grpc_addr: str (Optional) - Full address for the gRPC server,
                e.g. "<host>:<port>" or a unix socket. Default = server.DEFAULT_SERVER_ADDRESS.
            ib_host: str (Optional) - IB Gateway/TWS host. Default = session.DEFAULT_IB_HOST.
            ib_port: int (Optional) - IB Gateway/TWS port. Default = session.DEFAULT_IB_PORT.
            ib_client_id: int (Optional) - IB API client id. Default = session.DEFAULT_IB_CLIENT_ID.
            enable_drainer: bool (Optional) - Start Django outbox drainer if available. Default = False
            drainer_worker_id: str (Optional) - Worker id tag for checkpointing. Default = "core-drainer"
        """
        self.grpc_addr = grpc_addr
        self.ib_host = ib_host
        self.ib_port = int(ib_port)
        self.ib_client_id = int(ib_client_id)
        self.enable_drainer = bool(enable_drainer)
        self.drainer_worker_id = drainer_worker_id

        # Shared singletons
        self.db = InMemoryDB()
        self.ib_session = IBSession(host=self.ib_host, port=self.ib_port, client_id=self.ib_client_id)
        self.position_tracker = None
        self.execution_tracker = None
        self.order_tracker = None
        self.api = None

        # gRPC server handle
        self.server = None

        # Optional drainer
        self._drainer = None

        self._stopping = False

    def start(self):
        """Connect to IB, start trackers, create API, and start gRPC server.

        Returns:
            App - Self, for chaining.
        """
        # Connect once
        self.ib_session.connect()
        # Sanity check: IB loop pinned
        assert self.ib_session.ib.isConnected()
        assert getattr(self.ib_session.ib, 'loop', None) is not None

        # Trackers share the same IB + DB
        self.position_tracker = PositionTracker(self.ib_session.ib, db=self.db)
        self.position_tracker.start()

        self.execution_tracker = ExecutionTracker(self.ib_session.ib, db=self.db)
        self.execution_tracker.start()

        self.order_tracker = OrderTracker(self.ib_session.ib, db=self.db)
        self.order_tracker.start()
        # Take an initial order snapshot
        try:
            self.order_tracker.refresh_now()
        except Exception:
            logging.exception("Initial order snapshot failed. Will rely on subsequent events.")

        # Shared API instance
        self.api = TradingAPI(self.ib_session.ib, self.db, position_tracker=self.position_tracker)

        # Start gRPC using the shared instances (do not block)
        self.server, _handles = grpc_server_module.serve(
            address=self.grpc_addr,
            db=self.db,
            ib=self.ib_session,  # OK to pass the session; serve() normalizes to ib
            api=self.api,
            position_tracker=self.position_tracker,
            execution_tracker=self.execution_tracker,
            auto_connect=False,     # Already connected
            start_trackers=False,   # Trackers already started here
            wait=False
        )

        # Start drainer if enabled and importable
        if self.enable_drainer:
            try:
                from db.drainer import OutboxDrainer
                self._drainer = OutboxDrainer(self.db, worker_id=self.drainer_worker_id)
                self._drainer.start()
                logging.info("Outbox drainer started (worker_id=%s).", self.drainer_worker_id)

            except Exception:
                logging.exception("Failed to start drainer; continuing without it.")

        global APP
        APP = self
        return self

    def wait_forever(self):
        """Run until interrupted by SIGINT/SIGTERM, then shutdown."""
        self._install_signal_handlers()
        try:
            while not self._stopping:
                time.sleep(0.5)
        finally:
            self.shutdown()

    def _install_signal_handlers(self):
        """Install signal handlers for graceful shutdown."""
        def _handler(signum, frame):
            logging.info("Signal %s received; shutting down...", signum)
            self._stopping = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except Exception:
                pass

    def shutdown(self):
        """Stop gRPC, trackers, and IB session (reverse order)."""
        logging.info("Shutting down...")

        if self._drainer:
            try:
                self._drainer.stop()
            except Exception:
                logging.exception("Error stopping drainer.")
            self._drainer = None

        if self.server:
            try:
                self.server.stop(grace=None)
            except Exception:
                logging.exception("Error stopping gRPC server.")
            self.server = None

        if self.execution_tracker:
            try:
                self.execution_tracker.stop()
            except Exception:
                logging.exception("Error stopping ExecutionTracker.")

        if self.position_tracker:
            try:
                self.position_tracker.stop()
            except Exception:
                logging.exception("Error stopping PositionTracker.")

        if self.order_tracker:
            try:
                self.order_tracker.stop()
            except Exception:
                logging.exception("Error stopping OrderTracker.")

        try:
            self.ib_session.disconnect()
        except Exception:
            logging.exception("Error disconnecting IB session.")

        logging.info("Shutdown complete.")


def start_app(**kwargs):
    """Create and start the global APP once; return it.

    Args:
        **kwargs: dict - Passed to App(...), e.g. grpc_addr, ib_host/port/client_id,
            enable_drainer, drainer_worker_id.

    Returns:
        App - The started global application container.
    """
    global APP
    if APP is not None:
        return APP

    with _APP_LOCK:
        if APP is not None:
            return APP
        APP = App(**kwargs).start()
        return APP


def get_app():
    """Return the global APP, starting it with defaults if not present.

    Returns:
        App - The application container.
    """
    if APP is not None:
        return APP

    # Defaults come from session/server modules; drainer off by default
    return start_app(
        grpc_addr=grpc_server_module.DEFAULT_SERVER_ADDRESS,
        ib_host=DEFAULT_IB_HOST,
        ib_port=DEFAULT_IB_PORT,
        ib_client_id=DEFAULT_IB_CLIENT_ID,
        enable_drainer=(os.getenv("ENABLE_DRAINER", "0") == "1"),
        drainer_worker_id="core-drainer",
    )


def close_app():
    """Shutdown and clear the global APP.

    Returns:
        bool - True if an app was closed, False if nothing to do.
    """
    global APP
    if APP is None:
        return False

    with _APP_LOCK:
        try:
            APP.shutdown()
        finally:
            APP = None
        return True
