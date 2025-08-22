"""Entrypoint

Bootstraps the application by:
- Loading environment variables from env.py (must exist, based on sample_env.py).
- Starting the main runtime with IB connectivity, database integration, and a gRPC server.
- Configuring logging.

## How to run

From the project root, run:

    python -m main [OPTIONS]

### Options

--grpc-addr ADDR
    Address for the gRPC server to bind to.
    Default: DEFAULT_SERVER_ADDRESS (e.g. "0.0.0.0:50057"), configured in the env file.

--ib-host HOST
    Hostname or IP of the IB Gateway/TWS instance.
    Default: "127.0.0.1".

--ib-port PORT
    Port number for IB Gateway/TWS connection.
    Default: 7497.

--ib-client-id ID
    Client ID for the IB API session (must be unique per client).
    Default: 1.

--enable-drainer
    Enable the DB drainer process (for syncing in-memory db to Postgres).
    By default, enabled if USE_PERSISTENT_DB=1 in the environment.

--drainer-worker-id ID
    Identifier for the drainer worker instance.
    Default: "core-drainer".

--log-level LEVEL
    Logging verbosity. One of: DEBUG, INFO, WARNING, ERROR, CRITICAL.
    Default: LOG_LEVEL env var or "INFO".

### Example usage

# Run with defaults from env.py
python -m main

# Override gRPC bind address and increase verbosity
python -m main --grpc-addr 0.0.0.0:50058 --log-level DEBUG

# Connect to a remote IB Gateway/TWS with a specific client id
python -m main --ib-host 10.0.0.5 --ib-port 4002 --ib-client-id 3

# Enable drainer explicitly and set a custom worker id
python -m main --enable-drainer --drainer-worker-id core-drainer-1
"""

import argparse
import logging
import os
import sys

# Do this before everything else as it sets env variables used throughout the app.
try:
    import env

except ImportError:
    logging.error("env.py not found. Create and populate it based on sample_env.py.")
    sys.exit(1)

from runtime import start_app
from server import DEFAULT_SERVER_ADDRESS
from session import DEFAULT_IB_CLIENT_ID, DEFAULT_IB_HOST, DEFAULT_IB_PORT


def _parse_args(argv):
    """Parse CLI arguments.

    Args:
        argv: list - Command line args.

    Returns:
        argparse.Namespace - Parsed arguments.
    """
    p = argparse.ArgumentParser(
        description="App entrypoint (shared IB + DB + gRPC). "
                    "See module docstring for details."
    )
    p.add_argument(
        "--grpc-addr",
        default=DEFAULT_SERVER_ADDRESS,
        help="Address for the gRPC server to bind to. "
             'Default: DEFAULT_SERVER_ADDRESS (e.g. "0.0.0.0:50057"), configured in the env file.'
    )
    p.add_argument(
        "--ib-host",
        default=DEFAULT_IB_HOST,
        help='Hostname or IP of the IB Gateway/TWS instance. '
             'Default: "127.0.0.1".'
    )
    p.add_argument(
        "--ib-port",
        type=int,
        default=DEFAULT_IB_PORT,
        help="Port number for IB Gateway/TWS connection. "
             "Default: 7497."
    )
    p.add_argument(
        "--ib-client-id",
        type=int,
        default=DEFAULT_IB_CLIENT_ID,
        help="Client ID for the IB API session (must be unique per client). "
             "Default: 1."
    )
    p.add_argument(
        "--enable-drainer",
        action="store_true",
        default=os.getenv("USE_PERSISTENT_DB", "0") == "1",
        help="Enable the DB drainer process (for syncing in-memory db to Postgres). "
             "By default, enabled if USE_PERSISTENT_DB=1 in the environment."
    )
    p.add_argument(
        "--drainer-worker-id",
        default="core-drainer",
        help='Identifier for the drainer worker instance. '
             'Default: "core-drainer".'
    )
    p.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help='Logging verbosity. One of: DEBUG, INFO, WARNING, ERROR, CRITICAL. '
             'Default: LOG_LEVEL env var or "INFO".'
    )

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

    try:
        app = start_app(
            grpc_addr=args.grpc_addr,
            ib_host=args.ib_host,
            ib_port=args.ib_port,
            ib_client_id=args.ib_client_id,
            enable_drainer=args.enable_drainer,
            drainer_worker_id=args.drainer_worker_id,
        )
        app.wait_forever()
        return 0
    except Exception:
        logging.exception("Fatal error in main()")
        try:
            # best-effort cleanup
            app.shutdown()  # if app was created
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
