"""Development Test Playground."""

import asyncio
import logging

from session import SessionManager
from contracts import stock_contract, option_contract


async def main():
    session = SessionManager()
    await session.connect()
    await session.wait_until_connected(timeout=10)

    # Test creating contracts
    aapl_stock = stock_contract('AAPL')
    spy_call = option_contract('SPY', '20250919', 450, 'C')

    print("Stock contract:", aapl_stock)
    print("Option contract:", spy_call)

    session.disconnect()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
