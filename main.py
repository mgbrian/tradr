"""Application entrypoint starting up all the components and exposing API.

Creates shared singletons:
   - IB session,
   - InMemoryDB,
   - Position and Execiton trackers
   - API
and starts the gRPC server.

Keeps one source of truth for objects that must not be duplicated
(IB connection, in-memory DB), so both the gRPC interface and any in-process
callers use the exact same instances.

Raw API usage:
--------------
If you want to use the API directly from within the same process (bypassing gRPC),
import main.APP after startup and call methods on APP.api.

For example:

    from main import APP

    result = APP.api.place_order(symbol="AAPL", qty=100, side="BUY")

This uses the exact same shared IB connection, trackers, and in-memory DB as gRPC,
ensuring consistent state.
"""

import argparse
import logging
import os
import signal
import sys
import time

from api import TradingAPI
from db.inmemorydb import InMemoryDB
from execution_tracker import ExecutionTracker
from position_tracker import PositionTracker
import server as grpc_server_module
from session import IBSession, DEFAULT_IB_CLIENT_ID, DEFAULT_IB_HOST, DEFAULT_IB_PORT


# Global handle to allow in-process code to access raw API (as opposed to using the gRPC layer)
# API is available via app.api
APP = None


class App:
    """Owns shared state and the gRPC server lifecycle."""

    def __init__(self, grpc_addr, ib_host, ib_port, ib_client_id, enable_drainer=False, drainer_worker_id='core-drainer'):
        """Initialize the App container.

        Args:
            grpc_addr: str - Full address for the gRPC server, e.g. "<host>:<port>" or a unix socket.
            ib_host: str - IB Gateway/TWS host.
            ib_port: int - IB Gateway/TWS port.
            ib_client_id: int - IB API client id.
            enable_drainer: bool - Start Django outbox drainer if available.
            drainer_worker_id: str - Worker id tag for checkpointing.
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
        self.api = None

        # gRPC server handle
        self.server = None

        # Optional drainer
        self._drainer = None

        self._stopping = False

    def start(self):
        """Connect to IB, start trackers, create API, and start gRPC server."""
        # Connect once
        self.ib_session.connect()
        # These should trigger a failure if the IB loop is unavailable. See session.py
        assert self.ib_session.ib.isConnected()
        assert getattr(self.ib_session.ib, 'loop', None) is not None

        # Trackers share the same IB + DB
        self.position_tracker = PositionTracker(self.ib_session.ib, db=self.db)
        self.position_tracker.start()
        self.execution_tracker = ExecutionTracker(self.ib_session.ib, db=self.db)
        self.execution_tracker.start()

        # Shared API instance
        self.api = TradingAPI(self.ib_session.ib, self.db, position_tracker=self.position_tracker)

        # Start gRPC using the shared instances (do not block)
        self.server, _handles = grpc_server_module.serve(
            address=self.grpc_addr,
            db=self.db,
            ib=self.ib_session, # OK to pass the session, serve() will normalize to ib
            api=self.api,
            position_tracker=self.position_tracker,
            execution_tracker=self.execution_tracker,
            auto_connect=False,  # Already connected
            start_trackers=False,  # Trackers already started here.
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

        try:
            self.ib_session.disconnect()

        except Exception:
            logging.exception("Error disconnecting IB session.")

        logging.info("Shutdown complete.")


def _parse_args(argv):
    """Parse CLI arguments.

    Args:
        argv: list - Command line args.

    Returns:
        argparse.Namespace - Parsed arguments.
    """
    p = argparse.ArgumentParser(description="Trading app entrypoint (shared IB + DB + gRPC).")
    p.add_argument("--grpc-addr", default=grpc_server_module.DEFAULT_SERVER_ADDRESS)
    p.add_argument("--ib-host", default=DEFAULT_IB_HOST)
    p.add_argument("--ib-port", type=int, default=DEFAULT_IB_PORT)
    p.add_argument("--ib-client-id", type=int, default=DEFAULT_IB_CLIENT_ID)
    p.add_argument("--enable-drainer", action="store_true", default=os.getenv("ENABLE_DRAINER", "0") == "1")
    p.add_argument("--drainer-worker-id", default="core-drainer")
    p.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))

    return p.parse_args(argv)


def main(argv=None):
    """Main entrypoint.

    Args:
        argv: list - Optional CLI args.

    Returns:
        int - Exit code (0 success).
    """
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )

    app = App(
        grpc_addr=args.grpc_addr,
        ib_host=args.ib_host,
        ib_port=args.ib_port,
        ib_client_id=args.ib_client_id,
        enable_drainer=args.enable_drainer,
        drainer_worker_id=args.drainer_worker_id,
    )

    try:
        app.start()
        app.wait_forever()
        return 0

    except Exception:
        logging.exception("Fatal error in main()")
        try:
            app.shutdown()

        except Exception:
            pass

        return 1

    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt.")
        try:
            app.shutdown()
            return 0

        except Exception as e:
            logging.exception(f"Error encountered while shutting down: {e}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
