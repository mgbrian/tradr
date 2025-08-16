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
    p = argparse.ArgumentParser(description="Trading app entrypoint (shared IB + DB + gRPC).")
    p.add_argument("--grpc-addr", default=DEFAULT_SERVER_ADDRESS)
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
