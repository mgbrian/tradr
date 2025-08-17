"""Parity tests between the raw in-process API (TradingAPI) and the gRPC client (TradingClient).

For developer UX and code consistency.

Contract:
- The gRPC client API is a *superset* of the raw API.
- For every public method on TradingAPI (non-underscore, callable):
  - TradingClient must expose a method with the same name, OR a PascalCase
    counterpart (e.g. place_stock_order -> [PlaceStockOrder or place_stock_order]).
  - The client method must not require *more* non-optional parameters than the raw API.
    (Extra optional parameters on the client, like `timeout`, are allowed.)

TODO:
- Constants, globals, return types, etc.
"""

import inspect
import unittest

from api import TradingAPI
from client import TradingClient


def _is_public_callable(cls, name):
    if name.startswith("_"):
        return False
    try:
        attr = getattr(cls, name)
    except AttributeError:
        return False
    return callable(attr)


def _snake_to_pascal(name):
    return "".join(part.capitalize() or "_" for part in name.split("_"))


def _required_param_names(func):
    """Return a set of required (non-default) parameter names, excluding 'self'."""
    sig = inspect.signature(func)
    required = set()

    for p in sig.parameters.values():
        if p.name == "self":
            continue

        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY):
            if p.default is inspect._empty:
                required.add(p.name)
        # VAR_POSITIONAL (*args) or VAR_KEYWORD (**kwargs) means we cannot reason
        # about strictness; we treat raw method as flexible in that case.
    return required


def _has_varargs(func):
    sig = inspect.signature(func)
    return any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in sig.parameters.values())


class TestGrpcApiParity(unittest.TestCase):
    def test_grpc_is_superset_of_raw_api(self):
        """For every public method on TradingAPI, TradingClient has a compatible method."""
        raw_methods = [name for name in dir(TradingAPI) if _is_public_callable(TradingAPI, name)]

        # Methods that are not part of the business API (lifecycle/helpers) can be ignored here if present.
        # Keep this list minimal.
        ignore = {"close"}  # TradingClient has close(); TradingAPI may not.
        raw_methods = [m for m in raw_methods if m not in ignore]

        missing = []
        incompatible = []

        for raw_name in raw_methods:
            raw_func = getattr(TradingAPI, raw_name)

            # Find the client method: prefer same snake_case name; else try PascalCase (RPC)
            if hasattr(TradingClient, raw_name) and callable(getattr(TradingClient, raw_name)):
                client_name = raw_name

            else:
                rpc_name = _snake_to_pascal(raw_name)
                if hasattr(TradingClient, rpc_name) and callable(getattr(TradingClient, rpc_name)):
                    client_name = rpc_name

                else:
                    missing.append((raw_name, "no matching method (snake_case or PascalCase)"))
                    continue

            client_func = getattr(TradingClient, client_name)

            # If raw uses *args/**kwargs, we can't demand strict param matching; skip strictness check.
            if _has_varargs(raw_func):
                continue

            raw_required = _required_param_names(raw_func)
            client_required = _required_param_names(client_func)

            # Client must not require MORE parameters than raw (superset must be easier to call).
            # i.e. all raw required params must be required by client (or at least present),
            # and client must not have extra required params not in raw.
            extra_required_on_client = client_required - raw_required
            missing_required_on_client = raw_required - set(inspect.signature(client_func).parameters.keys())

            if extra_required_on_client:
                incompatible.append(
                    (raw_name, client_name, f"client has extra required params: {sorted(extra_required_on_client)}")
                )

            if missing_required_on_client:
                incompatible.append(
                    (raw_name, client_name, f"client missing raw params: {sorted(missing_required_on_client)}")
                )

        err_msgs = []
        if missing:
            for raw_name, reason in missing:
                err_msgs.append(f"- {raw_name}: {reason}")

        if incompatible:
            for raw_name, client_name, reason in incompatible:
                err_msgs.append(f"- {raw_name} -> {client_name}: {reason}")

        if err_msgs:
            self.fail("gRPC client is not a superset of the raw API:\n" + "\n".join(err_msgs))


if __name__ == "__main__":
    unittest.main()
