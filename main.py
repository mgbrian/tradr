"""Test Playground."""

import asyncio
import logging
import os

from session import IBSession
from contracts import create_stock_contract, create_option_contract

logger = logging.getLogger(__name__)


async def main():
    session = IBSession()

    try:
        _ = session.connect()
    except RuntimeError:
        logger.error("Couldn't connect to IB. Exiting...")
        exit()

    # Test creating contracts
    aapl_stock = create_stock_contract('AAPL')
    spy_call = create_option_contract('SPY', '20250919', 450, 'C')

    print("Stock contract:", aapl_stock)
    print("Option contract:", spy_call)

    session.disconnect()

if __name__ == "__main__":
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(name)s] %(levelname)s: %(message)s"
    )
    asyncio.run(main())
