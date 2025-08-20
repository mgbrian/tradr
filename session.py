"""IB Session Manager.

TODO:
- Enforce singleton IBSession per client to ensure system-wide consistency with
  the IB (asyncio) loop.
  See connect().
"""
import asyncio
import inspect
import logging
import os
import threading
import time

from ib_async import IB, util


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
    """Interactive Brokers session manager using ib_async.

    Ensures the asyncio event loop is *running* in a background thread so that
    cross-thread calls (e.g. via OrderManager using run_coroutine_threadsafe)
    do not time out.
    """

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
        self.loop = None  # asyncio loop used by IB, will be set on connect
        self._loop_thread = None  # background thread running loop.run_forever()
        self._owns_loop_thread = False

    def connect(self, *, auto_open_orders=True, seed_open_orders=True, seed_all_open_orders=False,
        seed_completed_orders=False, completed_api_only=False):
        """Connect to IB Gateway/TWS and ensure the IB asyncio loop is running.

        Also (optionally) enables/requests order synchronization from TWS so orders
        created/modified/cancelled in the TWS GUI are reflected in this client:

        - auto_open_orders=True calls reqAutoOpenOrders(True) so subsequent TWS changes stream in.
        - seed_open_orders=True calls reqOpenOrders() to fetch current open orders for this client.
        - seed_all_open_orders=True calls reqAllOpenOrders() (requires master permissions).
        - seed_completed_orders=True calls reqCompletedOrders(apiOnly=completed_api_only) to backfill
          recent completed orders (fills/cancels), useful on startup.

        Returns:
            bool - True if connected and loop running.

        Raises:
            RuntimeError - If connection fails or loop cannot be started.
        """
        logger.info(f"Connecting to IB at {self.host}:{self.port} (clientId={self.client_id})...")
        # Uncomment for debugging
        # util.logToConsole('DEBUG')

        # Connect synchronously
        self.ib.connect(self.host, self.port, clientId=self.client_id)
        if not self.ib.isConnected():
            raise RuntimeError("Failed to connect to IB Gateway/TWS")

        # Pin / start loop (unchanged)
        loop = util.getLoop()
        self.loop = loop
        # Make the loop discoverable by downstream code (OrderManager uses this)
        setattr(self.ib, 'loop', self.loop)

        # If the loop isn't running, start it in a background thread
        if not self.loop.is_running():
            logger.debug("IB asyncio loop not running; starting background loop thread...")
            self._loop_thread = threading.Thread(
                target=self.loop.run_forever, name="ib-asyncio", daemon=True
            )
            self._loop_thread.start()

            # Spin briefly to ensure the loop begins running
            for _ in range(200):  # ~2s max (200 * 0.01)
                if self.loop.is_running():
                    break
                time.sleep(0.01)

            if not self.loop.is_running():
                raise RuntimeError("Failed to start IB asyncio loop")

            self._owns_loop_thread = True
            logger.debug("IB asyncio loop is running in background thread.")

        def _schedule_async(awaitable, label):
            """Schedule a coroutine/awaitable on the IB loop without blocking.

            Accepts:
                - coroutine objects  -> scheduled via run_coroutine_threadsafe
                - Future/Task/other awaitables -> wrapped and scheduled on the loop
                - None/non-awaitables -> treated as fire-and-forget (no-op)
            Logs any eventual exception via a done-callback.
            """
            try:
                if awaitable is None:
                    logger.debug("%s returned None; nothing to await", label)
                    return

                # Case 1: proper coroutine
                if asyncio.iscoroutine(awaitable):
                    cfut = asyncio.run_coroutine_threadsafe(awaitable, self.loop)

                    def _log_done(f):
                        try:
                            f.result()
                        except Exception:
                            logger.exception("%s failed (async)", label)

                    cfut.add_done_callback(_log_done)
                    return

                # Case 2: awaitable that's not a coroutine (e.g., Future/Task bound to the IB loop)
                if inspect.isawaitable(awaitable):
                    async def _await_it(x):
                        return await x

                    def _on_loop():
                        try:
                            task = asyncio.create_task(_await_it(awaitable))
                            def _done(t):
                                exc = t.exception()
                                if exc:
                                    logger.exception("%s failed (async)", label, exc_info=exc)
                            task.add_done_callback(_done)
                        except Exception:
                            logger.exception("Failed to schedule %s on loop", label)

                    # Ensure we create the task from within the IB loop thread
                    self.loop.call_soon_threadsafe(_on_loop)
                    return

                # Non-awaitable: nothing to do
                logger.debug("%s returned a non-awaitable (%r); treating as no-op", label, awaitable)

            except Exception:
                logger.exception("Failed to schedule %s", label)

        # --- Order sync & seeding from TWS (best-effort; failures won't abort connect) ---

        try:
            if auto_open_orders:
                # Direct client call; no util.run involved
                self.ib.reqAutoOpenOrders(True)
        except Exception:
            logger.exception("Failed to enable auto-open orders streaming (reqAutoOpenOrders)")

        try:
            if seed_open_orders:
                if self.loop.is_running():
                    _schedule_async(self.ib.reqOpenOrdersAsync(), "request open orders (reqOpenOrdersAsync)")
                else:
                    self.ib.reqOpenOrders()
        except Exception:
            logger.exception("Failed to request open orders")

        try:
            if seed_all_open_orders:
                if self.loop.is_running():
                    _schedule_async(self.ib.reqAllOpenOrdersAsync(), "request all open orders (reqAllOpenOrdersAsync)")
                else:
                    self.ib.reqAllOpenOrders()
        except Exception:
            logger.exception("Failed to request all open orders")

        try:
            if seed_completed_orders:
                if self.loop.is_running():
                    _schedule_async(
                        self.ib.reqCompletedOrdersAsync(apiOnly=bool(completed_api_only)),
                        "request completed orders (reqCompletedOrdersAsync)"
                    )
                else:
                    self.ib.reqCompletedOrders(apiOnly=bool(completed_api_only))
        except Exception:
            logger.exception("Failed to request completed orders")

    def disconnect(self):
        """Disconnect from IB Gateway/TWS and stop the background loop if owned.

        Returns:
            bool - True if disconnected successfully, False otherwise.
        """
        ok = False
        try:
            if self.ib.isConnected():
                self.ib.disconnect()
                ok = True
                logger.info("Disconnected from IB Gateway/TWS")

            else:
                logger.warning("Already disconnected from IB Gateway/TWS")

        finally:
            # If we started the loop thread, stop and close the loop cleanly
            if self._owns_loop_thread and self.loop:
                try:
                    if self.loop.is_running():
                        # Stop the loop from its own thread
                        self.loop.call_soon_threadsafe(self.loop.stop)
                        # Join the runner thread
                        if self._loop_thread is not None:
                            self._loop_thread.join(timeout=2.0)
                    # Close the loop if not already closed
                    if not self.loop.is_closed():
                        self.loop.close()
                    logger.debug("IB asyncio loop stopped and closed.")

                except Exception:
                    logger.exception("Error while stopping/closing IB asyncio loop")

                finally:
                    self._owns_loop_thread = False
                    self._loop_thread = None
                    self.loop = None

                    # Also remove the attribute from ib to avoid stale references
                    if hasattr(self.ib, 'loop'):
                        try:
                            delattr(self.ib, 'loop')
                        except Exception:
                            pass

        return ok

    def is_connected(self):
        """Check if the session is connected.

        Returns:
            bool - True if connected, False otherwise.
        """
        return self.ib.isConnected()

    def ensure_order_ids_ready(self, timeout=15.0):
        """Block until IB has delivered nextValidId so orders can be placed.

        If nextValidId not received, orders cannot be placed.

        Call session.ensure_order_ids_ready() once after connect or from
        OrderManager on first submit.

        """
        loop = getattr(self.ib, 'loop', None)
        if loop is None:
            raise RuntimeError("IB event loop not pinned")

        async def _prime():
            # If IDs not yet available, request and wait briefly
            if getattr(self.ib.client, '_reqIdSeq', None) is None:
                self.ib.reqIds(1)
                for _ in range(300):  # ~15s at 50ms
                    if getattr(self.ib.client, '_reqIdSeq', None) is not None:
                        return True
                    await asyncio.sleep(0.05)
                return False

            return True

        fut = asyncio.run_coroutine_threadsafe(_prime(), loop)
        ok = fut.result(timeout)

        if not ok:
            raise TimeoutError("Timed out waiting for IB nextValidId")

    def _debug_ib_state(self):
        """Debug util to check IB object status."""
        ib = self.ib
        loop = getattr(ib, 'loop', None)

        return {
            'connected': ib.isConnected(),
            'loop_attached': bool(loop),
            'loop_running': bool(getattr(loop, 'is_running', lambda: False)()),
            'has_req_id_seq': getattr(getattr(ib, 'client', None), '_reqIdSeq', None) is not None,
            'order_id': getattr(getattr(ib, 'client', None), 'orderId', None),
        }
